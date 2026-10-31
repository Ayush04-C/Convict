"""Readable projection of saved findings; never generates analysis or changes scores."""

import re

from report.presentation import (
    AGENT_LABELS,
    FIELD_LABELS,
    GATE_LABELS,
    REASON_LABELS,
    SEVERITY_LABELS,
    STATUS_LABELS,
    TOPIC_LABELS,
    date_text,
    label,
    money,
    number,
    percent,
    source_summary,
    text,
)


def render_markdown(report: dict) -> str:
    evidence = report["evidence"]
    refs = {item["evidence_id"]: i for i, item in enumerate(evidence, 1)}
    replacements = {key: f"Source {i}" for key, i in refs.items()}
    for claim in report["claims"]:
        replacements[claim["id"]] = f"claim {claim['id']} (slide {claim['slide']})"
        for finding in claim["findings"]:
            replacements[finding["finding_id"]] = (
                f"{AGENT_LABELS.get(finding['agent'], label(finding['agent']))} review of claim {claim['id']}"
            )
    # Only the machine-only agent name needs substitution in free prose.
    # Ordinary words such as "funding" must retain their original meaning.
    replacements["scale_team"] = AGENT_LABELS["scale_team"]
    replacements.update({key: value.rstrip(".") for key, value in GATE_LABELS.items()})
    pattern = re.compile(
        r"(?<![\w:])("
        + "|".join(re.escape(k) for k in sorted(replacements, key=len, reverse=True))
        + r")(?![\w:])"
    )

    def prose(value):
        value = REASON_LABELS.get(str(value), str(value))
        value = pattern.sub(lambda m: replacements[m[0]], value)
        value = re.sub(r"\bseries_([a-z])\b", lambda m: "Series " + m[1].upper(), value, flags=re.I)
        return text(value)

    def citations(ids):
        return (
            ", ".join(
                f"[Source {refs[eid]}](#source-{refs[eid]})"
                if eid in refs
                else "Source reference unavailable"
                for eid in dict.fromkeys(ids)
            )
            or "No source linked"
        )

    company = report["company"]
    decision = report["decision"]
    lines = [
        "# Company diligence report: "
        + text(company["resolved_name"] or company["submitted_name"]),
        "",
        "**Recommendation: " + text(report["verdict_label"]) + "**",
        "",
        "Website: " + text(company["domain"]),
        "",
    ]
    if report["is_stub"]:
        lines += ["**DEMO / INCOMPLETE COMPONENTS — not a completed real assessment.**", ""]
    if decision:
        lines += [
            "| Measure | Result |",
            "|---|---|",
            f"| Overall score | {number(decision['total_score'])} / 100 |",
            f"| Claim credibility | {number(decision['credibility'])} / 100 |"
            if decision["credibility"] is not None
            else "| Claim credibility | Not available |",
            f"| Assessment confidence | {percent(decision['confidence'])} |",
            f"| Suggested investment under fund policy | {money(decision['check_size_usd'])} |",
            "",
            "Confidence reflects the assessment inputs, not the probability that the company will succeed. A confident assessment can still recommend against investing.",
            "",
            text(report["sizing_label"]),
            "",
            "## Why this recommendation",
            "",
        ]
        for gate in report["confirmed_gates"]:
            ids = [eid for p in gate["proposals"] for eid in p["evidence_ids"]]
            lines += [
                "- **Exclusion rule:** "
                + text(GATE_LABELS.get(gate["name"], label(gate["name"])))
                + " "
                + citations(ids)
                + "."
            ]
        lines += ["- " + prose(reason) for reason in decision["reasons"]]
        if not report["confirmed_gates"] and not decision["reasons"]:
            lines += ["No separate decision reasons were recorded."]
    lines += [
        "",
        "## What was checked",
        "",
        "Each section compares a statement in the pitch deck with the available evidence. “Not independently verified” means the available sources cannot establish the claim; it does not mean the claim is false.",
        "",
    ]
    for i, claim in enumerate(report["claims"], 1):
        lines += [
            f"### {i}. {text(TOPIC_LABELS.get(claim['topic'], label(claim['topic'])))} — slide {text(claim['slide'])}",
            "",
            "**Deck statement:** " + text(claim["text"]),
            "",
        ]
        if not claim["findings"]:
            lines += [
                "**Assessment:** Not independently verified. No specialist finding was recorded.",
                "",
            ]
        for finding in claim["findings"]:
            severity = (
                finding["severity_final"]
                if finding["severity_final"] is not None
                else finding["severity"]
            )
            lines += [
                "**Assessment:** "
                + text(STATUS_LABELS.get(finding["status"], label(finding["status"])))
                + f". Concern level: {text(SEVERITY_LABELS.get(severity, 'Unspecified'))} ({text(severity)}/5).",
                "",
                "**Review:** " + prose(finding["note"]),
                "",
                "**Reviewed by:** "
                + text(AGENT_LABELS.get(finding["agent"], label(finding["agent"])))
                + ". **Evidence:** "
                + citations(finding["evidence_ids"])
                + ".",
                "",
            ]
    lines += [
        "## Evidence and source dates",
        "",
        "Sources below are saved evidence records, not necessarily independent sources. Crustdata is the company-data provider. “Retrieved” is when the saved provider response was fetched; that response may have been reused from cache. “Source updated” is when the provider says its underlying record changed. An unavailable update date does not establish freshness.",
        "",
    ]
    for i, item in enumerate(evidence, 1):
        summary = item.get("display_summary") or (
            "Full company-data snapshot retained in the ledger."
            if item["is_full_dossier_reference"]
            else source_summary(item["value"], item["field_path"])
        )
        lines += [
            f'<a id="source-{i}"></a>',
            "",
            f"### Source {i} — {text(FIELD_LABELS.get(item['field_path'], label(item['field_path'])))}",
            "",
            text(summary),
            "",
            "**Provider:** "
            + text(
                "Crustdata company data"
                if item["source"] == "crustdata.company.enrich"
                else item["source"]
            )
            + ".  ",
            "**Retrieved:** "
            + text(
                date_text(item["provider_fetched_at"])
                if item["provider_fetched_at"]
                else "Not established from saved evidence"
            )
            + ".  ",
            "**Source updated:** " + text(date_text(item["source_updated_at"])) + ".",
            "",
        ]
    lines += [
        "## Specialist assessments",
        "",
        "| Review | Score / 100 | Confidence | Stance |",
        "|---|---|---|---|",
    ]
    for verdict in report["verdicts"]:
        lines += [
            f"| {text(AGENT_LABELS.get(verdict['agent'], label(verdict['agent'])))} | {number(verdict['score'])} | {percent(verdict['confidence'])} | {text(label(verdict['stance']))} |"
        ]
    flags = [
        (verdict["agent"], flag) for verdict in report["verdicts"] for flag in verdict["flags"]
    ]
    if flags:
        lines += ["", "**Specialist notes:**", ""]
        lines += [
            "- " + text(AGENT_LABELS.get(agent, label(agent))) + ": " + prose(flag)
            for agent, flag in flags
        ]
    breakdown = report.get("scoring_breakdown")
    lines += ["", "## Saved scoring calculation", ""]
    if breakdown:
        lines += [
            "Displayed numbers are rounded; the saved calculation retains full precision.",
            "",
            "| Component | Weight | Score / 100 | Points contributed |",
            "|---|---|---|---|",
            f"| Claim credibility | {percent(breakdown['credibility_weight'])} | {number(decision['credibility'])} | {number(breakdown['credibility_contribution'])} |",
        ]
        for dimension in breakdown["dimensions"]:
            lines += [
                f"| {text(AGENT_LABELS.get(dimension['agent'], label(dimension['agent'])))} | {percent(dimension['weight'])} | {number(dimension['score'])} | {number(dimension['contribution'])} |"
            ]
        lines += [
            "",
            "**Weighted score before decision rules:** "
            + number(breakdown["weighted_total"])
            + " / 100.",
            "",
            "Decision rules can override the score. Specialist concern penalties are already included and are not deducted a second time.",
            "",
        ]
        cap_labels = {
            "CONFIRMED_HARD_GATE": "Confirmed exclusion rule",
            "SEVERITY_5": "Critical concern",
            "NO_VERIFIABLE_CLAIMS": "No verifiable claims",
            "MISSING_PROFIT_DOCUMENTS": "Required profit documents are missing",
        }
        lines += [
            "- Applied rule: " + text(cap_labels.get(cap, label(cap)))
            for cap in breakdown["applied_caps"]
        ]
        lines += [
            "",
            "Investment amount = fund policy limit × decision-tier percentage × assessment confidence.",
            "",
        ]
    elif decision:
        lines += [
            "Scoring calculation breakdown was not saved for this older decision; its score has not been recomputed.",
            "",
        ]
    lines += ["## Review discussion", ""]
    debate = report["debate"]
    for challenge in debate["challenges"]:
        lines += [
            "- **Question from "
            + text(AGENT_LABELS.get(challenge["from_agent"], label(challenge["from_agent"])))
            + " to "
            + text(AGENT_LABELS.get(challenge["to_agent"], label(challenge["to_agent"])))
            + ":** "
            + prose(challenge["argument"])
        ]
    for reply in debate["replies"]:
        lines += ["- **Response (" + text(label(reply["action"])) + "):** " + prose(reply["text"])]
    for revision in debate["revisions"]:
        lines += [
            "- **Revised concern level:** "
            + text(revision["severity"])
            + "/5. "
            + prose(revision["reason"])
        ]
    if debate["unanswered_challenge_ids"]:
        lines += [
            f"- {len(debate['unanswered_challenge_ids'])} review questions remain unanswered; see the JSON audit record for their references."
        ]
    if debate["adjudication"]:
        lines += ["", "**Lead reviewer:** " + prose(debate["adjudication"]["narrative"]), ""]
    elif not debate["challenges"]:
        lines += ["No review discussion was recorded.", ""]
    lines += ["## Limitations and outstanding documents", ""]
    lines += ["- " + prose(v) for v in report["limitations"] + report["must_obtain"]]
    lines += [
        "",
        "ARR means annual recurring revenue. EBITDA means earnings before interest, taxes, depreciation and amortization. Provider revenue estimates are not audited recurring revenue.",
        "",
    ]
    if report["resolution"]:
        lines += ["**Company matching:** " + prose(report["resolution"]["reason"]), ""]
    if report["failure_code"]:
        lines += ["**Run failure:** " + text(label(report["failure_code"])), ""]
    lines += [
        "<details>",
        "<summary>Technical audit references and execution status</summary>",
        "",
        "Run: " + text(report["run_id"]) + ". Status: " + text(label(report["status"])) + ".",
        "",
        "| Source | Internal evidence reference | Recorded field |",
        "|---|---|---|",
    ]
    for i, item in enumerate(evidence, 1):
        lines += [f"| Source {i} | {text(item['evidence_id'])} | {text(item['field_path'])} |"]
    lines += ["", "### Specialist execution", ""]
    for state in report["agent_statuses"]:
        lines += [
            "- "
            + text(AGENT_LABELS.get(state["agent"], label(state["agent"])))
            + ": "
            + text(label(state["phase"]))
            + " — "
            + text(label(state["state"]))
        ]
    lines += [
        "",
        "The JSON report retains the original identifiers, findings, metrics, formulas, source values, confidence components, and saved decision for audit. Shortened evidence excerpts remain identified as such; full run snapshots, where retained, remain in the ledger.",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"
