# Provider integration

Ayush owns these adapters. They provide data and validated model output; entity
resolution, scoped agent tools, orchestration and run APIs are later stages.

## Setup and verification

Install `requirements.txt`. Put keys only in ignored `.env`; `.env.example` must
contain blank key placeholders. Set `CRUSTDATA_API_KEY`, `GEMINI_API_KEY`, and
`MODEL_FAST`, `MODEL_MID`, `MODEL_PRO` to models available to your account.
`MODE=live` validates all five settings and opens shared clients during API startup.
`MODE=stub` opens no live clients. Startup configuration validation does not probe
model availability. Existing authentication configuration is also required.

```sh
make test
make lint
make provider-demo
python -m scripts.provider_smoke --live --domain retool.com
python -m scripts.gemini_smoke --live
```

The first three commands are offline. Smoke scripts require explicit `--live` and
may spend provider credits. Without that flag they make no requests. The Crustdata
probe checks permissions, identifies a domain, enriches one high-confidence unique
ID and checks a cached repeat; it disables transport retries for the diagnostic.
Gemini probes FAST (or MID when FAST is empty) with one small structured response;
invalid structured output can trigger one repair request. Smoke scripts print no keys.

## Crustdata contract

`CrustdataClient` offers `identify`, `enrich`, `job_search`, `web_search`,
`web_fetch` and `account_permissions`. Calls use the fixed provider host and API
version. Methods preserve native provider response envelopes; `enrich_result`
also exposes `data`, `fetched_at` and `cache_hit`. Identify returns candidates;
it does not decide company identity. Enrichment permits the eleven configured
company field groups and checks an explicitly requested company ID.

Inputs, JSON finiteness and response envelopes are validated. Domain inputs are
normalized; invalid IDs, URL credentials, oversized batches and unsupported fields
fail before HTTP. Job search is capped at 50 and has no automatic pagination.
Permission discovery is explicit; a denied field is not silently removed.

At most three retries follow connection establishment failures, HTTP 429 or 5xx.
Read timeouts/read errors are not retried because the provider may already have
processed a billable request. Backoff respects Retry-After and rate-limit reset
headers within the total deadline. Cancellation propagates. Error objects contain
safe kinds/statuses, never upstream bodies or credentials.

| Setting | Default | Bounds |
|---|---|---|
| `PROVIDER_REQUEST_TIMEOUT` | 15 seconds | >0, <=60 |
| `PROVIDER_TOTAL_TIMEOUT` | 45 seconds | >0, <=180 |
| `PROVIDER_MAX_RETRIES` | 3 | 0..3 |
| `GEMINI_TIMEOUT` | 45 seconds | >0, <=180 |

## Cache and evidence

SQLite `api_cache` keys include endpoint, canonical parameters, API version and a
hash identifying the account key. Plain credentials are not stored. Identify TTL
is 30 days, enrichment/jobs 24 hours, web search/fetch 6 hours. Expired, malformed,
nonfinite or future-dated cache entries miss. Partial failed web fetches are not
cached. Identical concurrent requests share one call within a client instance;
there is no cross-process request lock.

Cache hits preserve the original retrieval timestamp. `build_dossier` keeps raw
company groups and nulls, records retrieval time and preserves a provider update
time when supplied. Missing data remains unknown. `dossier_evidence` creates a
run-specific evidence record; callers explicitly append it through the ledger.
Cached provider data is reusable across runs, while ledger evidence belongs to a
specific run. Cache failure raises a typed error rather than silently spending again.

## Gemini contract

`GeminiClient.generate(prompt, schema, model)` accepts a Pydantic model class and
a model configured in FAST/MID/PRO. It requests JSON schema output and validates
the parsed result. A schema failure gets one repair attempt with safe validation
codes. Authentication, permission, missing model, rate limit, timeout, network and
server errors are typed; they do not trigger automatic model switching or generic
transport retries. The SDK's automatic retries are disabled. Owned async clients
close on application shutdown, including partial startup failure.

Daksh's ingestion adapter/fallback remains his separate implementation. This
shared adapter does not enable a BharatCode fallback or wire a live pipeline.
`INGEST_INR_PER_USD` is an optional positive configured normalization rate; no
exchange rate is invented or fetched automatically.

## Validation recorded on 2026-10-31

All 251 branch test cases passed offline. Crustdata live Identify and Enrich for
Retool succeeded: three HTTP requests including permissions, zero requests for a
repeat enrichment. Gemini FAST `gemini-3.1-flash-lite` returned valid structured
output in one request. MID/PRO generation and the complete agent pipeline were
not exercised by these probes. CI runs the full pytest suite and all three offline
demos on pushes and pull requests without live keys.

References: [Crustdata enrichment](https://docs.crustdata.com/company-docs/enrichment/reference),
[permissions](https://docs.crustdata.com/general/permissions),
[rate limits](https://docs.crustdata.com/general/rate-limits),
[Google GenAI SDK](https://googleapis.github.io/python-genai/).
