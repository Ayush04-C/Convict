# Foundation decisions

These explicit gap resolutions supplement the original blueprints. Modules and HTTP
paths retain their original layout. Implementations must use these contracts.

## Persistence additions

- `runs.phase` is the exact coordination phase, independent of user-facing status.
- `events` stores an ordered integer cursor, run ID, kind, JSON payload and timestamp.
  It supplies SSE progress. Operational logs are separate; do not store secrets in either.
- `context_packets` stores each agent's serialized frozen context, keyed by run and agent.
- `replies` and `finding_revisions` are append-only. Original findings/challenges are
  immutable. Effective severity is the latest authorized revision, or initial severity.
- `runs` and `agent_status` are coordinator-maintained control records and may change.
  API cache entries may refresh. Append-only applies to evidence/debate records, not
  these control records. No UPDATE/DELETE method is exposed to specialist agents.
- Enable foreign keys per connection. All IDs referenced by a write must belong to
  the same run. Enforce write ownership and phase visibility in LedgerWriter.

The schema is initialized by `ledger.db.initialize`; repository operations and
the bound writer are implemented. See `ledger.md` for transactional access rules.
`LedgerPort.get_evidence()` is an additive read helper: own/coordinator evidence
before the round-one barrier and shared evidence afterward. Claim-set metadata is
stored separately to preserve company name and stub provenance, including empty sets.

## Ingestion signatures

| Function | Input | Output | Responsibility |
|---|---|---|---|
| `parse_deck(path: str)` | saved PDF path | `DeckContent` | Validate PDF; cap processing at 30 pages, record truncation, preserve path |
| `extract_claims(deck: DeckContent)` | validated deck | `ClaimSet` | Direct Gemini PDF input, one schema retry, image fallback |
| `normalize_claims(claims: ClaimSet)` | extracted claims | `ClaimSet` | Units, dates and configured exchange-rate conversion |

All three are async. Their initial versions are visibly marked stubs. Synthetic
sample claims are loaded only by the stub extractor, never used as a live fallback.
PDF only in v1; encrypted or malformed files are rejected; upload limit is 20 MB.
API size checks occur before background execution. Async PDF validation failures
become failed-run events, not a retroactive HTTP 422 after a 202 response.

## protocol.py versus base.py

`agents/protocol.py`: structural interfaces only: SpecialistAgent, LedgerPort,
ToolPort and LlmClient, plus immutable AgentContext. No SDK initialization,
network calls, registry imports, prompts or provider-specific logic.

`agents/base.py`: reusable StubSpecialist behavior implementing those interfaces:
write one UNVERIFIABLE finding per testable claim, no challenges, a neutral verdict
(50, 0.3), and explicit stub labels. Daksh's real agents can implement the protocol
directly; inheriting the stub is not required. The base file remains Ayush-owned.

Registry imports are the composition exception: the foundation depends on the
protocol; only `agents/registry.py` imports the four concrete specialist modules.
Daksh preserves those exported class names, and never edits the registry.

Frozen Pydantic context packets use tuple fields. Raw provider data is stored as
JSON text, not mutable nested dicts. Each agent receives its own packet.

## Claims and evidence

Add `identity_founder` to the topic list and route it to Identity. `product_other`
is retained as unverified context, with no credibility credit until a policy-backed
check exists. Agent findings must cite same-run evidence; unsupported assessments
are UNVERIFIABLE. Revenue estimates are estimates, not audited ARR confirmation.

## Scoring edge cases

- Credibility counts each **testable claim once**, even when multiple agents see it.
  Prefer the primary agent's verifiable finding; if several exist, take the least
  credit. Fall back to the least-credit verifiable secondary finding only if the
  primary supplied none. Unverifiable findings contribute no credit or denominator.
- No verifiable claims: credibility is null, effective score is zero, outcome is
  DIG_DEEPER with zero sizing unless a confirmed gate requires PASS.
- Failed/missing agent verdicts contribute zero to their fixed score weights and
  zero to the four-agent confidence average; do not redistribute their weights.
- Reject nonfinite or out-of-range score inputs through schema validation. Reject
  duplicate agent verdicts.
- Scores >=80 with a severity-5 finding are capped at INVEST_WITH_CONDITIONS.
- Tiers use real thresholds >=80, >=65, >=50, else PASS (including decimals).
- Hard gates require explicit evidence of failure. Missing founder/funding/status
  fields mean unknown, not confirmed fraud. An unresolved entity is cannot_assess
  at pipeline level and PASS/zero sizing if represented in the scoring function.
- Missing EBITDA/profit documents cap INVEST at INVEST_WITH_CONDITIONS, as designed.
- Severity penalties belong inside Daksh's dimension scores; Ayush does not apply
  them again when combining scores.
- ARR ratio: <=1 VERIFIED, >1 through 1.5 PARTIAL, >1.5 CONTRADICTED; only >3 triggers
  the critical gate. Missing/nonpositive denominator is UNVERIFIABLE.
- Check size uses the final tier after all caps and cannot exceed the policy limit.
- Stub decisions remain explicitly marked; zero-size/no-evidence results do not
  imply a completed real assessment.

## API and execution boundaries

JWT demo login uses a real password hash verifier (constant-time digest comparison
alone does not hash a supplied password). HS256, expiry, user identity and run owner
must be checked before live run access. Never put JWTs into SSE URLs.

Each phase allows 60 seconds and at most six scoped tool calls per agent. Challenges
are limited to two. Agent read helpers enforce the phase barrier; a convention of
not calling a public raw repo method is not sufficient isolation. Async isolation
does not provide process or memory isolation; execute no LLM-generated code.
