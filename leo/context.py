"""Deterministic routing and independent, finite-JSON specialist dossier slices."""

import json

from app.schemas import AgentName, ClaimSet, ContextPacket, Dossier, Policy
from crustdata.cache import json_text

PRIMARY: dict[str, AgentName] = {
    **dict.fromkeys(("identity_founding", "identity_hq", "identity_founder"), "identity"),
    **dict.fromkeys(("team_size", "team_growth"), "scale_team"),
    **dict.fromkeys(
        (
            "funding_raised",
            "funding_round",
            "investors",
            "revenue_arr",
            "revenue_growth",
            "valuation_ask",
        ),
        "funding",
    ),
    **dict.fromkeys(("traction_users", "traction_customers", "hiring"), "traction"),
}
SECONDARY: dict[str, tuple[AgentName, ...]] = {
    "investors": ("identity",),
    "revenue_arr": ("scale_team",),
    "revenue_growth": ("scale_team",),
    "valuation_ask": ("scale_team",),
    "traction_users": ("scale_team",),
    "traction_customers": ("scale_team",),
    "hiring": ("scale_team",),
}
AGENTS: tuple[AgentName, ...] = ("identity", "scale_team", "funding", "traction")
DOSSIER_GROUPS = {
    "identity": ("basic_info", "locations", "people", "news"),
    "scale_team": ("basic_info", "headcount", "employee_reviews"),
    "funding": ("basic_info", "funding", "revenue", "headcount"),
    "traction": ("basic_info", "hiring", "web_traffic", "followers"),
}


def json_object(text: str) -> dict:
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError
        json_text(value)
    except (ValueError, TypeError):
        raise ValueError("Dossier slices must contain finite JSON objects.") from None
    return value


def slice_dossier(dossier: Dossier) -> dict[AgentName, str]:
    data = json_object(dossier.data_json)
    return {
        agent: json_text({key: data[key] for key in groups if key in data})
        for agent, groups in DOSSIER_GROUPS.items()
    }


def build_context_packets(
    claims: ClaimSet, dossier_slices: dict[AgentName, str] | Dossier, policy: Policy
) -> tuple[ContextPacket, ...]:
    is_stub = claims.is_stub
    if isinstance(dossier_slices, Dossier):
        is_stub = is_stub or dossier_slices.is_stub
        dossier_slices = slice_dossier(dossier_slices)
    if set(dossier_slices) - set(AGENTS):
        raise ValueError("Unknown specialist dossier slice.")
    slices = {}
    for agent in AGENTS:
        data = json_object(dossier_slices.get(agent, "{}"))
        if set(data) - set(DOSSIER_GROUPS[agent]):
            raise ValueError("Dossier slice contains groups outside this specialist's scope.")
        slices[agent] = json_text(data)
    ids = [claim.id for claim in claims.claims]
    if len(ids) != len(set(ids)):
        raise ValueError("Claim IDs must be unique.")
    return tuple(
        ContextPacket(
            agent=agent,
            claims=tuple(
                c.model_copy(update={"testable": False}) if c.topic == "product_other" else c
                for c in claims.claims
                if PRIMARY.get(c.topic) == agent
                or agent in SECONDARY.get(c.topic, ())
                or c.topic == "product_other"
            ),
            dossier_json=slices[agent],
            policy=policy,
            is_stub=is_stub,
        )
        for agent in AGENTS
    )
