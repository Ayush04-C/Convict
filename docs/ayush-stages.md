# Ayush implementation stages

Foundation, authentication, ledger, providers, resolution/context and sandbox
execution, pipeline coordination and run APIs are implemented. The real lead
adjudicator is implemented (`adjudicator.md`). Saved reports and authenticated CLI are implemented;
final scoring is verified against the existing weights and thresholds, with a
persisted calculation breakdown. Test starter docstrings do not count
as implemented tests. See `authentication.md` for setup and manual verification.

| Stage | Scope | Gate |
|---|---|---|
| Foundation | Full tree, frozen contracts, owned stubs, offline harness, CI | Install, lint, checks and offline demo pass |
| Authentication | Demo password hash verification, JWT, configuration checks, safe errors | Valid login; missing/expired/wrong-signature tokens rejected |
| Ledger | Implement LedgerRepository, transactions, events and bounded phase waits | All entity reads/writes, same-run references, rollback, timeout and ownership |
| Providers (implemented) | Crustdata client/cache/dossier, Gemini adapter and typed errors | Mocked failures, cached repeat call, live smoke checks; no live CI |
| Resolution/context (implemented) | Identity gate, lookalikes, bounded web fallback and dossier slices | Clear/tied/empty candidates, frozen packets and ledger persistence |
| Sandbox (implemented) | Scoped writers/tools, call limits and phase execution | Timeout/cancellation, duplicate execution and capability revocation tests |
| Pipeline (implemented) | Phase controller, barriers, live agent wiring, confirmed gates and persistent run | Run saves a decision; failures are reflected honestly |
| API (implemented) | Upload, status, event stream/replay, saved reports and owner checks | Authenticated client completes a run; other owners cannot access it |
| Report/CLI (implemented) | Project existing saved scores, gates, findings, dates and debate into JSON/Markdown | No scoring recalculation; API and CLI preserve the same decision |
| Final scoring (implemented) | Policy-backed claim credit, fixed contributions, confidence, gates and sizing | Boundary/routing/validation tests and atomic saved breakdown; no analyst penalty repeated |
| Demo | CLI and saved report only; no frontend and no chat | Repeatable honest/inflated/fake-company demo from the CLI |
| Submission (later) | Record, document, submit after integration | Green checks, clear limits, under-two-minute recording |

Recalculate timeboxes against the actual 3:30 PM deadline; the original schedule
is a reference, not permission to consume the submission buffer. Cut frontend
polish before API correctness. Keep identity/traction stubs if feature work slips,
with visible disclosure. No new features after 3:20 PM.

Daksh can begin contract-based feature work immediately; full real ledger/provider
integration depends on the corresponding stages above. His arrival need not wait
until all of Ayush's API work is complete.
