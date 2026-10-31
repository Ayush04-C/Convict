"""Real specialist agents driven through the real ledger (phases, ownership, evidence rules).

Everything is SYNTHETIC: a Retool-shaped company_data dict and an inflated deck. Models are
scripted fakes, so no provider is called. The ledger is the real SQLite ledger, which enforces
the same rules the live pipeline will.
"""
import asyncio
import json

import pytest

from agents.engine import (
    MAX_EVIDENCE_CHARS, ChallengeDrafts, ReplyBundle, _dossier, _evidence_json, confidence_of,
    score_findings,
)
from agents.funding import FundingAgent
from agents.identity import IdentityAgent
from agents.protocol import AgentContext
from agents.scale_team import ScaleTeamAgent
from agents.traction import TractionAgent
from app.schemas import Challenge, Claim, ClaimSet, Finding, Policy
from ledger.errors import LedgerPermissionError
from leo.context import AGENTS, build_context_packets
from leo.scoring import compute
from sandbox.ledger_writer import LedgerWriter
from tests.fakes import FakeCrustdata
from tests.ledger_support import advance

MODELS = ("fast-model", "mid-model")
# Each agent receives only its own dossier groups. Use the orchestrator's scopes when they exist
# (leo.context.DOSSIER_GROUPS); the fallback mirrors docs/resolution-context.md.
try:
    from leo.context import DOSSIER_GROUPS as SCOPES
except ImportError:
    SCOPES = {
        "identity": ("basic_info", "locations", "people", "news"),
        "scale_team": ("basic_info", "headcount", "employee_reviews"),
        "funding": ("basic_info", "funding", "revenue", "headcount"),
        "traction": ("basic_info", "hiring", "web_traffic", "followers"),
    }
POLICY = Policy(max_check_usd=100_000)


def person(name, title):
    return {"basic_profile": {"name": name, "current_title": title}}


COMPANY = {
    "basic_info": {"status": "active", "primary_domain": "acme.com", "all_domains": ["acme.com"],
                   "year_founded": 2017},
    "locations": {"city": "San Francisco", "state": "California", "country": "USA",
                  "headquarters": "San Francisco, California, United States", "all_office_addresses": []},
    "people": {"founders": [person("David Hsu", "Founder, CEO"), person("Anthony G.", "Founder, CTO")],
               "cxos": [], "decision_makers": []},
    "news": [{"article_title": "Acme launches a product", "article_publish_date": "2026-10-05"}],
    "headcount": {"total": 413, "growth_percent": {"yoy": -10.4},
                  "by_role_percent": {"Engineering": 28.6, "Sales": 12.6}},
    "employee_reviews": {"overall_rating": {"rating": 3.8, "total_count": 131}},
    "funding": {"total_investment_usd": 141_000_000.0, "last_round_amount_usd": 45_000_000.0,
                "last_round_type": "series_c", "last_fundraise_date": "2022-07-27",
                "investors": ["Sequoia Capital"], "milestones": [{"round_type": "series_c"}]},
    "revenue": {"estimated": {"lower_bound_usd": 20_000_000, "upper_bound_usd": 50_000_000}},
    "hiring": {"openings_count": 1, "openings_growth_percent": {"qoq": -0.93}},
    "web_traffic": {"domain_traffic": {"acme.com": {"monthly_visitors": 1_260_000.0, "qoq_pct": -23.3,
                                                    "source_search_pct": 11.0, "source_direct_pct": 72.0}}},
    "followers": {"count": 43706, "yoy_percent": 22.8},
}


def claim(cid, topic, text, value=None, unit=None, period=None, testable=True):
    return Claim(id=cid, topic=topic, text=text, value=value, unit=unit, period=period, slide=1,
                 testable=testable)


