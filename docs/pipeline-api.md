# Pipeline and run API

The coordinator now connects PDF validation/extraction, normalization, company
resolution, dossier evidence, frozen contexts, the three sandboxed specialist
phases, adjudication hooks and the existing deterministic scorer. Run status,
agent states, evidence and progress events persist in SQLite.

## Start and test

Run from Leo after installing dependencies and configuring authentication:

```sh
make test
make lint
make api-demo
make run
```

The API demo uses generated credentials, a generated PDF, temporary SQLite and
synthetic providers. It makes no live calls and prints an explicit stub label.
It verifies login, 202 upload, twelve specialist phase records, status and SSE.

For real processing, set `MODE=live` in ignored `.env`, configure the existing
Crustdata/Gemini keys and all model names, then restart the server. `MODE=stub`
uses a synthetic claim/provider path and stub specialists; it does not analyze
the uploaded deck's actual claims. Both modes still validate the PDF.

Use one server process (`make run` does this). The job queue is in memory; its
results/events are persistent. Multiple Uvicorn workers or a distributed queue
are outside this hackathon implementation. Startup marks interrupted runs failed
instead of repeating paid calls. Shutdown cancels/awaits queued and active work
before closing provider resources.

## Manual HTTP flow

1. Log in through `POST /auth/login` using the configured account; see
   `authentication.md`. Use the returned access token as a Bearer Token in Postman.
2. Submit `POST /runs`, Body -> form-data, with these fields:

| Field | Type | Rule |
|---|---|---|
| deck | File | PDF, nonempty, at most 20 MB |
| company_name | Text | Required, 1–500 characters |
| domain | Text | Optional company domain |
| max_check_usd | Text/number | Required finite nonnegative policy limit |
| stage | Text | Optional, default unspecified; at most 100 characters |

3. Expect 202 with `run_id`, `status_url` and `events_url`.
4. Poll `GET /runs/{run_id}` with the same bearer token. Terminal statuses are
   `done`, `cannot_assess`, `failed`. A done result includes the saved decision,
   agent phase states and stub provenance. Raw upload paths and user IDs are omitted.
5. Stream `GET /runs/{run_id}/events`. In a shell where TOKEN and RUN_ID are set:

```sh
curl -N -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/runs/$RUN_ID/events"
```

SSE events have `id`, `event` and JSON `data`. Reconnect with `Last-Event-ID` or
`?after=<cursor>`; the header takes precedence. Cursors are nonnegative signed
64-bit integers. Events are replayed in order, with heartbeats while waiting.
Streams close after draining terminal events. Disconnecting does not cancel the
run. Bearer tokens belong in headers, never SSE URLs.

## Processing and errors

The run advances QUEUED -> INGEST -> RESOLVE -> ROUND1 -> CHALLENGE -> REPLY ->
ADJUDICATE -> SCORING -> DONE. Unresolved identity ends as CANNOT_ASSESS. Each
specialist phase finishes before the next begins; agents read debate data through
their own ledger capabilities rather than coordinator-forwarded messages.

One failed agent is excluded from later phases and scoring; surviving agents
continue. All-agent failure or no final verdict fails the run. Already committed
records remain available for audit. Pipeline cancellation/timeout produces a safe
terminal failure, without raw exception messages or credentials.

Analyst `gate:*` metrics are proposals. Confirmation re-runs the existing pure
analyst rule against the original dossier and normalized claims and checks matching
same-run specialist citations. Missing facts remain unknown. A valid round-one gate
is retained if the proposing agent subsequently fails during debate. Scoring
formulas are unchanged; they receive confirmed gates explicitly.

| Response | Meaning |
|---|---|
| 401 | Missing/invalid bearer token; upload body is not processed |
| 404 | Missing run or a run owned by someone else |
| 413 | File over 20 MB or request over 20 MB plus 64 KB multipart overhead |
| 415 | Unsupported upload file type |
| 422 | Invalid metadata, empty file or invalid event cursor |
| 429 | Pending/active run capacity is full |

PDF structure validation occurs in the background after 202. Damaged/encrypted
PDFs become failed runs with `DECK_REJECTED`. Upload storage uses generated private
filenames. Creation/scheduling failures remove partial files; accepted uploads are
retained locally for processing/audit and are not downloadable through this API.

## Configuration and remaining work

| Setting | Default |
|---|---|
| UPLOAD_DIRECTORY | data/uploads |
| MAX_PENDING_RUNS | 10, including active jobs and reserved submissions |
| MAX_ACTIVE_RUNS | 1 |
| INGEST_TIMEOUT | 180 seconds |
| PHASE_TIMEOUT | 60 seconds, including adjudication hook |
| PIPELINE_TIMEOUT | 480 seconds |

The managed extraction adapter preserves Gemini's document path and the optional
configured BharatCode outage fallback. Agent models are supplied from application
settings rather than re-reading the developer environment during tests.

The real lead adjudicator is still Daksh-owned unfinished work. Its current stub
marks resulting decisions `is_stub=true`, even when ingestion and specialists are
real. This limitation is visible in `/health` and in saved reports. Chat remains
deferred and returns 501. Scoring formulas and thresholds are unchanged.

## Company reports and CLI

`GET /runs/{run_id}/report` returns an owned terminal run's JSON report. Add
`?format=markdown` for the same information as Markdown. Both are non-cacheable.
Processing runs return 409. Failed/unresolved runs return a partial report with
no invented decision, score or check size. Unknown/other-owner runs return 404.

Reports include the exact saved decision, confirmed gates and citations, claim
findings with effective severity, recorded metrics, specialist verdicts/statuses,
debate/revisions and the stored lead narrative. PASS is labelled DO NOT INVEST;
the raw saved verdict is preserved. Synthetic/unfinished results are prominent.
No extra model call or new scoring computation is performed to generate a report.

New runs include the saved `scoring_breakdown`: claim credits, dimension weights,
confidence components, applied caps and sizing inputs. See `scoring.md`. Older
decisions remain unchanged and show no saved breakdown; they are never backfilled
by recalculation.

Source dates distinguish the agent's recorded timestamp from the original provider
retrieval time where the cited value matches the run's saved dossier. Old reports
never read a newly refreshed cache. Full dossier payloads remain in the ledger;
the report exposes cited field values and a dossier reference. Large Markdown
values are labelled previews; JSON retains the cited values. Deck/provider text
is escaped in Markdown and terminal controls are removed.

Run the API with `make run`, then from a second activated terminal:

```sh
python -m scripts.leo_cli --username ayush run \
  --deck /path/to/deck.pdf --company "Company name" \
  --domain example.com --max-check-usd 100000 --stage seed

python -m scripts.leo_cli --username ayush --format json report RUN_ID
python -m scripts.leo_cli --username ayush --output company-report.md report RUN_ID
```

The password prompt is hidden. An optional `LEO_ACCESS_TOKEN` environment variable
can supply an existing token without prompting; never put credentials in the API
URL. Global options (`--base-url`, `--format`, `--output`, `--timeout`) precede
`run`/`report`. The default wait timeout is 600 seconds. Report export creates a
new file with permissions 0600 and refuses to overwrite existing files. Without
`--output`, it prints to the terminal. A failed run exits with code 1.

In MODE=stub the CLI output remains explicitly synthetic. For real deck extraction
use MODE=live and valid provider settings. API/CLI tests use offline dependencies
and are included in the existing full-suite workflow.
