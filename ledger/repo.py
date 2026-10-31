"""Ayush: stable repository surface. Stage 3 implements these operations.

Repository instances belong to one database, while every operation is explicitly
run-scoped. Never pass this unrestricted object to a specialist; use LedgerWriter.
"""
from pathlib import Path

from app.schemas import (
    AdjudicationResult, AgentName, Challenge, ClaimSet, ContextPacket, Decision,
    Evidence, Finding, FindingRevision, Metric, Phase, Policy, Reply, RunStatus, Verdict,
)


class LedgerRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    async def create_run(self, user: str, company_name: str, domain: str | None,
                         policy: Policy, deck_path: str) -> str:
        raise NotImplementedError("Stage 3: create_run")

    async def get_run(self, run_id: str) -> dict:
        raise NotImplementedError("Stage 3: get_run")

    async def set_phase(self, run_id: str, phase: Phase, status: RunStatus) -> None:
        raise NotImplementedError("Stage 3: set_phase")

    async def wait_for_phase(self, run_id: str, phase: Phase, timeout: float) -> bool:
        raise NotImplementedError("Stage 3: bounded phase wait")

    async def save_context_packets(self, run_id: str, packets: tuple[ContextPacket, ...]) -> None:
        raise NotImplementedError("Stage 3: save_context_packets")

    async def add_claims(self, run_id: str, claims: ClaimSet) -> None:
        raise NotImplementedError("Stage 3: add_claims")

    async def add_evidence(self, run_id: str, evidence: tuple[Evidence, ...]) -> None:
        raise NotImplementedError("Stage 3: add_evidence")

    async def add_metrics(self, run_id: str, metrics: tuple[Metric, ...]) -> None:
        raise NotImplementedError("Stage 3: append metrics")

    async def add_findings(self, run_id: str, findings: tuple[Finding, ...]) -> None:
        raise NotImplementedError("Stage 3: append findings")

    async def get_all_findings(self, run_id: str) -> tuple[Finding, ...]:
        raise NotImplementedError("Stage 3: effective findings with revisions")

    async def add_challenges(self, run_id: str, challenges: tuple[Challenge, ...]) -> None:
        raise NotImplementedError("Stage 3: add_challenges")

    async def get_challenges_for(self, run_id: str, agent: AgentName) -> tuple[Challenge, ...]:
        raise NotImplementedError("Stage 3: get_challenges_for")

    async def add_reply(self, run_id: str, reply: Reply) -> None:
        raise NotImplementedError("Stage 3: append reply")

    async def add_revision(self, run_id: str, revision: FindingRevision) -> None:
        raise NotImplementedError("Stage 3: append revision")

    async def add_verdict(self, run_id: str, verdict: Verdict) -> None:
        raise NotImplementedError("Stage 3: add_verdict")

    async def get_verdicts(self, run_id: str) -> tuple[Verdict, ...]:
        raise NotImplementedError("Stage 3: get_verdicts")

    async def set_agent_status(self, run_id: str, agent: AgentName, phase: Phase,
                               state: str, error: str | None = None) -> None:
        raise NotImplementedError("Stage 3: set_agent_status")

    async def add_event(self, run_id: str, kind: str, payload: dict) -> int:
        raise NotImplementedError("Stage 3: add_event")

    async def get_events(self, run_id: str, after: int = 0) -> tuple:
        raise NotImplementedError("Stage 3: get_events")

    async def save_decision(self, run_id: str, decision: Decision,
                            adjudication: AdjudicationResult) -> None:
        raise NotImplementedError("Stage 3: save_decision")