CLAIMS = (
    claim("C1", "identity_founding", "Founded in 2017", 2017.0, "year"),
    claim("C2", "identity_founder", "Founded by David Hsu and Anthony Gao"),
    claim("C3", "team_size", "400 employees", 400.0, "count"),
    claim("C4", "team_growth", "Headcount up 100% YoY", 100.0, "percent", "YoY"),
    claim("C5", "funding_raised", "Raised $141M", 141_000_000.0, "USD"),
    claim("C6", "funding_round", "Series C", "Series C"),
    claim("C7", "revenue_arr", "$200M ARR", 200_000_000.0, "USD"),
    claim("C8", "hiring", "We are hiring aggressively"),
    claim("C9", "traction_customers", "500 customers", 500.0, "count"),
    claim("C10", "revenue_growth", "Revenue grew 3x"),
    claim("C11", "product_other", "Best in class", testable=False),
)


class QueueLlm:
    """Scripted model: returns queued replies (dict, or an Exception to raise); records prompts."""

    def __init__(self, *responses):
        self.responses, self.prompts = list(responses), []

    async def generate(self, prompt, schema, model):
        self.prompts.append((prompt, schema, model))
        if not self.responses:
            raise AssertionError("unexpected model call")
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return schema.model_validate(reply)


def make_agents():
    return [IdentityAgent(models=MODELS), ScaleTeamAgent(models=MODELS), FundingAgent(models=MODELS),
            TractionAgent(models=MODELS)]


async def setup_run(ledger, dossier=COMPANY, claims=CLAIMS, stub=False, llms=None):
    run_id = await ledger.create_run("tester", "Acme", None, POLICY, "deck.pdf")
    await advance(ledger, run_id, "INGEST")
    claim_set = ClaimSet(company_name="Acme", claims=claims, is_stub=stub)
    await ledger.add_claims(run_id, claim_set)
    await advance(ledger, run_id, "RESOLVE")
    slices = {a: json.dumps({k: dossier[k] for k in SCOPES[a] if k in dossier}) for a in AGENTS}
    packets = {p.agent: p for p in build_context_packets(claim_set, slices, POLICY)}
    await ledger.save_context_packets(run_id, tuple(packets.values()))
    llms = llms or {}
    contexts = {a: AgentContext(run_id, packets[a], LedgerWriter(run_id, a, ledger), FakeCrustdata(),
                                llms.get(a, QueueLlm())) for a in AGENTS}
    return run_id, contexts, claim_set


async def run_phase(ledger, run_id, agents, contexts, phase, method):
    await advance(ledger, run_id, phase)

    async def go(agent):
        await ledger.set_agent_status(run_id, agent.name, phase, "RUNNING")
        await getattr(agent, method)(contexts[agent.name])
        await ledger.set_agent_status(run_id, agent.name, phase, "DONE")

    await asyncio.gather(*(go(a) for a in agents))


async def finding_of(ledger, run_id, agent, claim_id=None, check_note=None):
    for f in await ledger.get_all_findings(run_id):
        if f.agent == agent and f.claim_id == claim_id and (check_note is None or check_note in f.note):
            return f
    raise AssertionError(f"no finding for {agent} {claim_id}")


# ---- helpers ---------------------------------------------------------------------------

def test_evidence_json_keeps_small_values_and_labels_truncated_ones():
    assert json.loads(_evidence_json({"a": 1})) == {"a": 1}
    big = _evidence_json({"rows": ["x" * 1000] * 200})
    parsed = json.loads(big)
    assert parsed["_truncated"] is True and parsed["_chars"] > MAX_EVIDENCE_CHARS
    assert len(parsed["preview"]) == MAX_EVIDENCE_CHARS
    assert json.loads(_evidence_json(float("nan"))) == "nan"   # never invalid JSON for the ledger


@pytest.mark.parametrize("raw, expected", [
    ('{"basic_info": {"status": "active"}}', {"basic_info": {"status": "active"}}),
    ('{"company_data": {"headcount": {"total": 3}}}', {"headcount": {"total": 3}}),
    ("not json", {}), ("[1, 2]", {}), ("", {}),
])
def test_dossier_parsing_is_tolerant(raw, expected):
    from app.schemas import ContextPacket
    packet = ContextPacket(agent="funding", claims=(), policy=POLICY, dossier_json=raw)
    assert _dossier(packet) == expected


def make_finding(status, severity, final=None, fid="F"):
    return Finding(finding_id=fid, agent="funding", status=status, severity=severity,
                   severity_final=final, note="n")


