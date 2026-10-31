"""Runtime enforcement tests: synthetic agents, temporary SQLite and mocked tools."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.base import StubSpecialist
from agents.protocol import AgentContext
from app.schemas import Finding, Resolution
from ledger.errors import LedgerConflict, LedgerPermissionError, LedgerStateError
from leo.context import build_context_packets
from sandbox.ledger_writer import LedgerWriter
from sandbox.runner import SandboxRunner
from sandbox.scoped_tools import ScopedCrustdata
from tests.fakes import FakeLlm
from tests.ledger_support import advance


def provider():
    return SimpleNamespace(
        enrich=AsyncMock(return_value=[]),
        web_search=AsyncMock(return_value={}),
        job_search=AsyncMock(return_value={}),
    )


@pytest.fixture
async def contexts(ledger, run):
    await advance(ledger, run, "RESOLVE")
    await ledger.save_resolution(
        run, Resolution(status="resolved", company_id=1, reason="Synthetic")
    )
    packets = build_context_packets(
        await ledger.get_claims(run), {}, (await ledger.get_run(run))["policy"]
    )
    await ledger.save_context_packets(run, packets)
    await advance(ledger, run, "ROUND1")
    return {
        p.agent: AgentContext(
            run,
            p,
            LedgerWriter(run, p.agent, ledger),
            ScopedCrustdata(p.agent, provider(), company_id=1),
            FakeLlm(),
        )
        for p in packets
    }


async def test_all_agents_finish_each_phase_and_receive_fresh_budgets(ledger, run, contexts):
    runner = SandboxRunner(ledger)
    seen = []
    for phase in ("ROUND1", "CHALLENGE", "REPLY"):
        await advance(ledger, run, phase)
        agents = []
        for name in contexts:
            agent = StubSpecialist(name)
            original = getattr(
                agent,
                {
                    "ROUND1": "run_round1",
                    "CHALLENGE": "run_challenge",
                    "REPLY": "run_reply_verdict",
                }[phase],
            )

            async def action(ctx, original=original):
                assert ctx.tools.calls == 0
                await ctx.tools.call("enrich")
                seen.append(ctx.tools)
                await original(ctx)

            setattr(
                agent,
                {
                    "ROUND1": "run_round1",
                    "CHALLENGE": "run_challenge",
                    "REPLY": "run_reply_verdict",
                }[phase],
                action,
            )
            agents.append(agent)
        assert all(await asyncio.gather(*(runner.run(a, contexts[a.name], phase) for a in agents)))
    assert len({id(scope) for scope in seen}) == 12
    assert all(s["state"] == "DONE" for s in await ledger.get_agent_statuses(run))
    assert len(await ledger.get_verdicts(run)) == 4


async def test_timeout_marks_one_failed_and_three_other_agents_complete(ledger, run, contexts):
    agent = StubSpecialist("identity")
    cancelled = asyncio.Event()

    async def slow(ctx):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    agent.run_round1 = slow
    runner = SandboxRunner(ledger)
    result = await asyncio.gather(
        runner.run(agent, contexts["identity"], "ROUND1", timeout=0.01),
        *(
            runner.run(StubSpecialist(n), contexts[n], "ROUND1")
            for n in contexts
            if n != "identity"
        ),
    )
    assert result == [False, True, True, True] and cancelled.is_set()
    states = {s["agent"]: s for s in await ledger.get_agent_statuses(run)}
    assert states["identity"]["state"] == "FAILED" and states["identity"]["error"] == "TIMEOUT"
    assert (await ledger.get_run(run))["phase"] == "ROUND1"


async def test_agent_exception_is_safe_and_late_capabilities_are_revoked(ledger, run, contexts):
    agent = StubSpecialist("identity")
    saved = []

    async def fail(ctx):
        saved.append(ctx)
        raise RuntimeError("secret-key-and-prompt")

    agent.run_round1 = fail
    assert not await SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1")
    assert (await ledger.get_agent_statuses(run))[0]["error"] == "AGENT_ERROR"
    assert "secret-key" not in str(await ledger.get_events(run))
    with pytest.raises(PermissionError):
        await saved[0].tools.call("enrich")
    with pytest.raises(PermissionError):
        await saved[0].ledger.add_findings(())


async def test_outer_cancellation_propagates_after_failed_status(ledger, run, contexts):
    started = asyncio.Event()
    agent = StubSpecialist("identity")

    async def slow(ctx):
        started.set()
        await asyncio.sleep(10)

    agent.run_round1 = slow
    task = asyncio.create_task(SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await ledger.get_agent_statuses(run))[0]["error"] == "CANCELLED"


async def test_duplicate_execution_is_rejected_atomically(ledger, run, contexts):
    started, release = asyncio.Event(), asyncio.Event()
    agent = StubSpecialist("identity")

    async def slow(ctx):
        started.set()
        await release.wait()

    agent.run_round1 = slow
    task = asyncio.create_task(SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1"))
    await started.wait()
    with pytest.raises(LedgerConflict):
        await SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1")
    release.set()
    assert await task


@pytest.mark.parametrize("change", ["writer", "packet", "tools", "company", "run"])
async def test_mismatched_context_is_rejected_before_execution(ledger, run, contexts, change):
    ctx = contexts["identity"]
    if change == "writer":
        ctx = replace(ctx, ledger=contexts["funding"].ledger)
    elif change == "packet":
        ctx = replace(
            ctx, packet=ctx.packet.model_copy(update={"dossier_json": '{"basic_info":null}'})
        )
    elif change == "tools":
        ctx = replace(ctx, tools=contexts["funding"].tools)
    elif change == "company":
        ctx = replace(ctx, tools=ScopedCrustdata("identity", provider(), company_id=2))
    else:
        ctx = replace(ctx, run_id="wrong")
    with pytest.raises(LedgerPermissionError):
        await SandboxRunner(ledger).run(StubSpecialist("identity"), ctx, "ROUND1")
    assert await ledger.get_agent_statuses(run) == ()


async def test_barrier_and_namespace_guards_remain_effective_inside_runner(ledger, run, contexts):
    agent = StubSpecialist("identity")

    async def act(ctx):
        with pytest.raises(LedgerPermissionError):
            await ctx.ledger.get_all_findings()
        with pytest.raises(LedgerPermissionError):
            await ctx.ledger.add_findings(
                (Finding(finding_id="forged", agent="funding", note="Fake", status="VERIFIED"),)
            )
        await ctx.ledger.add_findings(
            (
                Finding(
                    finding_id="unsupported",
                    agent="identity",
                    note="No evidence",
                    status="VERIFIED",
                ),
            )
        )

    agent.run_round1 = act
    assert await SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1")
    assert (await ledger.get_all_findings(run))[0].status == "UNVERIFIABLE"


@pytest.mark.parametrize(
    "phase,timeout", [("RESOLVE", 60), ("ROUND1", 61), ("ROUND1", 0), ("ROUND1", float("nan"))]
)
async def test_runner_rejects_invalid_phase_and_timeout(ledger, contexts, phase, timeout):
    with pytest.raises(ValueError):
        await SandboxRunner(ledger).run(
            StubSpecialist("identity"), contexts["identity"], phase, timeout
        )


async def test_runner_cannot_run_a_future_phase(ledger, contexts):
    with pytest.raises(LedgerStateError):
        await SandboxRunner(ledger).run(
            StubSpecialist("identity"), contexts["identity"], "CHALLENGE"
        )


async def test_budget_failure_is_contained_as_agent_failure(ledger, run, contexts):
    agent = StubSpecialist("identity")

    async def spend(ctx):
        for _ in range(7):
            await ctx.tools.call("enrich")

    agent.run_round1 = spend
    assert not await SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1")
    assert contexts["identity"].tools._client.enrich.await_count == 6
    assert (await ledger.get_agent_statuses(run))[0]["state"] == "FAILED"


async def test_background_tool_work_is_cancelled_before_done(ledger, run, contexts):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    tasks = []

    async def slow(**kwargs):
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    contexts["identity"].tools._client.enrich.side_effect = slow
    agent = StubSpecialist("identity")

    async def spawn(ctx):
        tasks.append(asyncio.create_task(ctx.tools.call("enrich")))
        await started.wait()

    agent.run_round1 = spawn
    assert await SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1")
    assert tasks[0].cancelled() and cancelled.is_set()


async def test_captured_ledger_cannot_write_in_a_later_phase(ledger, run, contexts):
    saved = []
    agent = StubSpecialist("identity")

    async def capture(ctx):
        saved.append(ctx.ledger)

    agent.run_round1 = capture
    runner = SandboxRunner(ledger)
    assert await runner.run(agent, contexts["identity"], "ROUND1")
    await advance(ledger, run, "CHALLENGE")
    with pytest.raises(PermissionError):
        await saved[0].add_evidence(())
    with pytest.raises(AttributeError):
        saved[0].delete


async def test_background_ledger_work_is_cancelled_on_scope_close(ledger, contexts, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()
    tasks = []

    async def slow(*args, **kwargs):
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    monkeypatch.setattr(ledger, "add_evidence", slow)
    agent = StubSpecialist("identity")

    async def spawn(ctx):
        tasks.append(asyncio.create_task(ctx.ledger.add_evidence(())))
        await started.wait()

    agent.run_round1 = spawn
    assert await SandboxRunner(ledger).run(agent, contexts["identity"], "ROUND1")
    assert tasks[0].cancelled() and cancelled.is_set()
