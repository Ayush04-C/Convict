"""Ayush: deterministic claim routing, including founder claims."""
from app.schemas import AgentName, ClaimSet, ContextPacket, Policy

PRIMARY: dict[str, AgentName] = {
    **dict.fromkeys(("identity_founding", "identity_hq", "identity_founder"), "identity"),
    **dict.fromkeys(("team_size", "team_growth"), "scale_team"),
    **dict.fromkeys(("funding_raised", "funding_round", "investors", "revenue_arr",
                     "revenue_growth", "valuation_ask"), "funding"),
    **dict.fromkeys(("traction_users", "traction_customers", "hiring"), "traction"),
}
SECONDARY: dict[str, tuple[AgentName, ...]] = {
    "investors": ("identity",), "revenue_arr": ("scale_team",),
    "revenue_growth": ("scale_team",), "valuation_ask": ("scale_team",),
    "traction_users": ("scale_team",), "traction_customers": ("scale_team",),
    "hiring": ("scale_team",),
}
AGENTS: tuple[AgentName, ...] = ("identity", "scale_team", "funding", "traction")


def build_context_packets(claims: ClaimSet, dossier_slices: dict[AgentName, str],
                          policy: Policy) -> tuple[ContextPacket, ...]:
    return tuple(ContextPacket(
        agent=agent,
        claims=tuple(c for c in claims.claims if PRIMARY.get(c.topic) == agent
                     or agent in SECONDARY.get(c.topic, ())),
        dossier_json=dossier_slices.get(agent, "{}"), policy=policy, is_stub=claims.is_stub,
    ) for agent in AGENTS)
