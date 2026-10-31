"""Scale and Team analyst. SYNTHETIC data only, shaped like Crustdata company_data
(headcount.total / timeseries / by_role_percent, employee_reviews)."""
import pytest

from agents.analysts import scale_team
from app.schemas import Claim


def claim(value, unit="count", topic="team_size", period=None, **kw):
    return Claim(id="C5", topic=topic, text="claim", value=value, unit=unit, period=period, slide=2, **kw)


def series(*pairs):
    return {"headcount": {"timeseries": [{"date": d, "employee_count": n} for d, n in pairs]}}


DOUBLING = series(("2024-12-01", 50), ("2025-06-01", 75), ("2025-12-01", 100))


# ---- team size -------------------------------------------------------------------------

@pytest.mark.parametrize("claimed, status, severity", [
    (100, "VERIFIED", 1), (114, "VERIFIED", 1), (86, "VERIFIED", 1),
    (116, "PARTIAL", 2), (140, "PARTIAL", 2),       # +40% is the upper edge of PARTIAL
    (141, "CONTRADICTED", 3), (200, "CONTRADICTED", 3), (201, "CONTRADICTED", 4),
    (50, "PARTIAL", 2),                              # understated: not an exaggeration
])
def test_team_size_boundaries(claimed, status, severity):
    result = scale_team.check_team_size(claim(float(claimed)), {"headcount": {"total": 100}})
    assert (result.status, result.severity) == (status, severity)
    assert "undercount" in result.note and result.claim_id == "C5"


def test_small_teams_get_an_absolute_allowance():
    data = {"headcount": {"total": 8}}
    assert scale_team.check_team_size(claim(11.0), data).status == "VERIFIED"   # +3 people
    assert scale_team.check_team_size(claim(12.0), data).status == "CONTRADICTED"  # +50%, over slack


@pytest.mark.parametrize("the_claim, data", [
    (claim(100.0, unit="USD"), {"headcount": {"total": 100}}),
    (claim("many"), {"headcount": {"total": 100}}),
    (claim(0.0), {"headcount": {"total": 100}}),
    (claim(100.0), {"headcount": {"total": 0}}),
    (claim(100.0), {}), (None, {"headcount": {"total": 100}}),
])
def test_team_size_unverifiable(the_claim, data):
    assert scale_team.check_team_size(the_claim, data).status == "UNVERIFIABLE"


# ---- growth ----------------------------------------------------------------------------

@pytest.mark.parametrize("period, months", [
    ("YoY", 12), ("year over year", 12), ("QoQ", 3), ("MoM", 1), ("last 6 months", 6),
    ("6m", 6), ("2 years", 24), ("past 18 mo", 18), ("", None), (None, None), ("sometime", None),
])
def test_window_months(period, months):
    assert scale_team.window_months(period) == months


@pytest.mark.parametrize("claimed, status, severity", [
    (100, "VERIFIED", 1), (90, "VERIFIED", 1), (110, "VERIFIED", 1),
    (120, "PARTIAL", 2), (125, "PARTIAL", 2),
    (126, "CONTRADICTED", 3), (150, "CONTRADICTED", 3), (201, "CONTRADICTED", 4),
    (40, "PARTIAL", 2),   # deck understates growth
])
def test_growth_against_the_timeseries(claimed, status, severity):
    result = scale_team.compute_growth(claim(float(claimed), "percent", "team_growth", "YoY"), DOUBLING)
    assert (result.status, result.severity) == (status, severity)
    assert result.metrics[0].value == pytest.approx(100.0)
    assert result.metrics[0].name == "headcount_growth_12m_pct"


def test_growth_notes_say_which_way_the_deck_is_off():
    over = scale_team.compute_growth(claim(120.0, "percent", "team_growth", "YoY"), DOUBLING)
    under = scale_team.compute_growth(claim(40.0, "percent", "team_growth", "YoY"), DOUBLING)
    assert "overstates by 20 points" in over.note and "understates" not in over.note
    assert "understates growth" in under.note and "overstates" not in under.note


def test_growth_uses_the_requested_window():
    result = scale_team.compute_growth(claim(33.0, "percent", "team_growth", "last 6 months"), DOUBLING)
    assert result.status == "VERIFIED" and result.metrics[0].value == pytest.approx(33.33, abs=0.01)


def test_multiple_claims_are_converted_to_percent():
    result = scale_team.compute_growth(claim(2.0, "multiple", "team_growth", "YoY"), DOUBLING)
    assert result.status == "VERIFIED"  # 2x = +100%


def test_growth_falls_back_to_reported_percent_without_a_timeseries():
    data = {"headcount": {"growth_percent": {"yoy": 100.0}}}
    result = scale_team.compute_growth(claim(100.0, "percent", "team_growth", "YoY"), data)
    assert result.status == "VERIFIED" and "growth_percent.yoy" in result.metrics[0].formula


def test_growth_from_a_tiny_base_is_never_contradicted():
    data = series(("2024-12-01", 4), ("2025-12-01", 20))
    result = scale_team.compute_growth(claim(900.0, "percent", "team_growth", "YoY"), data)
    assert result.status == "PARTIAL" and "too small" in result.note


