"""Persist a full, visibly STUB analysis using SQLite and real bound writers."""

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from agents.protocol import AgentContext
from app.schemas import AdjudicationResult, Policy
from ledger.db import initialize
from ledger.repo import LedgerRepository
from leo.context import build_context_packets
from leo.scoring import compute
from sandbox.ledger_writer import LedgerWriter
from scripts.demo import offline_inputs
from tests.fakes import FakeCrustdata, FakeLlm


async def run_ledger_demo(path: Path) -> dict:
    await initialize(path)
    repo = LedgerRepository(path)
    policy = Policy(max_check_usd=100000)
    run_id = await repo.create_run(
        "offline-demo", "Synthetic Demo Company", None, policy, "stub-only.pdf"
    )
    await repo.set_phase(run_id, "INGEST", "parsing")
    claims, agents = offline_inputs()
    await repo.add_claims(run_id, claims)
    await repo.set_phase(run_id, "RESOLVE", "resolving")
    # The real entity/provider stages are deliberately not simulated as verified.
    packets = {p.agent: p for p in build_context_packets(claims, {}, policy)}
    await repo.save_context_packets(run_id, tuple(packets.values()))
    contexts = {
        a.name: AgentContext(
            run_id, packets[a.name], LedgerWriter(run_id, a.name, repo), FakeCrustdata(), FakeLlm()
        )
        for a in agents
    }
    for phase, status, method in [
        ("ROUND1", "round1", "run_round1"),
        ("CHALLENGE", "round2", "run_challenge"),
        ("REPLY", "round2", "run_reply_verdict"),
    ]:
        await repo.set_phase(run_id, phase, status)

        async def execute(agent):
            await repo.set_agent_status(run_id, agent.name, phase, "RUNNING")
            await getattr(agent, method)(contexts[agent.name])
            await repo.set_agent_status(run_id, agent.name, phase, "DONE")

        await asyncio.gather(*(execute(a) for a in agents))
    await repo.set_phase(run_id, "ADJUDICATE", "round2")
    result = AdjudicationResult(narrative="STUB: No disputes adjudicated.", is_stub=True)
    await repo.set_phase(run_id, "SCORING", "scoring")
    decision = compute(
        await repo.get_claims(run_id),
        await repo.get_all_findings(run_id),
        await repo.get_verdicts(run_id),
        policy,
    )
    await repo.save_decision(run_id, decision, result)
    reopened = LedgerRepository(path)
    return {
        "mode": "STUB — no real entity resolution, extraction or verification",
        "database": str(path),
        "run_id": run_id,
        "status": (await reopened.get_run(run_id))["status"],
        "findings": len(await reopened.get_all_findings(run_id)),
        "verdicts": len(await reopened.get_verdicts(run_id)),
        "events": len(await reopened.get_events(run_id)),
        "decision": (await reopened.get_decision(run_id)).model_dump(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/ledger-demo.sqlite"))
    parser.add_argument(
        "--temporary", action="store_true", help="Use and clean up a temporary test database"
    )
    args = parser.parse_args()
    if args.temporary:
        with tempfile.TemporaryDirectory(prefix="leo-ledger-") as directory:
            result = asyncio.run(run_ledger_demo(Path(directory) / "demo.sqlite"))
    else:
        result = asyncio.run(run_ledger_demo(args.db))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
