"""Offline synthetic resolution, context and sandbox demo; not the live pipeline."""

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

import httpx

from agents.base import StubSpecialist
from agents.protocol import AgentContext
from app.schemas import Claim, ClaimSet, Policy
from app.settings import Settings
from crustdata.client import CrustdataClient
from crustdata.dossier import build_dossier, dossier_evidence
from ledger.db import initialize
from ledger.repo import LedgerRepository, STATUS
from leo.context import build_context_packets
from leo.entity_resolution import resolve_company
from sandbox.ledger_writer import LedgerWriter
from sandbox.runner import SandboxRunner
from sandbox.scoped_tools import ScopedCrustdata


class NoLlm:
    async def generate(self, *args, **kwargs):
        raise AssertionError("Synthetic specialists must not invoke Gemini.")


async def run_demo(path: Path, *, timeout_agent: bool = False):
    await initialize(path)
    repo = LedgerRepository(path)
    policy = Policy(max_check_usd=0)
    run = await repo.create_run("offline-demo", "Synthetic", "example.com", policy, "synthetic.pdf")
    claims = ClaimSet(
        company_name="Synthetic",
        is_stub=True,
        claims=(
            Claim(id="C1", topic="identity_founding", text="Founded 2020", value=2020, slide=1),
            Claim(id="C2", topic="team_size", text="12 employees", value=12, slide=1),
        ),
    )
    await repo.set_phase(run, "INGEST", STATUS["INGEST"])
    await repo.add_claims(run, claims)
    await repo.set_phase(run, "RESOLVE", STATUS["RESOLVE"])
    payload = [
        {
            "matched_on": "example.com",
            "matches": [
                {
                    "confidence_score": 1,
                    "company_data": {
                        "basic_info": {
                            "crustdata_company_id": 1,
                            "name": "Synthetic",
                            "primary_domain": "example.com",
                            "year_founded": 2020,
                        },
                        "headcount": {"total": 12},
                        "funding": None,
                    },
                }
            ],
        }
    ]
    settings = Settings(_env_file=None, crustdata_api_key="synthetic-offline")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        async with CrustdataClient(settings, http=http, cache_path=path) as provider:
            resolution = await resolve_company("Synthetic", "example.com", claims, provider)
            await repo.save_resolution(run, resolution)
            dossier = await build_dossier(resolution.company_id, provider)
            packets = build_context_packets(claims, dossier, policy)
            await repo.add_evidence(run, (dossier_evidence(run, dossier),))
            await repo.save_context_packets(run, packets)
            await repo.set_phase(run, "ROUND1", STATUS["ROUND1"])
            runner = SandboxRunner(repo)
            work = []
            for packet in packets:
                agent = StubSpecialist(packet.agent)
                if timeout_agent and packet.agent == "identity":

                    async def slow(ctx):
                        await asyncio.sleep(10)

                    agent.run_round1 = slow
                context = AgentContext(
                    run,
                    packet,
                    LedgerWriter(run, packet.agent, repo),
                    ScopedCrustdata(packet.agent, provider, company_id=resolution.company_id),
                    NoLlm(),
                )
                work.append(
                    runner.run(
                        agent,
                        context,
                        "ROUND1",
                        timeout=0.01 if timeout_agent and packet.agent == "identity" else 60,
                    )
                )
            results = await asyncio.gather(*work)
    return {
        "mode": "OFFLINE SYNTHETIC; NOT A LIVE ASSESSMENT",
        "resolution": resolution.status,
        "packets": len(packets),
        "agents_succeeded": sum(results),
        "agents_failed": len(results) - sum(results),
        "agent_statuses": {s["agent"]: s["state"] for s in await repo.get_agent_statuses(run)},
        "findings": len(await repo.get_all_findings(run)),
        "phase": (await repo.get_run(run))["phase"],
        "real_api_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-agent", action="store_true", help="Force Identity to exceed its deadline."
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="leo-context-") as directory:
        result = asyncio.run(
            run_demo(Path(directory) / "demo.sqlite", timeout_agent=args.timeout_agent)
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
