"""Tracked jobs consume failures safely even when persistence itself is unavailable."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.jobs import RunManager
from app.settings import Settings


async def test_cleanup_failure_is_consumed_without_logging_private_exception(monkeypatch, caplog):
    async def fail(*args, **kwargs):
        raise RuntimeError("private-secret")

    monkeypatch.setattr("app.jobs.run_pipeline", fail)
    repo = SimpleNamespace(fail_run=AsyncMock(side_effect=RuntimeError("private-storage-secret")))
    jobs = RunManager(repo, Settings(_env_file=None), None)
    async with jobs.admit() as submit:
        submit("synthetic-run")
    task = jobs._tasks["synthetic-run"]
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not jobs._tasks and jobs._reserved == 0
    assert "private" not in caplog.text and "RuntimeError" in caplog.text
