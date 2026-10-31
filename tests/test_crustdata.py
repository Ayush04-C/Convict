import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.settings import Settings
from crustdata.client import API_VERSION, CrustdataClient, CrustdataError
from crustdata.dossier import build_dossier, dossier_evidence
from ledger.db import initialize
from ledger.repo import LedgerRepository
from app.schemas import Policy

KEY = "test-only-provider-key"


def settings(**changes):
    return Settings(_env_file=None, crustdata_api_key=KEY, mode="stub", **changes)


def company_response(company_id=123, **data):
    return [
        {
            "matched_on": "example.test",
            "matches": [
                {
                    "confidence_score": 1,
                    "company_data": {
                        "crustdata_company_id": company_id,
                        "basic_info": {"name": "Synthetic", "primary_domain": "example.test"},
                        **data,
                    },
                }
            ],
        }
    ]


@pytest.fixture
async def provider_db(tmp_path):
    path = tmp_path / "provider.sqlite"
    await initialize(path)
    return path


async def test_identify_headers_body_and_candidate_preservation():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=company_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(settings(), http=http) as client:
            result = await client.identify(domains=["https://EXAMPLE.test/"])
            assert result[0]["matches"][0]["confidence_score"] == 1
            assert client.request_count == 1
        assert not http.is_closed  # Injected transports belong to their caller.
    assert requests[0].headers["authorization"] == "Bearer " + KEY
    assert requests[0].headers["x-api-version"] == API_VERSION
    assert json.loads(requests[0].content) == {"domains": ["example.test"]}


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"names": ["x"], "domains": ["example.test"]},
        {"names": []},
        {"names": "x"},
        {"names": [""]},
        {"names": ["x"] * 26},
        {"domains": ["localhost"]},
        {"domains": ["https://u:p@example.test/"]},
        {"domains": ["example.test/path"]},
    ],
)
async def test_bad_identifiers_make_no_requests(kwargs):
    async with CrustdataClient(settings()) as client:
        with pytest.raises(ValueError):
            await client.identify(**kwargs)
        assert client.request_count == 0


@pytest.mark.parametrize(
    "status,kind",
    [
        (400, "request"),
        (401, "auth"),
        (402, "credits"),
        (403, "permission"),
        (404, "not_found"),
        (302, "response"),
    ],
)
async def test_nonretryable_errors_are_sanitized(status, kind, caplog):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, text=KEY))
    ) as http:
        async with CrustdataClient(settings(), http=http) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.kind == kind and client.request_count == 1
            assert KEY not in str(error.value) + caplog.text


async def test_403_credit_error_is_classified_without_leaking_body():
    body = {"error": {"type": "insufficient_credits", "message": KEY}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, json=body))
    ) as http:
        async with CrustdataClient(settings(), http=http) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.kind == "credits"
            assert KEY not in str(error.value)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_transient_failure_retries_then_succeeds(status):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return (
            httpx.Response(status) if count == 1 else httpx.Response(200, json=company_response())
        )

    sleeper = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(
            settings(), http=http, sleep=sleeper, jitter=lambda: 0
        ) as client:
            await client.identify(names=["Synthetic"])
            assert client.request_count == 2
    sleeper.assert_awaited_once_with(1)


async def test_retry_exhaustion_is_four_total_attempts():
    sleeper = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503))
    ) as http:
        async with CrustdataClient(
            settings(), http=http, sleep=sleeper, jitter=lambda: 0
        ) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.attempts == client.request_count == 4
    assert [c.args[0] for c in sleeper.await_args_list] == [1, 2, 4]


@pytest.mark.parametrize("header", ["Retry-After", "X-RateLimit-Reset"])
async def test_rate_limit_delay_is_respected(header):
    responses = iter(
        [httpx.Response(429, headers={header: "5"}), httpx.Response(200, json=company_response())]
    )
    sleeper = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(responses))) as http:
        async with CrustdataClient(
            settings(), http=http, sleep=sleeper, jitter=lambda: 0
        ) as client:
            await client.identify(names=["Synthetic"])
    sleeper.assert_awaited_once_with(5)


async def test_retry_after_beyond_deadline_fails_without_sleep():
    sleeper = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "999"}))
    ) as http:
        async with CrustdataClient(settings(), http=http, sleep=sleeper) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.kind == "rate_limit"
    sleeper.assert_not_awaited()


@pytest.mark.parametrize(
    "error_type,kind,count",
    [
        (httpx.ConnectError, "network", 2),
        (httpx.ReadTimeout, "timeout", 1),
        (httpx.ReadError, "network", 1),
    ],
)
async def test_transport_error_policy(error_type, kind, count):
    def handler(request):
        raise error_type(KEY, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(
            settings(provider_max_retries=1), http=http, sleep=AsyncMock(), jitter=lambda: 0
        ) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.kind == kind and client.request_count == count
            assert KEY not in str(error.value)


async def test_total_timeout_bounds_slow_transport():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=company_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(settings(provider_total_timeout=0.02), http=http) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.kind == "timeout"


@pytest.mark.parametrize(
    "data", [{}, [None], [{"matches": None}], [{"matches": [{"company_data": None}]}]]
)
async def test_bad_success_shape_is_rejected(data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    ) as http:
        async with CrustdataClient(settings(), http=http) as client:
            with pytest.raises(CrustdataError) as error:
                await client.identify(names=["Synthetic"])
            assert error.value.kind == "response"


async def test_invalid_json_is_not_cached(provider_db):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=KEY))
    ) as http:
        async with CrustdataClient(settings(), http=http, cache_path=provider_db) as client:
            for _ in range(2):
                with pytest.raises(CrustdataError):
                    await client.identify(names=["Synthetic"])
            assert client.request_count == 2


