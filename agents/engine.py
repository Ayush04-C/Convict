"""Daksh: the real specialist agent, shared by Identity, Scale and Team, Funding and Traction.

Three methods, one per phase, each reading from and writing to the ledger itself:

  run_round1        NO model. Runs the analyst checks on the claims routed to this agent and
                    writes evidence, metrics and findings. Findings come from code, not an LLM.
  run_challenge     LLM. Reads the OTHER agents' findings and evidence (readable only after the
                    round-one barrier) and files up to two challenges. Every challenge is
                    validated before it is written.
  run_reply_verdict LLM + code. Answers challenges addressed to this agent, then writes a verdict
                    whose score and confidence are computed from the findings by code. The model
                    only supplies the stance sentence and up to three flags.

If a model call fails, the agent degrades instead of dying: no challenges, no replies, and a
verdict from the deterministic score with a code-built stance.

Analysts only PROPOSE hard gates. A proposal is written as a metric named "gate:<name>" (value 1,
unit "proposal") so the orchestrator can find it; the orchestrator decides whether to confirm.
"""
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from agents.analysts.common import CheckResult, get, unverifiable
from agents.prompts import CHALLENGE_PROMPT, REPLY_VERDICT_PROMPT, SEVERITY_SCALE
from agents.protocol import AgentContext
from app.schemas import (
    AgentName, Challenge, Claim, ContextPacket, Evidence, Finding, Metric, Reply, Verdict,
)

MAX_CHALLENGES = 2
MAX_ARGUMENT_CHARS = 350
MAX_REPLY_CHARS = 300
MAX_STANCE_CHARS = 200
MAX_FLAG_CHARS = 140
MAX_FLAGS = 3
# A stored evidence value is cut to this many characters. It must stay large: the pipeline confirms a
# hard gate only if each cited value equals the trusted dossier's value exactly, and a truncated value
# never does (Retool's funding.milestones is about 13,000 characters, Zepto's about 31,000). Prompts
# show only a short excerpt, so this does not enlarge them.
MAX_EVIDENCE_CHARS = 100_000
PROMPT_NOTE_CHARS = 300
PROMPT_EVIDENCE_CHARS = 160
PROMPT_EVIDENCE_LINES = 80
MAX_SEVERITY_SWING = 2          # a reply may move a severity by at most this much
CRITICAL_FLOOR = 4              # a reply cannot lower a severity-5 finding below this
SOURCE = "crustdata.company.enrich"

CheckFn = Callable[[Claim | None, dict], CheckResult]


class ChallengeDraft(BaseModel):
    finding_id: str
    type: Literal["CONTRADICTS", "CONTEXT", "CORROBORATES", "GAP"]
    argument: str
    evidence_ids: list[str] = []


class ChallengeDrafts(BaseModel):
    challenges: list[ChallengeDraft] = []


class ReplyDraft(BaseModel):
    challenge_id: str
    action: Literal["CONCEDE", "REBUT", "REVISE"]
    text: str
    new_severity: int | None = Field(default=None)


