"""Daksh: implement null-safe pure-Python checks from low-level design section 8.

Each check takes its relevant claim (or None) and an isolated dossier slice.
Return structured finding/metric data; perform no HTTP or LLM calls here.
"""


def check_founding_year(claim, data):
    raise NotImplementedError("Daksh: check_founding_year; return UNVERIFIABLE on absent data")


def check_hq(claim, data):
    raise NotImplementedError("Daksh: check_hq; return UNVERIFIABLE on absent data")


def check_status_and_domain(claim, data):
    raise NotImplementedError("Daksh: check_status_and_domain; return UNVERIFIABLE on absent data")


def check_founders(claim, data):
    raise NotImplementedError("Daksh: check_founders; return UNVERIFIABLE on absent data")


def scan_news(claim, data):
    raise NotImplementedError("Daksh: scan_news; return UNVERIFIABLE on absent data")
