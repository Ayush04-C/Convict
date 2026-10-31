"""Persistent phase coordinator. Specialists communicate through their own ledger ports."""

import asyncio

from agents.protocol import AgentContext
from app.settings import Settings
from crustdata.client import CrustdataError
from crustdata.dossier import build_dossier, dossier_evidence
from ingest.pdf_parser import DeckRejectedError
from ingest.claim_extractor import ExtractionError
from ledger.errors import LedgerError
from ledger.repo import LedgerRepository, STATUS
from leo.context import AGENTS, build_context_packets
from leo.entity_resolution import resolve_company
from leo.gates import confirm_gates
from leo.runtime import PipelineDependencies
from leo.scoring import compute
from sandbox.ledger_writer import LedgerWriter
from sandbox.runner import SandboxRunner
from sandbox.scoped_tools import ScopedCrustdata


async def run_pipeline(
    run_id: str, *, repo: LedgerRepository, settings: Settings, dependencies: PipelineDependencies
) -> None:
    # Duplicate attempts must not mark an already running coordinator as FAILED.
    await repo.claim_run(run_id)
    try:
        async with asyncio.timeout(settings.pipeline_timeout):
            await _execute(run_id, repo, settings, dependencies)
    except asyncio.CancelledError:
        await repo.fail_run(run_id, "CANCELLED")
        raise
    except TimeoutError:
        await repo.fail_run(run_id, "PIPELINE_TIMEOUT")
    except DeckRejectedError:
        await repo.fail_run(run_id, "DECK_REJECTED")
    except ExtractionError:
        await repo.fail_run(run_id, "EXTRACTION_FAILED")
    except CrustdataError:
        await repo.fail_run(run_id, "PROVIDER_FAILED")
    except LedgerError:
        await repo.fail_run(run_id, "LEDGER_FAILED")
        raise
    except Exception:
        await repo.fail_run(run_id, "PIPELINE_FAILED")


async def _execute(run_id, repo, settings, deps):
    run = await repo.get_run(run_id)
    async with asyncio.timeout(settings.ingest_timeout):
        deck = await deps.parse(run["deck_path"])
        await repo.add_event(
            run_id, "deck_parsed", {"pages": deck.processed_pages, "warnings": deck.warnings}
        )
        claims = await deps.extract(deck)
        claims = await deps.normalize(claims, inr_per_usd=settings.ingest_inr_per_usd)
    if settings.mode == "live" and (deck.is_stub or claims.is_stub):
        raise RuntimeError("Live ingestion returned synthetic data.")
    await repo.add_claims(run_id, claims)
    await repo.set_phase(run_id, "RESOLVE", STATUS["RESOLVE"])
    resolution = await resolve_company(run["company_name"], run["domain"], claims, deps.provider)
    await repo.save_resolution(run_id, resolution)
    if resolution.status != "resolved":
        return
    dossier = await build_dossier(resolution.company_id, deps.provider)
    if settings.mode == "stub":
        dossier = dossier.model_copy(update={"is_stub": True})
    await repo.add_evidence(run_id, (dossier_evidence(run_id, dossier),))
    packets = {p.agent: p for p in build_context_packets(claims, dossier, run["policy"])}
    await repo.save_context_packets(run_id, tuple(packets.values()))
    agents = deps.agents()
    if len(agents) != 4 or {a.name for a in agents} != set(AGENTS):
        raise ValueError("Exactly four distinct specialist agents are required.")
    runner, survivors = SandboxRunner(repo), {a.name for a in agents}
    for phase in ("ROUND1", "CHALLENGE", "REPLY"):
        await repo.set_phase(run_id, phase, STATUS[phase])
        active = [a for a in agents if a.name in survivors]
        for agent in agents:
            if agent.name not in survivors:
                await repo.set_agent_status(
                    run_id, agent.name, phase, "FAILED", "PREVIOUS_PHASE_FAILED"
                )

        async def execute(agent):
            ctx = AgentContext(
                run_id,
                packets[agent.name],
                LedgerWriter(run_id, agent.name, repo),
                ScopedCrustdata(agent.name, deps.provider, company_id=resolution.company_id),
                deps.llm,
            )
            return await runner.run(agent, ctx, phase, timeout=settings.phase_timeout)

        tasks = [asyncio.create_task(execute(a)) for a in active]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        survivors = {a.name for a, succeeded in zip(active, results) if succeeded}
        if phase == "ROUND1":
            proposal_agents = survivors.copy()
        if not survivors:
            await repo.fail_run(run_id, "ALL_AGENTS_FAILED")
            return
    await repo.set_phase(run_id, "ADJUDICATE", STATUS["ADJUDICATE"])
    async with asyncio.timeout(settings.phase_timeout):
        adjudication = await deps.adjudicate(
            run_id, repo=repo, llm=deps.llm, model=settings.model_pro or None
        )
    # The lead analyst writes its own rulings to the ledger and reports every one of them. Apply
    # only the revisions not already stored, so that neither a ruling it persisted nor a retried
    # call can collide with the ledger's unique revision ids.
    stored = {r.revision_id for r in await repo.get_revisions(run_id)}
    for revision in adjudication.revisions:
        if revision.revision_id not in stored:
            await repo.add_revision(run_id, revision)
    await repo.set_phase(run_id, "SCORING", STATUS["SCORING"])
    findings = tuple(f for f in await repo.get_all_findings(run_id) if f.agent in survivors)
    verdicts = tuple(v for v in await repo.get_verdicts(run_id) if v.agent in survivors)
    if not verdicts:
        await repo.fail_run(run_id, "NO_AGENT_VERDICTS")
        return
    gates = await confirm_gates(repo, run_id, claims, dossier, proposal_agents)
    await repo.add_event(run_id, "gates_confirmed", {"gates": gates})
    decision = compute(claims, findings, verdicts, run["policy"], confirmed_gates=gates)
    await repo.save_decision(run_id, decision, adjudication)
