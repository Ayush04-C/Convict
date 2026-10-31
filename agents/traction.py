"""Daksh: the real Traction and Momentum specialist (checks live in agents/analysts/traction.py)."""
from agents.analysts import traction as checks
from agents.engine import AnalystAgent


class TractionAgent(AnalystAgent):
    name = "traction"
    title = "Traction and Momentum analyst"
    role = ("You verify momentum: hiring activity, web traffic and its quality, audience growth, and "
            "traction claims. Customer and user counts are not in the data, so those stay unverified. "
            "You see hiring, web traffic and follower data, not financials.")
    topic_checks = {
        "traction_users": checks.check_traction_claim,
        "traction_customers": checks.check_traction_claim,
        "hiring": checks.check_traction_claim,
    }
    flag_checks = (
        (checks.traffic_trend, None, None),
        (checks.follower_trend, None, None),
        (checks.hiring_trend, None, "hiring"),
    )
