import asyncio

import pytest

from ledger.errors import LedgerNotFound, LedgerStateError
from tests.ledger_support import advance


async def test_phases_are_monotonic_and_idempotent(ledger, run):
    await advance(ledger, run, "ROUND1")
    before = await ledger.get_events(run)
    await ledger.set_phase(run, "ROUND1", "round1")
    assert await ledger.get_events(run) == before
    for phase, status in [
        ("QUEUED", "queued"),
        ("REPLY", "round2"),
        ("CHALLENGE", "round1"),
        ("DONE", "done"),
    ]:
        with pytest.raises(LedgerStateError):
            await ledger.set_phase(run, phase, status)
    assert (await ledger.get_run(run))["phase"] == "ROUND1"


async def test_phase_wait_returns_on_change_deadline_and_passed_target(ledger, run):
    async def change():
        await asyncio.sleep(0.01)
        await ledger.set_phase(run, "INGEST", "parsing")

    changer = asyncio.create_task(change())
    assert await ledger.wait_for_phase(run, "INGEST", 0.5)
    await changer
    assert not await ledger.wait_for_phase(run, "RESOLVE", 0.02)
    assert not await ledger.wait_for_phase(run, "QUEUED", 0.5)
    assert await ledger.wait_for_phase(run, "INGEST", 0)
    assert not await ledger.wait_for_phase(run, "RESOLVE", 0)
    with pytest.raises(LedgerNotFound):
        await ledger.wait_for_phase("absent", "INGEST", 0.01)


async def test_failure_wakes_waiters_and_cannot_reopen(ledger, run):
    async def fail():
        await asyncio.sleep(0.01)
        await ledger.set_phase(run, "FAILED", "failed")

    failure = asyncio.create_task(fail())
    assert not await ledger.wait_for_phase(run, "ROUND1", 1)
    await failure
    assert await ledger.wait_for_phase(run, "FAILED", 0)
    with pytest.raises(LedgerStateError):
        await ledger.set_phase(run, "INGEST", "parsing")
    assert (await ledger.get_events(run))[-1].kind == "done"


async def test_cannot_assess_only_during_resolution(ledger, run):
    with pytest.raises(LedgerStateError):
        await ledger.set_phase(run, "CANNOT_ASSESS", "cannot_assess")
    await advance(ledger, run, "RESOLVE")
    await ledger.set_phase(run, "CANNOT_ASSESS", "cannot_assess")
    assert not await ledger.wait_for_phase(run, "ROUND1", 0.1)


async def test_agent_status_is_current_phase_bound_and_terminal(ledger, run):
    await advance(ledger, run, "ROUND1")
    await ledger.set_agent_status(run, "funding", "ROUND1", "RUNNING")
    await ledger.set_agent_status(run, "funding", "ROUND1", "DONE")
    status = (await ledger.get_agent_statuses(run))[0]
    assert status["started_at"] and status["ended_at"]
    await ledger.set_agent_status(run, "funding", "ROUND1", "DONE")
    with pytest.raises(LedgerStateError):
        await ledger.set_agent_status(run, "funding", "ROUND1", "RUNNING")
    with pytest.raises(LedgerStateError):
        await ledger.set_agent_status(run, "identity", "CHALLENGE", "RUNNING")


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
async def test_invalid_wait_timeout_rejected(ledger, run, timeout):
    with pytest.raises(ValueError):
        await ledger.wait_for_phase(run, "ROUND1", timeout)
