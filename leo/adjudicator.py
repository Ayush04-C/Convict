"""Daksh: the lead analyst. Rules on open disputes, writes the narrative, confirms hard gates.

Three jobs, in order of how much a model is trusted with each:

  confirm_gates   NO model. A pure function. A gate an analyst proposed (the metric "gate:<name>")
                  is confirmed only when its evidence backs a CONTRADICTED finding whose effective
                  severity is still at least 4. The orchestrator passes the result to scoring as
                  `confirmed_gates`; without it a fabricated headline claim is not capped.
  rulings         MODEL, then validated by code. A dispute is a challenge that was rebutted or never
                  answered. The model may propose a severity change; code bounds it to two steps,
                  allows one revision per finding, and refuses to LOWER a finding that backs a gate.
                  Accepted rulings are written to the ledger as revisions authored by "leo".
  narrative       MODEL, with a code-built fallback. Grounded in the findings; never states the
                  investment decision, which is computed afterwards from the scoring formula.

Everything degrades safely: no model or a failed call means no rulings and a deterministic
narrative. A call without a ledger returns the visibly stubbed result the offline demos expect.

Known limit: agent verdict scores are written before adjudication and are append-only, so a ruling
changes a finding's effective severity (credibility inputs, the severity-5 cap) but not the score the
agent already gave.
"""
from dataclasses import dataclass

from pydantic import BaseModel

from agents.prompts import ADJUDICATE_PROMPT, SEVERITY_SCALE
from agents.protocol import LlmClient
from app.schemas import AdjudicationResult, Challenge, Claim, Finding, FindingRevision, Metric, Reply
from ledger.errors import LedgerStateError
from ledger.repo import LedgerRepository
from leo.context import AGENTS

KNOWN_GATES = frozenset({"arr_ratio_over_3", "funding_round_absent", "entity_not_active",
                         "founder_not_found"})
GATE_MIN_SEVERITY = 4           # a gate stays confirmed only while its backing finding is this severe
MAX_DISPUTES = 6
MAX_SEVERITY_SWING = 2
MAX_REASON_CHARS = 300
MAX_NARRATIVE_CHARS = 900
PROMPT_NOTE_CHARS = 300
PROMPT_EVIDENCE_CHARS = 160
PROMPT_FINDING_LINES = 30
PROMPT_UNVERIFIED_LINES = 12
REVISION_PREFIX = "leo:"


class RulingDraft(BaseModel):
    challenge_id: str
    new_severity: int | None = None
    reason: str = ""


class AdjudicationDraft(BaseModel):
    rulings: list[RulingDraft] = []
    narrative: str = ""


@dataclass(frozen=True)
class Dispute:
    challenge: Challenge
    finding: Finding
    reply: Reply | None


