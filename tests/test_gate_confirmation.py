"""Proposed gates require authoritative facts and complete matching citations."""

import json

import pytest

from app.schemas import Claim, ClaimSet, Dossier, Evidence, Metric, Policy
from leo.gates import confirm_gates


@pytest.mark.parametrize(
    "amount,upper,unit,expected",
    [
        (150, 50, "USD", ()),
        (150.01, 50, "USD", ("arr_ratio_over_3",)),
        (200, 0, "USD", ()),
        (200, None, "USD", ()),
        (200, 50, "INR", ()),
    ],
)
async def test_arr_gate_boundary_missing_denominator_and_currency(
    ledger, amount, upper, unit, expected
):
    run = await ledger.create_run("test", "Synthetic", None, Policy(max_check_usd=0), "deck.pdf")
    claims = ClaimSet(
        claims=(Claim(id="C1", topic="revenue_arr", text="ARR", value=amount, unit=unit, slide=1),)
    )
    data = {"revenue": {"estimated": {"upper_bound_usd": upper}}}
    dossier = Dossier(company_id=1, data_json=json.dumps(data), fetched_at="2026-10-31T00:00:00Z")
    evidence = Evidence(
        run_id=run,
        evidence_id="E1",
        agent="funding",
        source="crustdata.company.enrich",
        field_path="revenue.estimated.upper_bound_usd",
        value_json=json.dumps(upper),
        fetched_at=dossier.fetched_at,
    )
    await ledger.add_evidence(run, (evidence,))
    await ledger.add_metrics(
        run,
        (
            Metric(
                run_id=run,
                agent="funding",
                name="gate:arr_ratio_over_3",
                value=1,
                unit="proposal",
                formula="proposed",
                evidence_ids=("E1",),
            ),
        ),
    )
    assert await confirm_gates(ledger, run, claims, dossier, {"funding"}) == expected


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_value",
        "wrong_source",
        "wrong_owner",
        "wrong_unit",
        "unknown_gate",
        "failed_agent",
        "missing_citation",
    ],
)
async def test_untrusted_or_incomplete_proposal_cannot_force_gate(ledger, fault):
    run = await ledger.create_run("test", "Synthetic", None, Policy(max_check_usd=0), "deck.pdf")
    data = {"revenue": {"estimated": {"lower_bound_usd": 20, "upper_bound_usd": 50}}}
    claims = ClaimSet(
        claims=(Claim(id="C1", topic="revenue_arr", text="ARR", value=200, unit="USD", slide=1),)
    )
    dossier = Dossier(company_id=1, data_json=json.dumps(data), fetched_at="2026-10-31T00:00:00Z")
    items = []
    for eid, key, value in [("E1", "lower_bound_usd", 20), ("E2", "upper_bound_usd", 50)]:
        items.append(
            Evidence(
                run_id=run,
                evidence_id=eid,
                agent="identity" if fault == "wrong_owner" else "funding",
                source="invented" if fault == "wrong_source" else "crustdata.company.enrich",
                field_path="revenue.estimated." + key,
                value_json=json.dumps(999 if fault == "wrong_value" else value),
                fetched_at=dossier.fetched_at,
            )
        )
    await ledger.add_evidence(run, tuple(items))
    await ledger.add_metrics(
        run,
        (
            Metric(
                run_id=run,
                agent="funding",
                name="gate:unknown" if fault == "unknown_gate" else "gate:arr_ratio_over_3",
                value=1,
                unit="not-proposal" if fault == "wrong_unit" else "proposal",
                formula="test",
                evidence_ids=("E1",) if fault == "missing_citation" else ("E1", "E2"),
            ),
        ),
    )
    assert (
        await confirm_gates(
            ledger, run, claims, dossier, set() if fault == "failed_agent" else {"funding"}
        )
        == ()
    )
