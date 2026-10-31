"""Structural contracts only: no concrete-agent or SDK imports."""
from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas import AgentName, ContextPacket, Finding, Verdict


class LedgerPort(Protocol):
    async def add_findings(self, findings: tuple[Finding, ...]) -> None: ...
    async def add_verdict(self, verdict: Verdict) -> None: ...
    async def get_all_findings(self) -> tuple[Finding, ...]: ...


class ToolPort(Protocol):
    async def call(self, method: str, **params: Any) -> Any: ...


class LlmClient(Protocol):
    async def generate(self, prompt: str, schema: type, model: str) -> Any: ...


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    packet: ContextPacket
    ledger: LedgerPort
    tools: ToolPort
    llm: LlmClient


class SpecialistAgent(Protocol):
    name: AgentName

    async def run_round1(self, ctx: AgentContext) -> None: ...
    async def run_challenge(self, ctx: AgentContext) -> None: ...
    async def run_reply_verdict(self, ctx: AgentContext) -> None: ...