class ReplyBundle(BaseModel):
    replies: list[ReplyDraft] = []
    stance: str = ""
    flags: list[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _evidence_json(value: object) -> str:
    """JSON text for a stored evidence value; big values keep a labelled preview."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        text = json.dumps(str(value), ensure_ascii=False)
    if len(text) <= MAX_EVIDENCE_CHARS:
        return text
    return json.dumps({"_truncated": True, "_chars": len(text), "preview": text[:MAX_EVIDENCE_CHARS]})


def _dossier(packet: ContextPacket) -> dict:
    """The company_data-shaped dict from the packet; empty (all UNVERIFIABLE) if unreadable."""
    try:
        parsed = json.loads(packet.dossier_json or "{}")
    except ValueError:
        return {}
    if isinstance(parsed, dict) and isinstance(parsed.get("company_data"), dict):
        parsed = parsed["company_data"]
    return parsed if isinstance(parsed, dict) else {}


def _severity(finding: Finding) -> int:
    return finding.severity_final or finding.severity


def score_findings(findings: list[Finding] | tuple[Finding, ...]) -> float:
    """100 minus 6 per severity step above 1 on each CONTRADICTED finding, floored at 0.

    Uses effective severities, so a revision made during the reply round is counted.
    """
    penalty = sum(6 * (_severity(f) - 1) for f in findings if f.status == "CONTRADICTED")
    return max(0.0, 100.0 - penalty)


def confidence_of(findings: list[Finding] | tuple[Finding, ...]) -> float:
    """Share of this agent's findings that could actually be verified."""
    if not findings:
        return 0.0
    return sum(1 for f in findings if f.status != "UNVERIFIABLE") / len(findings)


def _format_finding(finding: Finding) -> str:
    cites = ",".join(finding.evidence_ids) or "none"
    return (f"- {finding.finding_id} | {finding.agent} | claim {finding.claim_id or '-'} | "
            f"{finding.status} severity {_severity(finding)} | {_clean(finding.note, PROMPT_NOTE_CHARS)} "
            f"| evidence: {cites}")


def _format_evidence(items: list[Evidence]) -> str:
    lines = [f"- {e.evidence_id} | {e.agent} | {e.field_path} = {_clean(e.value_json, PROMPT_EVIDENCE_CHARS)}"
             for e in items[:PROMPT_EVIDENCE_LINES]]
    return "\n".join(lines) or "- (none)"


class AnalystAgent:
    """Subclasses set the class attributes below; behaviour is shared."""

    name: AgentName
    title: str = "analyst"
    role: str = ""
    topic_checks: dict[str, CheckFn] = {}
    # (check, topic, skip_if_topic): run `check` with the first claim of `topic` (or None);
    # skip it entirely when a claim of `skip_if_topic` is present (that claim has its own check).
    flag_checks: tuple[tuple[CheckFn, str | None, str | None], ...] = ()

    def __init__(self, *, models: tuple[str, str] | None = None):
        self._models = models  # (fast, mid); read from settings lazily when omitted

    # ---- helpers ---------------------------------------------------------------------
    def _model(self, kind: Literal["fast", "mid"]) -> str:
        if self._models:
            return self._models[0 if kind == "fast" else 1]
        from app.settings import Settings

        settings = Settings()
        return (settings.model_fast or settings.model_mid) if kind == "fast" else settings.model_mid

    def _guard(self, ctx: AgentContext) -> None:
        if ctx.packet.agent != self.name:
            raise ValueError("Context belongs to another specialist")

    def _run_check(self, fn: CheckFn, claim: Claim | None, data: dict) -> CheckResult:
        try:
            return fn(claim, data)
        except Exception as exc:  # one broken check must not take the whole agent down
            return unverifiable(getattr(fn, "__name__", "check"), claim,
                                f"Check failed internally ({type(exc).__name__}); treated as unverifiable.")

    def _checks(self, claims: tuple[Claim, ...], data: dict) -> list[CheckResult]:
        results: list[CheckResult] = []
        for claim in claims:
            if not claim.testable or claim.topic not in self._primary_topics():
                continue
            fn = self.topic_checks.get(claim.topic)
            if fn is None:
                results.append(unverifiable(
                    f"{claim.topic}_unchecked", claim,
                    f"No Crustdata field can verify {claim.topic} claims, so this stays unverified."))
            else:
                results.append(self._run_check(fn, claim, data))
        topics = {c.topic for c in claims if c.testable}
        for fn, topic, skip_if in self.flag_checks:
            if skip_if and skip_if in topics:
                continue
            claim = next((c for c in claims if c.topic == topic and c.testable), None) if topic else None
            results.append(self._run_check(fn, claim, data))
        return results

    def _primary_topics(self) -> set[str]:
        from leo.context import PRIMARY

        return {topic for topic, agent in PRIMARY.items() if agent == self.name}

    def _records(self, ctx: AgentContext, results: list[CheckResult], data: dict):
        fetched, updated = _now(), get(data, "updated_at")
        updated = updated if isinstance(updated, str) else None
        ids: dict[str, str] = {}
        evidence: list[Evidence] = []

        def cite(path: str) -> str | None:
            if path in ids:
                return ids[path]
            value = get(data, *path.split("."))
            if value is None:
                return None
            evidence_id = f"{self.name}:E{len(evidence) + 1}"
            evidence.append(Evidence(run_id=ctx.run_id, evidence_id=evidence_id, agent=self.name,
                                     source=SOURCE, field_path=path, value_json=_evidence_json(value),
                                     fetched_at=fetched, source_updated_at=updated))
            ids[path] = evidence_id
            return evidence_id

        findings, metrics = [], []
        for number, result in enumerate(results, start=1):
            cited = tuple(dict.fromkeys(e for p in result.evidence_paths if (e := cite(p))))
            status, note = result.status, result.note
            if status != "UNVERIFIABLE" and not cited:
                status, note = "UNVERIFIABLE", f"{note} (no citable evidence)"
            findings.append(Finding(
                finding_id=f"{self.name}:F{number}", agent=self.name, claim_id=result.claim_id,
                status=status, severity=result.severity, note=note, evidence_ids=cited,
                round=1, is_stub=ctx.packet.is_stub))
            suffix = f"[{result.claim_id}]" if result.claim_id else ""
            for metric in result.metrics:
                metrics.append(Metric(run_id=ctx.run_id, agent=self.name, name=f"{metric.name}{suffix}",
                                      value=metric.value, unit=metric.unit, formula=metric.formula,
                                      evidence_ids=cited))
            if result.gate and cited:  # a gate needs explicit evidence behind it (contracts.md)
                metrics.append(Metric(run_id=ctx.run_id, agent=self.name, name=f"gate:{result.gate}",
                                      value=1.0, unit="proposal", evidence_ids=cited,
                                      formula=f"proposed by {result.check}; the orchestrator must confirm"))
        return tuple(evidence), tuple(metrics), tuple(findings)

    # ---- phase 1: findings from code -------------------------------------------------
    async def run_round1(self, ctx: AgentContext) -> None:
        self._guard(ctx)
        data = _dossier(ctx.packet)
        results = self._checks(ctx.packet.claims, data)
        evidence, metrics, findings = self._records(ctx, results, data)
        if evidence:
            await ctx.ledger.add_evidence(evidence)
        if metrics:
            await ctx.ledger.add_metrics(metrics)
        if findings:
            await ctx.ledger.add_findings(findings)

    # ---- phase 2: cross-examination --------------------------------------------------
    def _valid_challenges(self, drafts: ChallengeDrafts, others: dict[str, Finding],
                          known_evidence: set[str]) -> tuple[Challenge, ...]:
        accepted: list[Challenge] = []
        targeted: set[str] = set()
        for draft in drafts.challenges:
            target = others.get(draft.finding_id)
            cited = tuple(dict.fromkeys(e for e in draft.evidence_ids if e in known_evidence))
            argument = _clean(draft.argument, MAX_ARGUMENT_CHARS)
            if target is None or not cited or not argument or draft.finding_id in targeted:
                continue
            targeted.add(draft.finding_id)
            accepted.append(Challenge(
                challenge_id=f"{self.name}:C{len(accepted) + 1}", from_agent=self.name,
                to_agent=target.agent, finding_id=target.finding_id, type=draft.type,
                argument=argument, evidence_ids=cited))
            if len(accepted) == MAX_CHALLENGES:
                break
        return tuple(accepted)

    async def run_challenge(self, ctx: AgentContext) -> None:
        self._guard(ctx)
        findings = await ctx.ledger.get_all_findings()
        own = [f for f in findings if f.agent == self.name]
        others = {f.finding_id: f for f in findings if f.agent != self.name}
        if not others:
            return
        evidence = list(await ctx.ledger.get_evidence())
        prompt = CHALLENGE_PROMPT.format(
            title=self.title, role=self.role, max_challenges=MAX_CHALLENGES, max_chars=MAX_ARGUMENT_CHARS,
            own_findings="\n".join(_format_finding(f) for f in own) or "- (none)",
            other_findings="\n".join(_format_finding(f) for f in others.values()),
            evidence=_format_evidence(evidence))
        try:
            drafts = await ctx.llm.generate(prompt, ChallengeDrafts, self._model("fast"))
        except Exception:
            return  # no model, no challenges: the debate is optional, the findings stand
        challenges = self._valid_challenges(drafts, others, {e.evidence_id for e in evidence})
        if challenges:
            await ctx.ledger.add_challenges(challenges)

    # ---- phase 3: replies and verdict ------------------------------------------------
    def _valid_replies(self, drafts: ReplyBundle, challenges: tuple[Challenge, ...],
                       own: dict[str, Finding]) -> tuple[Reply, ...]:
        by_id = {c.challenge_id: c for c in challenges}
        accepted: list[Reply] = []
        answered: set[str] = set()
        for draft in drafts.replies:
            challenge = by_id.get(draft.challenge_id)
            if challenge is None or challenge.challenge_id in answered:
                continue
            answered.add(challenge.challenge_id)
            action, severity = draft.action, None
            if action == "REVISE":
                current = _severity(own[challenge.finding_id]) if challenge.finding_id in own else None
                proposed = draft.new_severity
                if current is None or proposed is None or not 1 <= proposed <= 5:
                    action = "CONCEDE"
                else:
                    proposed = max(current - MAX_SEVERITY_SWING, min(current + MAX_SEVERITY_SWING, proposed))
                    if current == 5:
                        proposed = max(proposed, CRITICAL_FLOOR)
                    action, severity = ("CONCEDE", None) if proposed == current else ("REVISE", proposed)
            accepted.append(Reply(
                reply_id=f"{self.name}:R{len(accepted) + 1}", challenge_id=challenge.challenge_id,
                agent=self.name, action=action, new_severity=severity,
                text=_clean(draft.text, MAX_REPLY_CHARS) or "No reasoning given."))
        return tuple(accepted)

    @staticmethod
    def _code_summary(findings: list[Finding]) -> tuple[str, tuple[str, ...]]:
        counts = {s: sum(1 for f in findings if f.status == s)
                  for s in ("VERIFIED", "PARTIAL", "CONTRADICTED", "UNVERIFIABLE")}
        stance = (f"{counts['VERIFIED']} verified, {counts['PARTIAL']} partial, {counts['CONTRADICTED']} "
                  f"contradicted, {counts['UNVERIFIABLE']} unverifiable of {len(findings)} checks.")
        concerns = sorted((f for f in findings if f.status in ("CONTRADICTED", "PARTIAL")),
                          key=lambda f: (f.status != "CONTRADICTED", -_severity(f)))
        return stance, tuple(_clean(f.note, MAX_FLAG_CHARS) for f in concerns[:MAX_FLAGS])

    async def run_reply_verdict(self, ctx: AgentContext) -> None:
        self._guard(ctx)
        challenges = await ctx.ledger.get_challenges_for()
        findings = await ctx.ledger.get_all_findings()
        own = {f.finding_id: f for f in findings if f.agent == self.name}
        bundle: ReplyBundle | None = None
        if challenges:
            known = {e.evidence_id: e for e in await ctx.ledger.get_evidence()}
            cited = [known[i] for c in challenges for i in c.evidence_ids if i in known]
            cited += [known[i] for f in own.values() for i in f.evidence_ids if i in known]
            lines = [f"- {c.challenge_id} | from {c.from_agent} | on {c.finding_id} | {c.type} | "
                     f"{_clean(c.argument, MAX_ARGUMENT_CHARS)} | evidence: {','.join(c.evidence_ids)}"
                     for c in challenges]
            prompt = REPLY_VERDICT_PROMPT.format(
                title=self.title, role=self.role, max_chars=MAX_REPLY_CHARS, stance_chars=MAX_STANCE_CHARS,
                flag_chars=MAX_FLAG_CHARS, severity_scale=SEVERITY_SCALE,
                own_findings="\n".join(_format_finding(f) for f in own.values()) or "- (none)",
                challenges="\n".join(lines),
                evidence=_format_evidence(list({e.evidence_id: e for e in cited}.values())))
            try:
                bundle = await ctx.llm.generate(prompt, ReplyBundle, self._model("mid"))
            except Exception:
                bundle = None  # unanswered challenges are left for the lead analyst to rule on
        if bundle is not None:
            for reply in self._valid_replies(bundle, challenges, own):
                await ctx.ledger.add_reply(reply)
        final = [f for f in await ctx.ledger.get_all_findings() if f.agent == self.name]
        stance, flags = self._code_summary(final)
        if bundle is not None:
            stance = _clean(bundle.stance, MAX_STANCE_CHARS) or stance
            flags = tuple(_clean(f, MAX_FLAG_CHARS) for f in bundle.flags if f.strip())[:MAX_FLAGS] or flags
        await ctx.ledger.add_verdict(Verdict(
            agent=self.name, score=round(score_findings(final), 1), confidence=round(confidence_of(final), 3),
            stance=stance, flags=flags, is_stub=ctx.packet.is_stub))
