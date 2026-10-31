"""The real lead analyst driven through the real orchestrator (the other orchestrator tests mock it)."""
from unittest.mock import AsyncMock

import pytest

from app.schemas import AdjudicationResult, FindingRevision, Policy
from app.settings import Settings
from leo.adjudicator import adjudicate
from leo.orchestrator import run_pipeline
from leo.runtime import synthetic_dependencies
from tests.pipeline_support import dependencies, pdf_bytes


async def launch(ledger, tmp_path, deps, **settings):
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(pdf_bytes())
    run = await ledger.create_run(
        "tester", "Synthetic", "example.com", Policy(max_check_usd=100000), str(path))
    config = Settings(_env_file=None, pipeline_timeout=20, phase_timeout=5, **{"mode": "live", **settings})
    await run_pipeline(run, repo=ledger, settings=config, dependencies=deps)
    return run, config


async def test_the_real_adjudicator_gives_a_real_not_stub_decision(ledger, tmp_path):
    deps = dependencies()
    deps.adjudicate = adjudicate              # the default wiring, not a mock
    run, _ = await launch(ledger, tmp_path, deps)
    assert (await ledger.get_run(run))["status"] == "done"
    decision, adjudication = await ledger.get_decision(run), await ledger.get_adjudication(run)
    assert decision.is_stub is False and adjudication.is_stub is False
    assert not adjudication.narrative.startswith("STUB")
    assert "EBITDA and profit" in adjudication.narrative       # the code narrative (no model rulings here)
    assert (decision.verdict, decision.gates, decision.check_size_usd) == ("PASS", ("arr_ratio_over_3",), 0)
    assert deps.llm.calls == 5                                  # four challenge calls plus the adjudicator


async def test_the_orchestrator_hands_the_adjudicator_its_ledger_model_client_and_model(ledger, tmp_path):
    deps = dependencies()
    run, _ = await launch(ledger, tmp_path, deps, model_pro="pro-model")
    deps.adjudicate.assert_awaited_once_with(run, repo=ledger, llm=deps.llm, model="pro-model")


async def test_an_unset_pro_model_is_passed_as_none_so_the_adjudicator_picks_a_default(ledger, tmp_path):
    deps = dependencies()
    await launch(ledger, tmp_path, deps)
    assert deps.adjudicate.await_args.kwargs["model"] is None


async def test_revisions_the_adjudicator_already_persisted_do_not_crash_the_run(ledger, tmp_path):
    deps = dependencies()

    async def persisting(run_id, *, repo, llm, model):
        finding = next(f for f in await repo.get_all_findings(run_id)
                       if f.agent == "funding" and f.claim_id == "C1")
        revision = FindingRevision(revision_id="leo:test", finding_id=finding.finding_id, author="leo",
                                   severity=4, reason="Ruled", evidence_ids=finding.evidence_ids)
        await repo.add_revision(run_id, revision)     # what the real adjudicator does itself
        return AdjudicationResult(narrative="Persisted ruling.", revisions=(revision,))

    deps.adjudicate = persisting
    run, _ = await launch(ledger, tmp_path, deps)
    assert (await ledger.get_run(run))["status"] == "done"      # used to be LedgerConflict
    assert [r.revision_id for r in await ledger.get_revisions(run)] == ["leo:test"]
    assert (await ledger.get_adjudication(run)).revisions[0].revision_id == "leo:test"


async def test_revisions_returned_without_being_persisted_are_still_applied(ledger, tmp_path):
    deps = dependencies()

    async def returning(run_id, **_):
        finding = next(f for f in await ledger.get_all_findings(run_id)
                       if f.agent == "funding" and f.claim_id == "C1")
        return AdjudicationResult(narrative="Returned ruling.", revisions=(FindingRevision(
            revision_id="lead:R1", finding_id=finding.finding_id, author="leo", severity=4,
            reason="Ruled", evidence_ids=finding.evidence_ids),))

    deps.adjudicate = returning
    run, _ = await launch(ledger, tmp_path, deps)
    assert any(r.revision_id == "lead:R1" for r in await ledger.get_revisions(run))


async def test_in_stub_mode_the_run_still_completes_and_stays_labelled_a_stub(ledger, tmp_path):
    run, _ = await launch(ledger, tmp_path, synthetic_dependencies(), mode="stub")
    assert (await ledger.get_run(run))["status"] == "done"
    assert (await ledger.get_decision(run)).is_stub is True       # the claims and agents are synthetic
    assert (await ledger.get_adjudication(run)).narrative          # and the dead model did not stop it


@pytest.mark.parametrize("failure", [RuntimeError("model down"), TimeoutError()])
async def test_a_failing_model_does_not_fail_the_run_at_adjudication(ledger, tmp_path, failure):
    deps = dependencies()

    async def flaky(prompt, schema, model):
        if schema.__name__ == "AdjudicationDraft":
            raise failure
        return schema.model_validate({})

    deps.llm = AsyncMock()
    deps.llm.generate = flaky
    deps.adjudicate = adjudicate
    run, _ = await launch(ledger, tmp_path, deps)
    assert (await ledger.get_run(run))["status"] == "done"
    assert (await ledger.get_adjudication(run)).is_stub is False
