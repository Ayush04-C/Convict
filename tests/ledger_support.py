"""Ayush-owned ledger fixtures; synthetic records only."""

import pytest

from app.schemas import Claim, ClaimSet, Evidence, Finding, Policy
from ledger.db import initialize
from ledger.repo import LedgerRepository, PHASES, STATUS


@pytest.fixture
async def ledger(tmp_path):
    path = tmp_path / "database with spaces.sqlite"
    await initialize(path)
    return LedgerRepository(path, timeout=1)


@pytest.fixture
async def run(ledger):
    run_id = await ledger.create_run(
        "ayush", "Synthetic Company", None, Policy(max_check_usd=1000), "demo.pdf"
    )
    await ledger.add_claims(
        run_id,
        ClaimSet(
            company_name="Synthetic Company",
            claims=(
                Claim(
                    id="C1", topic="team_size", text="10 people", value=10, unit="count", slide=1
                ),
                Claim(
                    id="C2",
                    topic="funding_raised",
                    text="Raised 100",
                    value=100,
                    unit="USD",
                    slide=2,
                ),
            ),
        ),
    )
    return run_id


async def advance(repo, run_id, target):
    current = (await repo.get_run(run_id))["phase"]
    for phase in PHASES[PHASES.index(current) + 1 : PHASES.index(target) + 1]:
        await repo.set_phase(run_id, phase, STATUS[phase])


def evidence(run_id, evidence_id="E1", agent="scale_team"):
    return Evidence(
        run_id=run_id,
        evidence_id=evidence_id,
        agent=agent,
        source="synthetic.fixture",
        field_path="headcount.total",
        value_json="10",
        fetched_at="2026-10-31T06:00:00+00:00",
    )


def finding(finding_id="F1", agent="scale_team", **updates):
    values = dict(
        finding_id=finding_id,
        agent=agent,
        claim_id="C1",
        status="VERIFIED",
        severity=4,
        note="Synthetic comparison",
        evidence_ids=("E1",),
    )
    values.update(updates)
    return Finding(**values)
