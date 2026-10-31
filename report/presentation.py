"""Deterministic investor-facing labels; no scoring, model calls or ledger writes."""

import html
import json
import re
from datetime import datetime, timezone

AGENT_LABELS = {
    "identity": "Identity and integrity",
    "scale_team": "Team and workplace",
    "funding": "Funding and financials",
    "traction": "Traction and hiring",
    "leo": "Lead reviewer",
}
STATUS_LABELS = {
    "VERIFIED": "Supported",
    "PARTIAL": "Partly supported",
    "CONTRADICTED": "Contradicted by available data",
    "UNVERIFIABLE": "Not independently verified",
}
SEVERITY_LABELS = {1: "Low", 2: "Minor", 3: "Material", 4: "Serious", 5: "Critical"}
TOPIC_LABELS = {
    "identity_founding": "Founding year",
    "identity_hq": "Headquarters",
    "identity_founder": "Founders",
    "team_size": "Team size",
    "team_growth": "Team growth",
    "funding_raised": "Funding raised",
    "funding_round": "Funding round",
    "investors": "Investors",
    "revenue_arr": "Annual recurring revenue (ARR)",
    "revenue_growth": "Revenue growth",
    "valuation_ask": "Valuation",
    "traction_users": "Active users",
    "traction_customers": "Customers",
    "hiring": "Open roles",
    "product_other": "Other statements",
}
GATE_LABELS = {
    "arr_ratio_over_3": "The revenue claim is more than three times the provider's upper revenue estimate.",
    "funding_round_absent": "The claimed funding round is absent from the available funding history.",
    "founder_not_found": "A named founder could not be matched to the available founder records.",
    "entity_not_active": "Available records show the company is no longer active.",
    "entity_unresolved": "The company's identity could not be confirmed.",
}
REASON_LABELS = {
    "Confirmed hard gate forces PASS.": "A confirmed exclusion rule requires a do-not-invest decision, regardless of the numeric score.",
    "No verifiable claims: no investment sizing.": "There are no verifiable claims to support an investment amount.",
    "Missing agents contribute zero; weights are not redistributed.": "Missing specialist results receive zero contribution; the remaining reviews are not given extra weight.",
    "Severity-5 finding caps the verdict.": "A critical concern prevents an unconditional investment recommendation.",
    "Must obtain founder EBITDA/profit documentation.": "Profit documentation is still required before an unconditional investment recommendation.",
}
FIELD_LABELS = {
    "company_data": "Complete company-data snapshot",
    "revenue.estimated.lower_bound_usd": "Lower revenue estimate",
    "revenue.estimated.upper_bound_usd": "Upper revenue estimate",
    "funding.total_investment_usd": "Total funding recorded",
    "funding.last_round_amount_usd": "Most recent funding amount",
    "funding.last_round_type": "Most recent funding round",
    "funding.last_fundraise_date": "Most recent funding date",
    "funding.milestones": "Funding history",
    "headcount.total": "Employee count",
    "headcount.timeseries": "Employee-count history",
    "headcount.growth_percent": "Employee growth history",
    "headcount.by_role_percent": "Workforce by role",
    "hiring.openings_count": "Open job positions",
    "hiring.openings_growth_percent": "Change in open positions",
    "hiring.open_jobs_timeseries": "Open-position history",
    "web_traffic.domain_traffic": "Website traffic",
    "followers.count": "Social followers",
    "followers.timeseries": "Follower history",
    "basic_info.status": "Operating status",
    "basic_info.year_founded": "Founding year",
    "basic_info.primary_domain": "Primary website",
    "basic_info.all_domains": "Known websites",
    "people.founders": "People listed as founders",
    "news": "News records",
    "employee_reviews.overall_rating": "Employee review rating",
}


def label(value):
    value = str(value)
    value = re.sub(r"\[([^]]+)\]", r" (\1)", value)
    value = value.replace("_", " ").replace(".", " ").strip()
    return value[:1].upper() + value[1:]


def text(value):
    value = "Not supplied" if value is None else str(value)
    value = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())
    # Quotes/apostrophes need no HTML encoding in Markdown text. Escaping # after
    # quote=True previously broke &#x27; into a visible &\#x27; entity.
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#!|>])", r"\\\1", value)


def number(value, decimals=1):
    if value is None:
        return "Not available"
    rendered = f"{float(value):,.{decimals}f}"
    return rendered.rstrip("0").rstrip(".") if decimals else rendered


def money(value):
    return "Not available" if value is None else "$" + number(value, 2)


def percent(value):
    return "Not available" if value is None else number(float(value) * 100) + "%"


