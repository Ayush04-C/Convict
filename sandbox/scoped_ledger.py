"""Revocable phase lifetime over the existing transactional ledger capability."""

import asyncio


METHODS = frozenset(
    (
        "add_evidence",
        "get_evidence",
        "add_metrics",
        "add_findings",
        "add_challenges",
        "get_challenges_for",
        "add_reply",
        "add_revision",
        "add_verdict",
        "get_all_findings",
    )
)


class ScopedLedger:
    def __init__(self, writer):
        self._writer = writer
        self._closed = False
        self._tasks = set()

    def __getattr__(self, method):
        if method not in METHODS:
            raise AttributeError(method)

        async def call(*args, **kwargs):
            if self._closed:
                raise PermissionError("Ledger phase capability is closed.")
            task = asyncio.current_task()
            self._tasks.add(task)
            try:
                return await getattr(self._writer, method)(*args, **kwargs)
            finally:
                self._tasks.discard(task)

        return call

    async def aclose(self):
        self._closed = True
        tasks = tuple(t for t in self._tasks if t is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
