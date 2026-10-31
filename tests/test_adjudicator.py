"""The lead analyst: gate confirmation, dispute rulings and the narrative.

Pure functions are tested directly; the full flow runs the four real agents through a debate on the
real SQLite ledger and then adjudicates and scores. All data is SYNTHETIC and models are scripted.
"""
import pytest

from app.schemas import AdjudicationResult, Challenge, Finding, Metric, Reply
from ledger.errors import LedgerStateError
from leo.adjudicator import (
    GATE_MIN_SEVERITY, MAX_DISPUTES, MAX_NARRATIVE_CHARS, MAX_REASON_CHARS, AdjudicationDraft, Dispute,
    RulingDraft, adjudicate, confirm_gates, confirmed_gates_for_run, fallback_narrative, gate_backers,
    open_disputes, valid_revisions,
)
from leo.context import AGENTS
from leo.scoring import compute
from tests.ledger_support import advance
from tests.test_agents_checks import (
    POLICY, QueueLlm, bundle, finding_of, make_agents, run_phase, setup_run, with_llm,
)


def finding(fid, agent="funding", status="CONTRADICTED", severity=5, final=None, evidence=("E1",),
            claim="C7", stub=False):
    return Finding(finding_id=fid, agent=agent, claim_id=claim, status=status, severity=severity,
                   severity_final=final, note=f"note for {fid}", evidence_ids=evidence, is_stub=stub)


def gate(name="arr_ratio_over_3", agent="funding", evidence=("E1",)):
    return Metric(run_id="r", agent=agent, name=f"gate:{name}", value=1.0, unit="proposal",
                  formula="proposed", evidence_ids=evidence)


def challenge(cid="traction:C1", finding_id="funding:F1", frm="traction", to="funding", evidence=("E9",)):
    return Challenge(challenge_id=cid, from_agent=frm, to_agent=to, finding_id=finding_id, type="CONTEXT",
                     argument="Some argument.", evidence_ids=evidence)


def reply(cid, action):
    return Reply(reply_id=f"x:{cid}", challenge_id=cid, agent="funding", action=action, text="because",
                 new_severity=3 if action == "REVISE" else None)


# ---- confirm_gates (no model) -----------------------------------------------------------

@pytest.mark.parametrize("severity, final, confirmed", [
    (5, None, True), (4, None, True), (3, None, False),
    (5, 3, False),    # a ruling lowered the backing finding: the gate is no longer confirmed
    (3, 5, True),     # raised by a ruling
])
def test_a_gate_needs_a_backing_finding_that_is_still_severe(severity, final, confirmed):
    result = confirm_gates([gate()], [finding("funding:F1", severity=severity, final=final)])
    assert result == (("arr_ratio_over_3",) if confirmed else ())
    assert GATE_MIN_SEVERITY == 4


@pytest.mark.parametrize("metrics, findings", [
    ([gate(evidence=())], [finding("funding:F1")]),                                    # no evidence behind it
    ([gate("made_up_gate")], [finding("funding:F1")]),                                  # not a known gate
    ([gate(agent="traction")], [finding("funding:F1")]),                                # another agent's finding
    ([gate()], [finding("funding:F1", status="VERIFIED")]),                             # not contradicted
    ([gate()], [finding("funding:F1", evidence=("E2",))]),                              # evidence does not overlap
    ([Metric(run_id="r", agent="funding", name="arr_ratio", value=4.0, unit="x",
             formula="f", evidence_ids=("E1",))], [finding("funding:F1")]),             # not a gate proposal
    ([], [finding("funding:F1")]),
])
def test_proposals_without_real_backing_are_ignored(metrics, findings):
    assert confirm_gates(metrics, findings) == ()


def test_confirmed_gates_are_sorted_and_deduplicated():
    metrics = [gate("founder_not_found", "identity", ("E3",)), gate("arr_ratio_over_3"),
               gate("arr_ratio_over_3")]
    findings = [finding("identity:F2", "identity", evidence=("E3",), claim="C2"), finding("funding:F1")]
    assert confirm_gates(metrics, findings) == ("arr_ratio_over_3", "founder_not_found")
    assert gate_backers(metrics, findings) == {"founder_not_found": ("identity:F2",),
                                               "arr_ratio_over_3": ("funding:F1",)}


# ---- disputes ---------------------------------------------------------------------------

