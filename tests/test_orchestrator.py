"""Persistent coordinator integration: real specialists, generated PDFs and no network."""

import asyncio

import pytest

from app.schemas import Policy, ClaimSet, AdjudicationResult, FindingRevision
from app.settings import Settings
from ingest.claim_extractor import ExtractionError
from ledger.errors import LedgerConflict
from leo.orchestrator import run_pipeline
from tests.pipeline_support import dependencies, pdf_bytes


@pytest.fixture
async def execution(ledger, tmp_path):
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(pdf_bytes())
    run = await ledger.create_run(
        "tester", "Synthetic", "example.com", Policy(max_check_usd=100000), str(path)
    )
    config = Settings(_env_file=None, mode="live", pipeline_timeout=20, phase_timeout=1)
    return run, config, dependencies()


async def launch(ledger, execution):
    run, config, deps = execution
    await run_pipeline(run, repo=ledger, settings=config, dependencies=deps)
    return await ledger.get_run(run)


async def test_real_agents_complete_and_arr_gate_is_confirmed(ledger, execution):
    assert (await launch(ledger, execution))["status"] == "done"
    run, _, deps = execution
    decision = await ledger.get_decision(run)
    assert decision.gates == ("arr_ratio_over_3",) and decision.verdict == "PASS"
    assert decision.check_size_usd == 0 and not decision.is_stub
    assert len(await ledger.get_verdicts(run)) == 4
    assert all(s["state"] == "DONE" for s in await ledger.get_agent_statuses(run))
    assert deps.llm.calls == 4
    events = await ledger.get_events(run)
    assert events[-1].kind == "done"
    phases = [
        __import__("json").loads(e.payload_json)["phase"]
        for e in events
        if e.kind == "phase_changed"
    ]
    assert phases == ["INGEST", "RESOLVE", "ROUND1", "CHALLENGE", "REPLY", "ADJUDICATE", "SCORING"]


async def test_cannot_assess_stops_before_enrichment_and_specialists(ledger, execution):
    execution[2].provider.identify.return_value = [{"matches": []}]
    assert (await launch(ledger, execution))["status"] == "cannot_assess"
    execution[2].provider.enrich_result.assert_not_awaited()
    assert await ledger.get_agent_statuses(execution[0]) == ()


@pytest.mark.parametrize(
    "stage,code",
    [("parse", "DECK_REJECTED"), ("extract", "EXTRACTION_FAILED"), ("provider", "PROVIDER_FAILED")],
)
async def test_safe_terminal_failures(ledger, execution, stage, code):
    from ingest.pdf_parser import DeckRejectedError
    from crustdata.client import CrustdataError
    from unittest.mock import AsyncMock

    run, _, deps = execution
    if stage == "parse":
        deps.parse = AsyncMock(side_effect=DeckRejectedError("private-path"))
    elif stage == "extract":
        deps.extract.side_effect = ExtractionError("private-key")
    else:
        deps.provider.identify.side_effect = CrustdataError("auth", 401, "private-key")
    assert (await launch(ledger, execution))["status"] == "failed"
    events = str(await ledger.get_events(run))
    assert code in events and "private-" not in events


@pytest.mark.parametrize("all_fail", [False, True])
async def test_agent_failures_are_contained_and_failed_agents_do_not_rejoin(
    ledger, execution, all_fail
):
    agents = execution[2].agents()

    async def fail(ctx):
        raise RuntimeError("private exception")

    for agent in agents if all_fail else agents[:1]:
        agent.run_round1 = fail
    execution[2].agents = lambda: agents
    result = await launch(ledger, execution)
    assert result["status"] == ("failed" if all_fail else "done")
    if not all_fail:
        statuses = [
            s for s in await ledger.get_agent_statuses(execution[0]) if s["agent"] == "identity"
        ]
        assert len(statuses) == 3 and all(s["state"] == "FAILED" for s in statuses)
        assert len(await ledger.get_verdicts(execution[0])) == 3


async def test_phase_timeout_keeps_other_agents_running(ledger, execution):
    agents = execution[2].agents()

    async def slow(ctx):
        await asyncio.sleep(10)

    agents[0].run_round1 = slow
    execution[2].agents = lambda: agents
    execution = (
        execution[0],
        execution[1].model_copy(update={"phase_timeout": 0.5}),
        execution[2],
    )
    assert (await launch(ledger, execution))["status"] == "done"
    assert len(await ledger.get_verdicts(execution[0])) == 3


