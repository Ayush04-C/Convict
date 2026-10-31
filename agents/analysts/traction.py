"""Daksh: implement null-safe pure-Python checks from low-level design section 8.

Each check takes its relevant claim (or None) and an isolated dossier slice.
Return structured finding/metric data; perform no HTTP or LLM calls here.
"""


def hiring_trend(claim, data):
    raise NotImplementedError("Daksh: hiring_trend; return UNVERIFIABLE on absent data")


def traffic_trend(claim, data):
    raise NotImplementedError("Daksh: traffic_trend; return UNVERIFIABLE on absent data")


def follower_trend(claim, data):
    raise NotImplementedError("Daksh: follower_trend; return UNVERIFIABLE on absent data")


def check_traction_claim(claim, data):
    raise NotImplementedError("Daksh: check_traction_claim; return UNVERIFIABLE on absent data")