@pytest.mark.parametrize("the_claim, data", [
    (claim(100.0, "percent", "team_growth", None), DOUBLING),        # no time basis
    (claim(100.0, "USD", "team_growth", "YoY"), DOUBLING),           # wrong unit
    (claim("fast", "percent", "team_growth", "YoY"), DOUBLING),
    (claim(100.0, "percent", "team_growth", "YoY"), {}),             # no history at all
    (claim(100.0, "percent", "team_growth", "YoY"), series(("2025-12-01", 100))),
    (claim(100.0, "percent", "team_growth", "YoY"), series(("2025-11-01", 90), ("2025-12-01", 100))),
    (None, DOUBLING),
])
def test_growth_unverifiable(the_claim, data):
    assert scale_team.compute_growth(the_claim, data).status == "UNVERIFIABLE"


# ---- function mix ----------------------------------------------------------------------

def test_function_mix():
    healthy = scale_team.function_mix(None, {"headcount": {"total": 400, "by_role_percent": {
        "Engineering": 28.5, "Sales": 12.6}}})
    assert healthy.status == "VERIFIED"
    assert {m.name: m.value for m in healthy.metrics} == {"engineering_share": 28.5, "sales_share": 12.6}
    flagged = scale_team.function_mix(None, {"headcount": {"total": 60, "by_role_percent": {
        "Engineering": 3.0, "Sales": 70.0}}})
    assert flagged.status == "PARTIAL"
    tiny = scale_team.function_mix(None, {"headcount": {"total": 10, "by_role_percent": {"Engineering": 0.0}}})
    assert tiny.status == "VERIFIED"  # too small a team to judge


@pytest.mark.parametrize("data", [{}, {"headcount": {"by_role_percent": {}}},
                                  {"headcount": {"by_role_percent": {"Sales": 40.0}}}])
def test_function_mix_unverifiable(data):
    assert scale_team.function_mix(None, data).status == "UNVERIFIABLE"


# ---- layoffs ---------------------------------------------------------------------------

@pytest.mark.parametrize("after, status, severity", [
    (90, "VERIFIED", 1), (85, "VERIFIED", 1),     # exactly 15% is not above the threshold
    (80, "PARTIAL", 3), (70, "PARTIAL", 3),       # exactly 30% stays at severity 3
    (60, "PARTIAL", 4),
])
def test_layoff_signal(after, status, severity):
    data = series(("2025-06-01", 100), ("2025-07-01", after), ("2025-12-01", after))
    result = scale_team.detect_layoffs(None, data)
    assert (result.status, result.severity) == (status, severity)


def test_layoffs_older_than_a_year_are_ignored():
    data = series(("2023-01-01", 100), ("2023-02-01", 50), ("2025-11-01", 50), ("2025-12-01", 52))
    assert scale_team.detect_layoffs(None, data).status == "VERIFIED"


def test_layoff_needs_history():
    assert scale_team.detect_layoffs(None, series(("2025-12-01", 100))).status == "UNVERIFIABLE"
    assert scale_team.detect_layoffs(None, {}).status == "UNVERIFIABLE"


def test_bad_timeseries_entries_are_skipped():
    data = {"headcount": {"timeseries": [{"date": "not a date", "employee_count": 5}, "junk",
                                         {"date": "2025-11-01", "employee_count": None},
                                         {"date": "2025-10-01", "employee_count": 100},
                                         {"date": "2025-12-01", "employee_count": 99}]}}
    assert scale_team.detect_layoffs(None, data).status == "VERIFIED"


# ---- culture ---------------------------------------------------------------------------

def reviews(overall, count=131, **extra):
    return {"employee_reviews": {"overall_rating": {"rating": overall, "total_count": count}, **extra}}


@pytest.mark.parametrize("overall, status, severity", [
    (3.8, "VERIFIED", 1), (3.5, "VERIFIED", 1), (3.3, "PARTIAL", 2), (3.0, "PARTIAL", 2),
    (2.9, "PARTIAL", 3),
])
def test_culture_snapshot_levels(overall, status, severity):
    result = scale_team.culture_snapshot(None, reviews(overall))
    assert (result.status, result.severity) == (status, severity)


def test_culture_snapshot_reports_sub_ratings_when_present():
    result = scale_team.culture_snapshot(None, reviews(
        3.8, culture_and_values_rating=3.9, work_life_balance_rating=4.1, senior_management_rating=0))
    names = {m.name for m in result.metrics}
    assert {"overall_rating", "review_count", "culture_and_values_rating", "work_life_balance_rating"} <= names
    assert "senior_management_rating" not in names  # zero means missing in this feed


@pytest.mark.parametrize("data", [{}, reviews(None), reviews(7.0), reviews(3.8, count=9), reviews(3.8, count=None)])
def test_culture_snapshot_unverifiable(data):
    assert scale_team.culture_snapshot(None, data).status == "UNVERIFIABLE"


# ---- null safety -----------------------------------------------------------------------

CHECKS = [scale_team.check_team_size, scale_team.compute_growth, scale_team.function_mix,
          scale_team.detect_layoffs, scale_team.culture_snapshot]


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("data", [{}, None, {"headcount": None, "employee_reviews": None},
                                  {"headcount": {"timeseries": "bad", "by_role_percent": 3, "total": "x"}},
                                  {"employee_reviews": {"overall_rating": 4}}])
@pytest.mark.parametrize("the_claim", [None, claim(5.0), claim("x", "percent", "team_growth", "YoY")])
def test_no_check_raises_on_missing_or_malformed_data(check, data, the_claim):
    result = check(the_claim, data)
    assert result.status == "UNVERIFIABLE" or 1 <= result.severity <= 5
