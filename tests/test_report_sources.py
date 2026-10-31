"""Readable source summaries must preserve raw evidence and honest provenance."""

import json

import pytest

from app.schemas import Policy
from report.builder import build_report
from report.markdown import render_markdown
from tests.ledger_support import advance


@pytest.mark.parametrize("matches", [True, False])
async def test_truncated_source_summary_uses_only_matching_saved_snapshot(ledger, matches):
    from app.schemas import Evidence

    run = await ledger.create_run("tester", "Synthetic", None, Policy(max_check_usd=0), "unused")
    history = [
        {
            "round_type": "series_c",
            "amount_usd": 45_000_000,
            "date": "2022-07-27",
            "description": "x" * 1600,
        }
    ]
    serialized = json.dumps(history, ensure_ascii=False, sort_keys=True, allow_nan=False)
    excerpt = {
        "_truncated": True,
        "_chars": len(serialized),
        "preview": serialized[:1500] if matches else "unrelated preview",
    }
    await advance(ledger, run, "ROUND1")
    for eid, agent, path, value in [
        ("dossier:test", "leo", "company_data", {"funding": {"milestones": history}}),
        ("funding:E1", "funding", "funding.milestones", excerpt),
    ]:
        await ledger.add_evidence(
            run,
            (
                Evidence(
                    run_id=run,
                    evidence_id=eid,
                    agent=agent,
                    source="crustdata.company.enrich",
                    field_path=path,
                    value_json=json.dumps(value),
                    fetched_at="2026-10-31T00:00:00Z",
                ),
            ),
        )
    await ledger.fail_run(run, "TEST_FAILURE")
    before = await ledger.get_evidence(run)
    report = await build_report(run, ledger)
    item = next(e for e in report["evidence"] if e["evidence_id"] == "funding:E1")
    assert item["value"] == excerpt
    if matches:
        assert item["provider_fetched_at"] == "2026-10-31T00:00:00Z"
        assert "Series C, $45,000,000, 27 Jul 2022" in item["display_summary"]
    else:
        assert item["provider_fetched_at"] is None
        assert "shortened excerpt" in item["display_summary"]
    assert item["source_updated_at"] is None
    rendered = render_markdown(report)
    assert "_truncated" not in rendered and "unrelated preview" not in rendered
    assert await ledger.get_evidence(run) == before


def test_plain_source_summaries_keep_units_and_zero_values():
    from report.presentation import source_summary

    assert source_summary(0, "funding.total_investment_usd") == "$0"
    assert source_summary(2020, "basic_info.year_founded") == "2020"
    assert source_summary(-0.9, "hiring.openings_growth_percent.qoq") == "-0.9%"
    assert source_summary({"yoy": -10.412}, "headcount.growth_percent") == "Year over year: -10.4%"
    assert source_summary([], "funding.milestones") == "No records supplied"
