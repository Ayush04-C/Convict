"""Daksh: implement null-safe pure-Python checks from low-level design section 8.

Each check takes its relevant claim (or None) and an isolated dossier slice.
Return structured finding/metric data; perform no HTTP or LLM calls here.
"""


def check_funding_total(claim, data):
    raise NotImplementedError("Daksh: check_funding_total; return UNVERIFIABLE on absent data")


def check_last_round(claim, data):
    raise NotImplementedError("Daksh: check_last_round; return UNVERIFIABLE on absent data")


def check_rounds_and_investors(claim, data):
    raise NotImplementedError("Daksh: check_rounds_and_investors; return UNVERIFIABLE on absent data")


def check_arr(claim, data):
    raise NotImplementedError("Daksh: check_arr; return UNVERIFIABLE on absent data")


def revenue_per_head(claim, data):
    raise NotImplementedError("Daksh: revenue_per_head; return UNVERIFIABLE on absent data")


def valuation_multiple(claim, data):
    raise NotImplementedError("Daksh: valuation_multiple; return UNVERIFIABLE on absent data")


def months_since_last_raise(claim, data):
    raise NotImplementedError("Daksh: months_since_last_raise; return UNVERIFIABLE on absent data")
