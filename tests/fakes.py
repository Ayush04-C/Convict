"""Offline test support, not a production ledger or isolation implementation."""
from app.schemas import AgentName, Finding, Verdict


class FakeStore:
    def __init__(self):
        self.findings: list[Finding] = []
        self.verdicts: list[Verdict] = []


class FakeLedger:
    def __init__(self, store: FakeStore, agent: AgentName):
        self.store, self.agent = store, agent

    async def add_findings(self, findings: tuple[Finding, ...]) -> None:
        if any(f.agent != self.agent for f in findings):
            raise PermissionError("Cannot impersonate another agent")
        self.store.findings.extend(findings)

    async def add_verdict(self, verdict: Verdict) -> None:
        if verdict.agent != self.agent:
            raise PermissionError("Cannot impersonate another agent")
        self.store.verdicts.append(verdict)

    async def get_all_findings(self) -> tuple[Finding, ...]:
        return tuple(self.store.findings)

    async def add_evidence(self, evidence):
        raise NotImplementedError("Extend fake when feature tests require evidence writes")

    async def add_metrics(self, metrics):
        raise NotImplementedError("Extend fake when feature tests require metrics")

    async def add_challenges(self, challenges):
        raise NotImplementedError("Extend fake when real challenge tests are added")

    async def get_challenges_for(self):
        return ()

    async def add_reply(self, reply):
        raise NotImplementedError("Extend fake when real reply tests are added")

    async def add_revision(self, revision):
        raise NotImplementedError("Extend fake when revision tests are added")


class FakeLlm:
    def __init__(self, responses: tuple = ()):
        self.responses = iter(responses)

    async def generate(self, prompt: str, schema: type, model: str):
        return schema.model_validate(next(self.responses))


class FakeCrustdata:
    async def call(self, method: str, **params):
        raise AssertionError("The specialist stub must not make provider calls")
