"""Policy regressions and auditable arithmetic; no changes to analyst dimension math."""

import pytest
from pydantic import ValidationError

from app.schemas import Claim, ClaimSet, Finding, Policy, Verdict
from leo.scoring import compute, compute_with_breakdown
from tests.test_scoring import case


def finding(ident, agent, claim="C1", status="VERIFIED", evidence=("E1",)):
    return Finding(
        finding_id=ident,
        agent=agent,
        claim_id=claim,
        status=status,
        evidence_ids=evidence,
        note="Synthetic citation",
    )


def test_only_the_declared_secondary_can_supply_fallback_credit():
    claims = ClaimSet(claims=(Claim(id="C1", topic="revenue_arr", text="ARR", slide=1),))
    inputs = (
        finding("F1", "funding", status="UNVERIFIABLE"),
        finding("F2", "scale_team", status="PARTIAL"),
        finding("F3", "identity"),
    )
    result, breakdown = compute_with_breakdown(claims, inputs, (), Policy(max_check_usd=0))
    assert result.credibility == 50
    assert breakdown["claim_credits"][0]["finding_ids"] == ["F2"]
    assert breakdown["claim_credits"][0]["source"] == "secondary"


def test_unassigned_agent_cannot_supply_claim_credit():
    claims, _, verdicts, policy = case()
    result = compute(claims, (finding("wrong", "funding"),), verdicts, policy)
    assert result.credibility is None and result.total_score == 0


def test_background_claims_do_not_change_confidence_or_score():
    claims, findings, verdicts, policy = case()
    background = Claim(id="C2", topic="product_other", text="Background only", slide=1)
    extra = claims.model_copy(update={"claims": (*claims.claims, background)})
    baseline = compute(claims, findings, verdicts, policy)
    result, breakdown = compute_with_breakdown(extra, findings, verdicts, policy)
    assert result == baseline and breakdown["excluded_claim_ids"] == ["C2"]


def test_unsupported_verified_finding_gets_no_credit():
    claims, _, verdicts, policy = case()
    result = compute(claims, (finding("F1", "scale_team", evidence=()),), verdicts, policy)
    assert result.credibility is None and result.check_size_usd == 0


def test_unverifiable_claim_is_excluded_from_credit_but_reduces_confidence():
    claims, findings, verdicts, policy = case()
    extra = Claim(id="C2", topic="hiring", text="Hiring claim", slide=1)
    claims = claims.model_copy(update={"claims": (*claims.claims, extra)})
    result, breakdown = compute_with_breakdown(claims, findings, verdicts, policy)
    assert result.credibility == 100 and result.confidence == pytest.approx(0.75)
    assert breakdown["eligible_claim_count"] == 2 and breakdown["verifiable_claim_count"] == 1
    assert breakdown["credibility_denominator"] == 5


def test_materiality_and_components_match_hand_calculation():
    claims = ClaimSet(
        claims=tuple(
            Claim(id=f"C{i}", topic=topic, text="Synthetic", slide=1)
            for i, topic in enumerate(("team_size", "identity_hq", "hiring"), 1)
        )
    )
    findings = (
        finding("F1", "scale_team", "C1"),
        finding("F2", "identity", "C2", "PARTIAL"),
        finding("F3", "traction", "C3", "CONTRADICTED"),
    )
    verdicts = tuple(
        Verdict(agent=a, score=s, confidence=c, stance="Synthetic")
        for a, s, c in [
            ("identity", 80, 0.8),
            ("scale_team", 70, 0.6),
            ("funding", 60, 0.4),
            ("traction", 50, 0.2),
        ]
    )
    result, breakdown = compute_with_breakdown(
        claims, findings, verdicts, Policy(max_check_usd=100000)
    )
    assert result.credibility == pytest.approx(100 * 6.5 / 12)
    assert result.total_score == pytest.approx(0.35 * 100 * 6.5 / 12 + 8 + 7 + 12 + 12.5)
    assert result.confidence == pytest.approx(0.5 + 0.3 * 0.5 + 0.2)
    assert breakdown["credibility_numerator"] == 6.5 and breakdown["credibility_denominator"] == 12
    assert sum(breakdown["confidence_components"].values()) == result.confidence
    assert breakdown["result"]["total_score"] == result.total_score


