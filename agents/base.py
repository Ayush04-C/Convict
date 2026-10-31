"""Ayush-owned reusable STUB behavior, not the live agent implementation."""
from agents.protocol import AgentContext
from app.schemas import AgentName, Finding, Verdict


class StubSpecialist:
    name: AgentName

    async def run_round1(self, ctx: AgentContext) -> None:
        if ctx.packet.agent != self.name:
            raise ValueError("Context belongs to another specialist")
        findings = tuple(
            Finding(finding_id=f"{self.name}-{claim.id}", agent=self.name, claim_id=claim.id,
                    status="UNVERIFIABLE", note="STUB: no real verification performed.",
                    is_stub=True)
            for claim in ctx.packet.claims if claim.testable
        )
        await ctx.ledger.add_findings(findings)

    async def run_challenge(self, ctx: AgentContext) -> None:
        return None  # Deliberately no challenges in the initial stub.

    async def run_reply_verdict(self, ctx: AgentContext) -> None:
        await ctx.ledger.add_verdict(Verdict(
            agent=self.name, score=50, confidence=0.3, stance="STUB: neutral",
            flags=("Placeholder agent; not a real assessment",), is_stub=True,
        ))
