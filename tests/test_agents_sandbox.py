"""The real specialist agents running under the real SandboxRunner (scoped ledger, scoped tools).

Needs the resolution and sandbox stage (Ayush's PR 13); skipped on a base without it. Models are
scripted, the ledger is real SQLite, and the runner is the production one.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.protocol import AgentContext
from app.schemas import ClaimSet, Resolution
from ledger.repo import LedgerRepository
from leo.context import AGENTS, build_context_packets
from leo.scoring import compute
from sandbox.ledger_writer import LedgerWriter
from sandbox.runner import SandboxRunner
from sandbox.scoped_tools import ScopedCrustdata
from tests.ledger_support import advance
from tests.test_agents_checks import (
    CLAIMS, COMPANY, POLICY, SCOPES, QueueLlm, finding_of, make_agents, with_llm,
)

pytestmark = pytest.mark.skipif(
    not hasattr(LedgerRepository, "save_resolution"), reason="needs the sandbox stage (PR 13)")


def provider():
    return SimpleNamespace(enrich=AsyncMock(return_value=[]), web_search=AsyncMock(return_value={}),
                           job_search=AsyncMock(return_value={}))


class HangingLlm:
    async def generate(self, prompt, schema, model):
        await asyncio.sleep(30)


async def runnable(ledger):
    run_id = await ledger.create_run("tester", "Acme", None, POLICY, "deck.pdf")
    await advance(ledger, run_id, "INGEST")
    claim_set = ClaimSet(company_name="Acme", claims=CLAIMS)
    await ledger.add_claims(run_id, claim_set)
    await advance(ledger, run_id, "RESOLVE")
    await ledger.save_resolution(run_id, Resolution(status="resolved", company_id=1, reason="Synthetic"))
    slices = {a: json.dumps({k: COMPANY[k] for k in SCOPES[a] if k in COMPANY}) for a in AGENTS}
    packets = build_context_packets(claim_set, slices, POLICY)
    await ledger.save_context_packets(run_id, packets)
    contexts = {p.agent: AgentContext(run_id, p, LedgerWriter(run_id, p.agent, ledger),
                                      ScopedCrustdata(p.agent, provider(), company_id=1), QueueLlm())
                for p in packets}
    return run_id, contexts


async def phase(ledger, runner, run_id, agents, contexts, name, timeout=60):
    await advance(ledger, run_id, name)
    return await asyncio.gather(*(runner.run(a, contexts[a.name], name, timeout) for a in agents))


async def statuses(ledger, run_id, name):
    return {s["agent"]: (s["state"], s.get("error")) for s in await ledger.get_agent_statuses(run_id)
            if s["phase"] == name}


async def test_all_four_agents_complete_the_whole_debate_under_the_real_runner(ledger):
    run_id, contexts = await runnable(ledger)
    runner, agents = SandboxRunner(ledger), make_agents()
    assert all(await phase(ledger, runner, run_id, agents, contexts, "ROUND1"))
    arr = await finding_of(ledger, run_id, "funding", "C7")
    cited = [e.evidence_id for e in await ledger.get_evidence(run_id) if e.agent == "scale_team"][0]
    challenge = {"challenges": [{"finding_id": arr.finding_id, "type": "CORROBORATES",
                                 "argument": "413 staff cannot support $200M ARR.", "evidence_ids": [cited]}]}
    scripts = {"scale_team": QueueLlm(challenge), "identity": QueueLlm({"challenges": []}),
               "traction": QueueLlm({"challenges": []}),
               "funding": QueueLlm({"challenges": []}, {"replies": [
                   {"challenge_id": "scale_team:C1", "action": "CONCEDE", "text": "Agreed."}],
                   "stance": "ARR claim is not credible.", "flags": []})}
    for name, llm in scripts.items():
        with_llm(contexts, name, llm)
    assert all(await phase(ledger, runner, run_id, agents, contexts, "CHALLENGE"))
    assert all(await phase(ledger, runner, run_id, agents, contexts, "REPLY"))
    for name in ("ROUND1", "CHALLENGE", "REPLY"):
        assert {a: s for a, (s, _) in (await statuses(ledger, run_id, name)).items()} == {a: "DONE" for a in AGENTS}
    verdicts = await ledger.get_verdicts(run_id)
    assert sorted(v.agent for v in verdicts) == sorted(AGENTS)
    assert len(await ledger.get_challenges_for(run_id, "funding")) == 1
    assert (await ledger.get_replies(run_id))[0].action == "CONCEDE"
    await advance(ledger, run_id, "SCORING")
    decision = compute(await ledger.get_claims(run_id), await ledger.get_all_findings(run_id), verdicts, POLICY)
    assert decision.is_stub is False


async def test_a_failing_model_degrades_inside_the_runner_and_the_agent_still_finishes(ledger):
    run_id, contexts = await runnable(ledger)
    runner, agents = SandboxRunner(ledger), make_agents()
    await phase(ledger, runner, run_id, agents, contexts, "ROUND1")
    for name in AGENTS:
        with_llm(contexts, name, QueueLlm(RuntimeError("model down")))
    assert all(await phase(ledger, runner, run_id, agents, contexts, "CHALLENGE"))
    assert {s for s, _ in (await statuses(ledger, run_id, "CHALLENGE")).values()} == {"DONE"}  # not FAILED


async def test_a_model_that_outlives_the_runner_marks_only_that_agent_failed(ledger):
    run_id, contexts = await runnable(ledger)
    runner, agents = SandboxRunner(ledger), make_agents()
    await phase(ledger, runner, run_id, agents, contexts, "ROUND1")
    for name in AGENTS:
        with_llm(contexts, name, HangingLlm() if name == "traction" else QueueLlm({"challenges": []}))
    results = await phase(ledger, runner, run_id, agents, contexts, "CHALLENGE", timeout=0.3)
    assert dict(zip([a.name for a in agents], results, strict=True)) == {
        "identity": True, "scale_team": True, "funding": True, "traction": False}
    state = await statuses(ledger, run_id, "CHALLENGE")
    assert state["traction"] == ("FAILED", "TIMEOUT")
    assert all(state[a][0] == "DONE" for a in ("identity", "scale_team", "funding"))