def _clean(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _severity(finding: Finding) -> int:
    return finding.severity_final or finding.severity


def gate_backers(metrics: tuple[Metric, ...] | list[Metric],
                 findings: tuple[Finding, ...] | list[Finding]) -> dict[str, tuple[str, ...]]:
    """gate name -> ids of the CONTRADICTED findings whose evidence backs the proposal."""
    backers: dict[str, list[str]] = {}
    for metric in metrics:
        gate = metric.name.removeprefix("gate:")
        if not metric.name.startswith("gate:") or gate not in KNOWN_GATES or not metric.evidence_ids:
            continue
        cited = set(metric.evidence_ids)
        ids = [f.finding_id for f in findings if f.agent == metric.agent and f.status == "CONTRADICTED"
               and cited & set(f.evidence_ids)]
        backers.setdefault(gate, []).extend(ids)
    return {gate: tuple(dict.fromkeys(ids)) for gate, ids in backers.items() if ids}


def confirm_gates(metrics: tuple[Metric, ...] | list[Metric],
                  findings: tuple[Finding, ...] | list[Finding]) -> tuple[str, ...]:
    """Gates to pass to scoring as `confirmed_gates`; sorted, so the result is reproducible."""
    by_id = {f.finding_id: f for f in findings}
    return tuple(sorted(
        gate for gate, ids in gate_backers(metrics, findings).items()
        if any(_severity(by_id[i]) >= GATE_MIN_SEVERITY for i in ids)))


def open_disputes(challenges: list[Challenge], replies: list[Reply],
                  findings: list[Finding]) -> list[Dispute]:
    """Challenges that were rebutted or never answered, most severe finding first, capped."""
    by_challenge = {r.challenge_id: r for r in replies}
    by_finding = {f.finding_id: f for f in findings}
    disputes = []
    for challenge in challenges:
        reply, finding = by_challenge.get(challenge.challenge_id), by_finding.get(challenge.finding_id)
        if finding is not None and (reply is None or reply.action == "REBUT"):
            disputes.append(Dispute(challenge, finding, reply))
    disputes.sort(key=lambda d: (-_severity(d.finding), d.challenge.challenge_id))
    return disputes[:MAX_DISPUTES]


def valid_revisions(draft: AdjudicationDraft, disputes: list[Dispute],
                    protected: set[str]) -> tuple[FindingRevision, ...]:
    by_challenge = {d.challenge.challenge_id: d for d in disputes}
    accepted: list[FindingRevision] = []
    revised: set[str] = set()
    for ruling in draft.rulings:
        dispute = by_challenge.get(ruling.challenge_id)
        proposed = ruling.new_severity
        if dispute is None or proposed is None or not 1 <= proposed <= 5:
            continue
        finding = dispute.finding
        current = _severity(finding)
        proposed = max(current - MAX_SEVERITY_SWING, min(current + MAX_SEVERITY_SWING, proposed))
        if proposed == current or finding.finding_id in revised:
            continue
        if finding.finding_id in protected and proposed < current:
            continue   # a finding that backs a hard gate can be raised, never lowered, by a ruling
        revised.add(finding.finding_id)
        reason = _clean(ruling.reason, MAX_REASON_CHARS) or "Ruled by the lead analyst."
        accepted.append(FindingRevision(
            revision_id=f"{REVISION_PREFIX}{dispute.challenge.challenge_id}", finding_id=finding.finding_id,
            author="leo", severity=proposed, reason=f"Lead analyst ruling: {reason}",
            evidence_ids=dispute.challenge.evidence_ids))
    return tuple(accepted)


def _unverified_claims(claims: list[Claim], findings: list[Finding]) -> list[str]:
    checked = {f.claim_id for f in findings if f.claim_id and f.status != "UNVERIFIABLE"}
    return [f"{c.id}: {_clean(c.text, 100)}" for c in claims if c.testable and c.id not in checked]


def fallback_narrative(findings: list[Finding], unverified: list[str], gates: tuple[str, ...]) -> str:
    """Deterministic summary used when no model is available."""
    problems = sorted((f for f in findings if f.status == "CONTRADICTED"), key=lambda f: -_severity(f))
    verified = sum(1 for f in findings if f.status == "VERIFIED")
    partial = sum(1 for f in findings if f.status == "PARTIAL")
    parts = []
    if problems:
        shown = "; ".join(f"{_clean(f.note, 140)} ({f.finding_id})" for f in problems[:3])
        parts.append(f"Confirmed contradictions: {shown}.")
    else:
        parts.append("No claim was contradicted by the available data.")
    parts.append(f"{verified} checks were verified and {partial} were only partly supported or flagged.")
    if gates:
        parts.append(f"Gate proposals with supporting evidence (scoring confirms them separately): "
                     f"{', '.join(gates)}.")
    if unverified:
        parts.append(f"{len(unverified)} claims could not be verified.")
    parts.append("Revenue is a Crustdata estimate, and EBITDA and profit are not available from this "
                 "data, so they remain unverified.")
    return _clean(" ".join(parts), MAX_NARRATIVE_CHARS)


def _format_finding(finding: Finding) -> str:
    return (f"- {finding.finding_id} | claim {finding.claim_id or '-'} | {finding.status} severity "
            f"{_severity(finding)} | {_clean(finding.note, PROMPT_NOTE_CHARS)}")


def _build_prompt(findings, verdicts, disputes, evidence, gates, protected, unverified) -> str:
    known = {e.evidence_id: e for e in evidence}

    def cited(ids) -> str:
        return "; ".join(f"{i}: {known[i].field_path} = {_clean(known[i].value_json, PROMPT_EVIDENCE_CHARS)}"
                         for i in ids if i in known)

    interesting = sorted((f for f in findings if f.status != "VERIFIED"),
                         key=lambda f: (f.status != "CONTRADICTED", -_severity(f)))[:PROMPT_FINDING_LINES]
    dispute_lines = []
    for d in disputes:
        reply = f"{d.reply.action}: {_clean(d.reply.text, PROMPT_NOTE_CHARS)}" if d.reply else "no reply"
        mark = " PROTECTED" if d.finding.finding_id in protected else ""
        dispute_lines.append(
            f"- {d.challenge.challenge_id} | {d.challenge.from_agent} challenges {d.challenge.to_agent}'s "
            f"finding {d.finding.finding_id} ({d.finding.status}, severity {_severity(d.finding)}{mark}): "
            f"{d.finding.note[:PROMPT_NOTE_CHARS]} | {d.challenge.type}: "
            f"{_clean(d.challenge.argument, PROMPT_NOTE_CHARS)} | reply: {reply} | "
            f"evidence: {cited(d.challenge.evidence_ids + d.finding.evidence_ids)}")
    verdict_lines = [f"- {v.agent}: score {v.score}, confidence {v.confidence}; {_clean(v.stance, 200)}; "
                     f"flags: {' / '.join(_clean(x, 120) for x in v.flags) or 'none'}" for v in verdicts]
    return ADJUDICATE_PROMPT.format(
        severity_scale=SEVERITY_SCALE, max_chars=MAX_REASON_CHARS, narrative_chars=MAX_NARRATIVE_CHARS,
        verdicts="\n".join(verdict_lines) or "- (none)",
        findings="\n".join(_format_finding(f) for f in interesting) or "- (none)",
        disputes="\n".join(dispute_lines) or "- (none: no challenge is still open)",
        gates="\n".join(f"- {g}" for g in gates) or "- (none)",
        unverified="\n".join(f"- {u}" for u in unverified[:PROMPT_UNVERIFIED_LINES]) or "- (none)")


def _default_model() -> str:
    from app.settings import Settings

    settings = Settings()
    return settings.model_pro or settings.model_mid


async def adjudicate(run_id: str, *, repo: LedgerRepository | None = None, llm: LlmClient | None = None,
                     model: str | None = None) -> AdjudicationResult:
    if repo is None:   # offline demos: a visibly stubbed result, never mistaken for a real ruling
        return AdjudicationResult(narrative="STUB: No disputes adjudicated.", is_stub=True)
    run = await repo.get_run(run_id)
    if run["phase"] != "ADJUDICATE":
        raise LedgerStateError("Adjudication runs in the ADJUDICATE phase.")

    findings = list(await repo.get_all_findings(run_id))
    metrics = await repo.get_metrics(run_id)
    gates = confirm_gates(metrics, findings)
    protected = {i for ids in gate_backers(metrics, findings).values() for i in ids}
    challenges = [c for agent in AGENTS for c in await repo.get_challenges_for(run_id, agent)]
    disputes = open_disputes(challenges, list(await repo.get_replies(run_id)), findings)
    claims = list((await repo.get_claims(run_id)).claims)
    verdicts = await repo.get_verdicts(run_id)
    evidence = await repo.get_evidence(run_id)
    unverified = _unverified_claims(claims, findings)

    draft: AdjudicationDraft | None = None
    if llm is not None:
        prompt = _build_prompt(findings, verdicts, disputes, evidence, gates, protected, unverified)
        try:
            draft = await llm.generate(prompt, AdjudicationDraft, model or _default_model())
        except Exception:
            draft = None  # no model: the findings and the deterministic narrative stand

    proposed = valid_revisions(draft, disputes, protected) if draft else ()
    existing = {r.revision_id for r in await repo.get_revisions(run_id)}
    for revision in proposed:
        if revision.revision_id not in existing:
            await repo.add_revision(run_id, revision)   # trusted write: author "leo"
    # Report every ruling the lead analyst has made on this run, so a retried call (after a crash,
    # say) still describes revisions that are really in the ledger.
    revisions = tuple(r for r in await repo.get_revisions(run_id)
                      if r.author == "leo" and r.revision_id.startswith(REVISION_PREFIX))
    if revisions:
        findings = list(await repo.get_all_findings(run_id))
        gates = confirm_gates(metrics, findings)

    narrative = _clean(draft.narrative, MAX_NARRATIVE_CHARS) if draft else ""
    return AdjudicationResult(
        narrative=narrative or fallback_narrative(findings, unverified, gates), revisions=revisions,
        is_stub=any(f.is_stub for f in findings))


async def confirmed_gates_for_run(run_id: str, repo: LedgerRepository) -> tuple[str, ...]:
    """Convenience for the orchestrator: gates to pass to `compute(..., confirmed_gates=...)`.

    Call it after `adjudicate`, so any ruling that lowered a finding's severity is taken into account.
    """
    return confirm_gates(await repo.get_metrics(run_id), list(await repo.get_all_findings(run_id)))
