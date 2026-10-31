"""Ayush: phase-scoped timeout/cancellation; not process isolation."""
from agents.protocol import AgentContext, SpecialistAgent
from app.schemas import Phase


class SandboxRunner:
    async def run(self, agent: SpecialistAgent, ctx: AgentContext,
                  phase: Phase, timeout: float = 60) -> None:
        raise NotImplementedError("Stage 6: phase runner, timeout and FAILED status")
