"""Report projections preserve stored scores and leave the ledger unchanged."""

import json
import pytest

from app.schemas import AdjudicationResult, Decision, Policy, Resolution
from app.settings import Settings
from ledger.errors import LedgerNotFound
from leo.orchestrator import run_pipeline
from report.builder import ReportNotReady, build_report
from report.markdown import render_markdown
from tests.pipeline_support import dependencies, pdf_bytes
from tests.ledger_support import advance


@pytest.fixture
async def completed(ledger, tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(pdf_bytes())
    run = await ledger.create_run(
        "tester", "Synthetic", "example.com", Policy(max_check_usd=100000), str(pdf)
    )
    await run_pipeline(
        run,
        repo=ledger,
        settings=Settings(_env_file=None, mode="live"),
        dependencies=dependencies(),
    )
    assert (await ledger.get_run(run))["status"] == "done"
    return run


async def test_report_preserves_saved_decision_and_does_not_write(ledger, completed):
    before = await ledger.get_decision(completed)
    events = await ledger.get_events(completed)
    report = await build_report(completed, ledger)
    assert (
        report["decision"] == before.model_dump(mode="json")
        and report["verdict_label"] == "DO NOT INVEST"
    )
    assert report["confirmed_gates"][0]["name"] == "arr_ratio_over_3"
    assert report["confirmed_gates"][0]["proposals"][0]["evidence_ids"]
    assert await ledger.get_events(completed) == events
    assert await ledger.get_decision(completed) == before
    assert "deck_path" not in report and "user" not in report


async def test_findings_and_source_dates_are_grounded(ledger, completed):
    report = await build_report(completed, ledger)
    assert len(report["claims"]) == 3 and len(report["verdicts"]) == 4
    arr = next(c for c in report["claims"] if c["id"] == "C1")
    assert any(f["status"] == "CONTRADICTED" for f in arr["findings"])
    source = next(
        e for e in report["evidence"] if e["field_path"] == "revenue.estimated.upper_bound_usd"
    )
    assert source["value"] == 50_000_000 and source["provider_fetched_at"] == "2026-10-31T00:00:00Z"
    assert source["recorded_fetched_at"]
    assert next(e for e in report["evidence"] if e["is_full_dossier_reference"])["value"] is None


@pytest.mark.parametrize(
    "verdict,label",
    [
        ("PASS", "DO NOT INVEST"),
        ("DIG_DEEPER", "MORE DILIGENCE REQUIRED"),
        ("INVEST_WITH_CONDITIONS", "INVEST WITH CONDITIONS"),
        ("INVEST", "INVEST"),
    ],
)
async def test_verdict_labels_never_recompute_scores(ledger, verdict, label):
    run = await ledger.create_run(
        "tester", "Synthetic", None, Policy(max_check_usd=100), "unused.pdf"
    )
    await advance(ledger, run, "SCORING")
    saved = Decision(
        total_score=73.125,
        credibility=51.25,
        confidence=0.42,
        verdict=verdict,
        check_size_usd=12.34,
    )
    await ledger.save_decision(run, saved, AdjudicationResult(narrative="Saved narrative."))
    report = await build_report(run, ledger)
    assert report["verdict_label"] == label and report["decision"] == saved.model_dump(mode="json")


async def test_pending_and_unknown_runs_are_not_fabricated(ledger):
    run = await ledger.create_run("tester", "Synthetic", None, Policy(max_check_usd=0), "unused")
    with pytest.raises(ReportNotReady):
        await build_report(run, ledger)
    with pytest.raises(LedgerNotFound):
        await build_report("missing", ledger)


@pytest.mark.parametrize("status", ["failed", "cannot_assess"])
async def test_incomplete_runs_have_no_invented_decision(ledger, status):
    run = await ledger.create_run("tester", "Synthetic", None, Policy(max_check_usd=0), "unused")
    if status == "failed":
        await ledger.fail_run(run, "TEST_FAILURE")
    else:
        await advance(ledger, run, "RESOLVE")
        await ledger.save_resolution(
            run, Resolution(status="cannot_assess", reason="Ambiguous candidates.")
        )
    report = await build_report(run, ledger)
    assert report["status"] == status and report["decision"] is None
    assert any("No completed" in item for item in report["limitations"])


async def test_stub_adjudication_is_explicitly_labelled(ledger):
    run = await ledger.create_run("tester", "Synthetic", None, Policy(max_check_usd=0), "unused")
    await advance(ledger, run, "SCORING")
    await ledger.save_decision(
        run,
        Decision(
            total_score=0, credibility=None, confidence=0, verdict="DIG_DEEPER", check_size_usd=0
        ),
        AdjudicationResult(narrative="STUB lead", is_stub=True),
    )
    report = await build_report(run, ledger)
    assert report["is_stub"] and "DEMO / INCOMPLETE" in render_markdown(report)
    assert any("Lead adjudication" in s for s in report["limitations"])


async def test_markdown_escapes_untrusted_text_and_controls(ledger, completed):
    report = await build_report(completed, ledger)
    report["company"]["resolved_name"] = '<script>alert("x")</script>\x1b[31m | fake'
    rendered = render_markdown(report)
    assert "<script>" not in rendered and "\x1b" not in rendered
    assert "&lt;script&gt;" in rendered and "\\|" in rendered
    assert "DO NOT INVEST" in rendered and "more than three times" in rendered


async def test_report_is_json_serializable(ledger, completed):
    report = await build_report(completed, ledger)
    assert json.loads(json.dumps(report, allow_nan=False)) == report


async def test_readable_markdown_preserves_precision_and_links_sources(ledger, completed):
    from copy import deepcopy

    report = await build_report(completed, ledger)
    before = deepcopy(report)
    rendered = render_markdown(report)
    narrative, audit = rendered.split("<details>", 1)
    assert report == before
    assert "fund's own rules" in rendered
    assert "&#x27;" not in rendered and "&\\#x27;" not in rendered
    assert "arr_ratio_over_3" not in narrative and "arr\\_ratio" not in narrative
    assert "scale\\_team" not in narrative and "CONFIRMED" not in narrative
    assert "$50,000,000" in narrative and "Not supplied by provider" in narrative
    assert "Team and workplace" in narrative and "more than three times" in narrative
    assert "funding:E" in audit
    assert "0.858888888888" not in narrative
    for item in report["evidence"]:
        index = report["evidence"].index(item) + 1
        assert f'<a id="source-{index}"></a>' in rendered
    assert "[Source " in narrative and "](#source-" in narrative


async def test_display_rounding_never_changes_saved_decision(ledger, completed):
    report = await build_report(completed, ledger)
    report["decision"]["total_score"] = 49.05151515151515
    report["decision"]["confidence"] = 0.8588888888888888
    rendered = render_markdown(report)
    assert "| Overall score | 49.1 / 100 |" in rendered
    assert "| Assessment confidence | 85.9% |" in rendered
    assert report["decision"]["total_score"] == 49.05151515151515
    assert report["decision"]["confidence"] == 0.8588888888888888


async def test_prose_relabels_identifiers_without_rewriting_ordinary_words(ledger, completed):
    report = await build_report(completed, ledger)
    report["claims"][0]["findings"][0]["note"] = (
        "No series_h round appears in Crustdata's funding history; see funding:E1."
    )
    rendered = render_markdown(report)
    assert "No Series H round appears in Crustdata's funding history; see Source " in rendered
    assert "Crustdata's Funding and financials history" not in rendered
