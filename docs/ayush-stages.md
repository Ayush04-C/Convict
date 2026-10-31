# Ayush implementation stages

Foundation, authentication, ledger, providers, resolution/context and sandbox
execution are implemented. Persistent pipeline coordination and live analysis
remains unfinished. Existing checks are real; test starter docstrings do not count
as implemented tests. See `authentication.md` for setup and manual verification.

| Stage | Scope | Gate |
|---|---|---|
| Foundation | Full tree, frozen contracts, owned stubs, offline harness, CI | Install, lint, checks and offline demo pass |
| Authentication | Demo password hash verification, JWT, configuration checks, safe errors | Valid login; missing/expired/wrong-signature tokens rejected |
| Ledger | Implement LedgerRepository, transactions, events and bounded phase waits | All entity reads/writes, same-run references, rollback, timeout and ownership |
| Providers (implemented) | Crustdata client/cache/dossier, Gemini adapter and typed errors | Mocked failures, cached repeat call, live smoke checks; no live CI |
| Resolution/context (implemented) | Identity gate, lookalikes, bounded web fallback and dossier slices | Clear/tied/empty candidates, frozen packets and ledger persistence |
| Sandbox (implemented) | Scoped writers/tools, call limits and phase execution | Timeout/cancellation, duplicate execution and capability revocation tests |
| Pipeline (next) | Phase controller, barriers, live agent wiring and persistent run | Run saves a decision; failures are reflected honestly |
| API | Upload, status, event stream/replay, report, owner checks | Authenticated client completes a run; other owners cannot access it |
| Scoring/report | Integrate confirmed gates and effective findings; report evidence dates | All policy boundaries and evidence references; no unverifiable data presented as fact |
| Chat/demo (deferred) | Grounded chat; minimal UI only if time permits | Evidence-only answers, repeatable honest/inflated/fake-company demo |
| Submission (later) | Record, document, submit after integration | Green checks, clear limits, under-two-minute recording |

Recalculate timeboxes against the actual 3:30 PM deadline; the original schedule
is a reference, not permission to consume the submission buffer. Cut frontend
polish before API correctness. Keep identity/traction stubs if feature work slips,
with visible disclosure. No new features after 3:20 PM.

Daksh can begin contract-based feature work immediately; full real ledger/provider
integration depends on the corresponding stages above. His arrival need not wait
until all of Ayush's API work is complete.
