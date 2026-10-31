"""Run one specialist phase; ledger failures remain visible to the coordinator."""

import asyncio
from dataclasses import replace
import math

from agents.protocol import AgentContext, SpecialistAgent
from app.schemas import Phase
from ledger.errors import LedgerPermissionError, LedgerStateError
from ledger.repo import LedgerRepository
from sandbox.ledger_writer import LedgerWriter
from sandbox.scoped_ledger import ScopedLedger
from sandbox.scoped_tools import ScopedCrustdata

PHASE_METHODS = {"ROUND1": "run_round1", "CHALLENGE": "run_challenge", "REPLY": "run_reply_verdict"}


class SandboxRunner:
    def __init__(self, repo: LedgerRepository):
        self.repo = repo

    async def run(
        self, agent: SpecialistAgent, ctx: AgentContext, phase: Phase, timeout: float = 60
    ) -> bool:
        if phase not in PHASE_METHODS or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("A specialist phase and timeout of at most 60 seconds are required.")
        if (
            not isinstance(ctx.ledger, LedgerWriter)
            or ctx.ledger._repo is not self.repo
            or ctx.run_id != ctx.ledger.run_id
            or agent.name != ctx.packet.agent
            or agent.name != ctx.ledger.agent
            or not isinstance(ctx.tools, ScopedCrustdata)
            or ctx.tools.agent != agent.name
        ):
            raise LedgerPermissionError("Agent context capabilities do not match its identity.")
        run = await self.repo.get_run(ctx.run_id)
        if run["phase"] != phase:
            raise LedgerStateError("Specialist execution does not match the current run phase.")
        if run["crustdata_company_id"] != ctx.tools.company_id:
            raise LedgerPermissionError("Tools do not match the run's resolved company.")
        if await self.repo.get_context_packet(ctx.run_id, agent.name) != ctx.packet:
            raise LedgerPermissionError("Context does not match the saved specialist packet.")
        await self.repo.set_agent_status(ctx.run_id, agent.name, phase, "RUNNING", exclusive=True)
        tools = ctx.tools._for_phase()
        ledger = ScopedLedger(ctx.ledger)
        execution = replace(ctx, tools=tools, ledger=ledger)
        failure = None
        try:
            async with asyncio.timeout(timeout):
                await getattr(agent, PHASE_METHODS[phase])(execution)
        except asyncio.CancelledError:
            failure = "CANCELLED"
            raise
        except TimeoutError:
            failure = "TIMEOUT"
        except Exception:
            # Arbitrary agent messages may contain prompts, keys or raw provider data.
            failure = "AGENT_ERROR"
        finally:
            await tools.aclose()
            await ledger.aclose()
            await self.repo.set_agent_status(
                ctx.run_id, agent.name, phase, "FAILED" if failure else "DONE", error=failure
            )
        return failure is None
