"""Ayush-owned scoring policy: deterministic aggregation, no repeated analyst penalty."""

from app.schemas import ClaimSet, Decision, Finding, Policy, Verdict
from leo.context import AGENTS, PRIMARY, SECONDARY

CREDIT = {"VERIFIED": 1.0, "PARTIAL": 0.5, "CONTRADICTED": 0.0}
WEIGHTS = {"identity": 0.10, "scale_team": 0.10, "funding": 0.20, "traction": 0.25}
CREDIBILITY_WEIGHT = 0.35
CONFIDENCE_WEIGHTS = {"claims": 0.5, "agent_confidence": 0.3, "agents_succeeded": 0.2}
TIER_PERCENT = {"INVEST": 1.0, "INVEST_WITH_CONDITIONS": 0.5, "DIG_DEEPER": 0.0, "PASS": 0.0}
RESULT_FIELDS = {"total_score", "credibility", "confidence", "verdict", "check_size_usd", "gates"}


def materiality(topic: str) -> int:
    if "growth" in topic or topic.startswith("traction") or topic == "hiring":
        return 4
    if topic.startswith(("funding", "revenue")) or topic in (
        "team_size",
        "investors",
        "valuation_ask",
    ):
        return 5
    return 3 if topic.startswith("identity") else 2


def compute(
    claims: ClaimSet,
    findings: tuple[Finding, ...],
    verdicts: tuple[Verdict, ...],
    policy: Policy,
    *,
    confirmed_gates: tuple[str, ...] = (),
    has_profit_documents: bool = False,
) -> Decision:
    decision, _ = compute_with_breakdown(
        claims,
        findings,
        verdicts,
        policy,
        confirmed_gates=confirmed_gates,
        has_profit_documents=has_profit_documents,
    )
    return decision


