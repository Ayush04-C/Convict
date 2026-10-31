"""Offline smoke harness only: fixture extraction -> stub agents -> scoring."""

import asyncio
from pathlib import Path

from agents.base import StubSpecialist
from agents.protocol import AgentContext
from app.schemas import ClaimSet, Policy
from leo.context import AGENTS, build_context_packets
from leo.scoring import compute
from tests.fakes import FakeCrustdata, FakeLedger, FakeLlm, FakeStore


def offline_inputs():
    """Never import live feature modules, even after Daksh replaces their stubs."""
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/sample_claims.json"
    claims = ClaimSet.model_validate_json(fixture.read_text()).model_copy(update={"is_stub": True})
    return claims, [StubSpecialist(name) for name in AGENTS]


async def run_demo():
    claims, agents = offline_inputs()
    policy = Policy(max_check_usd=100000)
    packets = {p.agent: p for p in build_context_packets(claims, {}, policy)}
    store = FakeStore()
    contexts = {
        a.name: AgentContext(
            "offline-stub", packets[a.name], FakeLedger(store, a.name), FakeCrustdata(), FakeLlm()
        )
        for a in agents
    }
    for method in ("run_round1", "run_challenge", "run_reply_verdict"):
        await asyncio.gather(*(getattr(a, method)(contexts[a.name]) for a in agents))
    decision = compute(claims, tuple(store.findings), tuple(store.verdicts), policy)
    return {
        "mode": "STUB — no live verification",
        "findings": len(store.findings),
        "verdicts": len(store.verdicts),
        "decision": decision.model_dump(),
        "adjudication": "STUB: No disputes adjudicated.",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(asyncio.run(run_demo()), indent=2))
