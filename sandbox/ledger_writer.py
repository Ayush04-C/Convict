"""Bound specialist capability; ownership and phase checks happen transactionally."""
from dataclasses import dataclass, field, InitVar

from app.schemas import (
    AgentName, Challenge, Evidence, Finding, FindingRevision, Metric, Reply, Verdict,
)
from ledger.errors import LedgerPermissionError
from ledger.repo import AGENTS, LedgerRepository


@dataclass(frozen=True)
class LedgerWriter:
    run_id: str
    agent: AgentName
    repo: InitVar[LedgerRepository]
    _repo: LedgerRepository = field(init=False, repr=False)

    def __post_init__(self, repo):
        if self.agent not in AGENTS:
            raise LedgerPermissionError("Unknown specialist namespace.")
        object.__setattr__(self, "_repo", repo)

    async def add_findings(self, findings: tuple[Finding, ...]) -> None:
        await self._repo.add_findings(self.run_id, findings, actor=self.agent)

    async def add_evidence(self, evidence: tuple[Evidence, ...]) -> None:
        await self._repo.add_evidence(self.run_id, evidence, actor=self.agent)

    async def add_metrics(self, metrics: tuple[Metric, ...]) -> None:
        await self._repo.add_metrics(self.run_id, metrics, actor=self.agent)

    async def add_challenges(self, challenges: tuple[Challenge, ...]) -> None:
        await self._repo.add_challenges(self.run_id, challenges, actor=self.agent)

    async def get_challenges_for(self) -> tuple[Challenge, ...]:
        return await self._repo.get_challenges_for(self.run_id, self.agent, actor=self.agent)

    async def add_reply(self, reply: Reply) -> None:
        await self._repo.add_reply(self.run_id, reply, actor=self.agent)

    async def add_revision(self, revision: FindingRevision) -> None:
        await self._repo.add_revision(self.run_id, revision, actor=self.agent)

    async def add_verdict(self, verdict: Verdict) -> None:
        await self._repo.add_verdict(self.run_id, verdict, actor=self.agent)

    async def get_all_findings(self) -> tuple[Finding, ...]:
        return await self._repo.get_all_findings(self.run_id, actor=self.agent)
