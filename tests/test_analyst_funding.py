"""Funding analyst: totals, rounds, investors, plausibility flags. SYNTHETIC data only,
shaped like Crustdata company_data (funding.milestones, revenue.estimated, headcount.total)."""
from datetime import date

import pytest

from agents.analysts import funding
from app.schemas import Claim


def claim(value=None, unit=None, topic="funding_raised", text="claim", **kw):
    return Claim(id="C9", topic=topic, text=text, value=value, unit=unit, slide=2, **kw)


def funding_data(**overrides):
    base = {
        "total_investment_usd": 141_000_000.0, "last_round_amount_usd": 45_000_000.0,
        "last_round_type": "series_c", "last_fundraise_date": "2022-07-27",
        "num_funding_rounds": 3,
        "investors": ["Sequoia Capital", "Accel", "Alex Solomon"],
        "milestones": [
            {"round_type": "seed", "amount_usd": 5_000_000, "investors": ["Accel"],
             "lead_investors": ["Accel"]},
            {"round_type": "series_a", "amount_usd": 20_000_000, "investors": ["Sequoia Capital"],
             "lead_investors": []},
            {"round_type": "series_c", "amount_usd": 45_000_000, "investors": ["Sequoia Capital"],
             "lead_investors": ["Sequoia Capital"]},
        ],
    }
    return {"funding": {**base, **overrides}}


# ---- total raised ----------------------------------------------------------------------

@pytest.mark.parametrize("claimed, status, severity", [
    (141_000_000, "VERIFIED", 1), (150_000_000, "VERIFIED", 1), (130_000_000, "VERIFIED", 1),
    (170_000_000, "PARTIAL", 2),     # +20%: inside the 25% allowance
    (100_000_000, "PARTIAL", 2),     # understated, may be a single round
    (190_000_000, "CONTRADICTED", 3),  # +35%
    (250_000_000, "CONTRADICTED", 4),  # +77%
])
def test_funding_total(claimed, status, severity):
    result = funding.check_funding_total(claim(float(claimed), "USD"), funding_data())
    assert (result.status, result.severity) == (status, severity)
    assert result.metrics[0].name == "funding_total_diff"


def test_funding_total_accepts_the_last_round_amount():
    result = funding.check_funding_total(claim(45_000_000.0, "USD"), funding_data())
    assert result.status == "VERIFIED" and "last round" in result.note


@pytest.mark.parametrize("data", [{}, funding_data(total_investment_usd=None),
                                  funding_data(total_investment_usd=0)])
def test_funding_total_unverifiable_without_a_total(data):
    assert funding.check_funding_total(claim(1e6, "USD"), data).status == "UNVERIFIABLE"


def test_funding_total_needs_a_usd_claim():
    assert funding.check_funding_total(claim(1e6, "INR"), funding_data()).status == "UNVERIFIABLE"


# ---- last round ------------------------------------------------------------------------

def test_last_round_match_by_value_or_text():
    assert funding.check_last_round(claim("Series C", topic="funding_round"), funding_data()).status == "VERIFIED"
    by_text = claim(None, topic="funding_round", text="We closed our Series C last year")
    assert funding.check_last_round(by_text, funding_data()).status == "VERIFIED"


def test_last_round_older_round_in_history_is_partial():
    result = funding.check_last_round(claim("Series A", topic="funding_round"), funding_data())
    assert (result.status, result.severity, result.gate) == ("PARTIAL", 2, None)


def test_last_round_absent_from_history_is_contradicted_and_proposes_a_gate():
    result = funding.check_last_round(claim("Series B", topic="funding_round"), funding_data())
    assert (result.status, result.severity, result.gate) == ("CONTRADICTED", 4, "funding_round_absent")


def test_last_round_without_history_compares_stage_order_and_proposes_no_gate():
    data = funding_data(milestones=[])
    ahead = funding.check_last_round(claim("Series D", topic="funding_round"), data)
    behind = funding.check_last_round(claim("Seed", topic="funding_round"), data)
    assert (ahead.status, ahead.gate) == ("CONTRADICTED", None)
    assert behind.status == "PARTIAL"


def test_last_round_date_mismatch_downgrades_a_type_match():
    result = funding.check_last_round(
        claim("Series C", topic="funding_round", as_of="2025"), funding_data())
    assert result.status == "PARTIAL" and "2022-07-27" in result.note


@pytest.mark.parametrize("the_claim, data", [
    (claim("something odd", topic="funding_round"), funding_data()),
    (claim("Series C", topic="funding_round"), funding_data(last_round_type=None)),
    (claim("Series C", topic="funding_round"), funding_data(last_round_type="grant")),
])
def test_last_round_unverifiable_cases(the_claim, data):
    assert funding.check_last_round(the_claim, data).status == "UNVERIFIABLE"


# ---- investors -------------------------------------------------------------------------

def test_investors_all_matched_with_fuzzy_names():
    result = funding.check_rounds_and_investors(
        claim(None, topic="investors", text="Backed by Sequoia and Accel Partners"), funding_data())
    assert result.status == "VERIFIED" and result.metrics[0].value == 1.0


