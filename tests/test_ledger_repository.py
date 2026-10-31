import asyncio

import pytest

from app.schemas import AdjudicationResult, ClaimSet, ContextPacket, Metric, Policy, Verdict
from ledger.db import connection, initialize, transaction
from ledger.errors import (
    LedgerBusy,
    LedgerConflict,
    LedgerNotFound,
    LedgerReferenceError,
    LedgerStateError,
    LedgerStorageError,
)
from ledger.repo import LedgerRepository
from leo.scoring import compute
from tests.ledger_support import advance, evidence, finding


async def test_run_reopens_with_owner_policy_and_bound_parameters(ledger):
    name = "Company'); DROP TABLE runs; --"
    policy = Policy(max_check_usd=1000, stage="seed")
    run_id = await ledger.create_run("ayush", name, "example.test", policy, "deck.pdf")
    reopened = LedgerRepository(ledger.path)
    record = await reopened.get_run(run_id, user="ayush")
    assert record["company_name"] == name and record["policy"] == policy
    assert record["phase"] == "QUEUED" and record["status"] == "queued"
    assert record["deck_path"] == "deck.pdf"
    with pytest.raises(LedgerNotFound):
        await reopened.get_run(run_id, user="another-user")


async def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(LedgerStorageError):
        await LedgerRepository(path).get_run("missing")
    assert not path.exists()


async def test_every_connection_enforces_constraints_and_initialization_is_additive(ledger, run):
    async with connection(ledger.path) as db:
        assert (await db.execute_fetchall("PRAGMA foreign_keys"))[0][0] == 1
        assert (await db.execute_fetchall("PRAGMA recursive_triggers"))[0][0] == 1
        assert (await db.execute_fetchall("PRAGMA journal_mode"))[0][0] == "wal"
        await db.execute("PRAGMA user_version=0")
    await initialize(ledger.path)
    assert (await ledger.get_run(run))["user"] == "ayush"
    async with connection(ledger.path) as db:
        assert (await db.execute_fetchall("PRAGMA user_version"))[0][0] == 1


async def test_future_schema_version_is_not_downgraded(ledger):
    async with connection(ledger.path) as db:
        await db.execute("PRAGMA user_version=99")
    with pytest.raises(LedgerStorageError, match="newer"):
        await initialize(ledger.path)
    async with connection(ledger.path) as db:
        assert (await db.execute_fetchall("PRAGMA user_version"))[0][0] == 99


async def test_claims_context_and_empty_claim_set_roundtrip(ledger, run):
    claims = await ledger.get_claims(run)
    assert claims.claims[0].value == 10
    packet = ContextPacket(
        agent="scale_team",
        claims=claims.claims[:1],
        policy=Policy(max_check_usd=1000),
        dossier_json='{"headcount":10}',
    )
    await ledger.save_context_packets(run, (packet,))
    assert await ledger.get_context_packet(run, "scale_team") == packet
    with pytest.raises(LedgerConflict):
        await ledger.save_context_packets(run, (packet,))
    other = await ledger.create_run("ayush", "Empty", None, Policy(max_check_usd=0), "empty.pdf")
    expected = ClaimSet(company_name="Empty", is_stub=True)
    await ledger.add_claims(other, expected)
    assert await ledger.get_claims(other) == expected


async def test_missing_run_and_missing_context(ledger, run):
    with pytest.raises(LedgerNotFound):
        await ledger.get_claims("absent")
    with pytest.raises(LedgerNotFound):
        await ledger.get_context_packet(run, "identity")
    with pytest.raises(LedgerNotFound):
        await ledger.get_decision(run)


async def test_evidence_metrics_and_finding_roundtrip(ledger, run):
    item = evidence(run)
    await ledger.add_evidence(run, (item,))
    metric = Metric(
        run_id=run,
        agent="scale_team",
        name="difference",
        value=0,
        unit="ratio",
        formula="(10-10)/10",
        evidence_ids=("E1",),
    )
    await ledger.add_metrics(run, (metric,))
    await ledger.add_findings(run, (finding(),))
    assert await ledger.get_evidence(run) == (item,)
    assert await ledger.get_metrics(run) == (metric,)
    stored = (await ledger.get_all_findings(run))[0]
    assert stored.status == "VERIFIED" and stored.severity == stored.severity_final == 4


async def test_evidence_is_required_for_verified_findings(ledger, run):
    await ledger.add_findings(run, (finding(evidence_ids=()),))
    assert (await ledger.get_all_findings(run))[0].status == "UNVERIFIABLE"


async def test_duplicate_evidence_batch_rolls_back_with_its_event(ledger, run):
    before = await ledger.get_events(run)
    with pytest.raises(LedgerConflict):
        await ledger.add_evidence(run, (evidence(run), evidence(run)))
    assert await ledger.get_evidence(run) == ()
    assert await ledger.get_events(run) == before


async def test_bad_reference_in_second_finding_rolls_back_first(ledger, run):
    await ledger.add_evidence(run, (evidence(run),))
    before = await ledger.get_events(run)
    with pytest.raises(LedgerReferenceError):
        await ledger.add_findings(run, (finding(), finding("F2", claim_id="another-claim")))
    assert await ledger.get_all_findings(run) == ()
    assert await ledger.get_events(run) == before


