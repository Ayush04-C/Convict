import pytest
from pydantic import ValidationError

from app.schemas import Claim, ContextPacket, Policy, Verdict
from scripts.demo import run_demo


def test_context_is_immutable():
    packet = ContextPacket(agent="identity", claims=(), policy=Policy(max_check_usd=100))
    with pytest.raises(ValidationError):
        packet.agent = "funding"


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_invalid_agent_scores_rejected(value):
    with pytest.raises(ValidationError):
        Verdict(agent="identity", score=value, confidence=0.3, stance="test")


def test_founder_claim_is_supported():
    assert Claim(id="C1", topic="identity_founder", text="Founder: Test", slide=1).testable


async def test_all_stubs_complete_offline():
    result = await run_demo()
    assert result["findings"] == 6  # revenue and traction claims also route to Scale.
    assert result["verdicts"] == 4
    assert result["decision"]["is_stub"] is True
    assert result["decision"]["credibility"] is None
    assert result["decision"]["check_size_usd"] == 0