def date_text(value):
    if not value:
        return "Not supplied by provider"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            return parsed.strftime("%d %b %Y")
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
        return parsed.strftime("%d %b %Y, %H:%M") + " (timezone not supplied)"
    except (ValueError, TypeError):
        return str(value)


def source_summary(value, path):
    if value is None:
        return "Not supplied"
    if isinstance(value, dict) and value.get("_truncated"):
        return "Only a shortened excerpt was recorded; details are not reconstructed from incomplete JSON."
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if path == "basic_info.year_founded":
            return str(value)
        if path.endswith("_usd"):
            return money(value)
        if "_percent" in path or path.endswith("_pct"):
            return number(value) + "%"
        suffix = {
            "headcount.total": " employees",
            "hiring.openings_count": " open roles",
            "followers.count": " followers",
        }.get(path, "")
        return number(value) + suffix
    if isinstance(value, str):
        if re.fullmatch(r"series_[a-z]", value, re.I):
            return value.replace("_", " ").title()
        return date_text(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else value
    if isinstance(value, list):
        if not value:
            return "No records supplied"
        if all(isinstance(v, str) for v in value):
            return "; ".join(value[:5]) + (f"; and {len(value) - 5} more" if len(value) > 5 else "")
        if path == "funding.milestones":
            dated = [
                v
                for v in value
                if isinstance(v, dict) and isinstance(v.get("date") or v.get("funding_date"), str)
            ]
            summary = f"{len(value):,} funding records."
            if dated:
                item = max(dated, key=lambda v: v.get("date") or v.get("funding_date"))
                parts = [source_summary(item["round_type"], "")] if item.get("round_type") else []
                if item.get("amount_usd") is not None:
                    parts.append(money(item["amount_usd"]))
                parts.append(date_text(item.get("date") or item.get("funding_date")))
                summary += " Latest dated record: " + ", ".join(parts) + "."
            return summary
        if path.endswith("timeseries"):
            dated = sorted(
                (v for v in value if isinstance(v, dict) and isinstance(v.get("date"), str)),
                key=lambda v: v["date"],
            )
            summary = f"{len(value):,} historical observations."
            if dated:
                summary += (
                    f" Period: {date_text(dated[0]['date'])} to {date_text(dated[-1]['date'])}."
                )
            return summary
        if path == "people.founders":
            names = [
                (v.get("basic_profile") or {}).get("name")
                for v in value
                if isinstance(v, dict) and isinstance(v.get("basic_profile"), dict)
            ]
            names = [n for n in names if isinstance(n, str)]
            return (
                "Provider people records: "
                + "; ".join(names[:5])
                + (f"; {len(names) - 5} more" if len(names) > 5 else "")
                if names
                else f"{len(value)} people records."
            )
        return f"{len(value):,} records available in the saved evidence."
    if isinstance(value, dict):
        periods = {
            "mom": "Month over month",
            "qoq": "Quarter over quarter",
            "yoy": "Year over year",
            "six_months": "Over six months",
            "two_years": "Over two years",
        }
        if path in ("headcount.growth_percent", "headcount.by_role_percent"):
            return (
                "; ".join(
                    f"{periods.get(k, label(k))}: {number(v)}%"
                    for k, v in value.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                )
                or "No numeric breakdown supplied"
            )
        if path == "followers":
            parts = []
            if value.get("count") is not None:
                parts.append(source_summary(value["count"], "followers.count"))
            if value.get("yoy_percent") is not None:
                parts.append(
                    "Year-over-year change: "
                    + source_summary(value["yoy_percent"], "followers.yoy_percent")
                )
            if parts:
                return "; ".join(parts)
        if path == "hiring":
            parts = []
            if value.get("openings_count") is not None:
                parts.append(source_summary(value["openings_count"], "hiring.openings_count"))
            if isinstance(value.get("recent_titles_csv"), str):
                parts.append("Recent job titles: " + value["recent_titles_csv"])
            if parts:
                return "; ".join(parts)
        if path == "web_traffic.domain_traffic":
            parts = [
                f"{domain}: {number(item.get('monthly_visitors'))} monthly visits"
                for domain, item in value.items()
                if isinstance(item, dict) and item.get("monthly_visitors") is not None
            ]
            if parts:
                return "; ".join(parts[:3])
        return f"{len(value):,} recorded fields; detailed values remain in the JSON report."
    return str(value)


def truncated_matches(value, original):
    if not isinstance(value, dict) or not value.get("_truncated") or original is None:
        return False
    preview = value.get("preview")
    if not isinstance(preview, str) or not preview:
        return False
    serialized = json.dumps(original, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return value.get("_chars") == len(serialized) and serialized.startswith(preview)
