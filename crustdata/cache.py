"""Credential-scoped SQLite response cache; separate from append-only evidence."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from ledger.db import connection, transaction

TTLS = {
    "identify": 30 * 86400,
    "enrich": 86400,
    "job_search": 86400,
    "web_search": 21600,
    "web_fetch": 21600,
}
ENDPOINT_TTLS = dict(
    zip(
        (
            "/company/identify",
            "/company/enrich",
            "/job/search",
            "/web/search/live",
            "/web/enrich/live",
        ),
        TTLS.values(),
    )
)


def utcnow():
    return datetime.now(timezone.utc)


def json_text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ProviderResponse:
    payload_json: str
    fetched_at: str
    cache_hit: bool = False

    @property
    def data(self):
        # Callers cannot mutate a cache entry through a returned dictionary.
        return json.loads(self.payload_json)


class ResponseCache:
    def __init__(self, path: str | Path, namespace: str, *, clock: Callable = utcnow):
        if not namespace:
            raise ValueError("Cache namespace is required.")
        self.path, self.namespace, self.clock = Path(path), namespace, clock

    def key(self, endpoint: str, params: dict, api_version: str) -> str:
        material = json_text([self.namespace, api_version, endpoint, params])
        return hashlib.sha256(material.encode()).hexdigest()

    async def get(self, endpoint: str, params: dict, api_version: str) -> ProviderResponse | None:
        async with connection(self.path) as db:
            async with db.execute(
                "SELECT response_json,fetched_at,ttl_seconds FROM api_cache WHERE cache_key=?",
                (self.key(endpoint, params, api_version),),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        try:
            timestamp = datetime.fromisoformat(row["fetched_at"])
            if timestamp.tzinfo is None or row["ttl_seconds"] <= 0:
                return None
            age = (self.clock() - timestamp).total_seconds()
            if not 0 <= age < row["ttl_seconds"]:
                return None
            data = json.loads(row["response_json"])
            json_text(data)  # Reject nonfinite/corrupt cached values.
            return ProviderResponse(row["response_json"], row["fetched_at"], True)
        except (ValueError, TypeError, OverflowError):
            return None

    async def put(
        self,
        endpoint: str,
        params: dict,
        api_version: str,
        response: object,
        *,
        fetched_at: str | None = None,
    ) -> ProviderResponse:
        if endpoint not in ENDPOINT_TTLS:
            raise ValueError("This endpoint is not cacheable.")
        entry = ProviderResponse(json_text(response), fetched_at or self.clock().isoformat())
        async with connection(self.path) as db, transaction(db):
            await db.execute(
                "INSERT INTO api_cache(cache_key,endpoint,response_json,fetched_at,ttl_seconds) "
                "VALUES (?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET "
                "endpoint=excluded.endpoint,response_json=excluded.response_json,"
                "fetched_at=excluded.fetched_at,ttl_seconds=excluded.ttl_seconds",
                (
                    self.key(endpoint, params, api_version),
                    endpoint,
                    entry.payload_json,
                    entry.fetched_at,
                    ENDPOINT_TTLS[endpoint],
                ),
            )
        return replace(entry, cache_hit=False)