def test_only_rebutted_or_unanswered_challenges_are_disputes():
    findings = [finding("funding:F1")]
    challenges = [challenge("a:C1"), challenge("b:C1"), challenge("c:C1"), challenge("d:C1"),
                  challenge("e:C1", finding_id="gone:F9")]
    replies = [reply("a:C1", "REBUT"), reply("b:C1", "CONCEDE"), reply("c:C1", "REVISE")]   # d unanswered
    got = open_disputes(challenges, replies, findings)
    assert sorted(d.challenge.challenge_id for d in got) == ["a:C1", "d:C1"]   # e: its finding is missing
    assert next(d for d in got if d.challenge.challenge_id == "d:C1").reply is None


def test_disputes_are_ordered_by_severity_and_capped():
    findings = [finding(f"funding:F{i}", severity=1 + i % 5) for i in range(1, 12)]
    challenges = [challenge(f"traction:C{i}", finding_id=f"funding:F{i}") for i in range(1, 12)]
    got = open_disputes(challenges, [], findings)
    assert len(got) == MAX_DISPUTES
    severities = [d.finding.severity for d in got]
    assert severities == sorted(severities, reverse=True) and severities[0] == 5


# ---- validating rulings -----------------------------------------------------------------

def dispute_for(f, cid="traction:C1", evidence=("E9",)):
    return Dispute(challenge(cid, f.finding_id, evidence=evidence), f, None)


def draft(*rulings):
    return AdjudicationDraft(rulings=list(rulings), narrative="n")


def ruling(cid="traction:C1", new=2, reason="Because."):
    return RulingDraft(challenge_id=cid, new_severity=new, reason=reason)


def test_an_accepted_ruling_becomes_a_leo_revision_with_the_challenges_evidence():
    f = finding("scale_team:F2", "scale_team", severity=4)
    (rev,) = valid_revisions(draft(ruling(new=2, reason="  A  gap\n exists.  ")), [dispute_for(f)], set())
    assert (rev.revision_id, rev.finding_id, rev.author, rev.severity) == ("leo:traction:C1", f.finding_id, "leo", 2)
    assert rev.reason == "Lead analyst ruling: A gap exists." and rev.evidence_ids == ("E9",)


@pytest.mark.parametrize("current, proposed, expected", [
    (5, 1, 3), (5, 4, 4), (1, 5, 3), (3, 3, None), (3, 0, None), (3, 9, None), (3, None, None),
])
def test_ruling_severities_are_bounded_to_two_steps(current, proposed, expected):
    f = finding("scale_team:F2", "scale_team", severity=current)
    got = valid_revisions(draft(ruling(new=proposed)), [dispute_for(f)], set())
    assert [r.severity for r in got] == ([] if expected is None else [expected])


def test_a_gate_backing_finding_can_be_raised_but_never_lowered_by_a_ruling():
    f = finding("funding:F1", severity=4)
    assert valid_revisions(draft(ruling(new=2)), [dispute_for(f)], {f.finding_id}) == ()
    (raised,) = valid_revisions(draft(ruling(new=5)), [dispute_for(f)], {f.finding_id})
    assert raised.severity == 5


def test_unknown_challenges_and_second_rulings_on_one_finding_are_dropped():
    f = finding("scale_team:F2", "scale_team", severity=4)
    disputes = [dispute_for(f, "traction:C1"), dispute_for(f, "identity:C1")]
    got = valid_revisions(draft(ruling("nobody:C9", 2), ruling("traction:C1", 3), ruling("identity:C1", 2)),
                          disputes, set())
    assert [(r.revision_id, r.severity) for r in got] == [("leo:traction:C1", 3)]


def test_long_reasons_are_cut():
    f = finding("scale_team:F2", "scale_team", severity=4)
    (rev,) = valid_revisions(draft(ruling(reason="x" * 900)), [dispute_for(f)], set())
    assert len(rev.reason) <= MAX_REASON_CHARS + len("Lead analyst ruling: ")


# ---- fallback narrative -----------------------------------------------------------------

def test_the_fallback_narrative_is_deterministic_and_honest():
    findings = [finding("funding:F3", severity=5), finding("a:F1", status="VERIFIED"),
                finding("b:F1", status="PARTIAL", severity=2)]
    text = fallback_narrative(findings, ["C9: 500 customers"], ("arr_ratio_over_3",))
    assert text == fallback_narrative(findings, ["C9: 500 customers"], ("arr_ratio_over_3",))
    assert "(funding:F3)" in text and "arr_ratio_over_3" in text and "1 claims could not be verified" in text
    assert "EBITDA and profit" in text and len(text) <= MAX_NARRATIVE_CHARS
    clean = fallback_narrative([finding("a:F1", status="VERIFIED")], [], ())
    assert "No claim was contradicted" in clean