async def test_duplicate_coordinator_cannot_fail_the_original_run(ledger, execution):
    started, release = asyncio.Event(), asyncio.Event()
    original = execution[2].parse

    async def blocked(path):
        started.set()
        await release.wait()
        return await original(path)

    execution[2].parse = blocked
    task = asyncio.create_task(launch(ledger, execution))
    await started.wait()
    with pytest.raises(LedgerConflict):
        await launch(ledger, execution)
    assert (await ledger.get_run(execution[0]))["status"] == "parsing"
    release.set()
    assert (await task)["status"] == "done"


@pytest.mark.parametrize("cancel", [False, True])
async def test_whole_run_deadline_and_outer_cancellation_are_terminal(ledger, execution, cancel):
    started = asyncio.Event()

    async def blocked(path):
        started.set()
        await asyncio.sleep(10)

    execution[2].parse = blocked
    run, config, deps = execution
    if not cancel:
        config = config.model_copy(update={"pipeline_timeout": 0.02})
    task = asyncio.create_task(run_pipeline(run, repo=ledger, settings=config, dependencies=deps))
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    assert (await ledger.get_run(run))["status"] == "failed"


async def test_live_mode_rejects_stub_extraction(ledger, execution):
    execution[2].extract.return_value = ClaimSet(is_stub=True)
    assert (await launch(ledger, execution))["status"] == "failed"


async def test_stub_adjudication_remains_visible(ledger, execution):
    execution[2].adjudicate.return_value = AdjudicationResult(narrative="Stub", is_stub=True)
    assert (await launch(ledger, execution))["status"] == "done"
    assert (await ledger.get_decision(execution[0])).is_stub


async def test_adjudication_revision_is_applied_before_decision(ledger, execution):
    run, _, deps = execution

    async def adjudicate(run_id):
        finding = next(
            f
            for f in await ledger.get_all_findings(run_id)
            if f.agent == "funding" and f.claim_id == "C1"
        )
        return AdjudicationResult(
            narrative="Test ruling",
            revisions=(
                FindingRevision(
                    revision_id="lead:R1",
                    finding_id=finding.finding_id,
                    author="leo",
                    severity=4,
                    reason="Synthetic ruling",
                    evidence_ids=finding.evidence_ids,
                ),
            ),
        )

    deps.adjudicate = adjudicate
    assert (await launch(ledger, execution))["status"] == "done"
    assert any(r.revision_id == "lead:R1" for r in await ledger.get_revisions(run))


async def test_restart_recovery_does_not_replay_provider_calls(ledger, execution):
    run, _, deps = execution
    await ledger.claim_run(run)
    await ledger.recover_interrupted_runs()
    assert (await ledger.get_run(run))["status"] == "failed"
    deps.provider.identify.assert_not_awaited()


async def test_valid_gate_survives_a_later_failure_of_its_proposing_agent(ledger, execution):
    agents = execution[2].agents()

    async def fail(ctx):
        raise RuntimeError("Synthetic reply failure")

    next(a for a in agents if a.name == "funding").run_reply_verdict = fail
    execution[2].agents = lambda: agents
    assert (await launch(ledger, execution))["status"] == "done"
    assert (await ledger.get_decision(execution[0])).gates == ("arr_ratio_over_3",)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("closed", "entity_not_active"),
        ("round", "funding_round_absent"),
        ("founder", "founder_not_found"),
        ("missing_founders", None),
    ],
)
async def test_other_gate_rules_and_missing_data_with_real_agents(
    ledger, execution, case, expected
):
    from copy import deepcopy
    from app.schemas import Claim
    from tests.pipeline_support import COMPANY

    data = deepcopy(COMPANY)
    if case == "closed":
        data["basic_info"]["status"] = "closed"
        claim = Claim(id="C1", topic="identity_founding", text="Founded 2020", value=2020, slide=1)
    elif case == "round":
        claim = Claim(id="C1", topic="funding_round", text="Series D", value="Series D", slide=1)
    else:
        claim = Claim(
            id="C1",
            topic="identity_founder",
            text="Founder: Grace Hopper",
            value="Grace Hopper",
            slide=1,
        )
        if case == "founder":
            data["people"] = {
                "founders": [
                    {"basic_profile": {"name": "Ada Lovelace", "current_title": "Founder"}}
                ]
            }
    deps = dependencies(data=data, claims=ClaimSet(claims=(claim,)))
    assert (await launch(ledger, (execution[0], execution[1], deps)))["status"] == "done"
    decision = await ledger.get_decision(execution[0])
    assert decision.gates == ((expected,) if expected else ())
    if expected:
        assert decision.verdict == "PASS" and decision.check_size_usd == 0
