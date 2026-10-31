"""Read-only report projection. Stored decisions are never recomputed or modified."""

import json

from ledger.repo import LedgerRepository
from ledger.repo import TERMINAL
from leo.context import AGENTS
from report.presentation import source_summary, truncated_matches

LABELS = {
    "PASS": "DO NOT INVEST",
    "DIG_DEEPER": "MORE DILIGENCE REQUIRED",
    "INVEST_WITH_CONDITIONS": "INVEST WITH CONDITIONS",
    "INVEST": "INVEST",
}


class ReportNotReady(Exception):
    pass


def field(data, path):
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


async def build_report(run_id: str, repo: LedgerRepository) -> dict:
    run = await repo.get_run(run_id)
    if run["phase"] not in TERMINAL:
        raise ReportNotReady("Run is still processing.")
    claims = await repo.get_claims(run_id)
    findings = await repo.get_all_findings(run_id)
    evidence = await repo.get_evidence(run_id)
    metrics = await repo.get_metrics(run_id)
    verdicts = await repo.get_verdicts(run_id)
    revisions = await repo.get_revisions(run_id)
    replies = await repo.get_replies(run_id)
    challenges = []
    for agent in AGENTS:
        challenges.extend(await repo.get_challenges_for(run_id, agent))
    decision = await repo.get_decision(run_id) if run["phase"] == "DONE" else None
    adjudication = await repo.get_adjudication(run_id) if decision else None
    resolved = await repo.latest_event(run_id, "resolution_saved")
    failed = await repo.latest_event(run_id, "run_failed")
    dossier = next(
        (
            e
            for e in evidence
            if e.agent == "leo"
            and e.source == "crustdata.company.enrich"
            and e.field_path == "company_data"
        ),
        None,
    )
    data = json.loads(dossier.value_json) if dossier else {}
    data = data if isinstance(data, dict) else {}
    limitations = []
    is_stub = (
        claims.is_stub
        or bool(decision and decision.is_stub)
        or any(v.is_stub for v in verdicts)
        or any(f.is_stub for f in findings)
    )
    if is_stub:
        limitations.append(
            "Contains synthetic or unfinished components; this is not a completed real assessment."
        )
    if adjudication and adjudication.is_stub:
        limitations.append(
            "Lead adjudication is a placeholder; unresolved disputes have not received a real lead ruling."
        )
    if run["phase"] != "DONE":
        limitations.append("No completed investment assessment or investment sizing is available.")
    states = await repo.get_agent_statuses(run_id)
    if any(s["state"] == "FAILED" for s in states):
        limitations.append(
            "One or more specialist phases failed; see agent states and retained audit findings."
        )
    limitations.append("Revenue ranges are provider estimates, not audited ARR.")
    # Preserve original agent records while exposing the actual run-dossier fetch
    # time when its value matches. Never use refreshed cache values for old runs.
    references = []
    for item in evidence:
        value = json.loads(item.value_json)
        original = field(data, item.field_path)
        matches_excerpt = truncated_matches(value, original)
        matching_dossier = bool(
            dossier
            and item.source == dossier.source
            and (item.evidence_id == dossier.evidence_id or original == value or matches_excerpt)
        )
        references.append(
            {
                "evidence_id": item.evidence_id,
                "agent": item.agent,
                "source": item.source,
                "field_path": item.field_path,
                "value": None if item is dossier else value,
                "display_summary": "Full company-data snapshot retained in the ledger."
                if item is dossier
                else (
                    "Saved company snapshot (specialist evidence contains only an excerpt): "
                    + source_summary(original, item.field_path)
                    if matching_dossier and matches_excerpt
                    else source_summary(value, item.field_path)
                ),
                "recorded_fetched_at": item.fetched_at,
                "provider_fetched_at": dossier.fetched_at if matching_dossier else None,
                "source_updated_at": dossier.source_updated_at
                if matching_dossier
                else item.source_updated_at,
                "is_full_dossier_reference": item is dossier,
            }
        )
    final = {s["agent"]: s["state"] for s in states if s["phase"] == "REPLY"}
    by_claim = [
        {
            **claim.model_dump(),
            "findings": [f.model_dump() for f in findings if f.claim_id == claim.id],
            "has_verifiable_finding": any(
                f.status != "UNVERIFIABLE" and f.evidence_ids
                for f in findings
                if f.claim_id == claim.id
            ),
        }
        for claim in claims.claims
    ]
    gates = []
    for name in decision.gates if decision else ():
        gates.append(
            {
                "name": name,
                "proposals": [m.model_dump() for m in metrics if m.name == "gate:" + name],
            }
        )
    result = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": run["status"],
        "phase": run["phase"],
        "company": {
            "submitted_name": run["company_name"],
            "resolved_name": (data.get("basic_info") or {}).get("name")
            if isinstance(data.get("basic_info"), dict)
            else None,
            "domain": run["domain"],
            "company_id": run["crustdata_company_id"],
        },
        "policy": run["policy"].model_dump(),
        "decision": decision.model_dump() if decision else None,
        "scoring_breakdown": await repo.get_scoring_breakdown(run_id) if decision else None,
        "verdict_label": LABELS[decision.verdict]
        if decision
        else "CANNOT ASSESS"
        if run["phase"] == "CANNOT_ASSESS"
        else "ANALYSIS FAILED",
        "is_stub": is_stub,
        "limitations": limitations,
        "must_obtain": [
            "Founder EBITDA/profit documentation; provider estimates do not replace these records."
        ],
        "sizing_label": "Policy-based sizing from the fund's own rules, not financial advice.",
        "resolution": json.loads(resolved.payload_json) if resolved else None,
        "failure_code": json.loads(failed.payload_json)["code"] if failed else None,
        "claims": by_claim,
        "findings": [f.model_dump() for f in findings],
        "metrics": [m.model_dump() for m in metrics],
        "verdicts": [v.model_dump() for v in verdicts],
        "agent_statuses": states,
        "agents_excluded_from_final_scoring": [a for a in AGENTS if final.get(a) != "DONE"],
        "confirmed_gates": gates,
        "evidence": references,
        "debate": {
            "challenges": [c.model_dump() for c in challenges],
            "replies": [r.model_dump() for r in replies],
            "revisions": [r.model_dump() for r in revisions],
            "unanswered_challenge_ids": [
                c.challenge_id
                for c in challenges
                if not any(r.challenge_id == c.challenge_id for r in replies)
            ],
            "adjudication": adjudication.model_dump() if adjudication else None,
        },
    }
    return json.loads(json.dumps(result, allow_nan=False))
