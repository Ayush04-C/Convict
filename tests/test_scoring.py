import pytest

from app.schemas import Claim, ClaimSet, Finding, Policy, Verdict
from leo.context import AGENTS
from leo.scoring import compute


def case(status="VERIFIED", severity=1, agent_score=100, missing=()):
    claims = ClaimSet(claims=(Claim(id="C1", topic="team_size", text="Team", slide=1),))
    findings = (Finding(finding_id="F1", agent="scale_team", claim_id="C1", status=status,
                        severity=severity, note="Fixture evidence", evidence_ids=("E1",)),)
    verdicts = tuple(Verdict(agent=a, score=agent_score, confidence=1, stance="test")
                     for a in AGENTS if a not in missing)
    return claims, findings, verdicts, Policy(max_check_usd=100000)


@pytest.mark.parametrize("score,tier", [(100,"INVEST"), (80,"INVEST"),
                                       (50,"INVEST_WITH_CONDITIONS"), (30,"DIG_DEEPER"), (0,"PASS")])
def test_dimension_scores_produce_policy_tiers(score, tier):
    result = compute(*case(agent_score=score), has_profit_documents=True)
    assert result.verdict == tier
    assert 0 <= result.check_size_usd <= 100000


def test_profit_document_downgrade():
    assert compute(*case()).verdict == "INVEST_WITH_CONDITIONS"


def test_severity_five_caps_invest():
    assert compute(*case(severity=5), has_profit_documents=True).verdict == "INVEST_WITH_CONDITIONS"


@pytest.mark.parametrize("gate", ["entity_unresolved", "founder_mismatch", "funding_round_absent",
                                   "arr_ratio_above_3", "inactive_entity"])
def test_confirmed_gates_force_pass(gate):
    result = compute(*case(), confirmed_gates=(gate,), has_profit_documents=True)
    assert result.verdict == "PASS"
    assert result.check_size_usd == 0


def test_no_verifiable_claims_has_no_sizing():
    result = compute(*case(status="UNVERIFIABLE"))
    assert result.credibility is None
    assert result.verdict == "DIG_DEEPER"
    assert result.check_size_usd == 0


def test_failed_agent_reduces_confidence_and_keeps_weights():
    full = compute(*case(), has_profit_documents=True)
    missing = compute(*case(missing=("funding",)), has_profit_documents=True)
    assert missing.total_score == pytest.approx(full.total_score - 20)
    assert missing.confidence == pytest.approx(0.875)


def test_duplicate_agent_verdict_rejected():
    claims, findings, verdicts, policy = case()
    with pytest.raises(ValueError, match="Duplicate agent"):
        compute(claims, findings, (*verdicts, verdicts[0]), policy)


def test_primary_claim_finding_is_not_double_counted():
    claims, findings, verdicts, policy = case()
    secondary = Finding(finding_id="F2", agent="funding", claim_id="C1", status="CONTRADICTED",
                        note="Secondary", evidence_ids=("E2",))
    assert compute(claims, (*findings, secondary), verdicts, policy).credibility == 100


def test_duplicate_primary_uses_least_credit():
    claims, findings, verdicts, policy = case()
    other = findings[0].model_copy(update={"finding_id": "F2", "status": "PARTIAL"})
    assert compute(claims, (*findings, other), verdicts, policy).credibility == 50


def test_unknown_claim_reference_rejected():
    claims, findings, verdicts, policy = case()
    with pytest.raises(ValueError, match="unknown claim"):
        compute(claims, (findings[0].model_copy(update={"claim_id": "C2"}),), verdicts, policy)


@pytest.mark.parametrize("target,tier", [(49.99,"PASS"), (50,"DIG_DEEPER"),
                                        (64.99,"DIG_DEEPER"), (65,"INVEST_WITH_CONDITIONS"),
                                        (79.99,"INVEST_WITH_CONDITIONS"), (80,"INVEST")])
def test_exact_and_fractional_tier_boundaries(target, tier):
    # Full credibility contributes 35 points; dimensions contribute the other 65.
    result = compute(*case(agent_score=(target - 35) / 0.65), has_profit_documents=True)
    assert result.total_score == pytest.approx(target)
    assert result.verdict == tier


def test_empty_claim_set_does_not_divide_by_zero():
    result = compute(ClaimSet(), (), (), Policy(max_check_usd=0))
    assert result.confidence == 0
    assert result.verdict == "DIG_DEEPER"
    assert result.check_size_usd == 0


def test_missing_data_does_not_override_confirmed_gate():
    result = compute(*case(status="UNVERIFIABLE"), confirmed_gates=("entity_unresolved",))
    assert result.verdict == "PASS"


def test_effective_revision_changes_severity_cap():
    claims, findings, verdicts, policy = case(severity=5)
    revised = findings[0].model_copy(update={"severity_final": 2})
    assert compute(claims, (revised,), verdicts, policy, has_profit_documents=True).verdict == "INVEST"