async def test_cross_run_evidence_and_metrics_rejected(ledger, run):
    other = await ledger.create_run("other", "Other", None, Policy(max_check_usd=0), "other.pdf")
    await ledger.add_evidence(other, (evidence(other, "other-evidence"),))
    with pytest.raises(LedgerReferenceError):
        await ledger.add_evidence(run, (evidence(other, "E2"),))
    with pytest.raises(LedgerReferenceError):
        await ledger.add_findings(run, (finding(evidence_ids=("other-evidence",)),))
    with pytest.raises(LedgerReferenceError):
        await ledger.add_metrics(
            run,
            (Metric(run_id=other, agent="scale_team", name="x", value=None, unit="", formula=""),),
        )


@pytest.mark.parametrize("invalid_json", ["{broken", "NaN", "Infinity", '{"value":1e999}'])
async def test_invalid_json_is_rejected_without_writing(ledger, run, invalid_json):
    with pytest.raises(ValueError, match="JSON"):
        await ledger.add_evidence(
            run, (evidence(run).model_copy(update={"value_json": invalid_json}),)
        )
    assert await ledger.get_evidence(run) == ()


async def test_progress_event_failure_rolls_back_record(ledger, run, monkeypatch):
    async def fail(*args):
        raise RuntimeError("simulated event failure")

    monkeypatch.setattr("ledger.repo.event", fail)
    with pytest.raises(RuntimeError):
        await ledger.add_evidence(run, (evidence(run),))
    assert await ledger.get_evidence(run) == ()


async def test_cancellation_rolls_back_transaction(ledger, run):
    with pytest.raises(asyncio.CancelledError):
        async with connection(ledger.path) as db, transaction(db):
            await db.execute(
                "INSERT INTO events(run_id,kind,payload_json) VALUES (?,'cancelled','{}')", (run,)
            )
            raise asyncio.CancelledError()
    assert all(e.kind != "cancelled" for e in await ledger.get_events(run))


async def test_busy_timeout_is_bounded_and_sanitized(ledger, run):
    impatient = LedgerRepository(ledger.path, timeout=0.02)
    async with connection(ledger.path) as db, transaction(db):
        with pytest.raises(LedgerBusy) as error:
            await impatient.add_event(run, "blocked", {})
        assert str(ledger.path) not in str(error.value)
    await impatient.add_event(run, "recovered", {})


async def test_events_are_ordered_paginated_and_run_scoped(ledger, run):
    other = await ledger.create_run("other", "Other", None, Policy(max_check_usd=0), "x.pdf")
    ids = await asyncio.gather(*(ledger.add_event(run, "sample", {"index": i}) for i in range(12)))
    await ledger.add_event(other, "other-run", {})
    assert len(set(ids)) == 12
    first = await ledger.get_events(run, limit=3)
    second = await ledger.get_events(run, after=first[-1].event_id)
    assert all(a.event_id < b.event_id for a, b in zip(first + second, (first + second)[1:]))
    assert all(e.run_id == run for e in first + second)
    assert not set(e.event_id for e in first) & set(e.event_id for e in second)


async def test_append_only_and_replace_cannot_erase_finding(ledger, run):
    await ledger.add_findings(run, (finding(evidence_ids=()),))
    for sql in [
        "UPDATE findings SET severity=1 WHERE run_id=?",
        "DELETE FROM findings WHERE run_id=?",
        "INSERT OR REPLACE INTO findings(run_id,finding_id,agent,status,severity) VALUES (?,'F1','identity','VERIFIED',1)",
    ]:
        with pytest.raises(LedgerConflict):
            async with connection(ledger.path) as db:
                await db.execute(sql, (run,))
    assert (await ledger.get_all_findings(run))[0].severity == 4


async def test_completed_decision_is_atomic_and_preserves_stub_provenance(ledger, run):
    await ledger.add_findings(run, (finding(evidence_ids=(), is_stub=True),))
    await advance(ledger, run, "REPLY")
    verdict = Verdict(agent="scale_team", score=50, confidence=0.3, stance="stub", is_stub=True)
    await ledger.add_verdict(run, verdict)
    assert await ledger.get_verdicts(run) == (verdict,)
    await advance(ledger, run, "SCORING")
    decision = compute(
        await ledger.get_claims(run),
        await ledger.get_all_findings(run),
        await ledger.get_verdicts(run),
        Policy(max_check_usd=1000),
    )
    adjudication = AdjudicationResult(narrative="No live assessment", is_stub=True)
    await ledger.save_decision(run, decision, adjudication)
    assert (await LedgerRepository(ledger.path).get_decision(run)).is_stub
    assert await ledger.get_adjudication(run) == adjudication
    assert (await ledger.get_run(run))["status"] == "done"
    assert (await ledger.get_events(run))[-1].kind == "done"
    with pytest.raises(LedgerStateError):
        await ledger.save_decision(run, decision, adjudication)
    with pytest.raises(LedgerStateError):
        await ledger.add_event(run, "late", {})
