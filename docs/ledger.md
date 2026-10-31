# SQLite evidence ledger

The ledger stores durable, run-scoped analysis records in SQLite through
`aiosqlite`. It needs no Crustdata/Gemini credentials and makes no live API calls.
Data files, including SQLite WAL/SHM files, remain ignored by Git.

## Setup and manual inspection

From `Leo/`, with the virtual environment active:

```sh
make ledger-init
make ledger-demo
```

`ledger-init` uses `DATABASE_PATH` from `.env`, defaulting to `data/leo.sqlite`.
API startup also initializes this database and attaches its repository at
`app.state.ledger`. Existing records are retained. Restart an already running
server to load the new startup behavior.

`ledger-demo` intentionally uses a separate `data/ledger-demo.sqlite`. It creates
a new run each time, explicitly selects fixture claims and neutral stub agents,
persists their results through real bound writers, and reopens the database to
verify the decision. It is not the live orchestrator or real company resolution.

Expected output: `status: done`, six findings, four verdicts, 43 events,
`is_stub: true`, and zero check sizing. Inspect the persisted records with:

```sh
sqlite3 data/ledger-demo.sqlite "SELECT run_id,user,status,phase FROM runs;"
sqlite3 data/ledger-demo.sqlite "SELECT agent,status,severity,severity_final FROM effective_findings;"
sqlite3 data/ledger-demo.sqlite "SELECT event_id,kind FROM events ORDER BY event_id;"
```

For a clean ephemeral demo: `python -m scripts.ledger_demo --temporary`.
Both offline demos avoid live Daksh-owned replacement points, so feature merges
do not make offline CI require provider keys.

## Interfaces and stored records

Trusted coordinator/report code constructs `LedgerRepository(path)`. It supports
runs, claims, context packets, evidence, metrics, findings, challenges, replies,
severity revisions, verdicts, decisions, agent status and ordered events. Typed read
helpers include `get_claims`, `get_context_packet`, `get_evidence`, `get_metrics`,
`get_all_findings`, `get_challenges_for`, `get_replies`, `get_revisions`,
`get_verdicts`, `get_decision`, `get_adjudication` and `get_events`.

```python
repo = LedgerRepository(path)
run_id = await repo.create_run(user, company, domain, policy, deck_path)
await repo.add_claims(run_id, claim_set)
```

`get_run(run_id, user=current_user)` hides other users' runs as not found. Other
repository reads are privileged: future API handlers must perform that owner check
before accessing events/reports. The current run HTTP endpoints remain 501 stubs.

Specialists receive `LedgerWriter(run_id, agent, repo)`, never the raw repository.
Its identity is frozen, and it exposes no update/delete method. Namespace, phase,
references and challenge limits are checked within the write transaction.

## Phase and ownership rules

The coordinator advances one step at a time:

`QUEUED → INGEST → RESOLVE → ROUND1 → CHALLENGE → REPLY → ADJUDICATE → SCORING`

- Only `save_decision` completes `SCORING → DONE`, atomically with the decision/event.
- Failure can terminate an active run. `CANNOT_ASSESS` is valid during resolution.
- Repeating the same phase is idempotent; backward/skipped transitions are rejected.
- Terminal runs reject new writes. The coordinator can update control/status rows;
  evidence/debate history is append-only.
- Specialist findings are written in ROUND1, challenges in CHALLENGE, replies,
  revisions and verdicts in REPLY. Evidence/metrics can be added in any agent phase.
- An agent marked DONE/FAILED cannot submit late writes in that phase.
- Before the round-one barrier, evidence reads/references see only the agent's own
  evidence and coordinator (`leo`) evidence. Cross-agent findings are denied.
- After the barrier, all findings/evidence are readable. Incoming challenges become
  readable after the challenge barrier, only for their addressed agent.
- A challenge must target another agent's same-run finding and cite existing
  evidence; at most two challenges per originating agent across all batches.
- Only the addressed agent can reply, once per challenge. REVISE requires a new
  severity and appends a revision in the same transaction as the reply.
- A specialist revises only its own finding. Trusted adjudication can append a
  revision with author `leo`. Apply these revisions before computing the decision;
  `save_decision` verifies its supplied adjudication revisions already exist.

`wait_for_phase` returns true on the requested phase, false on deadline, an already
passed target, or a different terminal outcome. Missing runs raise `LedgerNotFound`.
It polls without keeping a write transaction open and is cancellable.

## Integrity, transactions and errors

All SQL parameters are bound. Each mutation and its event commit together or roll
back together, including cancellation. Invalid records partway through a batch do
not leave earlier records committed. Evidence, claims and debate references must
belong to the same run; JSON fields reject invalid/nonfinite data.

Findings without evidence become UNVERIFIABLE. Original severity stays unchanged;
the effective-findings view resolves the latest authorized revision. Duplicate
immutable IDs conflict instead of replacing records. Claim-set, finding, verdict
and adjudication stub provenance is retained in the saved decision.

Connections enable foreign keys and recursive triggers; WAL allows reader/writer
overlap but SQLite still serializes writers. Lock waits are bounded (five seconds
by default). Transactions must stay short; never hold one across an LLM/HTTP call.
Schema version 1 upgrades the original version-0 skeleton additively; unknown newer
versions are rejected. This is not an arbitrary schema migration system.

Public errors: `LedgerNotFound`, `LedgerConflict`, `LedgerReferenceError`,
`LedgerPermissionError`, `LedgerStateError`, `LedgerBusy`, `LedgerStorageError`.
Messages omit SQL and stored payloads. Event payloads should contain IDs/counts and
safe progress data; never put credentials or raw exception text into events/status.

## Verification and remaining work

`make test` runs ledger checks alongside authentication/foundation checks. Coverage
includes reopen, rollback/cancellation, lock contention, concurrent agents, read
barriers, ownership, references, phase waits, history, event cursors, final decisions,
API startup and the persisted stub demo. CI runs both offline demos as well.
Local verification for this stage: 160 tests passed, Ruff passed, both offline
demos passed and configured database initialization succeeded.

Crustdata cache behavior, real providers, the timeout/tool-budget runner, live
orchestration, automatic interrupted-run recovery, SSE and report APIs remain later
stages. Persisted data survives restart; unfinished jobs are not automatically resumed.