# ---- the stubbed offline path -----------------------------------------------------------

async def test_without_a_ledger_the_result_is_visibly_stubbed():
    result = await adjudicate("any-run")
    assert result == AdjudicationResult(narrative="STUB: No disputes adjudicated.", is_stub=True)


# ---- the full flow on the real ledger ---------------------------------------------------

async def debate(ledger, *, stub=False, answer_replies=True):
    """Round 1, challenges (traction vs the ARR finding, identity vs the growth finding), replies."""
    run_id, contexts, claim_set = await setup_run(ledger, stub=stub)
    agents = make_agents()
    await run_phase(ledger, run_id, agents, contexts, "ROUND1", "run_round1")
    arr = await finding_of(ledger, run_id, "funding", "C7")
    growth = await finding_of(ledger, run_id, "scale_team", "C4")
    evidence = {a: [e.evidence_id for e in await ledger.get_evidence(run_id) if e.agent == a][0] for a in AGENTS}

    def challenge_json(target, evidence_id):
        return {"challenges": [{"finding_id": target.finding_id, "type": "CONTEXT",
                                "argument": "Context from my area.", "evidence_ids": [evidence_id]}]}

    def rebut(cid):
        return bundle([{"challenge_id": cid, "action": "REBUT", "text": "The finding stands."}])

    none = {"challenges": []}
    scripts = {"traction": [challenge_json(arr, evidence["traction"])],
               "identity": [challenge_json(growth, evidence["identity"])],
               "funding": [none] + ([rebut("traction:C1")] if answer_replies else [RuntimeError("down")]),
               "scale_team": [none] + ([rebut("identity:C1")] if answer_replies else [RuntimeError("down")])}
    for name, queue in scripts.items():
        with_llm(contexts, name, QueueLlm(*queue))
    await run_phase(ledger, run_id, agents, contexts, "CHALLENGE", "run_challenge")
    await run_phase(ledger, run_id, agents, contexts, "REPLY", "run_reply_verdict")
    return run_id, arr, growth


def adjudication_json(*rulings, narrative="The ARR claim is contradicted (funding:F3)."):
    return {"rulings": list(rulings), "narrative": narrative}


async def test_rulings_are_persisted_gates_confirmed_and_the_decision_saves(ledger):
    run_id, arr, growth = await debate(ledger)
    await advance(ledger, run_id, "ADJUDICATE")
    llm = QueueLlm(adjudication_json(
        {"challenge_id": "traction:C1", "new_severity": 3, "reason": "Traffic does not change the ARR maths."},
        {"challenge_id": "identity:C1", "new_severity": 2, "reason": "The gap is real but it is the deck's claim."}))
    result = await adjudicate(run_id, repo=ledger, llm=llm, model="pro-model")

    # the ARR finding backs a gate, so the attempt to lower it is refused; the growth ruling is applied
    assert [(r.finding_id, r.author, r.severity) for r in result.revisions] == [(growth.finding_id, "leo", 2)]
    assert result.narrative == "The ARR claim is contradicted (funding:F3)." and result.is_stub is False
    assert (await finding_of(ledger, run_id, "funding", "C7")).severity_final in (None, 5)
    assert (await finding_of(ledger, run_id, "scale_team", "C4")).severity_final == 2
    assert [r.revision_id for r in await ledger.get_revisions(run_id)
            if r.author == "leo"] == ["leo:identity:C1"]

    gates = await confirmed_gates_for_run(run_id, ledger)
    assert gates == ("arr_ratio_over_3",)
    await advance(ledger, run_id, "SCORING")
    claims, findings = await ledger.get_claims(run_id), await ledger.get_all_findings(run_id)
    verdicts = await ledger.get_verdicts(run_id)
    uncapped = compute(claims, findings, verdicts, POLICY)
    capped = compute(claims, findings, verdicts, POLICY, confirmed_gates=gates)
    assert uncapped.verdict != "PASS"                       # the risk this stage exists to remove
    assert (capped.verdict, capped.check_size_usd, capped.gates) == ("PASS", 0, ("arr_ratio_over_3",))
    await ledger.save_decision(run_id, capped, result)      # the ledger accepts the persisted revisions
    saved = await ledger.get_adjudication(run_id)
    assert saved.revisions == result.revisions and saved.narrative == result.narrative