def test_score_penalizes_only_contradicted_findings_at_effective_severity():
    assert score_findings([make_finding("CONTRADICTED", 5)]) == 76.0
    assert score_findings([make_finding("CONTRADICTED", 5, final=3)]) == 88.0   # revision counts
    assert score_findings([make_finding("PARTIAL", 5), make_finding("UNVERIFIABLE", 5)]) == 100.0
    assert score_findings([make_finding("CONTRADICTED", 5)] * 6) == 0.0


def test_confidence_is_the_verified_share():
    findings = [make_finding("VERIFIED", 1), make_finding("PARTIAL", 2), make_finding("UNVERIFIABLE", 1)]
    assert confidence_of(findings) == pytest.approx(2 / 3)
    assert confidence_of([]) == 0.0


# ---- round 1: findings from code, cited, accepted by the real ledger ----------------------

async def test_round_one_writes_cited_findings_for_every_testable_claim(ledger):
    run_id, contexts, _ = await setup_run(ledger)
    agents = make_agents()
    await run_phase(ledger, run_id, agents, contexts, "ROUND1", "run_round1")
    findings = await ledger.get_all_findings(run_id)   # privileged coordinator read
    by_agent = {a: [f for f in findings if f.agent == a] for a in AGENTS}
    assert {a: len(v) for a, v in by_agent.items()} == {
        "identity": 4, "scale_team": 5, "funding": 6, "traction": 4}
    evidence = {e.evidence_id: e for e in await ledger.get_evidence(run_id)}
    for finding in findings:
        assert finding.round == 1 and finding.is_stub is False
        assert finding.finding_id.startswith(finding.agent + ":F")
        if finding.status != "UNVERIFIABLE":
            assert finding.evidence_ids, "every verifiable finding must cite evidence"
        for evidence_id in finding.evidence_ids:
            assert evidence[evidence_id].agent == finding.agent       # only its own evidence
            assert evidence[evidence_id].source == "crustdata.company.enrich"
    claimed = {f.claim_id for f in findings if f.claim_id}
    assert claimed == {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10"}   # C11 untestable


async def test_round_one_verdicts_match_the_analysts(ledger):
    run_id, contexts, _ = await setup_run(ledger)
    await run_phase(ledger, run_id, make_agents(), contexts, "ROUND1", "run_round1")
    arr = await finding_of(ledger, run_id, "funding", "C7")
    assert (arr.status, arr.severity) == ("CONTRADICTED", 5)
    assert (await finding_of(ledger, run_id, "scale_team", "C4")).status == "CONTRADICTED"
    assert (await finding_of(ledger, run_id, "scale_team", "C3")).status == "VERIFIED"
    assert (await finding_of(ledger, run_id, "traction", "C8")).status == "CONTRADICTED"
    assert (await finding_of(ledger, run_id, "traction", "C9")).status == "UNVERIFIABLE"
    assert (await finding_of(ledger, run_id, "funding", "C10")).status == "UNVERIFIABLE"   # no analyst
    assert (await finding_of(ledger, run_id, "identity", "C2")).status == "PARTIAL"        # "Anthony G."


async def test_gate_proposals_are_recorded_as_evidenced_metrics(ledger):
    run_id, contexts, _ = await setup_run(ledger)
    await run_phase(ledger, run_id, make_agents(), contexts, "ROUND1", "run_round1")
    metrics = await ledger.get_metrics(run_id)
    gates = [m for m in metrics if m.name.startswith("gate:")]
    assert [(m.agent, m.name, m.value, m.unit) for m in gates] == [
        ("funding", "gate:arr_ratio_over_3", 1.0, "proposal")]
    assert gates[0].evidence_ids, "a gate proposal must carry evidence"
    ratio = next(m for m in metrics if m.name == "arr_ratio[C7]")
    assert ratio.value == pytest.approx(4.0)


async def test_round_one_never_calls_a_model(ledger):
    llm = QueueLlm()  # raises AssertionError on any call
    run_id, contexts, _ = await setup_run(ledger, llms={a: llm for a in AGENTS})
    await run_phase(ledger, run_id, make_agents(), contexts, "ROUND1", "run_round1")
    assert llm.prompts == []


async def test_missing_or_garbled_dossier_gives_unverifiable_findings_not_a_crash(ledger):
    run_id, contexts, _ = await setup_run(ledger, dossier={})
    await run_phase(ledger, run_id, make_agents(), contexts, "ROUND1", "run_round1")
    findings = await ledger.get_all_findings(run_id)
    assert findings and {f.status for f in findings} == {"UNVERIFIABLE"}
    assert await ledger.get_evidence(run_id) == ()


async def test_a_broken_check_becomes_an_unverifiable_finding(ledger):
    def explode(claim, data):
        raise RuntimeError("boom")

    class Fragile(ScaleTeamAgent):
        topic_checks = {**ScaleTeamAgent.topic_checks, "team_size": explode}

    run_id, contexts, _ = await setup_run(ledger)
    agent = Fragile(models=MODELS)
    await advance(ledger, run_id, "ROUND1")
    await agent.run_round1(contexts["scale_team"])
    finding = await finding_of(ledger, run_id, "scale_team", "C3")
    assert finding.status == "UNVERIFIABLE" and "RuntimeError" in finding.note
    assert "boom" not in finding.note       # no internal error text leaks into findings


async def test_stub_packets_mark_findings_as_stubs(ledger):
    run_id, contexts, _ = await setup_run(ledger, stub=True)
    await advance(ledger, run_id, "ROUND1")
    await FundingAgent(models=MODELS).run_round1(contexts["funding"])
    assert all(f.is_stub for f in await ledger.get_all_findings(run_id))


async def test_an_agent_refuses_another_agents_context(ledger):
    run_id, contexts, _ = await setup_run(ledger)
    await advance(ledger, run_id, "ROUND1")
    with pytest.raises(ValueError, match="another specialist"):
        await FundingAgent(models=MODELS).run_round1(contexts["traction"])


# ---- challenge round -------------------------------------------------------------------

async def after_round_one(ledger, **kwargs):
    run_id, contexts, claim_set = await setup_run(ledger, **kwargs)
    await run_phase(ledger, run_id, make_agents(), contexts, "ROUND1", "run_round1")
    await advance(ledger, run_id, "CHALLENGE")
    return run_id, contexts, claim_set


async def test_cross_agent_findings_are_unreadable_before_the_barrier(ledger):
    run_id, contexts, _ = await setup_run(ledger)
    await advance(ledger, run_id, "ROUND1")
    with pytest.raises(LedgerPermissionError, match="before the round-one barrier"):
        await TractionAgent(models=MODELS).run_challenge(contexts["traction"])


async def test_valid_challenges_are_written_and_invalid_ones_dropped(ledger):
    run_id, contexts, _ = await after_round_one(ledger)
    arr = await finding_of(ledger, run_id, "funding", "C7")
    growth = await finding_of(ledger, run_id, "scale_team", "C4")
    size = await finding_of(ledger, run_id, "scale_team", "C3")
    own = await finding_of(ledger, run_id, "traction", "C8")
    cited = [e.evidence_id for e in await ledger.get_evidence(run_id) if e.agent == "traction"][0]

    def draft(finding_id, kind, evidence_ids, argument="Specific argument"):
        return {"finding_id": finding_id, "type": kind, "argument": argument, "evidence_ids": evidence_ids}

    llm = QueueLlm({"challenges": [
        draft(own.finding_id, "GAP", [cited]),                          # own finding: dropped
        draft("funding:F999", "GAP", [cited]),                          # unknown finding: dropped
        draft(arr.finding_id, "CONTEXT", ["nope:E1"]),                  # only unknown evidence: dropped
        draft(arr.finding_id, "CORROBORATES", [cited, "nope:E1"], "  Traffic\n fell 23%  "),  # kept, cleaned
        draft(arr.finding_id, "GAP", [cited]),                          # same target again: dropped
        draft(growth.finding_id, "CONTEXT", [cited]),                   # kept
        draft(size.finding_id, "CONTEXT", [cited]),                     # a third: beyond the limit of two
    ]})
    with_llm(contexts, "traction", llm)
    await TractionAgent(models=MODELS).run_challenge(contexts["traction"])
    to_funding = await ledger.get_challenges_for(run_id, "funding")
    to_scale = await ledger.get_challenges_for(run_id, "scale_team")
    assert [(c.challenge_id, c.finding_id, c.type) for c in to_funding] == [
        ("traction:C1", arr.finding_id, "CORROBORATES")]
    assert to_funding[0].argument == "Traffic fell 23%"          # whitespace collapsed
    assert to_funding[0].evidence_ids == (cited,)                # the invented id was stripped
    assert [(c.challenge_id, c.finding_id) for c in to_scale] == [("traction:C2", growth.finding_id)]


async def test_the_challenge_prompt_carries_other_findings_evidence_and_the_data_warning(ledger):
    run_id, contexts, _ = await after_round_one(ledger)
    llm = QueueLlm({"challenges": []})
    ctx = AgentContext(run_id, contexts["traction"].packet, contexts["traction"].ledger, FakeCrustdata(), llm)
    await TractionAgent(models=MODELS).run_challenge(ctx)
    prompt, schema, model = llm.prompts[0]
    assert schema is ChallengeDrafts and model == "fast-model"
    assert "Traction and Momentum analyst" in prompt and "untrusted data" in prompt
    assert "Do not speculate about motives" in prompt
    own_part, rest = prompt.split("OTHER ANALYSTS' FINDINGS (data):")
    others_part, evidence_part = rest.split("AVAILABLE EVIDENCE (data):")
    own_part = own_part.split("YOUR OWN FINDINGS (data):")[1]
    assert "traction:F" in own_part and "funding:F" not in own_part
    for other in ("identity:F", "scale_team:F", "funding:F"):
        assert other in others_part
    assert "traction:F" not in others_part
    assert "funding:E" in evidence_part and "traction:E" in evidence_part   # evidence is shared after the barrier


async def test_a_failing_model_means_no_challenges_not_a_failed_agent(ledger):
    run_id, contexts, _ = await after_round_one(ledger)
    ctx = AgentContext(run_id, contexts["traction"].packet, contexts["traction"].ledger, FakeCrustdata(),
                       QueueLlm(RuntimeError("model down")))
    await TractionAgent(models=MODELS).run_challenge(ctx)
    assert await ledger.get_challenges_for(run_id, "funding") == ()


async def test_malformed_model_output_is_treated_like_a_failure(ledger):
    run_id, contexts, _ = await after_round_one(ledger)
    ctx = AgentContext(run_id, contexts["traction"].packet, contexts["traction"].ledger, FakeCrustdata(),
                       QueueLlm({"challenges": [{"finding_id": "x", "type": "SHOUT", "argument": "!"}]}))
    await TractionAgent(models=MODELS).run_challenge(ctx)   # invalid enum: schema validation raises
    assert await ledger.get_challenges_for(run_id, "funding") == ()


# ---- reply and verdict round -----------------------------------------------------------

async def challenged_run(ledger, *, challenger="traction", target="funding", claim_id="C7", **kwargs):
    run_id, contexts, claim_set = await after_round_one(ledger, **kwargs)
    finding = await finding_of(ledger, run_id, target, claim_id)
    evidence_id = [e.evidence_id for e in await ledger.get_evidence(run_id) if e.agent == challenger][0]
    await contexts[challenger].ledger.add_challenges((Challenge(
        challenge_id=f"{challenger}:C1", from_agent=challenger, to_agent=target,
        finding_id=finding.finding_id, type="CONTEXT", argument="Context from my area.",
        evidence_ids=(evidence_id,)),))
    await advance(ledger, run_id, "REPLY")
    return run_id, contexts, finding


def with_llm(contexts, agent, llm):
    old = contexts[agent]
    contexts[agent] = AgentContext(old.run_id, old.packet, old.ledger, old.tools, llm)


def bundle(replies, stance="Ok.", flags=()):
    return {"replies": replies, "stance": stance, "flags": list(flags)}


@pytest.mark.parametrize("proposed, action, severity", [
    (4, "REVISE", 4),      # one step down
    (3, "REVISE", 4),      # asked for two steps: allowed, but a severity-5 finding cannot go below 4
    (1, "REVISE", 4),      # asked for four steps: clamped, then floored at 4
    (5, "CONCEDE", None),  # an unchanged severity is just a concession
    (0, "CONCEDE", None),  # invalid severities cannot be applied
    (9, "CONCEDE", None),
    (None, "CONCEDE", None),
])
async def test_severity_revisions_are_bounded(ledger, proposed, action, severity):
    run_id, contexts, finding = await challenged_run(ledger)          # the ARR finding has severity 5
    assert finding.severity == 5
    reply = {"challenge_id": "traction:C1", "action": "REVISE", "text": "Persuaded.", "new_severity": proposed}
    with_llm(contexts, "funding", QueueLlm(bundle([reply])))
    await FundingAgent(models=MODELS).run_reply_verdict(contexts["funding"])
    (stored,) = await ledger.get_replies(run_id)
    assert (stored.action, stored.new_severity) == (action, severity)
    effective = await finding_of(ledger, run_id, "funding", "C7")
    assert (effective.severity_final or effective.severity) == (severity or 5)


async def test_a_reply_can_raise_or_lower_a_lower_severity_finding(ledger):
    run_id, contexts, finding = await challenged_run(ledger, target="traction", challenger="funding", claim_id="C8")
    assert finding.severity == 3
    reply = {"challenge_id": "funding:C1", "action": "REVISE", "text": "Context holds.", "new_severity": 1}
    with_llm(contexts, "traction", QueueLlm(bundle([reply])))
    await TractionAgent(models=MODELS).run_reply_verdict(contexts["traction"])
    effective = await finding_of(ledger, run_id, "traction", "C8")
    assert effective.severity_final == 1


async def test_unknown_and_duplicate_replies_are_dropped(ledger):
    run_id, contexts, _ = await challenged_run(ledger)
    good = {"challenge_id": "traction:C1", "action": "REBUT", "text": "x" * 500, "new_severity": 2}
    again = {"challenge_id": "traction:C1", "action": "CONCEDE", "text": "second answer"}
    stray = {"challenge_id": "nobody:C9", "action": "CONCEDE", "text": "not mine"}
    with_llm(contexts, "funding", QueueLlm(bundle([stray, good, again])))
    await FundingAgent(models=MODELS).run_reply_verdict(contexts["funding"])
    (stored,) = await ledger.get_replies(run_id)
    assert stored.action == "REBUT" and stored.new_severity is None      # REBUT cannot carry a severity
    assert len(stored.text) == 300 and stored.text.endswith("…")          # cut to the limit
    assert stored.reply_id == "funding:R1"


async def test_the_verdict_score_and_confidence_are_computed_by_code(ledger):
    run_id, contexts, _ = await challenged_run(ledger)
    reply = {"challenge_id": "traction:C1", "action": "REVISE", "text": "Persuaded.", "new_severity": 4}
    llm = QueueLlm(bundle([reply], stance="Revenue claim is inflated against the estimate.",
                          flags=["ARR far above the estimated range", "", "Last raise was long ago",
                                 "third", "fourth ignored"]))
    with_llm(contexts, "funding", llm)
    await FundingAgent(models=MODELS).run_reply_verdict(contexts["funding"])
    (verdict,) = [v for v in await ledger.get_verdicts(run_id) if v.agent == "funding"]
    assert verdict.score == 82.0                         # 100 - 6*(4-1): effective severity, not 5
    assert verdict.confidence == pytest.approx(0.833, abs=0.001)   # 5 of 6 checks verifiable
    assert verdict.stance == "Revenue claim is inflated against the estimate."
    assert verdict.flags == ("ARR far above the estimated range", "Last raise was long ago", "third")
    assert verdict.is_stub is False
    prompt, schema, model = llm.prompts[0]
    assert schema is ReplyBundle and model == "mid-model"
    assert "traction:C1" in prompt and "Do not output scores or confidence" in prompt
    assert "speculation about motives" in prompt


async def test_with_no_challenges_no_model_is_called_and_the_summary_comes_from_code(ledger):
    run_id, contexts, _ = await after_round_one(ledger)
    await advance(ledger, run_id, "REPLY")
    llm = QueueLlm()
    with_llm(contexts, "scale_team", llm)
    await ScaleTeamAgent(models=MODELS).run_reply_verdict(contexts["scale_team"])
    assert llm.prompts == []
    (verdict,) = [v for v in await ledger.get_verdicts(run_id) if v.agent == "scale_team"]
    assert "verified" in verdict.stance and "contradicted" in verdict.stance
    assert 1 <= len(verdict.flags) <= 3 and verdict.score == 82.0     # C4 contradicted at severity 4


async def test_a_failing_model_still_produces_a_deterministic_verdict(ledger):
    run_id, contexts, _ = await challenged_run(ledger)
    with_llm(contexts, "funding", QueueLlm(RuntimeError("model down")))
    await FundingAgent(models=MODELS).run_reply_verdict(contexts["funding"])
    assert await ledger.get_replies(run_id) == ()                    # challenge left for the lead
    (verdict,) = [v for v in await ledger.get_verdicts(run_id) if v.agent == "funding"]
    assert verdict.score == 76.0 and "contradicted" in verdict.stance and verdict.flags


# ---- the whole debate, then Ayush's scoring ---------------------------------------------

async def test_full_debate_feeds_the_real_scoring(ledger):
    run_id, contexts, claim_set = await setup_run(ledger)
    agents = make_agents()
    await run_phase(ledger, run_id, agents, contexts, "ROUND1", "run_round1")
    arr = await finding_of(ledger, run_id, "funding", "C7")
    evidence_id = [e.evidence_id for e in await ledger.get_evidence(run_id) if e.agent == "scale_team"][0]
    challenge = {"challenges": [{"finding_id": arr.finding_id, "type": "CORROBORATES",
                                 "argument": "413 staff cannot support $200M ARR.", "evidence_ids": [evidence_id]}]}
    llms = {"scale_team": QueueLlm(challenge, bundle([], stance="Team size holds up.")),
            "identity": QueueLlm({"challenges": []}), "traction": QueueLlm({"challenges": []}),
            "funding": QueueLlm({"challenges": []},
                                bundle([{"challenge_id": "scale_team:C1", "action": "CONCEDE", "text": "Agreed."}],
                                       stance="ARR claim is not credible."))}
    for name, llm in llms.items():
        with_llm(contexts, name, llm)
    await run_phase(ledger, run_id, agents, contexts, "CHALLENGE", "run_challenge")
    await run_phase(ledger, run_id, agents, contexts, "REPLY", "run_reply_verdict")
    verdicts = await ledger.get_verdicts(run_id)
    assert sorted(v.agent for v in verdicts) == sorted(AGENTS)
    await advance(ledger, run_id, "SCORING")
    decision = compute(await ledger.get_claims(run_id), await ledger.get_all_findings(run_id), verdicts, POLICY)
    assert decision.is_stub is False and decision.credibility is not None
    assert decision.verdict in ("INVEST", "INVEST_WITH_CONDITIONS", "DIG_DEEPER", "PASS")
    funding_verdict = next(v for v in verdicts if v.agent == "funding")
    assert funding_verdict.stance == "ARR claim is not credible." and funding_verdict.score == 76.0
    gates = [m.name for m in await ledger.get_metrics(run_id) if m.name.startswith("gate:")]
    assert gates == ["gate:arr_ratio_over_3"]       # proposed, never applied by the agents
    assert decision.gates == ()                     # confirming a gate is the orchestrator's decision


async def test_a_realistically_large_value_is_stored_whole_so_it_matches_the_dossier_exactly(ledger):
    milestones = [{"round_type": "series_c",
                   "investors_detailed": [{"name": f"Investor {i}", "note": "x" * 80} for i in range(120)]}]
    assert len(json.dumps(milestones)) > 10_000          # the size of a real company's funding history
    dossier = {**COMPANY, "funding": {**COMPANY["funding"], "milestones": milestones}}
    run_id, contexts, _ = await setup_run(ledger, dossier=dossier)
    await advance(ledger, run_id, "ROUND1")
    await FundingAgent(models=MODELS).run_round1(contexts["funding"])
    stored = next(e for e in await ledger.get_evidence(run_id) if e.field_path == "funding.milestones")
    assert json.loads(stored.value_json) == milestones    # exact: a truncated copy could never be confirmed
