"""Daksh: null-safe pure-Python team size, growth, mix, layoff and culture checks.

`data` is the company_data-shaped dict; this module needs the `headcount` and
`employee_reviews` groups. Crustdata headcount counts professional-network profiles,
so it can undercount small companies; notes say so where it matters.
"""
import re
from datetime import date, timedelta

from agents.analysts.common import (
    CheckResult, MetricResult, get, number, pct_diff, unverifiable,
)
from app.schemas import Claim

SIZE_VERIFIED = 0.15          # within 15 percent counts as a match
SIZE_PARTIAL = 0.40
SMALL_TEAM = 20               # below this, an absolute slack applies as well
SMALL_TEAM_SLACK = 3
GROWTH_VERIFIED_PP = 10.0     # percentage points of tolerance
GROWTH_PARTIAL_PP = 25.0
GROWTH_MIN_BASE = 10          # growth from fewer people is too unstable to contradict
LAYOFF_DROP_PCT = 15.0
LAYOFF_STRONG_PCT = 30.0
LOW_ENGINEERING_SHARE = 5.0
MIN_REVIEWS = 10
GOOD_RATING = 3.5
POOR_RATING = 3.0
WINDOW_KEYS = {1: "mom", 3: "qoq", 6: "six_months", 12: "yoy", 24: "two_years"}
UNDERCOUNT_NOTE = "Crustdata headcount counts professional-network profiles and can undercount."


