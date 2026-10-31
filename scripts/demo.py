"""Offline smoke harness only: fixture extraction -> stub agents -> scoring."""
import asyncio

from agents.protocol import AgentContext
from agents.registry import get_agents
from app.schemas import Policy
from ingest.claim_extractor import extract_claims
from ingest.pdf_parser import parse_deck
from leo.adjudicator import adjudicate
from leo.context import build_context_packets
from leo.scoring import compute
from tests.fakes import FakeCrustdata, FakeLedger, FakeLlm, FakeStore


async def run_demo():
    claims = await extract_claims(await parse_deck("stub-only.pdf"))
    policy = Policy(max_check_usd=100000)
    packets = {p.agent: p for p in build_context_packets(claims, {}, policy)}
    store = FakeStore()
    agents = get_agents()
    contexts = {a.name: AgentContext("offline-stub", packets[a.name], FakeLedger(store, a.name),
                                    FakeCrustdata(), FakeLlm()) for a in agents}
    for method in ("run_round1", "run_challenge", "run_reply_verdict"):
        await asyncio.gather(*(getattr(a, method)(contexts[a.name]) for a in agents))
    decision = compute(claims, tuple(store.findings), tuple(store.verdicts), policy)
    return {"mode": "STUB — no live verification", "findings": len(store.findings),
            "verdicts": len(store.verdicts), "decision": decision.model_dump(),
            "adjudication": (await adjudicate("offline-stub")).narrative}


if __name__ == "__main__":
    import json
    print(json.dumps(asyncio.run(run_demo()), indent=2))