def test_investors_partial_and_contradicted():
    partial = funding.check_rounds_and_investors(
        claim(None, topic="investors", text="Investors include Accel, Founders Fund"), funding_data())
    assert partial.status == "PARTIAL" and "Founders Fund" in partial.note
    none = funding.check_rounds_and_investors(
        claim(None, topic="investors", text="Backed by Tiger Global, SoftBank"), funding_data())
    assert (none.status, none.severity) == ("CONTRADICTED", 3) and "coverage" in none.note


def test_investors_unverifiable_without_names_or_data():
    assert funding.check_rounds_and_investors(
        claim(None, topic="investors", text="Backed by top-tier investors worldwide and many others "
                                            "across several continents"), funding_data()
    ).status == "UNVERIFIABLE"
    assert funding.check_rounds_and_investors(
        claim(None, topic="investors", text="Backed by Accel"), funding_data(investors=None, milestones=None)
    ).status == "UNVERIFIABLE"


@pytest.mark.parametrize("text", ["Backed by leading VCs", "Investors include several angels and others",
                                  "Backed by Tiger Global"])
def test_generic_phrases_are_not_treated_as_investor_names(text):
    result = funding.check_rounds_and_investors(claim(None, topic="investors", text=text), funding_data())
    # Generic descriptions are UNVERIFIABLE; a real name that is absent is judged on its own.
    expected = "CONTRADICTED" if "Tiger" in text else "UNVERIFIABLE"
    assert result.status == expected


def test_investor_names_are_read_from_dict_style_entries():
    data = funding_data(investors=[{"name": "Sequoia Capital"}], milestones=None)
    result = funding.check_rounds_and_investors(
        claim(None, topic="investors", text="Backed by Sequoia"), data)
    assert result.status == "VERIFIED"


# ---- plausibility flags ----------------------------------------------------------------

def plausibility_data(headcount, lower=2_000_000, upper=5_000_000):
    return {"headcount": {"total": headcount},
            "revenue": {"estimated": {"lower_bound_usd": lower, "upper_bound_usd": upper}}}


def test_revenue_per_head_uses_the_claim_when_given():
    inside = funding.revenue_per_head(claim(10_000_000.0, "USD", topic="revenue_arr"),
                                      plausibility_data(25))
    assert inside.status == "VERIFIED" and inside.metrics[0].value == pytest.approx(400_000)
    over = funding.revenue_per_head(claim(30_000_000.0, "USD", topic="revenue_arr"), plausibility_data(25))
    assert (over.status, over.severity, over.claim_id) == ("PARTIAL", 3, None)
    assert over.metrics[0].value == pytest.approx(1_200_000)
    under = funding.revenue_per_head(claim(100_000.0, "USD", topic="revenue_arr"), plausibility_data(25))
    assert under.status == "PARTIAL" and under.metrics[0].value == pytest.approx(4_000)


def test_revenue_per_head_falls_back_to_the_estimate_midpoint():
    result = funding.revenue_per_head(None, plausibility_data(35))
    assert result.status == "VERIFIED" and result.metrics[0].value == pytest.approx(100_000)
    assert "midpoint" in result.metrics[0].formula


@pytest.mark.parametrize("data", [plausibility_data(0), plausibility_data(None),
                                  plausibility_data(10, upper=0), {}])
def test_revenue_per_head_unverifiable(data):
    assert funding.revenue_per_head(None, data).status == "UNVERIFIABLE"


def test_valuation_multiple():
    ask = claim(300_000_000.0, "USD", topic="valuation_ask", text="Raising at a $300M valuation")
    high = funding.valuation_multiple(ask, plausibility_data(30))
    assert high.status == "PARTIAL" and high.metrics[0].value == pytest.approx(300 / 3.5)
    modest = funding.valuation_multiple(
        claim(20_000_000.0, "USD", topic="valuation_ask", text="valued at $20M"), plausibility_data(30))
    assert modest.status == "VERIFIED"


def test_valuation_multiple_ignores_raise_amounts():
    raise_amount = claim(5_000_000.0, "USD", topic="valuation_ask", text="Raising $5M")
    assert funding.valuation_multiple(raise_amount, plausibility_data(30)).status == "UNVERIFIABLE"


@pytest.mark.parametrize("today, status, severity", [
    (date(2024, 7, 27), "VERIFIED", 1),    # 24 months exactly
    (date(2024, 8, 28), "PARTIAL", 2),     # 25 months
    (date(2025, 8, 28), "PARTIAL", 3),     # 37 months
])
def test_months_since_last_raise(today, status, severity):
    result = funding.months_since_last_raise(None, funding_data(), today=today)
    assert (result.status, result.severity) == (status, severity)


def test_months_since_last_raise_future_or_missing_date():
    assert funding.months_since_last_raise(None, funding_data(), today=date(2020, 1, 1)).status == "UNVERIFIABLE"
    assert funding.months_since_last_raise(None, funding_data(last_fundraise_date=None)).status == "UNVERIFIABLE"
    assert funding.months_since_last_raise(None, funding_data(last_fundraise_date="soon")).status == "UNVERIFIABLE"
