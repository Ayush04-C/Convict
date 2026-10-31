"""Daksh: null-safe pure-Python funding and financial-plausibility checks.

Formulas follow low-level design section 8b and docs/contracts.md. `data` is the
company_data-shaped dict; this module needs the `funding`, `revenue` and `headcount`
groups. Revenue figures are Crustdata ESTIMATES, never audited ARR.
"""
import re
from datetime import date

from rapidfuzz import fuzz

from agents.analysts.common import (
    CheckResult, MetricResult, claim_amount_usd, get, number, pct_diff, unverifiable,
)
from app.schemas import Claim

AMOUNT_TOLERANCE = 0.10       # within 10 percent counts as a match
TOTAL_PARTIAL_LIMIT = 0.25    # up to 25 percent over is only PARTIAL
ARR_VERIFIED_MAX = 1.0        # contracts.md: ratio <= 1 VERIFIED
ARR_PARTIAL_MAX = 1.5         # > 1 through 1.5 PARTIAL, above is CONTRADICTED
ARR_GATE_RATIO = 3.0          # only a ratio above 3 proposes the critical gate
REV_PER_HEAD_MAX = 600_000.0
REV_PER_HEAD_MIN = 20_000.0
VALUATION_MULTIPLE_MAX = 50.0  # heuristic flag, not a market benchmark
STALE_MONTHS = 24
VERY_STALE_MONTHS = 36
INVESTOR_MATCH_SCORE = 90

ESTIMATE_NOTE = "Crustdata revenue is an estimated range, not audited revenue."
ROUND_ORDER = ["pre_seed", "seed"] + [f"series_{c}" for c in "abcdefghij"]
EVIDENCE_TOTAL = ("funding", "total_investment_usd")
EVIDENCE_LAST_AMOUNT = ("funding", "last_round_amount_usd")
EVIDENCE_LAST_TYPE = ("funding", "last_round_type")
EVIDENCE_LAST_DATE = ("funding", "last_fundraise_date")
EVIDENCE_MILESTONES = ("funding", "milestones")
EVIDENCE_INVESTORS = ("funding", "investors")


