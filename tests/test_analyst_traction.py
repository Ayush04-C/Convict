"""Traction analyst. SYNTHETIC data shaped like Crustdata company_data, including its mixed
units: hiring growth is a FRACTION, traffic and follower growth are PERCENT."""
import pytest

from agents.analysts import traction
from app.schemas import Claim


def claim(value=None, unit=None, topic="traction_users", text="claim", period=None, **kw):
    return Claim(id="C7", topic=topic, text=text, value=value, unit=unit, period=period, slide=3, **kw)


def hiring(count=8, qoq=0.2, jobs=None):
    group = {"openings_count": count, "openings_growth_percent": {"mom": 0.1, "qoq": qoq, "yoy": 1.0}}
    if jobs is not None:
        group["open_jobs_timeseries"] = [
            {"date": f"2026-{m:02d}-01T00:00:00.000Z", "open_jobs": n} for m, n in enumerate(jobs, 1)]
    return {"hiring": group}


def traffic(domains=None, primary="a.com"):
    domains = domains or {"a.com": {"monthly_visitors": 1_000_000.0, "mom_pct": 2.0, "qoq_pct": 25.0,
                                    "source_search_pct": 30.0, "source_direct_pct": 50.0,
                                    "source_paid_referral_pct": 5.0}}
    return {"basic_info": {"primary_domain": primary}, "web_traffic": {"domain_traffic": domains}}


# ---- hiring ----------------------------------------------------------------------------

def test_hiring_growth_fraction_is_converted_to_percent():
    result = traction.hiring_trend(None, hiring(count=1, qoq=-0.9285714285714286))
    metrics = {m.name: m.value for m in result.metrics}
    assert metrics["openings_growth_qoq_pct"] == pytest.approx(-92.857, abs=0.001)  # not -0.93
    assert "fraction" in next(m.formula for m in result.metrics if m.name == "openings_growth_qoq_pct")


@pytest.mark.parametrize("count, qoq, status, severity", [
    (8, 0.2, "VERIFIED", 1), (8, -0.25, "VERIFIED", 1),   # -25% is the edge of healthy
    (8, -0.30, "PARTIAL", 2), (3, 0.0, "VERIFIED", 1), (2, 0.5, "PARTIAL", 2), (0, 0.0, "PARTIAL", 3),
])
def test_hiring_flag_without_a_claim(count, qoq, status, severity):
    result = traction.hiring_trend(None, hiring(count, qoq))
    assert (result.status, result.severity) == (status, severity) and result.claim_id is None


def test_a_hiring_claim_is_contradicted_when_openings_collapsed():
    aggressive = claim(None, None, "hiring", "We are hiring aggressively")
    result = traction.hiring_trend(aggressive, hiring(count=1, qoq=-0.93))
    assert (result.status, result.severity, result.claim_id) == ("CONTRADICTED", 3, "C7")
    assert "noisy" in result.note
    still_flat = traction.hiring_trend(aggressive, hiring(count=1, qoq=0.0))
    assert still_flat.status == "PARTIAL"   # thin but not falling: not a contradiction


@pytest.mark.parametrize("claimed, actual, status, severity", [
    (10, 8, "VERIFIED", 1), (8, 8, "VERIFIED", 1), (4, 8, "PARTIAL", 2),
    (10, 4, "CONTRADICTED", 3), (8, 3, "CONTRADICTED", 3), (5, 4, "VERIFIED", 1),
])
def test_numeric_open_role_claims(claimed, actual, status, severity):
    the_claim = claim(float(claimed), "count", "hiring", "We have open roles across teams")
    result = traction.hiring_trend(the_claim, hiring(count=actual, qoq=0.0))
    assert (result.status, result.severity) == (status, severity)


def test_open_jobs_slope_follows_the_series():
    rising = traction.hiring_trend(None, hiring(12, 0.1, jobs=[2, 4, 6, 8, 10, 12]))
    falling = traction.hiring_trend(None, hiring(2, -0.1, jobs=[12, 10, 8, 6, 4, 2]))
    slope = lambda r: next(m.value for m in r.metrics if m.name == "open_jobs_slope")  # noqa: E731
    assert slope(rising) == pytest.approx(2.0, abs=0.1) and slope(falling) == pytest.approx(-2.0, abs=0.1)
    short = traction.hiring_trend(None, hiring(5, 0.0, jobs=[1, 2]))
    assert "open_jobs_slope" not in {m.name for m in short.metrics}


