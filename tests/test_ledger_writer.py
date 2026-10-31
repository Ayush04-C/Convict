import asyncio
from dataclasses import FrozenInstanceError

import pytest

from app.schemas import AdjudicationResult, Challenge, FindingRevision, Metric, Reply, Verdict
from ledger.errors import (
    LedgerConflict,
    LedgerPermissionError,
    LedgerReferenceError,
    LedgerStateError,
)
from leo.scoring import compute
from app.schemas import Policy
from sandbox.ledger_writer import LedgerWriter
from tests.ledger_support import advance, evidence, finding


@pytest.fixture
async def prepared(ledger, run):
    await advance(ledger, run, "ROUND1")
    writer = LedgerWriter(run, "scale_team", ledger)
    await writer.add_evidence((evidence(run),))
    await writer.add_findings((finding(),))
    return writer


def challenge(challenge_id="H1", **updates):
    fields = dict(
        challenge_id=challenge_id,
        from_agent="funding",
        to_agent="scale_team",
        finding_id="F1",
        type="CONTEXT",
        argument="Consider this evidence",
        evidence_ids=("E1",),
    )
    fields.update(updates)
    return Challenge(**fields)


async def test_writer_is_frozen_and_has_no_destructive_methods(ledger, run):
    writer = LedgerWriter(run, "funding", ledger)
    with pytest.raises(FrozenInstanceError):
        writer.agent = "identity"
    assert not hasattr(writer, "update") and not hasattr(writer, "delete")
    with pytest.raises(LedgerPermissionError):
        LedgerWriter(run, "leo", ledger)


async def test_round1_barrier_and_writer_ownership(ledger, run, prepared):
    with pytest.raises(LedgerPermissionError):
        await prepared.get_all_findings()
    with pytest.raises(LedgerPermissionError):
        await prepared.add_evidence((evidence(run, "other", agent="funding"),))
    with pytest.raises(LedgerPermissionError):
        await prepared.add_findings((finding("F2", agent="funding"),))
    with pytest.raises(LedgerPermissionError):
        await prepared.add_verdict(
            Verdict(agent="scale_team", score=50, confidence=0.3, stance="early")
        )
    await advance(ledger, run, "CHALLENGE")
    assert len(await prepared.get_all_findings()) == 1
    with pytest.raises(LedgerPermissionError):
        await prepared.add_findings((finding("F2"),))


async def test_finished_agent_cannot_write_late_output(ledger, run, prepared):
    await ledger.set_agent_status(run, "scale_team", "ROUND1", "RUNNING")
    await ledger.set_agent_status(run, "scale_team", "ROUND1", "FAILED", error="TIMEOUT")
    with pytest.raises(LedgerPermissionError):
        await prepared.add_findings((finding("late"),))


async def test_other_agents_evidence_is_private_until_barrier(ledger, run, prepared):
    await ledger.add_evidence(run, (evidence(run, "funding-only", "funding"),))
    assert {e.evidence_id for e in await prepared.get_evidence()} == {"E1"}
    with pytest.raises(LedgerPermissionError):
        await prepared.add_findings((finding("guessed", evidence_ids=("funding-only",)),))
    await ledger.add_evidence(run, (evidence(run, "shared-dossier", "leo"),))
    await prepared.add_findings((finding("shared", evidence_ids=("shared-dossier",)),))
    await advance(ledger, run, "CHALLENGE")
    assert {e.evidence_id for e in await prepared.get_evidence()} == {
        "E1",
        "funding-only",
        "shared-dossier",
    }


async def test_challenges_reply_revision_and_verdict_are_persisted(ledger, run, prepared):
    await advance(ledger, run, "CHALLENGE")
    challenger = LedgerWriter(run, "funding", ledger)
    await challenger.add_challenges((challenge(),))
    with pytest.raises(LedgerPermissionError):
        await prepared.get_challenges_for()
    await advance(ledger, run, "REPLY")
    assert await prepared.get_challenges_for() == (challenge(),)
    assert await challenger.get_challenges_for() == ()
    reply = Reply(
        reply_id="P1",
        challenge_id="H1",
        agent="scale_team",
        action="REVISE",
        text="Agreed",
        new_severity=2,
    )
    await prepared.add_reply(reply)
    assert await ledger.get_replies(run) == (reply,)
    revised = (await ledger.get_all_findings(run))[0]
    assert revised.severity == 4 and revised.severity_final == 2
    assert (await ledger.get_revisions(run))[0].revision_id == "reply:P1"
    with pytest.raises(LedgerConflict):
        await prepared.add_reply(reply.model_copy(update={"reply_id": "P2"}))
    verdict = Verdict(agent="scale_team", score=80, confidence=0.8, stance="conditions")
    await prepared.add_verdict(verdict)
    assert await ledger.get_verdicts(run) == (verdict,)
    with pytest.raises(LedgerConflict):
        await prepared.add_verdict(verdict)


