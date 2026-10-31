"""Render a stored report, escaping provider/deck text without generating new analysis."""

import html
import json
import re


def text(value):
    value = "Unknown" if value is None else str(value)
    value = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())
    value = html.escape(value)
    return re.sub(r"([\\`*_{}\[\]()#!|>])", r"\\\1", value)


def render_markdown(report: dict) -> str:
    company = report["company"]
    lines = [
        "# Company diligence report: "
        + text(company["resolved_name"] or company["submitted_name"]),
        "",
        "Outcome: **" + text(report["verdict_label"]) + "**",
        "",
        "Run: " + text(report["run_id"]),
        "Status: " + text(report["status"]),
        "",
    ]
    if report["is_stub"]:
        lines += ["**DEMO / INCOMPLETE COMPONENTS — not a completed real assessment.**", ""]
    decision = report["decision"]
    if decision:
        lines += [
            "## Saved decision",
            "",
            "| Field | Stored value |",
            "|---|---|",
            *[
                f"| {label} | {text(decision[key])} |"
                for label, key in [
                    ("Verdict", "verdict"),
                    ("Score", "total_score"),
                    ("Credibility", "credibility"),
                    ("Confidence", "confidence"),
                    ("Check size USD", "check_size_usd"),
                ]
            ],
            "",
            text(report["sizing_label"]),
            "",
            "## Confirmed gates",
            "",
        ]
        lines += [
            "- "
            + text(g["name"])
            + "; evidence: "
            + text(", ".join(sorted({eid for p in g["proposals"] for eid in p["evidence_ids"]})))
            for g in report["confirmed_gates"]
        ] or ["- None recorded."]
        lines += ["", "## Decision reasons", ""] + [
            "- " + text(reason) for reason in decision["reasons"]
        ]
    breakdown = report.get("scoring_breakdown")
    if breakdown:
        lines += [
            "",
            "## Saved scoring calculation",
            "",
            "| Component | Weight | Score | Contribution |",
            "|---|---|---|---|",
            f"| Credibility | {text(breakdown['credibility_weight'])} | {text(decision['credibility'])} | {text(breakdown['credibility_contribution'])} |",
        ]
        for dimension in breakdown["dimensions"]:
            lines.append(
                "| "
                + " | ".join(
                    text(v)
                    for v in [
                        dimension["agent"],
                        dimension["weight"],
                        dimension["score"],
                        dimension["contribution"],
                    ]
                )
                + " |"
            )
        lines += [
            "",
            "Weighted total before overrides: " + text(breakdown["weighted_total"]),
            "Confidence components: "
            + text(json.dumps(breakdown["confidence_components"], sort_keys=True)),
            "Applied caps/overrides: " + text(", ".join(breakdown["applied_caps"]) or "None"),
            "Sizing: policy limit × final tier percentage × stored confidence.",
            "Analyst severity penalties are already included in dimension scores; no second penalty is applied.",
        ]
    elif decision:
        lines += [
            "",
            "Scoring calculation breakdown was not saved for this older decision; its score has not been recomputed.",
        ]
    lines += [
        "",
        "## Claim findings",
        "",
        "| Claim / slide | Specialist | Status | Severity | Evidence | Note |",
        "|---|---|---|---|---|---|",
    ]
    for claim in report["claims"]:
        findings = claim["findings"] or [None]
        for finding in findings:
            lines.append(
                "| "
                + " | ".join(
                    text(v)
                    for v in [
                        f"{claim['id']} / {claim['slide']}: {claim['text']}",
                        finding["agent"] if finding else None,
                        finding["status"] if finding else "UNVERIFIABLE",
                        (finding["severity_final"] or finding["severity"]) if finding else None,
                        ", ".join(finding["evidence_ids"]) if finding else "None",
                        finding["note"] if finding else "No finding recorded.",
                    ]
                )
                + " |"
            )
    lines += [
        "",
        "## Evidence and source dates",
        "",
        "| Evidence | Source / path | Value | Provider fetched | Provider updated |",
        "|---|---|---|---|---|",
    ]
    for item in report["evidence"]:
        value = (
            "Full dossier retained in ledger"
            if item["is_full_dossier_reference"]
            else json.dumps(item["value"], ensure_ascii=False)
        )
        preview = (
            value if len(value) <= 800 else value[:800] + "… [preview; full value in JSON report]"
        )
        lines.append(
            "| "
            + " | ".join(
                text(v)
                for v in [
                    item["evidence_id"],
                    item["source"] + " / " + item["field_path"],
                    preview,
                    item["provider_fetched_at"],
                    item["source_updated_at"],
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## Saved specialist verdicts",
        "",
        "| Specialist | Score | Confidence | Stance | Flags |",
        "|---|---|---|---|---|",
    ]
    for verdict in report["verdicts"]:
        lines.append(
            "| "
            + " | ".join(
                text(value)
                for value in [
                    verdict["agent"],
                    verdict["score"],
                    verdict["confidence"],
                    verdict["stance"],
                    "; ".join(verdict["flags"]),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## Recorded metrics",
        "",
        "| Specialist | Metric | Value | Unit | Formula | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for metric in report["metrics"]:
        lines.append(
            "| "
            + " | ".join(
                text(value)
                for value in [
                    metric["agent"],
                    metric["name"],
                    metric["value"],
                    metric["unit"],
                    metric["formula"],
                    ", ".join(metric["evidence_ids"]),
                ]
            )
            + " |"
        )
    lines += ["", "## Specialist execution", ""]
    lines += [
        f"- {text(s['agent'])}: {text(s['phase'])} — {text(s['state'])}"
        for s in report["agent_statuses"]
    ]
    lines += ["", "## Debate", ""]
    for challenge in report["debate"]["challenges"]:
        lines.append(
            f"- Challenge {text(challenge['challenge_id'])}: {text(challenge['from_agent'])} → {text(challenge['to_agent'])}; {text(challenge['argument'])}"
        )
    for reply in report["debate"]["replies"]:
        lines.append(
            f"- Reply to {text(reply['challenge_id'])}: {text(reply['action'])}; {text(reply['text'])}"
        )
    for revision in report["debate"]["revisions"]:
        lines.append(
            f"- Revision {text(revision['revision_id'])}: finding {text(revision['finding_id'])}; severity {text(revision['severity'])}; {text(revision['reason'])}"
        )
    if report["debate"]["unanswered_challenge_ids"]:
        lines += [
            "- Unanswered challenges: "
            + text(", ".join(report["debate"]["unanswered_challenge_ids"]))
        ]
    adjudication = report["debate"]["adjudication"]
    if adjudication:
        lines += ["", "Lead narrative: " + text(adjudication["narrative"])]
    lines += ["", "## Limitations", ""] + ["- " + text(v) for v in report["limitations"]]
    if report["resolution"]:
        lines += ["", "Resolution: " + text(report["resolution"]["reason"])]
    if report["failure_code"]:
        lines += ["", "Failure: " + text(report["failure_code"])]
    lines += ["", "## Must obtain", ""] + ["- " + text(v) for v in report["must_obtain"]]
    return "\n".join(lines).rstrip() + "\n"