def _paths(*paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(".".join(p) for p in paths)


def _round_key(text: object) -> str | None:
    """'Series B', 'series_b', 'Pre-Seed' -> 'series_b', 'pre_seed'; None when unrecognized."""
    if not isinstance(text, str):
        return None
    low = text.lower().replace("-", " ").replace("_", " ")
    if re.search(r"\bpre\s*seed\b", low):
        return "pre_seed"
    if re.search(r"\bseed\b", low):
        return "seed"
    series = re.search(r"\bseries\s+([a-j])\b", low)
    if series:
        return f"series_{series.group(1)}"
    return "angel" if re.search(r"\bangel\b", low) else None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _names(items: object) -> list[str]:
    names = []
    for item in items if isinstance(items, list) else []:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _milestone_round_keys(data: dict) -> set[str]:
    milestones = get(data, *EVIDENCE_MILESTONES)
    keys = {_round_key(m.get("round_type")) for m in milestones if isinstance(m, dict)} \
        if isinstance(milestones, list) else set()
    return keys - {None}


def check_funding_total(claim: Claim | None, data: dict) -> CheckResult:
    name = "funding_total"
    evidence = _paths(EVIDENCE_TOTAL, EVIDENCE_LAST_AMOUNT)
    claimed = claim_amount_usd(claim)
    if claimed is None:
        return unverifiable(name, claim, "Claim has no USD amount to compare.", evidence)
    total = number(get(data, *EVIDENCE_TOTAL))
    if total is None or total <= 0:
        return unverifiable(name, claim, "Crustdata has no total funding for this company.", evidence)
    diff = pct_diff(claimed, total)
    metric = MetricResult("funding_total_diff", diff, "ratio",
                          "(claimed - total_investment_usd) / total_investment_usd")
    last_diff = pct_diff(claimed, number(get(data, *EVIDENCE_LAST_AMOUNT)))

    def result(status, severity, note):
        return CheckResult(name, status, severity, note, claim.id, evidence, (metric,))

    if abs(diff) <= AMOUNT_TOLERANCE:
        return result("VERIFIED", 1, f"Claimed ${claimed:,.0f} is within 10% of Crustdata's total ${total:,.0f}.")
    if last_diff is not None and abs(last_diff) <= AMOUNT_TOLERANCE:
        return result("VERIFIED", 1, "Claimed amount matches Crustdata's last round, not the total raised.")
    if diff < 0 or diff <= TOTAL_PARTIAL_LIMIT:
        side = "less" if diff < 0 else "up to 25% more"
        return result("PARTIAL", 2, f"Deck states {side} than Crustdata's total ${total:,.0f}; "
                                    "it may describe one round or an older total.")
    return result("CONTRADICTED", 4 if diff > 0.5 else 3,
                  f"Deck claims ${claimed:,.0f}, {diff:.0%} above Crustdata's total ${total:,.0f}.")


def check_last_round(claim: Claim | None, data: dict) -> CheckResult:
    name = "last_round"
    evidence = _paths(EVIDENCE_LAST_TYPE, EVIDENCE_LAST_DATE, EVIDENCE_MILESTONES)
    if claim is None:
        return unverifiable(name, claim, "No round claim to check.", evidence)
    claimed = _round_key(claim.value) or _round_key(claim.text)
    if claimed is None:
        return unverifiable(name, claim, "No recognizable funding stage in the claim.", evidence)
    actual_raw = get(data, *EVIDENCE_LAST_TYPE)
    actual = _round_key(actual_raw)
    if actual is None:
        return unverifiable(name, claim, "Crustdata has no recognizable last round type.", evidence)
    history = _milestone_round_keys(data)

    def result(status, severity, note, gate=None):
        return CheckResult(name, status, severity, note, claim.id, evidence, gate=gate)

    if claimed == actual:
        year = number(str(claim.as_of)[:4]) if claim.as_of else None
        last_date = _parse_date(get(data, *EVIDENCE_LAST_DATE))
        if year and last_date and abs(year - last_date.year) > 1:
            return result("PARTIAL", 2, f"Round type matches but the deck dates it {int(year)}; "
                                        f"Crustdata dates the last round {last_date.isoformat()}.")
        return result("VERIFIED", 1, f"Last round is {actual_raw}, as claimed.")
    if claimed in history:
        return result("PARTIAL", 2, f"The {claimed} round exists in history, but Crustdata's "
                                    f"latest round is {actual_raw}.")
    if history:
        gate = "funding_round_absent" if claimed in ROUND_ORDER else None
        return result("CONTRADICTED", 4, f"No {claimed} round appears in Crustdata's funding history.",
                      gate)
    if claimed in ROUND_ORDER and actual in ROUND_ORDER:
        if ROUND_ORDER.index(claimed) > ROUND_ORDER.index(actual):
            return result("CONTRADICTED", 3, f"Deck claims {claimed}; Crustdata's last round is only {actual_raw}.")
        return result("PARTIAL", 2, f"Deck claims {claimed}; Crustdata shows a later round ({actual_raw}).")
    return unverifiable(name, claim, "Funding history is not comparable with the claim.", evidence)


_LEADING = re.compile(r"^(?:we are |we're |our )?(?:backed|funded|supported)\s+by\s+"
                      r"|^(?:co-)?led\s+by\s+|^investors?\s*(?:include|including|are)?\s*:?\s*", re.I)


# Words that mark a phrase as a description ("top-tier investors"), not an investor name.
_GENERIC = {"investors", "investor", "others", "other", "firms", "funds", "vcs", "vc", "angels",
            "angel", "top", "tier", "top-tier", "leading", "several", "many", "various",
            "institutional", "strategic", "worldwide", "notable", "prominent"}


def claimed_investors(claim: Claim) -> list[str]:
    raw = claim.value if isinstance(claim.value, str) else claim.text
    raw = _LEADING.sub("", raw.strip().rstrip("."))
    parts = [p.strip(" .") for p in re.split(r",|;|&|\+|\band\b", raw)]
    return [p for p in parts if 2 <= len(p) and len(p.split()) <= 4
            and not _GENERIC.intersection(p.lower().split())]


def check_rounds_and_investors(claim: Claim | None, data: dict) -> CheckResult:
    name = "investors"
    evidence = _paths(EVIDENCE_INVESTORS, EVIDENCE_MILESTONES)
    if claim is None:
        return unverifiable(name, claim, "No investor claim to check.", evidence)
    candidates = claimed_investors(claim)
    if not candidates:
        return unverifiable(name, claim, "No investor names could be read from the claim.", evidence)
    pool = _names(get(data, *EVIDENCE_INVESTORS))
    for milestone in get(data, *EVIDENCE_MILESTONES) or []:
        if isinstance(milestone, dict):
            pool += _names(milestone.get("investors")) + _names(milestone.get("lead_investors"))
    if not pool:
        return unverifiable(name, claim, "Crustdata has no recorded investors.", evidence)
    matched = [c for c in candidates
               if any(fuzz.token_set_ratio(c.lower(), p.lower()) >= INVESTOR_MATCH_SCORE for p in pool)]
    share = len(matched) / len(candidates)
    metric = MetricResult("investors_matched_share", share, "ratio", "matched names / claimed names")

    def result(status, severity, note):
        return CheckResult(name, status, severity, note, claim.id, evidence, (metric,))

    missing = [c for c in candidates if c not in matched]
    if share == 1:
        return result("VERIFIED", 1, f"All {len(candidates)} claimed investors appear in Crustdata.")
    if share >= 0.5:
        return result("PARTIAL", 2, f"Not found in Crustdata: {', '.join(missing)}.")
    return result("CONTRADICTED", 3, f"Not found among Crustdata's recorded investors: "
                                     f"{', '.join(missing)} (coverage may be incomplete).")


def check_arr(claim: Claim | None, data: dict) -> CheckResult:
    name = "arr_vs_estimate"
    evidence = _paths(("revenue", "estimated", "lower_bound_usd"),
                      ("revenue", "estimated", "upper_bound_usd"))
    claimed = claim_amount_usd(claim)
    if claimed is None:
        return unverifiable(name, claim, "Claim has no USD revenue amount to compare.", evidence)
    lower = number(get(data, "revenue", "estimated", "lower_bound_usd"))
    upper = number(get(data, "revenue", "estimated", "upper_bound_usd"))
    if upper is None or upper <= 0:
        return unverifiable(name, claim, "Crustdata has no usable revenue estimate. " + ESTIMATE_NOTE,
                            evidence)
    ratio = claimed / upper
    metric = MetricResult("arr_ratio", ratio, "ratio", "claimed_arr / revenue.estimated.upper_bound_usd")
    band = f"estimated range ${(lower or 0):,.0f} to ${upper:,.0f}"

    def result(status, severity, note, gate=None):
        return CheckResult(name, status, severity, f"{note} {ESTIMATE_NOTE}", claim.id, evidence,
                           (metric,), gate)

    if ratio <= ARR_VERIFIED_MAX:
        return result("VERIFIED", 1, f"Claimed ${claimed:,.0f} is within Crustdata's {band}.")
    if ratio <= ARR_PARTIAL_MAX:
        return result("PARTIAL", 2, f"Claimed ${claimed:,.0f} is {ratio:.2f}x the top of the {band}.")
    if ratio > ARR_GATE_RATIO:
        return result("CONTRADICTED", 5, f"Claimed ${claimed:,.0f} is {ratio:.1f}x the top of the {band}.",
                      "arr_ratio_over_3")
    return result("CONTRADICTED", 4, f"Claimed ${claimed:,.0f} is {ratio:.2f}x the top of the {band}.")


def revenue_per_head(claim: Claim | None, data: dict) -> CheckResult:
    """Plausibility flag. Uses the claimed ARR when given in USD, else the estimate midpoint."""
    name = "revenue_per_head"
    evidence = _paths(("revenue", "estimated", "lower_bound_usd"),
                      ("revenue", "estimated", "upper_bound_usd"), ("headcount", "total"))
    headcount = number(get(data, "headcount", "total"))
    if headcount is None or headcount <= 0:
        return unverifiable(name, None, "Crustdata has no headcount.", evidence)
    claimed = claim_amount_usd(claim)
    if claimed is not None:
        revenue, basis = claimed, "claimed ARR"
    else:
        lower = number(get(data, "revenue", "estimated", "lower_bound_usd"))
        upper = number(get(data, "revenue", "estimated", "upper_bound_usd"))
        if lower is None or upper is None or upper <= 0:
            return unverifiable(name, None, "Crustdata has no revenue estimate.", evidence)
        revenue, basis = (lower + upper) / 2, "estimated revenue midpoint"
    value = revenue / headcount
    metric = MetricResult("revenue_per_head", value, "USD", f"{basis} / headcount.total")
    if REV_PER_HEAD_MIN <= value <= REV_PER_HEAD_MAX:
        return CheckResult(name, "VERIFIED", 1, f"${value:,.0f} per head ({basis}) is plausible. "
                           + ESTIMATE_NOTE, None, evidence, (metric,))
    return CheckResult(name, "PARTIAL", 3, f"${value:,.0f} per head ({basis}) is outside the plausible "
                       f"${REV_PER_HEAD_MIN:,.0f} to ${REV_PER_HEAD_MAX:,.0f} range. " + ESTIMATE_NOTE,
                       None, evidence, (metric,))


def valuation_multiple(claim: Claim | None, data: dict) -> CheckResult:
    name = "valuation_multiple"
    evidence = _paths(("revenue", "estimated", "lower_bound_usd"),
                      ("revenue", "estimated", "upper_bound_usd"))
    claimed = claim_amount_usd(claim)
    if claimed is None:
        return unverifiable(name, claim, "Claim has no USD valuation to compare.", evidence)
    if not re.search(r"valuation|valued|post-money|pre-money", claim.text, re.I):
        return unverifiable(name, claim, "The claim looks like a raise amount, not a valuation.", evidence)
    lower = number(get(data, "revenue", "estimated", "lower_bound_usd"))
    upper = number(get(data, "revenue", "estimated", "upper_bound_usd"))
    if lower is None or upper is None or (lower + upper) <= 0:
        return unverifiable(name, claim, "Crustdata has no revenue estimate. " + ESTIMATE_NOTE, evidence)
    multiple = claimed / ((lower + upper) / 2)
    metric = MetricResult("valuation_multiple", multiple, "x", "claimed valuation / revenue midpoint")
    if multiple > VALUATION_MULTIPLE_MAX:
        return CheckResult(name, "PARTIAL", 3, f"Valuation is {multiple:.0f}x estimated revenue; a high "
                           f"multiple (heuristic flag above {VALUATION_MULTIPLE_MAX:.0f}x). " + ESTIMATE_NOTE,
                           claim.id, evidence, (metric,))
    return CheckResult(name, "VERIFIED", 1, f"Valuation is {multiple:.1f}x estimated revenue. " + ESTIMATE_NOTE,
                       claim.id, evidence, (metric,))


def months_since_last_raise(claim: Claim | None, data: dict, *, today: date | None = None) -> CheckResult:
    name = "months_since_last_raise"
    evidence = _paths(EVIDENCE_LAST_DATE)
    last = _parse_date(get(data, *EVIDENCE_LAST_DATE))
    if last is None:
        return unverifiable(name, None, "Crustdata has no last fundraise date.", evidence)
    now = today or date.today()
    months = (now.year - last.year) * 12 + (now.month - last.month) - (1 if now.day < last.day else 0)
    metric = MetricResult("months_since_last_raise", float(months), "months",
                          "whole months between last_fundraise_date and today")
    if months < 0:
        return unverifiable(name, None, "Last fundraise date is in the future.", evidence)
    if months > VERY_STALE_MONTHS:
        return CheckResult(name, "PARTIAL", 3, f"{months} months since the last raise: funding may be "
                           "running low or the company may be self-funded.", None, evidence, (metric,))
    if months > STALE_MONTHS:
        return CheckResult(name, "PARTIAL", 2, f"{months} months since the last raise.", None,
                           evidence, (metric,))
    return CheckResult(name, "VERIFIED", 1, f"{months} months since the last raise.", None, evidence,
                       (metric,))
