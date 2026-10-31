"""Company-bound tool capabilities; one fresh, revocable scope per agent phase."""

import asyncio

from app.schemas import AgentName
from leo.context import DOSSIER_GROUPS

METHODS = {
    "identity": frozenset(("enrich", "web_search")),
    "scale_team": frozenset(("enrich",)),
    "funding": frozenset(("enrich",)),
    "traction": frozenset(("enrich", "job_search")),
}


class ToolBudgetExceeded(PermissionError):
    pass


class ScopedCrustdata:
    def __init__(
        self,
        agent: AgentName,
        client,
        allowed: tuple[str, ...] | None = None,
        max_calls: int = 6,
        *,
        company_id: int,
    ):
        if agent not in METHODS or type(company_id) is not int or company_id <= 0:
            raise ValueError("A known agent and resolved company ID are required.")
        if type(max_calls) is not int or not 1 <= max_calls <= 6:
            raise ValueError("Per-phase call limit must be between one and six.")
        allowed = METHODS[agent] if allowed is None else frozenset(allowed)
        if not allowed <= METHODS[agent]:
            raise PermissionError("Cannot grant tools outside the specialist allowlist.")
        self.agent, self._client, self.allowed, self.max_calls = agent, client, allowed, max_calls
        self.company_id = company_id
        self.calls = 0
        self._closed = False
        self._tasks = set()

    def _for_phase(self):
        if self._closed:
            raise PermissionError("Cannot reuse a closed tool capability.")
        return ScopedCrustdata(
            self.agent,
            self._client,
            tuple(self.allowed),
            self.max_calls,
            company_id=self.company_id,
        )

    async def aclose(self):
        self._closed = True
        tasks = tuple(t for t in self._tasks if t is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def call(self, method: str, **params):
        if self._closed:
            raise PermissionError("Tool scope is closed.")
        if not isinstance(method, str) or method not in self.allowed:
            raise PermissionError("Tool is not allowed for this specialist.")
        if method in ("enrich", "job_search"):
            supplied = params.get("company_id", self.company_id)
            if type(supplied) is not int or supplied != self.company_id or "domain" in params:
                raise PermissionError("Tool request must use the resolved company ID.")
            params["company_id"] = self.company_id
        if method == "enrich":
            fields = params.get("fields", DOSSIER_GROUPS[self.agent])
            if (
                not isinstance(fields, (tuple, list))
                or not fields
                or not all(isinstance(f, str) for f in fields)
                or not set(fields) <= set(DOSSIER_GROUPS[self.agent])
            ):
                raise PermissionError("Enrichment fields are outside the specialist dossier scope.")
            params["fields"] = list(fields)
        # Reserve before awaiting: concurrent calls cannot overspend the phase budget.
        # Failed/cancelled calls still consume a slot; denied calls do not reach HTTP.
        if self.calls >= self.max_calls:
            raise ToolBudgetExceeded("Specialist exhausted its per-phase tool budget.")
        self.calls += 1
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            return await getattr(self._client, method)(**params)
        finally:
            self._tasks.discard(task)
