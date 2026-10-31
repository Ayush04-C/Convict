"""Confirm analyst proposals from same-run citations and the original company dossier."""

import json

from agents.analysts.funding import check_arr, check_last_round
from agents.analysts.identity import check_founders, check_status_and_domain
from app.schemas import ClaimSet, Dossier

RULES = {
    "arr_ratio_over_3": ("funding", "revenue_arr", check_arr),
    "funding_round_absent": ("funding", "funding_round", check_last_round),
    "founder_not_found": ("identity", "identity_founder", check_founders),
    "entity_not_active": ("identity", None, check_status_and_domain),
}


def field_value(data, path):
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


async def confirm_gates(
    repo, run_id: str, claims: ClaimSet, dossier: Dossier, successful_agents: set[str]
):
    data = json.loads(dossier.data_json)
    evidence = {e.evidence_id: e for e in await repo.get_evidence(run_id)}
    confirmed = set()
    for metric in await repo.get_metrics(run_id):
        name = metric.name.removeprefix("gate:") if metric.name.startswith("gate:") else ""
        if (
            name not in RULES
            or metric.value != 1
            or metric.unit != "proposal"
            or not metric.evidence_ids
        ):
            continue
        owner, topic, rule = RULES[name]
        if metric.agent != owner or owner not in successful_agents:
            continue
        cited = [evidence.get(i) for i in metric.evidence_ids]
        if any(
            e is None or e.agent != owner or e.source != "crustdata.company.enrich" for e in cited
        ):
            continue
        # Validate every citation against the trusted coordinator dossier, not agent text.
        paths = set()
        for item in cited:
            value = field_value(data, item.field_path)
            if value is not None and json.loads(item.value_json) == value:
                paths.add(item.field_path)
        relevant = (
            (None,)
            if topic is None
            else tuple(c for c in claims.claims if c.topic == topic and c.testable)
        )
        for claim in relevant:
            result = rule(claim, data)
            required = {p for p in result.evidence_paths if field_value(data, p) is not None}
            if result.gate == name and required and required <= paths:
                confirmed.add(name)
    return tuple(sorted(confirmed))
