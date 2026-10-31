"""Crustdata gateway: bounded read-only requests, validated responses and caching."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import math
import random
from urllib.parse import urlsplit

import httpx

from app.settings import Settings
from crustdata.cache import ProviderResponse, ResponseCache, json_text, utcnow
from ledger.errors import LedgerError

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
DEFAULT_FIELDS = (
    "basic_info",
    "locations",
    "people",
    "news",
    "headcount",
    "employee_reviews",
    "funding",
    "revenue",
    "hiring",
    "web_traffic",
    "followers",
)


class CrustdataError(Exception):
    def __init__(self, kind: str, status: int | None, message: str, *, attempts: int = 0):
        self.kind, self.status, self.attempts = kind, status, attempts
        super().__init__(message)


def strings(values, maximum=25):
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, (tuple, list))
        or not 1 <= len(values) <= maximum
    ):
        raise ValueError("Provide a nonempty identifier list within the endpoint limit.")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in values):
        raise ValueError("Identifiers must be nonempty strings of at most 500 characters.")
    return [v.strip() for v in values]


def normalize_domain(value):
    value = strings([value])[0].lower()
    parsed = urlsplit(value if "://" in value else "https://" + value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or "." not in parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.port is not None
    ):
        raise ValueError("Provide a company domain without credentials, paths or query parameters.")
    return parsed.hostname.rstrip(".")


def company_id(data):
    basic = data.get("basic_info") or {}
    basic = basic if isinstance(basic, dict) else {}
    value = data.get("crustdata_company_id", basic.get("crustdata_company_id"))
    return value if type(value) is int and value > 0 else None


def validate_response(endpoint, data, params):
    valid = False
    if endpoint in ("/company/identify", "/company/enrich"):
        valid = isinstance(data, list) and len(data) == len(
            next(v for k, v in params.items() if k in ("names", "domains", "crustdata_company_ids"))
        )
        if valid:
            for item in data:
                if not isinstance(item, dict) or not isinstance(item.get("matches"), list):
                    valid = False
                    break
                for match in item["matches"]:
                    if not isinstance(match, dict) or not isinstance(
                        match.get("company_data"), dict
                    ):
                        valid = False
                        break
                    score = match.get("confidence_score")
                    if (
                        type(score) not in (int, float)
                        or not math.isfinite(score)
                        or not 0 <= score <= 1
                    ):
                        valid = False
                        break
                    if (
                        endpoint == "/company/enrich"
                        and "crustdata_company_ids" in params
                        and company_id(match["company_data"]) not in params["crustdata_company_ids"]
                    ):
                        valid = False
                        break
    elif endpoint == "/job/search":
        valid = (
            isinstance(data, dict)
            and isinstance(data.get("job_listings"), list)
            and all(isinstance(j, dict) for j in data["job_listings"])
        )
    elif endpoint == "/web/search/live":
        valid = (
            isinstance(data, dict)
            and data.get("success") is True
            and isinstance(data.get("results"), list)
            and all(isinstance(r, dict) for r in data["results"])
        )
    elif endpoint == "/web/enrich/live":
        valid = (
            isinstance(data, list)
            and len(data) == len(params["urls"])
            and all(isinstance(r, dict) and r.get("success") in (True, False, None) for r in data)
        )
    else:
        valid = isinstance(data, (dict, list))
    if not valid:
        raise CrustdataError("response", 200, "Crustdata returned an unexpected response shape.")
    try:
        json_text(data)
    except (ValueError, TypeError):
        raise CrustdataError(
            "response", 200, "Crustdata returned nonfinite or invalid JSON data."
        ) from None


class CrustdataClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        cache_path=None,
        sleep=asyncio.sleep,
        jitter=random.random,
    ):
        settings.require_live_credentials("crustdata")
        self._key = settings.crustdata_api_key.get_secret_value()
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(follow_redirects=False)
        namespace = hashlib.sha256(self._key.encode()).hexdigest()
        self.cache = ResponseCache(cache_path, namespace) if cache_path is not None else None
        self.request_timeout, self.total_timeout = (
            settings.provider_request_timeout,
            settings.provider_total_timeout,
        )
        self.max_retries, self._sleep, self._jitter = settings.provider_max_retries, sleep, jitter
        self._locks = {}
        self._closed = False
        self.request_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        self._closed = True
        if self._owns_http:
            await self._http.aclose()

    @asynccontextmanager
    async def _lock(self, key):
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._locks[key]
            if users == 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)

    async def _request(self, endpoint, params=None, method="POST"):
        deadline = asyncio.get_running_loop().time() + self.total_timeout
        attempts = 0
        while True:
            attempts += 1
            self.request_count += 1
            try:
                response = await self._http.request(
                    method,
                    BASE_URL + endpoint,
                    headers={
                        "authorization": "Bearer " + self._key,
                        "x-api-version": API_VERSION,
                        "content-type": "application/json",
                    },
                    json=params if method == "POST" else None,
                    timeout=self.request_timeout,
                    follow_redirects=False,
                )
            except (httpx.ConnectTimeout, httpx.ConnectError):
                failure = CrustdataError(
                    "network", None, "Could not connect to Crustdata.", attempts=attempts
                )
                delay = 2 ** (attempts - 1) + self._jitter() * 0.25
            except httpx.TimeoutException:
                raise CrustdataError(
                    "timeout", None, "Crustdata request timed out.", attempts=attempts
                ) from None
            except httpx.HTTPError:
                raise CrustdataError(
                    "network", None, "Crustdata transport failed.", attempts=attempts
                ) from None
            else:
                status = response.status_code
                if status == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        raise CrustdataError(
                            "response",
                            status,
                            "Crustdata returned invalid JSON.",
                            attempts=attempts,
                        ) from None
                    validate_response(endpoint, data, params or {})
                    return data
                kinds = {
                    400: "request",
                    401: "auth",
                    402: "credits",
                    403: "permission",
                    404: "not_found",
                    429: "rate_limit",
                }
                kind = kinds.get(status, "server" if status >= 500 else "response")
                if status == 403:
                    try:
                        body = response.json()
                        error_type = (body.get("error") or {}).get("type")
                        if error_type in (
                            "insufficient_credits",
                            "credit_limit_exceeded",
                            "insufficient_credit",
                        ):
                            kind = "credits"
                    except (ValueError, TypeError, AttributeError):
                        pass
                failure = CrustdataError(
                    kind, status, "Crustdata request failed (" + kind + ").", attempts=attempts
                )
                if status != 429 and status < 500:
                    raise failure
                delay = 2 ** (attempts - 1) + self._jitter() * 0.25
                raw = response.headers.get("Retry-After") or response.headers.get(
                    "X-RateLimit-Reset"
                )
                if raw:
                    try:
                        delay = max(delay, float(raw))
                    except ValueError:
                        try:
                            delay = max(
                                delay,
                                (
                                    parsedate_to_datetime(raw) - datetime.now(timezone.utc)
                                ).total_seconds(),
                            )
                        except (ValueError, TypeError, OverflowError):
                            pass
            if (
                attempts > self.max_retries
                or not math.isfinite(delay)
                or delay >= deadline - asyncio.get_running_loop().time()
            ):
                raise failure from None
            await self._sleep(delay)

    async def _result(self, endpoint, params, *, method="POST"):
        if self._closed:
            raise CrustdataError("closed", None, "Crustdata client is closed.")
        key = (
            self.cache.key(endpoint, params, API_VERSION)
            if self.cache
            else json_text([endpoint, params])
        )
        try:
            async with asyncio.timeout(self.total_timeout), self._lock(key):
                if self.cache and method == "POST":
                    cached = await self.cache.get(endpoint, params, API_VERSION)
                    if cached is not None:
                        try:
                            validate_response(endpoint, cached.data, params)
                            return cached
                        except CrustdataError:
                            pass  # A corrupt/stale-format cache entry is replaced by a real result.
                data = await self._request(endpoint, params, method)
                fetched_at = utcnow().isoformat()
                cacheable = endpoint != "/web/enrich/live" or all(
                    r.get("success") is True for r in data
                )
                if self.cache and method == "POST" and cacheable:
                    return await self.cache.put(
                        endpoint, params, API_VERSION, data, fetched_at=fetched_at
                    )
                return ProviderResponse(json_text(data), fetched_at)
        except TimeoutError:
            raise CrustdataError(
                "timeout", None, "Crustdata operation exceeded its total deadline."
            ) from None
        except LedgerError:
            raise CrustdataError(
                "cache", None, "Provider cache is unavailable; initialize the ledger database."
            ) from None

    async def identify(self, names=None, domains=None):
        if (names is None) == (domains is None):
            raise ValueError("Provide exactly one of names or domains.")
        params = (
            {"names": strings(names)}
            if names is not None
            else {"domains": [normalize_domain(v) for v in strings(domains)]}
        )
        return (await self._result("/company/identify", params)).data

    async def enrich_result(self, domain=None, company_id=None, fields=None) -> ProviderResponse:
        if (domain is None) == (company_id is None):
            raise ValueError("Provide exactly one company domain or ID.")
        if company_id is not None and (type(company_id) is not int or company_id <= 0):
            raise ValueError("Company ID must be a positive integer.")
        selected = strings(list(DEFAULT_FIELDS) if fields is None else fields, len(DEFAULT_FIELDS))
        if set(selected) - set(DEFAULT_FIELDS):
            raise ValueError("Unsupported company field group.")
        params = (
            {"crustdata_company_ids": [company_id]}
            if company_id is not None
            else {"domains": [normalize_domain(domain)]}
        )
        params["fields"] = sorted(set(selected))
        return await self._result("/company/enrich", params)

    async def enrich(self, domain=None, company_id=None, fields=None):
        return (await self.enrich_result(domain, company_id, fields)).data

    async def job_search(self, company_id, title_filter=None, limit=50, *, cursor=None):
        if (
            type(company_id) is not int
            or company_id <= 0
            or type(limit) is not int
            or not 1 <= limit <= 50
        ):
            raise ValueError("Positive company ID and limit between 1 and 50 are required.")
        conditions = [{"field": "company.basic_info.company_id", "type": "=", "value": company_id}]
        if title_filter is not None:
            conditions.append(
                {"field": "job_details.title", "type": "=", "value": strings([title_filter])[0]}
            )
        params = {
            "filters": {"op": "and", "conditions": conditions},
            "fields": [
                "job_details.title",
                "job_details.url",
                "metadata.date_added",
                "location.raw",
            ],
            "sorts": [{"field": "metadata.date_added", "order": "desc"}],
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = strings([cursor])[0]
        return (await self._result("/job/search", params)).data

    async def web_search(self, query, sources=("web", "news"), start_date=None):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 5000:
            raise ValueError("Web query must contain 1–5000 characters.")
        selected = strings(sources, 2)
        if set(selected) - {"web", "news"}:
            raise ValueError("Only web and news sources are supported.")
        params = {"query": query.strip(), "sources": sorted(set(selected))}
        if start_date is not None:
            if isinstance(start_date, str):
                try:
                    parsed = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    start_date = int(
                        parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
                    )
                except ValueError:
                    raise ValueError("start_date must be ISO date/time or epoch seconds.") from None
            if type(start_date) is not int or start_date < 0:
                raise ValueError("start_date must be ISO date/time or epoch seconds.")
            params["start_date"] = start_date
        return (await self._result("/web/search/live", params)).data

    async def web_fetch(self, urls):
        values = strings(urls, 10)
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Fetch requires HTTP(S) URLs without embedded credentials.")
        return (await self._result("/web/enrich/live", {"urls": values})).data

    async def account_permissions(self):
        return (await self._result("/account/endpoints", {}, method="GET")).data