def _paths(*paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(".".join(p) for p in paths)


def check_team_size(claim: Claim | None, data: dict) -> CheckResult:
    name = "team_size"
    evidence = _paths(("headcount", "total"))
    claimed = number(claim.value) if claim is not None and claim.unit in (None, "count") else None
    if claimed is None or claimed <= 0:
        return unverifiable(name, claim, "Claim has no headcount number to compare.", evidence)
    actual = number(get(data, "headcount", "total"))
    if actual is None or actual <= 0:
        return unverifiable(name, claim, "Crustdata has no headcount for this company.", evidence)
    diff = pct_diff(claimed, actual)
    metric = MetricResult("team_size_diff", diff, "ratio", "(claimed - headcount.total) / headcount.total")

    def result(status, severity, note):
        return CheckResult(name, status, severity, f"{note} {UNDERCOUNT_NOTE}", claim.id, evidence, (metric,))

    slack_ok = actual < SMALL_TEAM and abs(claimed - actual) <= SMALL_TEAM_SLACK
    if abs(diff) <= SIZE_VERIFIED or slack_ok:
        return result("VERIFIED", 1, f"Claimed {claimed:.0f} vs Crustdata {actual:.0f}.")
    if diff < 0:  # deck understates: not an exaggeration
        return result("PARTIAL", 2, f"Deck says {claimed:.0f}, fewer than Crustdata's {actual:.0f}.")
    if diff <= SIZE_PARTIAL:
        return result("PARTIAL", 2, f"Deck says {claimed:.0f}, {diff:.0%} above Crustdata's {actual:.0f}.")
    return result("CONTRADICTED", 4 if diff > 1.0 else 3,
                  f"Deck says {claimed:.0f}, {diff:.0%} above Crustdata's {actual:.0f}.")


def window_months(period: str | None) -> int | None:
    """'YoY', 'last 6 months', '2 years', 'QoQ' -> months; None when no time basis is stated."""
    if not period:
        return None
    text = period.strip().lower()
    fixed = {"yoy": 12, "year over year": 12, "qoq": 3, "mom": 1, "month over month": 1}
    if text in fixed:
        return fixed[text]
    months = re.search(r"(\d+)\s*(?:m\b|mo\b|month)", text)
    if months:
        return int(months.group(1))
    years = re.search(r"(\d+)\s*(?:y\b|yr|year)", text)
    return int(years.group(1)) * 12 if years else None


def _points(data: dict) -> list[tuple[date, float]]:
    points = []
    for item in get(data, "headcount", "timeseries") or []:
        if not isinstance(item, dict):
            continue
        try:
            day = date.fromisoformat(str(item.get("date"))[:10])
        except ValueError:
            continue
        count = number(item.get("employee_count"))
        if count is not None:
            points.append((day, count))
    return sorted(points)


def _growth_from_series(points: list[tuple[date, float]], months: int) -> tuple[float, float] | None:
    """(growth percent, base headcount) between the latest point and ~`months` earlier."""
    if len(points) < 2:
        return None
    latest_day, latest = points[-1]
    target = latest_day - timedelta(days=round(months * 30.44))
    earlier = [p for p in points[:-1] if abs((p[0] - target).days) <= 45]
    if not earlier:
        return None
    base_day, base = min(earlier, key=lambda p: abs((p[0] - target).days))
    return ((latest - base) / base * 100, base) if base > 0 else None


def compute_growth(claim: Claim | None, data: dict) -> CheckResult:
    name = "headcount_growth"
    evidence = _paths(("headcount", "timeseries"), ("headcount", "growth_percent"))
    if claim is None:
        return unverifiable(name, claim, "No growth claim to check.", evidence)
    months = window_months(claim.period)
    claimed = number(claim.value)
    if claimed is None:
        return unverifiable(name, claim, "Claim has no growth number.", evidence)
    if months is None:
        return unverifiable(name, claim, "Claim states no time basis, so growth cannot be compared.", evidence)
    if claim.unit == "multiple":
        claimed = (claimed - 1) * 100  # 3x means +200 percent
    elif claim.unit not in (None, "percent"):
        return unverifiable(name, claim, "Growth claim is not a percentage or multiple.", evidence)

    series = _growth_from_series(_points(data), months)
    source = "headcount.timeseries"
    if series is None:
        key = WINDOW_KEYS.get(months)
        reported = number(get(data, "headcount", "growth_percent", key)) if key else None
        if reported is None:
            return unverifiable(name, claim, f"No {months}-month headcount history in Crustdata.", evidence)
        series, source = (reported, 0.0), f"headcount.growth_percent.{key}"
    actual, base = series
    metric = MetricResult(f"headcount_growth_{months}m_pct", actual, "percent", f"from {source}")
    gap = claimed - actual

    def result(status, severity, note):
        return CheckResult(name, status, severity, note, claim.id, evidence, (metric,))

    detail = f"Claimed {claimed:.0f}% over {months} months; Crustdata shows {actual:.0f}%."
    if abs(gap) <= GROWTH_VERIFIED_PP:
        return result("VERIFIED", 1, detail)
    if gap < 0 or abs(gap) <= GROWTH_PARTIAL_PP:
        return result("PARTIAL", 2, f"{detail} Deck understates or is within 25 points.")
    if 0 < base < GROWTH_MIN_BASE:
        return result("PARTIAL", 2, f"{detail} Base of {base:.0f} people is too small to contradict.")
    return result("CONTRADICTED", 4 if gap > 100 else 3, f"{detail} Deck overstates growth.")


def function_mix(claim: Claim | None, data: dict) -> CheckResult:
    name = "function_mix"
    evidence = _paths(("headcount", "by_role_percent"), ("headcount", "total"))
    mix = get(data, "headcount", "by_role_percent")
    if not isinstance(mix, dict) or not mix:
        return unverifiable(name, None, "Crustdata has no headcount split by function.", evidence)
    engineering = number(mix.get("Engineering"))
    sales = number(mix.get("Sales"))
    if engineering is None:
        return unverifiable(name, None, "Crustdata gives no engineering share.", evidence)
    metrics = (MetricResult("engineering_share", engineering, "percent", "headcount.by_role_percent.Engineering"),
               MetricResult("sales_share", sales, "percent", "headcount.by_role_percent.Sales"))
    total = number(get(data, "headcount", "total")) or 0
    if engineering < LOW_ENGINEERING_SHARE and total >= SMALL_TEAM:
        return CheckResult(name, "PARTIAL", 2, f"Only {engineering:.1f}% of {total:.0f} people are engineers; "
                           "check this against the product claims.", None, evidence, metrics)
    return CheckResult(name, "VERIFIED", 1, f"Engineering is {engineering:.1f}% of headcount.",
                       None, evidence, metrics)


def detect_layoffs(claim: Claim | None, data: dict) -> CheckResult:
    name = "layoff_signal"
    evidence = _paths(("headcount", "timeseries"))
    points = _points(data)
    if len(points) < 2:
        return unverifiable(name, None, "Not enough headcount history to look for layoffs.", evidence)
    cutoff = points[-1][0] - timedelta(days=365)
    recent = [p for p in points if p[0] >= cutoff]
    worst, worst_day = 0.0, None
    for (_, before), (day, after) in zip(recent, recent[1:], strict=False):
        if before > 0 and (before - after) / before * 100 > worst:
            worst, worst_day = (before - after) / before * 100, day
    metric = MetricResult("max_drop_pct", worst, "percent", "largest drop between consecutive points, last 12 months")
    if worst > LAYOFF_DROP_PCT:
        severity = 4 if worst > LAYOFF_STRONG_PCT else 3
        return CheckResult(name, "PARTIAL", severity, f"Headcount fell {worst:.0f}% between consecutive "
                           f"points ending {worst_day.isoformat()}: possible layoff.", None, evidence, (metric,))
    return CheckResult(name, "VERIFIED", 1, "No sharp headcount drop in the last 12 months.", None,
                       evidence, (metric,))


def culture_snapshot(claim: Claim | None, data: dict) -> CheckResult:
    name = "culture_snapshot"
    evidence = _paths(("employee_reviews", "overall_rating", "rating"),
                      ("employee_reviews", "overall_rating", "total_count"))
    overall = number(get(data, "employee_reviews", "overall_rating", "rating"))
    count = number(get(data, "employee_reviews", "overall_rating", "total_count"))
    if overall is None or not 0 <= overall <= 5:
        return unverifiable(name, None, "Crustdata has no usable employee rating.", evidence)
    if count is None or count < MIN_REVIEWS:
        return unverifiable(name, None, f"Fewer than {MIN_REVIEWS} reviews; too few to judge culture.", evidence)
    metrics = [MetricResult("overall_rating", overall, "stars", "employee_reviews.overall_rating.rating"),
               MetricResult("review_count", count, "reviews", "employee_reviews.overall_rating.total_count")]
    for key in ("culture_and_values_rating", "work_life_balance_rating", "senior_management_rating"):
        value = number(get(data, "employee_reviews", key))
        if value is not None and 0 < value <= 5:
            metrics.append(MetricResult(key, value, "stars", f"employee_reviews.{key}"))
    detail = f"{overall:.1f} of 5 across {count:.0f} reviews."
    if overall >= GOOD_RATING:
        return CheckResult(name, "VERIFIED", 1, f"Healthy employee rating: {detail}", None, evidence, tuple(metrics))
    if overall >= POOR_RATING:
        return CheckResult(name, "PARTIAL", 2, f"Middling employee rating: {detail}", None, evidence, tuple(metrics))
    return CheckResult(name, "PARTIAL", 3, f"Poor employee rating: {detail}", None, evidence, tuple(metrics))
