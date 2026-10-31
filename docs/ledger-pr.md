# feat(ledger): implement persistent SQLite evidence and agent access

Base: `main`

Head: `feat/ayush-ledger`

## Change

Replace ledger placeholders with run-scoped SQLite persistence for claims, evidence,
metrics, context, findings, debate, verdicts, decisions, execution status and events.
Each mutation and its progress event share one transaction. Add bounded phase waits,
versioned initialization, WAL connections, typed errors and rollback on cancellation.

Bind specialist writes and reads to agent identity and phase, preserve append-only
history, validate same-run references, enforce challenge limits and make final
decision persistence atomic with terminal state. Initialize the configured ledger
at API startup; retain unfinished run endpoints as explicit 501 responses.

Add a persisted offline stub demo and CI coverage. Keep offline checks independent
of Daksh's live replacement points. No Daksh-owned implementation files are changed,
and no live provider key or API credit is required.

## Validation

- **160 tests passed locally**, including all prior authentication/foundation tests.
- Ruff and both offline demos passed; configured SQLite initialization succeeded.
- The persistent demo completed with six findings, four verdicts, 43 events,
  explicit stub provenance and zero check sizing, then read its result after reopen.
- Integration tests cover real task cancellation, failed event commits, API startup,
  repeated persistent runs and independence from live feature replacement points.

Hosted CI is not claimed until this branch is pushed and its GitHub Actions run completes.

## Limits

This is storage and restricted ledger access, not the live orchestrator. Provider
clients/cache, execution timeouts/tool budgets, job recovery, SSE and report APIs
remain later stages. Raw repository methods are privileged; API ownership checks
must precede run-data reads. Software capabilities are not process isolation.

Use a merge commit to retain the focused implementation history. Local .env/database
files and the pre-existing edit to docs/foundation-pr.md are excluded.
