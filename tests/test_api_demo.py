"""The manual API harness stays offline and visibly synthetic."""

from scripts.api_demo import run_demo


async def test_api_demo_completes_with_twelve_agent_phase_records(tmp_path):
    result = await run_demo(tmp_path)
    assert result["login_status"] == 200 and result["upload_status"] == 202
    assert result["run_status"] == "done" and result["agent_phase_records"] == 12
    assert result["sse_terminal_event"] and result["decision_is_stub"]
    assert result["real_api_calls"] == 0
