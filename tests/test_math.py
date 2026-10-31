"""Analyst helpers, ARR boundaries and null-safety. All data here is SYNTHETIC, shaped like
Crustdata company_data; no real provider response is used or implied."""
import math

import pytest

from agents.analysts import funding
from agents.analysts.common import (
    CheckResult, analyst_confidence, claim_amount_usd, dimension_score, get, number, pct_diff,
    unverifiable,
)
from app.schemas import Claim


def claim(value, unit="USD", topic="revenue_arr", text="ARR", **kw):
    return Claim(id="C1", topic=topic, text=text, value=value, unit=unit, slide=1, **kw)


def revenue_data(lower=2_000_000, upper=5_000_000):
    return {"revenue": {"estimated": {"lower_bound_usd": lower, "upper_bound_usd": upper}}}


# ---- helpers ----------------------------------------------------------------------------

def test_get_is_null_safe():
    data = {"a": {"b": {"c": 5}}, "n": None}
    assert get(data, "a", "b", "c") == 5
    assert get(data, "a", "x", "c") is None
    assert get(data, "n", "c") is None
    assert get(None, "a") is None
    assert get("text", "a") is None


@pytest.mark.parametrize("value, expected", [
    (5, 5.0), (2.5, 2.5), ("1,000", 1000.0), (" 7 ", 7.0), (True, None), (None, None),
    ("abc", None), (float("nan"), None), (float("inf"), None), ([], None),
])
def test_number(value, expected):
    assert number(value) == expected


def test_claim_amount_usd_requires_usd_and_a_number():
    assert claim_amount_usd(claim(10.0)) == 10.0
    assert claim_amount_usd(claim(10.0, unit="INR")) is None
    assert claim_amount_usd(claim(10.0, unit=None)) is None
    assert claim_amount_usd(claim("ten")) is None
    assert claim_amount_usd(None) is None


def test_pct_diff():
    assert pct_diff(110, 100) == pytest.approx(0.10)
    assert pct_diff(50, 100) == pytest.approx(-0.5)
    assert pct_diff(10, 0) is None and pct_diff(10, -5) is None and pct_diff(10, None) is None


def test_dimension_score_penalizes_only_contradicted_findings():
    def r(status, severity):
        return CheckResult("c", status, severity)
    assert dimension_score([]) == 100
    assert dimension_score([r("VERIFIED", 1), r("PARTIAL", 3), r("UNVERIFIABLE", 5)]) == 100
    assert dimension_score([r("CONTRADICTED", 5)]) == 100 - 24
    assert dimension_score([r("CONTRADICTED", 5)] * 4) == 4
    assert dimension_score([r("CONTRADICTED", 5)] * 5) == 0  # floored, never negative


def test_analyst_confidence_is_the_verifiable_share():
    results = [CheckResult("a", "VERIFIED"), CheckResult("b", "UNVERIFIABLE"),
               CheckResult("c", "CONTRADICTED"), CheckResult("d", "UNVERIFIABLE")]
    assert analyst_confidence(results) == 0.5
    assert analyst_confidence([]) == 0.0


def test_unverifiable_helper_never_carries_severity_above_one():
    result = unverifiable("x", claim(1.0), "why", ("a.b",))
    assert (result.status, result.severity, result.claim_id, result.evidence_paths) == (
        "UNVERIFIABLE", 1, "C1", ("a.b",))


# ---- ARR boundaries (docs/contracts.md) -------------------------------------------------

@pytest.mark.parametrize("claimed, status, severity, gate", [
    (5_000_000, "VERIFIED", 1, None),        # ratio 1.0 exactly
    (1_000_000, "VERIFIED", 1, None),        # below the estimated range is not penalized
    (5_000_001, "PARTIAL", 2, None),         # just above 1.0
    (7_500_000, "PARTIAL", 2, None),         # ratio 1.5 exactly
    (7_500_001, "CONTRADICTED", 4, None),    # just above 1.5
    (15_000_000, "CONTRADICTED", 4, None),   # ratio 3.0 exactly: no gate
    (15_000_001, "CONTRADICTED", 5, "arr_ratio_over_3"),
    (50_000_000, "CONTRADICTED", 5, "arr_ratio_over_3"),
])
def test_arr_boundaries(claimed, status, severity, gate):
    result = funding.check_arr(claim(float(claimed)), revenue_data())
    assert (result.status, result.severity, result.gate) == (status, severity, gate)
    assert result.claim_id == "C1"
    assert result.metrics[0].name == "arr_ratio"
    assert "estimated" in result.note


def test_arr_ratio_metric_value():
    result = funding.check_arr(claim(10_000_000.0), revenue_data())
    assert result.metrics[0].value == pytest.approx(2.0)
    assert result.evidence_paths == ("revenue.estimated.lower_bound_usd",
                                     "revenue.estimated.upper_bound_usd")


@pytest.mark.parametrize("data", [
    {}, None, revenue_data(upper=0), revenue_data(upper=-5), revenue_data(upper=None),
    {"revenue": {"estimated": None}}, {"revenue": "n/a"},
])
def test_arr_without_a_usable_estimate_is_unverifiable(data):
    result = funding.check_arr(claim(1_000_000.0), data)
    assert result.status == "UNVERIFIABLE" and result.gate is None


@pytest.mark.parametrize("bad_claim", [
    claim(1_000_000.0, unit="INR"), claim(1_000_000.0, unit=None), claim("lots"), claim(None), None,
])
def test_arr_with_unusable_claim_is_unverifiable(bad_claim):
    assert funding.check_arr(bad_claim, revenue_data()).status == "UNVERIFIABLE"


def test_arr_never_proposes_a_gate_for_a_missing_denominator():
    assert funding.check_arr(claim(1e12), revenue_data(upper=0)).gate is None


# ---- null safety across every funding check --------------------------------------------

CHECKS = [funding.check_funding_total, funding.check_last_round, funding.check_rounds_and_investors,
          funding.check_arr, funding.revenue_per_head, funding.valuation_multiple,
          funding.months_since_last_raise]


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("data", [{}, None, {"funding": None, "revenue": None, "headcount": None},
                                  {"funding": {"milestones": "bad", "investors": 5}}])
@pytest.mark.parametrize("the_claim", [None, claim(1.0), claim("Series A", unit=None)])
def test_no_check_raises_on_missing_or_malformed_data(check, data, the_claim):
    result = check(the_claim, data)
    assert result.status in ("VERIFIED", "PARTIAL", "CONTRADICTED", "UNVERIFIABLE")
    assert 1 <= result.severity <= 5
    assert all(m.value is None or math.isfinite(m.value) for m in result.metrics)
