"""Daksh: the real Funding and Financials specialist (checks live in agents/analysts/funding.py)."""
from agents.analysts import funding as checks
from agents.engine import AnalystAgent


class FundingAgent(AnalystAgent):
    name = "funding"
    title = "Funding and Financials analyst"
    role = ("You verify funding and financial plausibility: money raised, funding rounds, investors, "
            "and revenue claims against Crustdata's ESTIMATED revenue range (never audited revenue). "
            "You see funding, revenue-estimate and headcount data; you never see EBITDA or profit.")
    topic_checks = {
        "funding_raised": checks.check_funding_total,
        "funding_round": checks.check_last_round,
        "investors": checks.check_rounds_and_investors,
        "revenue_arr": checks.check_arr,
        "valuation_ask": checks.valuation_multiple,
    }
    flag_checks = (
        (checks.revenue_per_head, "revenue_arr", None),
        (checks.months_since_last_raise, None, None),
    )
