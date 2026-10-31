# chore: establish Leo foundation contracts and owned skeleton

Base: `main`

Head: `chore/ayush-foundation-skeleton`

## Change

Leo needs a shared starting point so Ayush can build the infrastructure and API
while Daksh replaces feature placeholders without editing Ayush's files.

This PR creates the supplied repository structure, setup instructions, ownership
rules, and interfaces for both developers. It includes:

- Immutable domain schemas, a canonical ingestion contract, and specialist protocols.
- SQLite schema initialization, ordered events, stored agent contexts, and
  append-only findings, replies, and severity revisions.
- Four replaceable specialist stubs, fixture-backed extraction, and a stub adjudicator.
- Deterministic scoring with missing-data handling, hard gates, confidence,
  verdict thresholds, check sizing, and severity caps.
- A health API, explicit 501 responses for unfinished routes, provider/pipeline
  starter modules, and an offline demo requiring no credentials or API credits.
- Dependency constraints, GitHub Actions, the original architecture references,
  implementation stages, and Daksh's handoff instructions.

All implementation commits are on the foundation branch. `main` contains only
the empty bootstrap commit needed as a PR base.

## Verification

- 36 tests passed locally.
- Ruff and Python compilation passed.
- Offline demo completed with six placeholder findings, four stub verdicts,
  explicit stub labels, and zero check sizing.
- Local server startup and `/health` were verified.

Hosted CI results should be checked on this PR; local results do not imply a
successful GitHub Actions run.

## Limits

This is a foundation skeleton, not a complete diligence product. Live authentication,
PDF extraction, provider requests, ledger repository operations, sandbox enforcement,
persistent orchestration, and run/report endpoints remain unfinished.

Fixtures are synthetic; real provider fixtures and SDK/model compatibility still
need verification. Test starter files contain no tests and are not counted in the
36 passing checks. Stub outputs must not be presented as verified diligence.

The branch uses focused commits. Use a merge commit rather than squash if that
history should remain visible after merging.
