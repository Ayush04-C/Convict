"""Ayush: enforce per-agent allowlists and per-phase call budgets."""
from app.schemas import AgentName


class ScopedCrustdata:
    def __init__(self, agent: AgentName, client, allowed: tuple[str, ...], max_calls: int = 6):
        self.agent, self._client, self.allowed, self.max_calls = agent, client, allowed, max_calls

    async def call(self, method: str, **params):
        raise NotImplementedError("Stage 6: allowlist and per-phase call budget")
