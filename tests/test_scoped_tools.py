"""Company-bound provider capability tests; no real network calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from leo.context import DOSSIER_GROUPS
from sandbox.scoped_tools import METHODS, ScopedCrustdata, ToolBudgetExceeded


def provider():
    return SimpleNamespace(
        enrich=AsyncMock(return_value=[]),
        web_search=AsyncMock(return_value={}),
        job_search=AsyncMock(return_value={}),
    )


@pytest.mark.parametrize("agent", METHODS)
async def test_default_tool_methods_and_fields_are_specialist_scoped(agent):
    upstream = provider()
    tools = ScopedCrustdata(agent, upstream, company_id=1)
    await tools.call("enrich")
    upstream.enrich.assert_awaited_once_with(company_id=1, fields=list(DOSSIER_GROUPS[agent]))
    for method in ("identify", "account_permissions", "web_fetch", "aclose", "_request"):
        with pytest.raises(PermissionError):
            await tools.call(method)
    assert tools.calls == 1


@pytest.mark.parametrize(
    "agent,method",
    [("funding", "job_search"), ("scale_team", "web_search"), ("traction", "web_search")],
)
async def test_cross_agent_tool_access_is_denied(agent, method):
    tools = ScopedCrustdata(agent, provider(), company_id=1)
    with pytest.raises(PermissionError):
        await tools.call(method)
    assert tools.calls == 0


@pytest.mark.parametrize(
    "params",
    [
        {"company_id": 2},
        {"company_id": True},
        {"domain": "other.com"},
        {"fields": ["funding"]},
        {"fields": "basic_info"},
    ],
)
async def test_other_company_and_field_escalation_never_reach_provider(params):
    upstream = provider()
    tools = ScopedCrustdata("identity", upstream, company_id=1)
    with pytest.raises(PermissionError):
        await tools.call("enrich", **params)
    upstream.enrich.assert_not_awaited()


async def test_six_call_budget_is_atomic_under_concurrency():
    upstream = provider()
    tools = ScopedCrustdata("funding", upstream, company_id=1)
    result = await asyncio.gather(
        *(tools.call("enrich") for _ in range(20)), return_exceptions=True
    )
    assert tools.calls == upstream.enrich.await_count == 6
    assert sum(isinstance(r, ToolBudgetExceeded) for r in result) == 14


async def test_failed_and_cancelled_calls_consume_slots_and_new_phase_resets():
    upstream = provider()
    upstream.enrich.side_effect = RuntimeError("Synthetic")
    tools = ScopedCrustdata("funding", upstream, max_calls=1, company_id=1)
    with pytest.raises(RuntimeError):
        await tools.call("enrich")
    with pytest.raises(ToolBudgetExceeded):
        await tools.call("enrich")
    fresh = tools._for_phase()
    assert fresh.calls == 0 and fresh.company_id == 1

    async def slow(**kwargs):
        await asyncio.sleep(10)

    upstream.enrich.side_effect = slow
    task = asyncio.create_task(fresh.call("enrich"))
    await asyncio.sleep(0)
    await fresh.aclose()
    assert task.cancelled() and fresh.calls == 1
    with pytest.raises(PermissionError):
        await fresh.call("enrich")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agent": "unknown"},
        {"company_id": 0},
        {"company_id": True},
        {"max_calls": 7},
        {"max_calls": 0},
    ],
)
def test_invalid_scope_configuration(kwargs):
    values = dict(agent="identity", client=provider(), company_id=1)
    values.update(kwargs)
    with pytest.raises(ValueError):
        ScopedCrustdata(**values)


def test_constructor_cannot_grant_extra_methods():
    with pytest.raises(PermissionError):
        ScopedCrustdata("funding", provider(), allowed=("web_search",), company_id=1)
