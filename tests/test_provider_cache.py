from datetime import datetime, timedelta, timezone
import json

import pytest

from crustdata.cache import ResponseCache, TTLS
from ledger.db import connection, initialize


@pytest.fixture
async def cache_setup(tmp_path):
    path = tmp_path / "cache.sqlite"
    await initialize(path)
    clock = [datetime(2026, 10, 31, tzinfo=timezone.utc)]
    return path, clock, ResponseCache(path, "account-a", clock=lambda: clock[0])


async def test_cache_miss_then_hit_retains_date_and_copies_data(cache_setup):
    _, clock, cache = cache_setup
    assert await cache.get("/company/enrich", {"id": 1}, "v1") is None
    created = await cache.put("/company/enrich", {"id": 1}, "v1", {"headcount": {"total": 10}})
    clock[0] += timedelta(hours=1)
    read = await cache.get("/company/enrich", {"id": 1}, "v1")
    assert read.cache_hit and not created.cache_hit
    assert read.fetched_at == created.fetched_at
    mutated = read.data
    mutated["headcount"]["total"] = 999
    assert (await cache.get("/company/enrich", {"id": 1}, "v1")).data["headcount"]["total"] == 10


async def test_cache_expiry_is_exact_and_entry_can_refresh(cache_setup):
    _, clock, cache = cache_setup
    await cache.put("/company/enrich", {}, "v1", [])
    clock[0] += timedelta(seconds=TTLS["enrich"])
    assert await cache.get("/company/enrich", {}, "v1") is None
    refreshed = await cache.put("/company/enrich", {}, "v1", [{"new": True}])
    assert (await cache.get("/company/enrich", {}, "v1")).fetched_at == refreshed.fetched_at


async def test_keys_separate_parameters_versions_endpoint_and_account(cache_setup):
    path, clock, cache = cache_setup
    assert cache.key("e", {"a": 1, "b": 2}, "v1") == cache.key("e", {"b": 2, "a": 1}, "v1")
    other = ResponseCache(path, "account-b", clock=lambda: clock[0])
    keys = {
        cache.key("e", {}, "v1"),
        cache.key("e", {"x": 1}, "v1"),
        cache.key("f", {}, "v1"),
        cache.key("e", {}, "v2"),
        other.key("e", {}, "v1"),
    }
    assert len(keys) == 5
    assert all(len(k) == 64 for k in keys)


@pytest.mark.parametrize("bad", ["not-json", "NaN", '{"n":1e999}'])
async def test_corrupt_cache_entries_are_misses(cache_setup, bad):
    path, _, cache = cache_setup
    await cache.put("/company/enrich", {}, "v1", {})
    async with connection(path) as db:
        await db.execute("UPDATE api_cache SET response_json=?", (bad,))
    assert await cache.get("/company/enrich", {}, "v1") is None


async def test_future_fetch_time_is_not_treated_as_fresh(cache_setup):
    _, clock, cache = cache_setup
    await cache.put("/company/enrich", {}, "v1", {})
    clock[0] -= timedelta(seconds=1)
    assert await cache.get("/company/enrich", {}, "v1") is None


async def test_nonfinite_payload_and_unknown_endpoint_are_rejected(cache_setup):
    _, _, cache = cache_setup
    with pytest.raises(ValueError):
        await cache.put("/company/enrich", {}, "v1", {"bad": float("inf")})
    with pytest.raises(ValueError):
        await cache.put("/not-cacheable", {}, "v1", {})
    # Cache values have no request header or API credential field.
    assert "authorization" not in json.dumps(
        (await cache.put("/company/enrich", {}, "v1", {})).data
    )