@pytest.mark.parametrize("data", [{}, {"hiring": None}, {"hiring": {"openings_count": None}}])
def test_hiring_unverifiable_without_data(data):
    assert traction.hiring_trend(None, data).status == "UNVERIFIABLE"


# ---- web traffic -----------------------------------------------------------------------

def test_traffic_uses_the_primary_domain_not_the_busiest():
    domains = {"big.com": {"monthly_visitors": 9e6, "qoq_pct": 50.0},
               "small.com": {"monthly_visitors": 1e5, "qoq_pct": -40.0}}
    primary = traction.traffic_trend(None, traffic(domains, primary="small.com"))
    assert "small.com" in primary.note and primary.severity == 3
    fallback = traction.traffic_trend(None, traffic(domains, primary="missing.com"))
    assert "big.com" in fallback.note and fallback.status == "VERIFIED"


@pytest.mark.parametrize("qoq, status, severity", [
    (25.0, "VERIFIED", 1), (-10.0, "VERIFIED", 1), (-10.1, "PARTIAL", 2),
    (-23.33, "PARTIAL", 2), (-30.0, "PARTIAL", 2), (-30.1, "PARTIAL", 3),
])
def test_traffic_trend_levels(qoq, status, severity):
    data = traffic({"a.com": {"monthly_visitors": 1e6, "qoq_pct": qoq}})
    result = traction.traffic_trend(None, data)
    assert (result.status, result.severity) == (status, severity)


def test_traffic_falls_back_to_month_over_month_and_reports_quality_metrics():
    only_mom = traction.traffic_trend(None, traffic({"a.com": {"monthly_visitors": 5e5, "mom_pct": -50.0}}))
    assert only_mom.status == "PARTIAL" and only_mom.severity == 3
    full = traction.traffic_trend(None, traffic())
    metrics = {m.name: m.value for m in full.metrics}
    assert metrics["organic_traffic_share"] == 80.0 and metrics["paid_referral_share"] == 5.0
    paid_heavy = traction.traffic_trend(None, traffic({"a.com": {
        "monthly_visitors": 1e6, "qoq_pct": 5.0, "source_paid_referral_pct": 40.0}}))
    assert "Paid referrals are 40%" in paid_heavy.note


@pytest.mark.parametrize("data", [{}, {"web_traffic": None}, {"web_traffic": {"domain_traffic": {}}},
                                  traffic({"a.com": {"monthly_visitors": 1e6}}), traffic({"a.com": "bad"})])
def test_traffic_unverifiable_without_growth_data(data):
    assert traction.traffic_trend(None, data).status == "UNVERIFIABLE"


# ---- followers -------------------------------------------------------------------------

def test_follower_trend():
    growing = traction.follower_trend(None, {"followers": {"count": 43706, "qoq_percent": 3.9,
                                                           "yoy_percent": 22.8}})
    assert growing.status == "VERIFIED" and growing.metrics[0].value == 43706
    shrinking = traction.follower_trend(None, {"followers": {"count": 900, "yoy_percent": -12.0}})
    assert (shrinking.status, shrinking.severity) == ("PARTIAL", 2)
    half_year = traction.follower_trend(None, {"followers": {"count": 900, "six_months_growth_percent": -3.0}})
    assert half_year.status == "PARTIAL"


@pytest.mark.parametrize("data", [{}, {"followers": {"qoq_percent": 3.0}}, {"followers": {"count": 100}}])
def test_follower_trend_unverifiable(data):
    assert traction.follower_trend(None, data).status == "UNVERIFIABLE"


# ---- claims ----------------------------------------------------------------------------

def traffic_claim(value, period="QoQ", text="Website traffic grew"):
    return claim(float(value), "percent", text=text, period=period)


@pytest.mark.parametrize("claimed, status, severity", [
    (25, "VERIFIED", 1), (40, "VERIFIED", 1), (50, "PARTIAL", 2), (55, "PARTIAL", 2),
    (56, "CONTRADICTED", 3), (200, "CONTRADICTED", 4), (-10, "PARTIAL", 2),
])
def test_traffic_growth_claims_against_qoq(claimed, status, severity):
    result = traction.check_traction_claim(traffic_claim(claimed), traffic())
    assert (result.status, result.severity) == (status, severity)
    assert result.claim_id == "C7" and result.metrics[0].name == "web_traffic_growth_3m_pct"


