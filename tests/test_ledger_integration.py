import asyncio

import pytest

from app.main import create_app
from app.schemas import AdjudicationResult, Policy
from ledger.db import connection, transaction
from ledger.errors import LedgerNotFound
from ledger.repo import LedgerRepository
from leo.scoring import compute
from scripts.ledger_demo import run_ledger_demo
from tests.ledger_support import advance


async def test_persistent_stub_demo_survives_reopen_and_repeat(tmp_path):
    path = tmp_path / "demo.sqlite"
    first = await run_ledger_demo(path)
    second = await run_ledger_demo(path)
    assert first["run_id"] != second["run_id"]
    assert first["status"] == second["status"] == "done"
    assert first["findings"] == 6 and first["verdicts"] == 4
    assert first["events"] == 43
    repo = LedgerRepository(path)
    assert (await repo.get_decision(first["run_id"])).is_stub
    assert (await repo.get_decision(second["run_id"])).check_size_usd == 0


async def test_startup_initializes_configured_database_without_provider_calls(
    auth_settings, tmp_path
):
    path = tmp_path / "api.sqlite"
    app = create_app(auth_settings.model_copy(update={"database_path": path}))
    assert not path.exists()
    async with app.router.lifespan_context(app):
        assert path.exists()
        run = await app.state.ledger.create_run(
            "test-user", "Test", None, Policy(max_check_usd=0), "test.pdf"
        )
    assert (await LedgerRepository(path).get_run(run))["user"] == "test-user"


async def test_decision_and_terminal_state_rollback_when_event_fails(ledger, run, monkeypatch):
    await advance(ledger, run, "SCORING")
    decision = compute(await ledger.get_claims(run), (), (), Policy(max_check_usd=0))
    before = await ledger.get_events(run)

    async def fail(*args):
        raise RuntimeError("event failure")

    monkeypatch.setattr("ledger.repo.event", fail)
    with pytest.raises(RuntimeError):
        await ledger.save_decision(run, decision, AdjudicationResult(narrative="test"))
    assert (await ledger.get_run(run))["phase"] == "SCORING"
    with pytest.raises(LedgerNotFound):
        await ledger.get_decision(run)
    assert await ledger.get_events(run) == before


async def test_real_task_cancellation_releases_transaction(ledger, run):
    started = asyncio.Event()

    async def pending_write():
        async with connection(ledger.path) as db, transaction(db):
            await db.execute(
                "INSERT INTO events(run_id,kind,payload_json) VALUES (?,'cancel-me','{}')", (run,)
            )
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(pending_write())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await ledger.add_event(run, "after-cancel", {})
    kinds = [e.kind for e in await ledger.get_events(run)]
    assert "cancel-me" not in kinds and "after-cancel" in kinds


async def test_offline_demo_never_imports_daksh_replacement_points(tmp_path, monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("ingest.", "agents.registry", "leo.adjudicator")):
            raise AssertionError("Offline demo imported a live feature replacement point")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    result = await run_ledger_demo(tmp_path / "offline.sqlite")
    assert result["decision"]["is_stub"]
