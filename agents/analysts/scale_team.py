"""Daksh: implement null-safe pure-Python checks from low-level design section 8.

Each check takes its relevant claim (or None) and an isolated dossier slice.
Return structured finding/metric data; perform no HTTP or LLM calls here.
"""


def check_team_size(claim, data):
    raise NotImplementedError("Daksh: check_team_size; return UNVERIFIABLE on absent data")


def compute_growth(claim, data):
    raise NotImplementedError("Daksh: compute_growth; return UNVERIFIABLE on absent data")


def function_mix(claim, data):
    raise NotImplementedError("Daksh: function_mix; return UNVERIFIABLE on absent data")


def detect_layoffs(claim, data):
    raise NotImplementedError("Daksh: detect_layoffs; return UNVERIFIABLE on absent data")


def culture_snapshot(claim, data):
    raise NotImplementedError("Daksh: culture_snapshot; return UNVERIFIABLE on absent data")