async def test_concurrent_identical_enrichment_hits_network_once(provider_db):
    async def handler(request):
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=company_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(settings(), http=http, cache_path=provider_db) as client:
            results = await asyncio.gather(
                *(client.enrich_result(company_id=123) for _ in range(8))
            )
            assert client.request_count == 1
            assert sum(r.cache_hit for r in results) == 7
            assert len({r.fetched_at for r in results}) == 1
            assert client._locks == {}


async def test_cache_separates_credentials_and_field_sets(provider_db):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=company_response()))
    ) as http:
        async with CrustdataClient(settings(), http=http, cache_path=provider_db) as first:
            await first.enrich(company_id=123, fields=["funding", "basic_info"])
            await first.enrich(company_id=123, fields=["basic_info", "funding"])
            await first.enrich(company_id=123, fields=["basic_info"])
            assert first.request_count == 2
        alternate = settings().model_copy(
            update={"crustdata_api_key": __import__("pydantic").SecretStr("other-test-key")}
        )
        async with CrustdataClient(alternate, http=http, cache_path=provider_db) as other:
            await other.enrich(company_id=123, fields=["basic_info"])
            assert other.request_count == 1


async def test_dossier_cache_timestamps_nulls_identity_and_ledger_evidence(provider_db):
    body = company_response(
        funding=None, headcount={"total": 10}, updated_at="2026-10-01T00:00:00Z"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    ) as http:
        async with CrustdataClient(settings(), http=http, cache_path=provider_db) as client:
            first = await build_dossier(123, client)
            second = await build_dossier(123, client)
            assert first == second and client.request_count == 1
    assert json.loads(first.data_json)["funding"] is None
    assert json.loads(first.data_json)["headcount"]["total"] == 10
    assert first.source_updated_at == "2026-10-01T00:00:00Z"
    repo = LedgerRepository(provider_db)
    run = await repo.create_run("ayush", "Synthetic", None, Policy(max_check_usd=100), "x.pdf")
    row = dossier_evidence(run, first)
    await repo.add_evidence(run, (row,))
    assert await repo.get_evidence(run) == (row,)


@pytest.mark.parametrize(
    "data,kind",
    [
        ([{"matches": []}], "not_found"),
        (company_response(999), "response"),
        (
            [
                {
                    "matches": [
                        {"confidence_score": 1, "company_data": {"crustdata_company_id": 123}},
                        {"confidence_score": 1, "company_data": {"crustdata_company_id": 123}},
                    ]
                }
            ],
            "response",
        ),
    ],
)
async def test_dossier_never_selects_wrong_or_ambiguous_company(data, kind):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    ) as http:
        async with CrustdataClient(settings(), http=http) as client:
            with pytest.raises(CrustdataError) as error:
                await build_dossier(123, client)
            assert error.value.kind == kind


async def test_jobs_web_and_account_requests():
    seen = []

    def handler(request):
        seen.append(request)
        bodies = {
            "/job/search": {"job_listings": [], "next_cursor": None, "total_count": None},
            "/web/search/live": {"success": True, "results": [], "metadata": {}},
            "/web/enrich/live": [{"success": False, "url": None}],
            "/account/endpoints": {"endpoints": []},
        }
        return httpx.Response(200, json=bodies[request.url.path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with CrustdataClient(settings(), http=http) as client:
            assert (await client.job_search(123, title_filter="Engineer", limit=5, cursor="next"))[
                "total_count"
            ] is None
            await client.web_search("Synthetic", sources=["news"], start_date="2026-10-01")
            assert (await client.web_fetch(["https://example.test"]))[0]["success"] is False
            assert await client.account_permissions() == {"endpoints": []}
    job = json.loads(seen[0].content)
    assert job["cursor"] == "next" and job["limit"] == 5
    assert job["filters"]["conditions"][0]["field"] == "company.basic_info.company_id"
    assert isinstance(json.loads(seen[1].content)["start_date"], int)
    assert seen[-1].method == "GET"


async def test_failed_fetch_items_are_not_cached(provider_db):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=[{"success": False, "url": None}])
        )
    ) as http:
        async with CrustdataClient(settings(), http=http, cache_path=provider_db) as client:
            await client.web_fetch(["https://example.test"])
            await client.web_fetch(["https://example.test"])
            assert client.request_count == 2


async def test_closed_client_rejects_calls():
    client = CrustdataClient(settings())
    await client.aclose()
    with pytest.raises(CrustdataError) as error:
        await client.identify(names=["Synthetic"])
    assert error.value.kind == "closed"
