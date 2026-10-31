"""Daksh: null-safe pure-Python hiring, web traffic and audience checks.

`data` is the company_data-shaped dict; this module needs the `hiring`, `web_traffic` and
`followers` groups, plus `basic_info.primary_domain` when available to pick the right domain.

UNITS DIFFER BY GROUP in Crustdata and every conversion below is deliberate:
  hiring.openings_growth_percent.*   FRACTION  (-0.93 means -93%; 7.0 means +700%)
  web_traffic.domain_traffic.*.{mom,qoq}_pct, followers.*_percent   PERCENT (-25.4 means -25.4%)
Open-role counts are noisy month to month, so notes say so and tolerances are generous.
"""
import re
from datetime import date

from agents.analysts.common import CheckResult, MetricResult, get, number, unverifiable
from agents.analysts.scale_team import growth_from_series, window_months
from app.schemas import Claim

GROWTH_VERIFIED_PP = 15.0   # percentage points of tolerance on a growth claim
GROWTH_PARTIAL_PP = 30.0
VOLUME_VERIFIED = 0.25      # visitor or opening counts within 25 percent match
VOLUME_PARTIAL = 0.50
HEALTHY_OPENINGS = 3
HEALTHY_OPENINGS_QOQ_PCT = -25.0
TRAFFIC_FALL_PCT = -10.0
TRAFFIC_SHARP_FALL_PCT = -30.0
PAID_SHARE_NOTE_PCT = 25.0
SLOPE_POINTS = 6
FOLLOWER_KEYS = {1: "mom_percent", 3: "qoq_percent", 6: "six_months_growth_percent", 12: "yoy_percent"}
TRAFFIC_KEYS = {1: "mom_pct", 3: "qoq_pct"}
NOISE_NOTE = "Open-role counts are noisy month to month."