async def test_the_prompt_carries_disputes_protection_and_the_guardrails(ledger):
    run_id, _, _ = await debate(ledger)
    await advance(ledger, run_id, "ADJUDICATE")
    llm = QueueLlm(adjudication_json())
    await adjudicate(run_id, repo=ledger, llm=llm, model="pro-model")
    prompt, schema, model = llm.prompts[0]
    assert schema is AdjudicationDraft and model == "pro-model"
    assert "untrusted data" in prompt and "Do not state an investment decision" in prompt
    assert "PROTECTED" in prompt                                   # the ARR finding backs a gate
    assert "traction:C1" in prompt and "identity:C1" in prompt and "REBUT" in prompt
    assert "arr_ratio_over_3" in prompt and "EBITDA and profit are never available" in prompt
    assert "Revenue figures are Crustdata ESTIMATES" in prompt


async def test_unanswered_challenges_are_disputes_too(ledger):
    run_id, _, _ = await debate(ledger, answer_replies=False)        # the model was down in the reply round
    await advance(ledger, run_id, "ADJUDICATE")
    llm = QueueLlm(adjudication_json())
    await adjudicate(run_id, repo=ledger, llm=llm, model="m")
    assert llm.prompts[0][0].count("reply: no reply") == 2


async def test_a_failing_model_gives_no_rulings_and_a_deterministic_narrative(ledger):
    run_id, _, _ = await debate(ledger)
    await advance(ledger, run_id, "ADJUDICATE")
    result = await adjudicate(run_id, repo=ledger, llm=QueueLlm(RuntimeError("model down")), model="m")
    assert result.revisions == () and "funding:" in result.narrative and "EBITDA and profit" in result.narrative
    assert [r for r in await ledger.get_revisions(run_id) if r.author == "leo"] == []
    no_model = await adjudicate(run_id, repo=ledger, llm=None)
    assert no_model.narrative == result.narrative and no_model.revisions == ()


async def test_malformed_model_output_is_treated_like_a_failure(ledger):
    run_id, _, _ = await debate(ledger)
    await advance(ledger, run_id, "ADJUDICATE")
    bad = QueueLlm({"rulings": [{"challenge_id": "x", "new_severity": "very high"}]})
    result = await adjudicate(run_id, repo=ledger, llm=bad, model="m")
    assert result.revisions == () and result.narrative


async def test_an_empty_or_overlong_model_narrative_is_replaced_or_cut(ledger):
    run_id, _, _ = await debate(ledger)
    await advance(ledger, run_id, "ADJUDICATE")
    empty = await adjudicate(run_id, repo=ledger, llm=QueueLlm(adjudication_json(narrative="   ")), model="m")
    assert "funding:" in empty.narrative                                # fell back to the code narrative
    long = await adjudicate(run_id, repo=ledger, llm=QueueLlm(adjudication_json(narrative="word " * 600)), model="m")
    assert len(long.narrative) <= MAX_NARRATIVE_CHARS


async def test_adjudication_can_run_twice_without_duplicating_revisions(ledger):
    run_id, _, growth = await debate(ledger)
    await advance(ledger, run_id, "ADJUDICATE")
    ruling_json = {"challenge_id": "identity:C1", "new_severity": 2, "reason": "Same ruling."}
    first = await adjudicate(run_id, repo=ledger, llm=QueueLlm(adjudication_json(ruling_json)), model="m")
    second = await adjudicate(run_id, repo=ledger, llm=QueueLlm(adjudication_json(ruling_json)), model="m")
    assert first.revisions == second.revisions
    assert len([r for r in await ledger.get_revisions(run_id) if r.author == "leo"]) == 1
    assert growth.finding_id == first.revisions[0].finding_id


async def test_it_only_runs_in_the_adjudicate_phase(ledger):
    run_id, _, _ = await debate(ledger)           # the run is still in REPLY
    with pytest.raises(LedgerStateError, match="ADJUDICATE"):
        await adjudicate(run_id, repo=ledger, llm=QueueLlm(adjudication_json()), model="m")


async def test_stub_findings_make_the_adjudication_a_stub(ledger):
    run_id, _, _ = await debate(ledger, stub=True)
    await advance(ledger, run_id, "ADJUDICATE")
    result = await adjudicate(run_id, repo=ledger, llm=QueueLlm(adjudication_json()), model="m")
    assert result.is_stub is True
