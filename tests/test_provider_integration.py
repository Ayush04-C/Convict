import asyncio
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr
import pytest

from app.main import create_app
from crustdata.client import CrustdataClient, CrustdataError
from scripts.provider_demo import run_demo
from tests.test_crustdata import company_response, settings


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("enrich", {}),
        ("enrich", {"domain": "example.test", "company_id": 123}),
        ("enrich", {"company_id": True}),
        ("enrich", {"company_id": 123, "fields": ["social_posts"]}),
        ("job_search", {"company_id": 123, "limit": 51}),
        ("web_search", {"query": ""}),
        ("web_search", {"query": "x", "sources": ["ai"]}),
        ("web_search", {"query": "x", "start_date": -1}),
        ("web_fetch", {"urls": ["file:///tmp/private"]}),
        ("web_fetch", {"urls": ["https://user:pass@example.test"]}),
    ],
)
async def test_bad_endpoint_arguments_are_rejected_before_network(method, kwargs):
    async with CrustdataClient(settings()) as client:
        with pytest.raises(ValueError):
            await getattr(client, method)(**kwargs)
        assert client.request_count == 0


async def test_missing_cache_database_fails_before_spending_credits(tmp_path):
    async with CrustdataClient(settings(), cache_path=tmp_path / "absent.sqlite") as client:
        with pytest.raises(CrustdataError) as error:
            await client.identify(names=["Synthetic"])
        assert error.value.kind == "cache" and client.request_count == 0


async def test_cancelled_waiter_does_not_leak_request_locks(tmp_path):
    from ledger.db import initialize

    path = tmp_path / "cache.sqlite"
    await initialize(path)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=company_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(settings(), http=http, cache_path=path) as client:
            first = asyncio.create_task(client.identify(names=["Synthetic"]))
            await started.wait()
            waiter = asyncio.create_task(client.identify(names=["Synthetic"]))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            await first
            assert client._locks == {} and client.request_count == 1


async def test_provider_demo_is_offline_and_persists_evidence(tmp_path):
    result = await run_demo(tmp_path / "provider.sqlite")
    assert result["real_api_calls"] == 0
    assert result["ledger_evidence_rows"] == 1
    assert result["repeat_from_cache"] and result["mock_gemini_validated"]


async def test_live_lifespan_owns_and_closes_both_clients(auth_settings, tmp_path, monkeypatch):
    closed = []

    class Resource:
        def __init__(self, *args, **kwargs):
            self.name = "resource"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self.name)

    class Data(Resource):
        name = "data"

        def __init__(self, *args, **kwargs):
            self.name = "data"

    class Llm(Resource):
        def __init__(self, *args, **kwargs):
            self.name = "llm"

    monkeypatch.setattr("app.main.CrustdataClient", Data)
    monkeypatch.setattr("app.main.GeminiClient", Llm)
    config = auth_settings.model_copy(
        update={
            "mode": "live",
            "database_path": tmp_path / "live.sqlite",
            "crustdata_api_key": SecretStr("fake"),
            "gemini_api_key": SecretStr("fake"),
            "model_fast": "fast",
            "model_mid": "mid",
            "model_pro": "pro",
        }
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        assert app.state.crustdata.name == "data" and app.state.llm.name == "llm"
    assert closed == ["llm", "data"]


async def test_partial_lifespan_failure_closes_already_created_client(
    auth_settings, tmp_path, monkeypatch
):
    closed = AsyncMock()

    class Data:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await closed()

    def fail(*args, **kwargs):
        raise RuntimeError("simulated SDK initialization failure")

    monkeypatch.setattr("app.main.CrustdataClient", Data)
    monkeypatch.setattr("app.main.GeminiClient", fail)
    config = auth_settings.model_copy(
        update={
            "mode": "live",
            "database_path": tmp_path / "live.sqlite",
            "crustdata_api_key": SecretStr("fake"),
            "gemini_api_key": SecretStr("fake"),
            "model_fast": "fast",
            "model_mid": "mid",
            "model_pro": "pro",
        }
    )
    app = create_app(config)
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app):
            pass
    closed.assert_awaited_once()