def test_no_second_severity_penalty_is_applied():
    result = compute(*case(status="CONTRADICTED", severity=4, agent_score=80))
    assert result.total_score == pytest.approx(0.65 * 80)


@pytest.mark.parametrize(
    "missing",
    [
        ("identity",),
        ("scale_team",),
        ("funding",),
        ("traction",),
        ("identity", "scale_team", "funding", "traction"),
    ],
)
def test_missing_verdicts_keep_fixed_weights_and_zero_confidence(missing):
    result, breakdown = compute_with_breakdown(*case(missing=missing))
    rows = {row["agent"]: row for row in breakdown["dimensions"]}
    for agent in missing:
        assert not rows[agent]["available"] and rows[agent]["contribution"] == 0
    assert result.confidence == pytest.approx(0.5 + 0.5 * (4 - len(missing)) / 4)


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", float("nan")),
        ("score", 101),
        ("confidence", float("inf")),
        ("confidence", -1),
        ("agent", "unknown"),
    ],
)
def test_bypassed_verdict_validation_is_rejected(field, value):
    claims, findings, verdicts, policy = case()
    corrupted = verdicts[0].model_copy(update={field: value})
    with pytest.raises(ValidationError):
        compute(claims, findings, (corrupted, *verdicts[1:]), policy)


@pytest.mark.parametrize("value", ["false", 1, None])
def test_profit_document_flag_requires_explicit_boolean(value):
    with pytest.raises(ValueError):
        compute(*case(), has_profit_documents=value)


def test_duplicate_finding_ids_are_rejected():
    claims, findings, verdicts, policy = case()
    with pytest.raises(ValueError, match="Duplicate finding"):
        compute(claims, (*findings, findings[0]), verdicts, policy)


def test_gates_override_no_data_and_all_other_caps():
    result, breakdown = compute_with_breakdown(
        *case(status="UNVERIFIABLE", severity=5),
        confirmed_gates=("arr_ratio_over_3", "arr_ratio_over_3"),
    )
    assert result.verdict == "PASS" and result.check_size_usd == 0
    assert result.gates == ("arr_ratio_over_3",)
    assert "CONFIRMED_HARD_GATE" in breakdown["applied_caps"]


def test_no_verifiable_override_explains_zero_effective_total():
    result, breakdown = compute_with_breakdown(*case(status="UNVERIFIABLE"))
    assert breakdown["weighted_total"] == 65 and result.total_score == 0
    assert "NO_VERIFIABLE_CLAIMS" in breakdown["applied_caps"]


@pytest.mark.parametrize("gates", ["arr_ratio_over_3", ("",), (None,)])
def test_malformed_confirmed_gate_names_are_rejected(gates):
    with pytest.raises(ValueError):
        compute(*case(), confirmed_gates=gates)


@pytest.mark.parametrize(
    "kind,updates",
    [
        ("policy", {"max_check_usd": -1}),
        ("policy", {"max_check_usd": float("nan")}),
        ("finding", {"severity": 6}),
        ("finding", {"status": "unsupported"}),
        ("claim", {"value": float("inf")}),
    ],
)
def test_other_validation_bypasses_are_rejected(kind, updates):
    claims, findings, verdicts, policy = case()
    if kind == "policy":
        policy = policy.model_copy(update=updates)
    elif kind == "finding":
        findings = (findings[0].model_copy(update=updates),)
    else:
        claims = claims.model_copy(
            update={"claims": (claims.claims[0].model_copy(update=updates),)}
        )
    with pytest.raises(ValidationError):
        compute(claims, findings, verdicts, policy)