def compute_with_breakdown(
    claims: ClaimSet,
    findings: tuple[Finding, ...],
    verdicts: tuple[Verdict, ...],
    policy: Policy,
    *,
    confirmed_gates: tuple[str, ...] = (),
    has_profit_documents: bool = False,
) -> tuple[Decision, dict]:
    # Revalidate immutable models too: model_copy/model_construct can bypass validators.
    claims = ClaimSet.model_validate(claims.model_dump(warnings=False))
    findings = tuple(Finding.model_validate(f.model_dump(warnings=False)) for f in findings)
    verdicts = tuple(Verdict.model_validate(v.model_dump(warnings=False)) for v in verdicts)
    policy = Policy.model_validate(policy.model_dump(warnings=False))
    if type(has_profit_documents) is not bool:
        raise ValueError("Profit-document confirmation must be a boolean.")
    if not isinstance(confirmed_gates, (tuple, list)) or any(
        not isinstance(gate, str) or not gate.strip() for gate in confirmed_gates
    ):
        raise ValueError("Confirmed gates must be nonempty names from a trusted coordinator.")
    confirmed_gates = tuple(dict.fromkeys(confirmed_gates))
    by_agent = {v.agent: v for v in verdicts}
    if len(by_agent) != len(verdicts):
        raise ValueError("Duplicate agent verdicts")
    if len({c.id for c in claims.claims}) != len(claims.claims):
        raise ValueError("Duplicate claim IDs")
    if len({f.finding_id for f in findings}) != len(findings):
        raise ValueError("Duplicate finding IDs")
    known_ids = {c.id for c in claims.claims}
    if any(f.claim_id is not None and f.claim_id not in known_ids for f in findings):
        raise ValueError("Finding refers to an unknown claim")

    eligible = [c for c in claims.claims if c.testable and c.topic in PRIMARY]
    numerator = denominator = verified_count = 0
    claim_rows = []
    for claim in eligible:
        citable = [
            f for f in findings if f.claim_id == claim.id and f.status in CREDIT and f.evidence_ids
        ]
        primary = [f for f in citable if f.agent == PRIMARY[claim.topic]]
        secondary = [f for f in citable if f.agent in SECONDARY.get(claim.topic, ())]
        selected = primary or secondary
        credit = min(CREDIT[f.status] for f in selected) if selected else None
        weight = materiality(claim.topic)
        if selected:
            numerator += weight * credit
            denominator += weight
            verified_count += 1
        claim_rows.append(
            {
                "claim_id": claim.id,
                "topic": claim.topic,
                "primary_agent": PRIMARY[claim.topic],
                "source": "primary" if primary else "secondary" if secondary else None,
                "finding_ids": sorted(f.finding_id for f in selected),
                "evidence_ids": sorted({eid for f in selected for eid in f.evidence_ids}),
                "credit": credit,
                "materiality": weight,
                "weighted_credit": weight * credit if credit is not None else None,
                "included_in_credibility": bool(selected),
            }
        )
    credibility = 100 * numerator / denominator if denominator else None
    share = verified_count / len(eligible) if eligible else 0
    confidence_parts = {
        "claims": CONFIDENCE_WEIGHTS["claims"] * share,
        "agent_confidence": CONFIDENCE_WEIGHTS["agent_confidence"]
        * sum(v.confidence for v in verdicts)
        / 4,
        "agents_succeeded": CONFIDENCE_WEIGHTS["agents_succeeded"] * len(verdicts) / 4,
    }
    confidence = sum(confidence_parts.values())
    dimension_rows = [
        {
            "agent": a,
            "weight": WEIGHTS[a],
            "available": a in by_agent,
            "score": by_agent[a].score if a in by_agent else 0,
            "confidence": by_agent[a].confidence if a in by_agent else 0,
            "contribution": WEIGHTS[a] * by_agent[a].score if a in by_agent else 0,
        }
        for a in AGENTS
    ]
    credibility_contribution = CREDIBILITY_WEIGHT * (credibility or 0)
    weighted_total = credibility_contribution + sum(row["contribution"] for row in dimension_rows)
    total, reasons, caps = weighted_total, [], []
    if len(verdicts) < 4:
        reasons.append("Missing agents contribute zero; weights are not redistributed.")
    if credibility is None:
        total, tier = 0, "DIG_DEEPER"
        reasons.append("No verifiable claims: no investment sizing.")
        caps.append("NO_VERIFIABLE_CLAIMS")
    elif total >= 80:
        tier = "INVEST"
    elif total >= 65:
        tier = "INVEST_WITH_CONDITIONS"
    elif total >= 50:
        tier = "DIG_DEEPER"
    else:
        tier = "PASS"
    initial_tier = tier
    if tier == "INVEST" and any((f.severity_final or f.severity) == 5 for f in findings):
        tier = "INVEST_WITH_CONDITIONS"
        reasons.append("Severity-5 finding caps the verdict.")
        caps.append("SEVERITY_5")
    if tier == "INVEST" and not has_profit_documents:
        tier = "INVEST_WITH_CONDITIONS"
        reasons.append("Must obtain founder EBITDA/profit documentation.")
        caps.append("MISSING_PROFIT_DOCUMENTS")
    if confirmed_gates:
        tier = "PASS"
        reasons.append("Confirmed hard gate forces PASS.")
        caps.append("CONFIRMED_HARD_GATE")
    decision = Decision(
        total_score=total,
        credibility=credibility,
        confidence=confidence,
        verdict=tier,
        check_size_usd=policy.max_check_usd * TIER_PERCENT[tier] * confidence,
        gates=confirmed_gates,
        reasons=tuple(reasons),
        is_stub=claims.is_stub
        or any(v.is_stub for v in verdicts)
        or any(f.is_stub for f in findings),
    )
    breakdown = {
        "schema_version": "1.0",
        "policy": "contracts-v1",
        "claim_credits": claim_rows,
        "excluded_claim_ids": [
            c.id for c in claims.claims if not c.testable or c.topic not in PRIMARY
        ],
        "eligible_claim_count": len(eligible),
        "verifiable_claim_count": verified_count,
        "credibility_numerator": numerator,
        "credibility_denominator": denominator,
        "credibility_weight": CREDIBILITY_WEIGHT,
        "credibility_contribution": credibility_contribution,
        "dimensions": dimension_rows,
        "weighted_total": weighted_total,
        "confidence_components": confidence_parts,
        "confidence_weights": CONFIDENCE_WEIGHTS.copy(),
        "initial_tier": initial_tier,
        "applied_caps": caps,
        "has_profit_documents": has_profit_documents,
        "max_check_usd": policy.max_check_usd,
        "tier_percentage": TIER_PERCENT[tier],
        "analyst_penalties_reapplied": False,
        "result": decision.model_dump(mode="json", include=RESULT_FIELDS),
    }
    return decision, breakdown
