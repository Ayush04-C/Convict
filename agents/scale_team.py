"""Daksh: the real Scale and Team specialist (checks live in agents/analysts/scale_team.py)."""
from agents.analysts import scale_team as checks
from agents.engine import AnalystAgent


class ScaleTeamAgent(AnalystAgent):
    name = "scale_team"
    title = "Scale and Team analyst"
    role = ("You verify the size and growth of the team and how employees rate the company: headcount "
            "against the deck, headcount growth, the mix of functions, layoffs, and employee reviews. "
            "You see headcount and review data, not financials or web traffic.")
    topic_checks = {
        "team_size": checks.check_team_size,
        "team_growth": checks.compute_growth,
    }
    flag_checks = (
        (checks.function_mix, None, None),
        (checks.detect_layoffs, None, None),
        (checks.culture_snapshot, None, None),
    )
