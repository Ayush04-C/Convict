"""Ayush: deterministic aggregation; no LLM, no repeated severity penalty."""
from app.schemas import ClaimSet, Decision, Finding, Policy, Verdict
from leo.context import AGENTS, PRIMARY

CREDIT = {"VERIFIED": 1.0, "PARTIAL": 0.5, "CONTRADICTED": 0.0}
WEIGHTS = {"identity": 0.10, "scale_team": 0.10, "funding": 0.20, "traction": 0.25}


def materiality(topic: str) -> int:
    if "growth" in topic or topic.startswith("traction") or topic == "hiring":
        return 4
    if topic.startswith(("funding", "revenue")) or topic in ("team_size", "investors", "valuation_ask"):
        return 5
    return 3 if topic.startswith("identity") else 2


def compute(claims: ClaimSet, findings: tuple[Finding, ...], verdicts: tuple[Verdict, ...],
            policy: Policy, *, confirmed_gates: tuple[str, ...] = (),
            has_profit_documents: bool = False) -> Decision:
    by_agent = {v.agent: v for v in verdicts}
    if len(by_agent) != len(verdicts):
        raise ValueError("Duplicate agent verdicts")
    if len({c.id for c in claims.claims}) != len(claims.claims):
        raise ValueError("Duplicate claim IDs")
    known_ids = {c.id for c in claims.claims}
    if any(f.claim_id is not None and f.claim_id not in known_ids for f in findings):
        raise ValueError("Finding refers to an unknown claim")
    eligible = [c for c in claims.claims if c.testable]
    numerator = denominator = verified_count = 0
    for claim in eligible:
        candidates = [f for f in findings if f.claim_id == claim.id and f.status in CREDIT]
        if claim.topic not in PRIMARY:
            continue  # product_other has no agreed policy-backed verifier.
        primary = [f for f in candidates if f.agent == PRIMARY[claim.topic]]
        candidates = primary or candidates
        if candidates:
            credit = min(CREDIT[f.status] for f in candidates)
            weight = materiality(claim.topic)
            numerator += weight * credit
            denominator += weight
            verified_count += 1
    credibility = 100 * numerator / denominator if denominator else None
    share = verified_count / len(eligible) if eligible else 0
    confidence = (0.5 * share + 0.3 * sum(v.confidence for v in verdicts) / 4
                  + 0.2 * len(verdicts) / 4)
    total = 0.35 * (credibility or 0) + sum(
        WEIGHTS[a] * by_agent[a].score for a in AGENTS if a in by_agent
    )
    reasons = []
    if len(verdicts) < 4:
        reasons.append("Missing agents contribute zero; weights are not redistributed.")
    if credibility is None:
        total, tier = 0, "DIG_DEEPER"
        reasons.append("No verifiable claims: no investment sizing.")
    elif total >= 80:
        tier = "INVEST"
    elif total >= 65:
        tier = "INVEST_WITH_CONDITIONS"
    elif total >= 50:
        tier = "DIG_DEEPER"
    else:
        tier = "PASS"
    if tier == "INVEST" and any((f.severity_final or f.severity) == 5 for f in findings):
        tier = "INVEST_WITH_CONDITIONS"
        reasons.append("Severity-5 finding caps the verdict.")
    if tier == "INVEST" and not has_profit_documents:
        tier = "INVEST_WITH_CONDITIONS"
        reasons.append("Must obtain founder EBITDA/profit documentation.")
    if confirmed_gates:
        tier = "PASS"
        reasons.append("Confirmed hard gate forces PASS.")
    pct = {"INVEST": 1, "INVEST_WITH_CONDITIONS": 0.5, "DIG_DEEPER": 0, "PASS": 0}[tier]
    return Decision(
        total_score=total, credibility=credibility, confidence=confidence, verdict=tier,
        check_size_usd=policy.max_check_usd * pct * confidence,
        gates=confirmed_gates, reasons=tuple(reasons),
        is_stub=claims.is_stub or any(v.is_stub for v in verdicts) or any(f.is_stub for f in findings),
    )
