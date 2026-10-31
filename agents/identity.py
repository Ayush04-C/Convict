"""Daksh: the real Identity and Integrity specialist (checks live in agents/analysts/identity.py)."""
from agents.analysts import identity as checks
from agents.engine import AnalystAgent


class IdentityAgent(AnalystAgent):
    name = "identity"
    title = "Identity and Integrity analyst"
    role = ("You verify who the company is: founding year, headquarters, operating status, the named "
            "founders, and adverse news. You see company identity and people data, not financials.")
    topic_checks = {
        "identity_founding": checks.check_founding_year,
        "identity_hq": checks.check_hq,
        "identity_founder": checks.check_founders,
    }
    flag_checks = (
        (checks.check_status_and_domain, None, None),
        (checks.scan_news, None, None),
    )