@pytest.mark.parametrize(
    "updates,error",
    [
        ({"from_agent": "traction"}, LedgerPermissionError),
        ({"to_agent": "funding"}, LedgerPermissionError),
        ({"finding_id": "absent"}, LedgerReferenceError),
        ({"to_agent": "identity"}, LedgerReferenceError),
        ({"evidence_ids": ("absent",)}, LedgerReferenceError),
    ],
)
async def test_invalid_challenges_are_rejected(ledger, run, prepared, updates, error):
    await advance(ledger, run, "CHALLENGE")
    with pytest.raises(error):
        await LedgerWriter(run, "funding", ledger).add_challenges((challenge(**updates),))
    assert await ledger.get_challenges_for(run, "scale_team") == ()


async def test_challenge_limit_cannot_be_bypassed_by_multiple_batches(ledger, run, prepared):
    await advance(ledger, run, "CHALLENGE")
    writer = LedgerWriter(run, "funding", ledger)
    await writer.add_challenges((challenge("H1"),))
    await writer.add_challenges((challenge("H2"),))
    with pytest.raises(LedgerConflict):
        await writer.add_challenges((challenge("H3"),))
    assert len(await ledger.get_challenges_for(run, "scale_team")) == 2


async def test_challenge_limit_is_atomic_under_concurrent_writes(ledger, run, prepared):
    await advance(ledger, run, "CHALLENGE")
    writer = LedgerWriter(run, "funding", ledger)
    results = await asyncio.gather(
        *(writer.add_challenges((challenge("H" + str(i)),)) for i in range(4)),
        return_exceptions=True,
    )
    assert sum(r is None for r in results) == 2
    assert sum(isinstance(r, LedgerConflict) for r in results) == 2


async def test_replies_cannot_impersonate_target_or_set_inconsistent_severity(
    ledger, run, prepared
):
    await advance(ledger, run, "CHALLENGE")
    await LedgerWriter(run, "funding", ledger).add_challenges((challenge(),))
    await advance(ledger, run, "REPLY")
    with pytest.raises(LedgerPermissionError):
        await LedgerWriter(run, "funding", ledger).add_reply(
            Reply(reply_id="P1", challenge_id="H1", agent="funding", action="REBUT", text="x")
        )
    for action, severity in [("REVISE", None), ("REBUT", 2), ("CONCEDE", 2)]:
        with pytest.raises(LedgerStateError):
            await prepared.add_reply(
                Reply(
                    reply_id="P1",
                    challenge_id="H1",
                    agent="scale_team",
                    action=action,
                    text="x",
                    new_severity=severity,
                )
            )
    assert await ledger.get_replies(run) == ()


async def test_revisions_are_owned_and_adjudication_must_precede_scoring(ledger, run, prepared):
    await advance(ledger, run, "REPLY")
    revision = FindingRevision(
        revision_id="R1",
        finding_id="F1",
        author="scale_team",
        severity=3,
        reason="Clarified",
        evidence_ids=("E1",),
    )
    with pytest.raises(LedgerPermissionError):
        await LedgerWriter(run, "funding", ledger).add_revision(revision)
    await prepared.add_revision(revision)
    await advance(ledger, run, "ADJUDICATE")
    final = revision.model_copy(update={"revision_id": "R2", "author": "leo", "severity": 1})
    await ledger.add_revision(run, final)
    assert (await ledger.get_all_findings(run))[0].severity_final == 1
    await advance(ledger, run, "SCORING")
    decision = compute(
        await ledger.get_claims(run),
        await ledger.get_all_findings(run),
        (),
        Policy(max_check_usd=1000),
    )
    missing = final.model_copy(update={"revision_id": "missing"})
    with pytest.raises(LedgerReferenceError):
        await ledger.save_decision(
            run, decision, AdjudicationResult(narrative="x", revisions=(missing,))
        )
    assert (await ledger.get_run(run))["phase"] == "SCORING"
    await ledger.save_decision(
        run, decision, AdjudicationResult(narrative="Resolved", revisions=(final,))
    )
    assert (await ledger.get_adjudication(run)).revisions == (final,)


async def test_four_agents_can_write_without_cross_run_leaks(ledger, run):
    await advance(ledger, run, "ROUND1")

    async def write(agent):
        writer = LedgerWriter(run, agent, ledger)
        evidence_id = "e-" + agent
        await writer.add_evidence((evidence(run, evidence_id, agent),))
        await writer.add_metrics(
            (
                Metric(
                    run_id=run,
                    agent=agent,
                    name="fixture",
                    value=1,
                    unit="count",
                    formula="1",
                    evidence_ids=(evidence_id,),
                ),
            )
        )
        await writer.add_findings((finding("f-" + agent, agent, evidence_ids=(evidence_id,)),))

    await asyncio.gather(*(write(a) for a in ("identity", "scale_team", "funding", "traction")))
    assert len(await ledger.get_evidence(run)) == 4
    assert len(await ledger.get_metrics(run)) == 4
    assert len(await ledger.get_all_findings(run)) == 4
