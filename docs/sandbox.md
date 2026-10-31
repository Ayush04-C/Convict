# Specialist sandbox

`SandboxRunner(repo).run(agent, ctx, phase, timeout=60)` executes ROUND1, CHALLENGE
or REPLY and returns True on completion, False on contained agent failure.
Outer cancellation is recorded and propagated. Ledger/infrastructure errors remain
visible to the coordinator. No global run phase is advanced by this runner.

Before execution it checks the saved context packet, run ID, agent/writer/tool
identity, resolved company and current phase. An exclusive transactional RUNNING
claim prevents two runners from executing the same agent phase simultaneously.

Every execution receives fresh, revocable tool and ledger capabilities:

| Specialist | Allowed provider methods |
|---|---|
| Identity | enrich, web_search |
| Scale/team | enrich |
| Funding | enrich |
| Traction | enrich, job_search |

Enrichment fields are restricted to that specialist's dossier groups. Enrichment
and job search are bound to the run's resolved company ID. Identify, account access,
arbitrary provider methods and cross-company enrichment are not exposed.

At most six tool calls are reserved per phase, including failed and cancelled
calls. The reservation happens before awaiting, so concurrent requests cannot
overspend the budget. Cached calls also count. A tool call may include bounded
provider transport retries; the six-call limit is not a six-HTTP-request guarantee.

The default phase deadline is 60 seconds and may only be shortened. Timeout or
agent exception marks that agent FAILED; the coordinator can continue the other
workers. Errors are stored as TIMEOUT, CANCELLED or AGENT_ERROR, never arbitrary
exception messages. Successful execution is marked DONE. Existing ledger rules
still enforce namespace ownership, evidence references, phase visibility and the
round-one read barrier. Findings without evidence become UNVERIFIABLE.

On phase exit, capabilities are revoked and pending scoped provider/ledger calls
are cancelled and awaited. Captured old capabilities cannot write into later
phases. Previously committed rows remain as append-only audit records; an agent
failure does not roll back its whole phase.

This is cooperative async isolation for trusted Python specialists. It is not
process/memory isolation or a hard stop for blocking code that ignores cancellation.
Private Python attributes are not a security boundary. No generated code is run.
ADK sessions, live specialist construction, phase advancement/barrier coordination
and final scoring remain the responsibilities of agent/pipeline integration.

```sh
python -m pytest tests/test_scoped_tools.py tests/test_sandbox.py
python -m scripts.context_demo --timeout-agent
```

The forced-timeout demo should report Identity FAILED, three other specialists
DONE, and zero real API calls. Tests and both demo modes run in the full CI workflow.
