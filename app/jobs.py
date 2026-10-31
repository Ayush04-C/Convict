"""Bounded, tracked in-process jobs. Run a single API worker for this hackathon."""

import asyncio
from contextlib import asynccontextmanager
import logging

from leo.orchestrator import run_pipeline
from ledger.errors import LedgerConflict

logger = logging.getLogger("leo.jobs")


class QueueFull(Exception):
    pass


class RunManager:
    def __init__(self, repo, settings, dependencies):
        self.repo, self.settings, self.dependencies = repo, settings, dependencies
        self._slots = asyncio.Semaphore(settings.max_active_runs)
        self._tasks = {}
        self._reserved = 0
        self._closed = False

    @asynccontextmanager
    async def admit(self):
        if self._closed or self._reserved >= self.settings.max_pending_runs:
            raise QueueFull
        self._reserved += 1
        submitted = False

        def submit(run_id):
            nonlocal submitted
            if submitted or run_id in self._tasks or self._closed:
                raise QueueFull
            task = asyncio.create_task(self._run(run_id), name="leo-run-" + run_id)
            self._tasks[run_id] = task
            submitted = True

            def finished(done):
                self._tasks.pop(run_id, None)
                self._reserved -= 1
                if not done.cancelled():
                    error = done.exception()
                    if error is not None:
                        logger.error("Background job cleanup failed: %s", type(error).__name__)

            task.add_done_callback(finished)

        try:
            yield submit
        finally:
            if not submitted:
                self._reserved -= 1

    async def _run(self, run_id):
        try:
            async with self._slots:
                await run_pipeline(
                    run_id, repo=self.repo, settings=self.settings, dependencies=self.dependencies
                )
        except asyncio.CancelledError:
            await self.repo.fail_run(run_id, "CANCELLED")
            raise
        except LedgerConflict:
            logger.error("Duplicate run execution rejected.")
        except Exception as error:
            logger.error("Run execution failed: %s", type(error).__name__)
            await self.repo.fail_run(run_id, "EXECUTION_FAILED")

    async def aclose(self):
        self._closed = True
        pending = tuple(self._tasks.items())
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(t for _, t in pending), return_exceptions=True)
        # A queued task cancelled before its coroutine starts still needs a terminal row.
        for run_id, _ in pending:
            await self.repo.fail_run(run_id, "PROCESS_STOPPED")
