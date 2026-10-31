"""Ayush: implement authorization and phase checks before using with live agents."""
from app.schemas import AgentName
from ledger.repo import LedgerRepository


class LedgerWriter:
    def __init__(self, run_id: str, agent: AgentName, repo: LedgerRepository):
        self.run_id, self.agent, self._repo = run_id, agent, repo

    async def add_findings(self, findings):
        raise NotImplementedError("Stage 6: bind writer, validate evidence and ownership")

    async def add_evidence(self, evidence):
        raise NotImplementedError("Stage 6: same-run append-only evidence")

    async def add_metrics(self, metrics):
        raise NotImplementedError("Stage 6: append-only metrics")

    async def add_challenges(self, challenges):
        raise NotImplementedError("Stage 6: validate phase, evidence, targets and max two")

    async def get_challenges_for(self):
        raise NotImplementedError("Stage 6: own challenges only, after barrier")

    async def add_reply(self, reply):
        raise NotImplementedError("Stage 6: append own reply")

    async def add_revision(self, revision):
        raise NotImplementedError("Stage 6: own finding revisions only")

    async def add_verdict(self, verdict):
        raise NotImplementedError("Stage 6: own verdict")

    async def get_all_findings(self):
        raise NotImplementedError("Stage 6: deny cross-agent reads before barrier")