def _paths(*paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(".".join(p) for p in paths)


def _series(items: object, key: str) -> list[tuple[date, float]]:
    points = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            day = date.fromisoformat(str(item.get("date"))[:10])
        except ValueError:
            continue
        value = number(item.get(key))
        if value is not None:
            points.append((day, value))
    return sorted(points)


def _slope(points: list[tuple[date, float]]) -> float | None:
    """Least-squares change per month over the last SLOPE_POINTS points."""
    recent = points[-SLOPE_POINTS:]
    if len(recent) < 3:
        return None
    xs = [(day - recent[0][0]).days / 30.44 for day, _ in recent]
    ys = [value for _, value in recent]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread


def _traffic_entry(data: dict) -> tuple[str, dict] | None:
    """The primary domain's traffic when present, else the busiest domain."""
    traffic = get(data, "web_traffic", "domain_traffic")
    if not isinstance(traffic, dict):
        return None
    entries = {d: e for d, e in traffic.items() if isinstance(e, dict)}
    if not entries:
        return None
    primary = get(data, "basic_info", "primary_domain")
    if primary in entries:
        return primary, entries[primary]
    return max(entries.items(), key=lambda item: number(item[1].get("monthly_visitors")) or 0)


def _openings(data: dict) -> tuple[float, float | None, float | None] | None:
    count = number(get(data, "hiring", "openings_count"))
    if count is None:
        return None
    fraction = number(get(data, "hiring", "openings_growth_percent", "qoq"))
    qoq_pct = fraction * 100 if fraction is not None else None  # fraction -> percent
    return count, qoq_pct, _slope(_series(get(data, "hiring", "open_jobs_timeseries"), "open_jobs"))


def hiring_trend(claim: Claim | None, data: dict) -> CheckResult:
    name = "hiring_trend"
    evidence = _paths(("hiring", "openings_count"), ("hiring", "openings_growth_percent", "qoq"),
                      ("hiring", "open_jobs_timeseries"))
    found = _openings(data)
    if found is None:
        return unverifiable(name, claim, "Crustdata has no open-role data.", evidence)
    count, qoq_pct, slope = found
    metrics = [MetricResult("openings_count", count, "roles", "hiring.openings_count")]
    if qoq_pct is not None:
        metrics.append(MetricResult("openings_growth_qoq_pct", qoq_pct, "percent",
                                    "hiring.openings_growth_percent.qoq x 100 (stored as a fraction)"))
    if slope is not None:
        metrics.append(MetricResult("open_jobs_slope", slope, "roles/month",
                                    f"least-squares slope of the last {SLOPE_POINTS} open_jobs points"))
    trend = f"{count:.0f} open roles" + (f", {qoq_pct:+.0f}% QoQ" if qoq_pct is not None else "")
    claim_id = claim.id if claim else None

    def result(status, severity, note):
        return CheckResult(name, status, severity, f"{note} {NOISE_NOTE}", claim_id, evidence, tuple(metrics))

    claimed = number(claim.value) if claim and claim.unit in (None, "count") else None
    if claimed is not None and re.search(r"open|role|position|job|vacanc", claim.text, re.I):
        diff = abs(claimed - count)
        allowed = max(2.0, VOLUME_VERIFIED * count)
        if diff <= allowed:
            return result("VERIFIED", 1, f"Claimed {claimed:.0f} open roles vs Crustdata {count:.0f}.")
        if claimed > 2 * count and diff > 4:
            return result("CONTRADICTED", 3, f"Claimed {claimed:.0f} open roles; Crustdata shows {count:.0f}.")
        return result("PARTIAL", 2, f"Claimed {claimed:.0f} open roles vs Crustdata {count:.0f}.")

    healthy = count >= HEALTHY_OPENINGS and (qoq_pct is None or qoq_pct >= HEALTHY_OPENINGS_QOQ_PCT)
    if healthy:
        return result("VERIFIED", 1, f"Hiring looks active: {trend}.")
    if claim is not None and count <= 2 and (qoq_pct is not None and qoq_pct < 0):
        return result("CONTRADICTED", 3, f"Deck points to active hiring, but Crustdata shows {trend}.")
    return result("PARTIAL", 3 if count == 0 else 2, f"Hiring has slowed or is thin: {trend}.")


def traffic_trend(claim: Claim | None, data: dict) -> CheckResult:
    name = "traffic_trend"
    evidence = _paths(("web_traffic", "domain_traffic"))
    entry = _traffic_entry(data)
    if entry is None:
        return unverifiable(name, claim, "Crustdata has no web traffic data.", evidence)
    domain, traffic = entry
    visitors = number(traffic.get("monthly_visitors"))
    mom, qoq = number(traffic.get("mom_pct")), number(traffic.get("qoq_pct"))
    trend = qoq if qoq is not None else mom
    if trend is None:
        return unverifiable(name, claim, f"No traffic growth figures for {domain}.", evidence)
    search, direct, paid = (number(traffic.get(k)) for k in
                            ("source_search_pct", "source_direct_pct", "source_paid_referral_pct"))
    organic = search + direct if search is not None and direct is not None else None
    metrics = [MetricResult("monthly_visitors", visitors, "visitors", f"web_traffic {domain}"),
               MetricResult("traffic_mom_pct", mom, "percent", "domain_traffic.mom_pct"),
               MetricResult("traffic_qoq_pct", qoq, "percent", "domain_traffic.qoq_pct"),
               MetricResult("organic_traffic_share", organic, "percent", "source_search_pct + source_direct_pct"),
               MetricResult("paid_referral_share", paid, "percent", "source_paid_referral_pct")]
    paid_note = (f" Paid referrals are {paid:.0f}% of traffic." if paid is not None and paid > PAID_SHARE_NOTE_PCT
                 else "")
    detail = f"{domain}: {visitors:,.0f} monthly visitors, {trend:+.0f}% over the period." if visitors \
        else f"{domain}: {trend:+.0f}% over the period."
    claim_id = claim.id if claim else None
    if trend >= TRAFFIC_FALL_PCT:
        return CheckResult(name, "VERIFIED", 1, detail + paid_note, claim_id, evidence, tuple(metrics))
    severity = 3 if trend < TRAFFIC_SHARP_FALL_PCT else 2
    return CheckResult(name, "PARTIAL", severity, f"Traffic is falling. {detail}{paid_note}", claim_id,
                       evidence, tuple(metrics))


def follower_trend(claim: Claim | None, data: dict) -> CheckResult:
    name = "follower_trend"
    evidence = _paths(("followers", "count"), ("followers", "yoy_percent"))
    count = number(get(data, "followers", "count"))
    if count is None:
        return unverifiable(name, claim, "Crustdata has no follower data.", evidence)
    yoy = number(get(data, "followers", "yoy_percent"))
    half = number(get(data, "followers", "six_months_growth_percent"))
    trend = yoy if yoy is not None else half
    metrics = (MetricResult("follower_count", count, "followers", "followers.count"),
               MetricResult("follower_qoq_pct", number(get(data, "followers", "qoq_percent")), "percent",
                            "followers.qoq_percent"),
               MetricResult("follower_yoy_pct", yoy, "percent", "followers.yoy_percent"))
    claim_id = claim.id if claim else None
    if trend is None:
        return unverifiable(name, claim, "No follower growth figures.", evidence)
    if trend < 0:
        return CheckResult(name, "PARTIAL", 2, f"Audience is shrinking: {count:,.0f} followers, "
                           f"{trend:+.0f}% over the period.", claim_id, evidence, metrics)
    return CheckResult(name, "VERIFIED", 1, f"{count:,.0f} followers, {trend:+.0f}% over the period.",
                       claim_id, evidence, metrics)


def _compare_growth(name: str, claim: Claim, claimed: float, actual: float, label: str,
                    evidence: tuple[str, ...], months: int) -> CheckResult:
    metric = MetricResult(f"{label}_growth_{months}m_pct", actual, "percent", f"Crustdata {label}")
    gap = claimed - actual
    detail = f"Claimed {claimed:.0f}% {label} growth over {months} months; Crustdata shows {actual:.0f}%."
    if abs(gap) <= GROWTH_VERIFIED_PP:
        return CheckResult(name, "VERIFIED", 1, detail, claim.id, evidence, (metric,))
    if gap < 0 or abs(gap) <= GROWTH_PARTIAL_PP:
        side = "Deck understates growth." if gap < 0 else f"Deck overstates by {gap:.0f} points."
        return CheckResult(name, "PARTIAL", 2, f"{detail} {side}", claim.id, evidence, (metric,))
    return CheckResult(name, "CONTRADICTED", 4 if gap > 100 else 3, f"{detail} Deck overstates growth.",
                       claim.id, evidence, (metric,))


def _traffic_growth(data: dict, months: int) -> float | None:
    entry = _traffic_entry(data)
    if entry is None:
        return None
    _, traffic = entry
    key = TRAFFIC_KEYS.get(months)
    if key and number(traffic.get(key)) is not None:
        return number(traffic.get(key))
    series = growth_from_series(_series(traffic.get("monthly_visitors_timeseries"), "monthly_visitors"), months)
    return series[0] if series else None


def _follower_growth(data: dict, months: int) -> float | None:
    key = FOLLOWER_KEYS.get(months)
    if key and number(get(data, "followers", key)) is not None:
        return number(get(data, "followers", key))
    series = growth_from_series(_series(get(data, "followers", "timeseries"), "follower_count"), months)
    return series[0] if series else None


def check_traction_claim(claim: Claim | None, data: dict) -> CheckResult:
    name = "traction_claim"
    evidence = _paths(("web_traffic", "domain_traffic"), ("followers",), ("hiring",))
    if claim is None:
        return unverifiable(name, claim, "No traction claim to check.", evidence)
    if claim.topic == "hiring":
        return hiring_trend(claim, data)
    if claim.topic == "traction_customers":
        return unverifiable(name, claim, "Crustdata has no customer counts, so this cannot be verified.",
                            evidence)
    claimed = number(claim.value)
    text = claim.text.lower()
    if claimed is None:
        return unverifiable(name, claim, "Claim has no number to compare.", evidence)

    if claim.unit == "percent":
        months = window_months(claim.period)
        if months is None:
            return unverifiable(name, claim, "Growth claim states no time basis.", evidence)
        if re.search(r"traffic|visitor|website|\bsite\b", text):
            actual, label = _traffic_growth(data, months), "web traffic"
            evidence = _paths(("web_traffic", "domain_traffic"))
        elif re.search(r"follower|audience|social|linkedin", text):
            actual, label = _follower_growth(data, months), "follower"
            evidence = _paths(("followers",))
        else:
            return unverifiable(name, claim, "Cannot tell which metric the growth claim refers to.", evidence)
        if actual is None:
            return unverifiable(name, claim, f"No {months}-month {label} history in Crustdata.", evidence)
        return _compare_growth(name, claim, claimed, actual, label.replace(" ", "_"), evidence, months)

    if claim.unit in (None, "count") and re.search(r"visitor|traffic", text):
        entry = _traffic_entry(data)
        visitors = number(entry[1].get("monthly_visitors")) if entry else None
        evidence = _paths(("web_traffic", "domain_traffic"))
        if not visitors or visitors <= 0:
            return unverifiable(name, claim, "Crustdata has no monthly visitor count.", evidence)
        ratio = claimed / visitors
        metric = MetricResult("visitors_claim_ratio", ratio, "ratio", "claimed / monthly_visitors")
        detail = f"Claimed {claimed:,.0f} visitors vs Crustdata's {visitors:,.0f} per month."
        if abs(ratio - 1) <= VOLUME_VERIFIED:
            return CheckResult(name, "VERIFIED", 1, detail, claim.id, evidence, (metric,))
        if ratio > 2:
            return CheckResult(name, "CONTRADICTED", 4 if ratio > 5 else 3, f"{detail} Deck overstates.",
                               claim.id, evidence, (metric,))
        return CheckResult(name, "PARTIAL", 2, detail, claim.id, evidence, (metric,))
    return unverifiable(name, claim, "User counts are not in Crustdata; web traffic is only a proxy.", evidence)
