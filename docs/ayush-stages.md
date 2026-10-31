# Ayush implementation stages

The current branch establishes the starter repository only. Existing scoring and
contract checks are real; test starter docstrings do not count as implemented tests.

| Stage | Scope | Gate |
|---|---|---|
| Foundation (current) | Full tree, frozen contracts, owned stubs, offline harness, CI | Install, lint, checks and offline demo pass |
| Authentication | Demo password hash verification, JWT, configuration checks, safe errors | Valid login; missing/expired/wrong-signature tokens rejected |
| Ledger | Implement LedgerRepository, transactions, events and bounded phase waits | All entity reads/writes, same-run references, rollback, timeout and ownership |
| Providers | Crustdata client/cache/dossier, Gemini adapter and typed errors | Mocked timeout/429/402/5xx/null cases, cached repeat call, no live CI |
| Resolution/context | Identity gate, lookalikes, web fallback and dossier slices | Clear/tied/empty candidates, immutable private packets |
| Sandbox/pipeline | Phase barriers, scoped writers/tools, limits, persistent run | Stub run saves decision; one failed agent does not hang others |
| API | Upload, status, event stream/replay, report, owner checks | Authenticated client completes a run; other owners cannot access it |
| Scoring/report | Integrate confirmed gates and effective findings; report evidence dates | All policy boundaries and evidence references; no unverifiable data presented as fact |
| Chat/demo | Grounded chat; minimal UI only if time permits | Evidence-only answers, repeatable honest/inflated/fake-company demo |
| Submission | Integrate Daksh behind interfaces, record, document, submit | Green checks, clear limits, under-two-minute recording |

Recalculate timeboxes against the actual 3:30 PM deadline; the original schedule
is a reference, not permission to consume the submission buffer. Cut frontend
polish before API correctness. Keep identity/traction stubs if feature work slips,
with visible disclosure. No new features after 3:20 PM.

Daksh can begin contract-based feature work immediately; full real ledger/provider
integration depends on the corresponding stages above. His arrival need not wait
until all of Ayush's API work is complete.
