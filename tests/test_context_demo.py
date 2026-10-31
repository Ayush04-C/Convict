"""Offline manual harness regression checks."""
import pytest


@pytest.mark.parametrize("timeout_agent", [False, True])
async def test_context_demo_is_offline_and_timeout_is_contained(tmp_path, timeout_agent):
    from scripts.context_demo import run_demo

    result = await run_demo(tmp_path / "demo.sqlite", timeout_agent=timeout_agent)
    assert result["real_api_calls"] == 0 and result["resolution"] == "resolved"
    assert result["packets"] == 4 and result["agents_failed"] == int(timeout_agent)
    assert result["agents_succeeded"] == (3 if timeout_agent else 4)
    assert result["phase"] == "ROUND1"