def test_twelve_month_traffic_growth_uses_the_visitor_series():
    series = [{"date": "2025-09-01T00:00:00+00:00", "monthly_visitors": 100_000},
              {"date": "2026-03-01T00:00:00+00:00", "monthly_visitors": 150_000},
              {"date": "2026-09-01T00:00:00+00:00", "monthly_visitors": 200_000}]
    data = traffic({"a.com": {"monthly_visitors": 200_000.0, "monthly_visitors_timeseries": series}})
    assert traction.check_traction_claim(traffic_claim(100, "YoY"), data).status == "VERIFIED"
    assert traction.check_traction_claim(traffic_claim(400, "YoY"), data).status == "CONTRADICTED"


def test_growth_notes_say_which_way_the_deck_is_off():
    over = traction.check_traction_claim(traffic_claim(50), traffic())    # +25% actual, claims +50%
    under = traction.check_traction_claim(traffic_claim(-10), traffic())
    assert "overstates by 25 points" in over.note and "understates" not in over.note
    assert "understates growth" in under.note and "overstates" not in under.note


def test_follower_growth_claims():
    data = {"followers": {"count": 43706, "yoy_percent": 22.8, "qoq_percent": 3.9}}
    ok = traction.check_traction_claim(claim(25.0, "percent", text="Followers grew 25% YoY", period="YoY"), data)
    assert ok.status == "VERIFIED"
    bad = traction.check_traction_claim(claim(120.0, "percent", text="Audience up 120% YoY", period="YoY"), data)
    assert bad.status == "CONTRADICTED"


@pytest.mark.parametrize("the_claim", [
    claim(100.0, "percent", text="Users grew 100% YoY", period="YoY"),        # which metric?
    claim(100.0, "percent", text="Traffic grew 100%"),                         # no time basis
    claim(100.0, "percent", text="Traffic grew 100% over the sometime", period="sometime"),
    claim(500.0, "count", "traction_customers", "We serve 500 customers"),     # no customer data
    claim(50_000.0, "count", text="We have 50,000 users"),                     # users are not visitors
    claim("many", None, text="Lots of traffic"), None,
])
def test_claims_that_cannot_be_checked_are_unverifiable(the_claim):
    assert traction.check_traction_claim(the_claim, traffic()).status == "UNVERIFIABLE"


@pytest.mark.parametrize("claimed, status, severity", [
    (1_200_000, "VERIFIED", 1), (1_250_000, "VERIFIED", 1), (1_800_000, "PARTIAL", 2),
    (500_000, "PARTIAL", 2), (3_000_000, "CONTRADICTED", 3), (6_000_000, "CONTRADICTED", 4),
])
def test_monthly_visitor_claims(claimed, status, severity):
    data = traffic({"a.com": {"monthly_visitors": 1_000_000.0, "qoq_pct": 1.0}})
    the_claim = claim(float(claimed), "count", text="We get monthly visitors")
    result = traction.check_traction_claim(the_claim, data)
    assert (result.status, result.severity) == (status, severity)


def test_hiring_claims_are_delegated():
    the_claim = claim(None, None, "hiring", "Hiring aggressively")
    assert traction.check_traction_claim(the_claim, hiring(1, -0.9)).check == "hiring_trend"


# ---- null safety -----------------------------------------------------------------------

CHECKS = [traction.hiring_trend, traction.traffic_trend, traction.follower_trend,
          traction.check_traction_claim]


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("data", [{}, None, {"hiring": None, "web_traffic": None, "followers": None},
                                  {"hiring": "x", "web_traffic": 5, "followers": []},
                                  {"hiring": {"open_jobs_timeseries": "bad", "openings_count": "n/a"}},
                                  {"web_traffic": {"domain_traffic": {"a.com": {"qoq_pct": "fast"}}}}])
@pytest.mark.parametrize("the_claim", [None, claim(5.0), claim(5.0, "percent", period="QoQ", text="traffic")])
def test_no_check_raises_on_missing_or_malformed_data(check, data, the_claim):
    result = check(the_claim, data)
    assert result.status in ("VERIFIED", "PARTIAL", "CONTRADICTED", "UNVERIFIABLE")
    assert 1 <= result.severity <= 5
