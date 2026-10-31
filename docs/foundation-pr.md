# Proposed PR

Title: chore: establish Leo foundation contracts and owned skeleton

Base: main
Head: chore/ayush-foundation-skeleton

## Change

Establish the supplied module layout with explicit Ayush/Daksh file ownership,
immutable contracts, append-only debate storage, canonical ingestion signatures,
four replaceable specialist stubs, and deterministic scoring edge-case policies.

Provide local setup, a health API, an offline fixture-backed smoke demo, and CI.
Live auth, extraction, provider calls, persistent orchestration and run endpoints
remain explicitly unimplemented. This PR is the starting point for parallel work.

## Verification

Local verification: 36 tests passed; Ruff passed; the offline demo completed with
six placeholder findings, four stub verdicts and zero check sizing. Python modules
compile successfully. Hosted CI has not run; no remote is configured yet.

## Limits

Synthetic claims only; real provider fixtures and SDK/model verification remain.
Do not present stub results as verified diligence. Preserve this branch's focused
commits if merging without squashing is desired.
