"""Persist exactly the calculation used for new decisions; never backfill old scores."""

import pytest

from app.schemas import AdjudicationResult
from ledger.errors import LedgerReferenceError
from leo.scoring import compute_with_breakdown
from report.builder import build_report
from report.markdown import render_markdown
from tests.ledger_support import advance
from tests.test_scoring import case


async def test_breakdown_matches_saved_result_and_round_trips(ledger, run):
    await advance(ledger, run, "SCORING")
    decision, breakdown = compute_with_breakdown(*case(), confirmed_gates=("arr_ratio_over_3",))
    await ledger.save_decision(
        run, decision, AdjudicationResult(narrative="Synthetic"), scoring_breakdown=breakdown
    )
    assert await ledger.get_scoring_breakdown(run) == breakdown
    report = await build_report(run, ledger)
    assert report["scoring_breakdown"] == breakdown
    assert report["decision"]["total_score"] == breakdown["result"]["total_score"]
    assert "Saved scoring calculation" in render_markdown(report)


async def test_mismatched_breakdown_cannot_complete_the_run(ledger, run):
    await advance(ledger, run, "SCORING")
    decision, breakdown = compute_with_breakdown(*case())
    breakdown["result"]["total_score"] = 1
    before = await ledger.get_events(run)
    with pytest.raises(LedgerReferenceError):
        await ledger.save_decision(
            run, decision, AdjudicationResult(narrative="Synthetic"), scoring_breakdown=breakdown
        )
    assert (await ledger.get_run(run))["phase"] == "SCORING" and await ledger.get_events(
        run
    ) == before


async def test_legacy_decisions_are_not_recomputed_for_a_breakdown(ledger, run):
    await advance(ledger, run, "SCORING")
    decision, _ = compute_with_breakdown(*case())
    await ledger.save_decision(run, decision, AdjudicationResult(narrative="Legacy record"))
    before = await ledger.get_events(run)
    report = await build_report(run, ledger)
    assert report["scoring_breakdown"] is None
    assert "has not been recomputed" in render_markdown(report)
    assert await ledger.get_events(run) == before


async def test_pipeline_saves_the_breakdown_consumed_by_api_and_cli(auth_settings, tmp_path):
    from tests.test_run_api import api, upload, terminal

    async with api(auth_settings, tmp_path) as (app, client):
        run = (await upload(client)).json()["run_id"]
        saved = (await terminal(client, run))["decision"]
        report = (await client.get(f"/runs/{run}/report")).json()
        breakdown = report["scoring_breakdown"]
        assert breakdown["result"] == {key: saved[key] for key in breakdown["result"]}
        assert breakdown == await app.state.ledger.get_scoring_breakdown(run)


async def test_decision_breakdown_and_terminal_event_roll_back_together(ledger, run, monkeypatch):
    from ledger.errors import LedgerNotFound

    await advance(ledger, run, "SCORING")
    decision, breakdown = compute_with_breakdown(*case())

    async def fail(*args):
        raise RuntimeError("Synthetic event failure")

    monkeypatch.setattr("ledger.repo.event", fail)
    with pytest.raises(RuntimeError):
        await ledger.save_decision(
            run, decision, AdjudicationResult(narrative="Synthetic"), scoring_breakdown=breakdown
        )
    with pytest.raises(LedgerNotFound):
        await ledger.get_scoring_breakdown(run)
    assert (await ledger.get_run(run))["phase"] == "SCORING"
