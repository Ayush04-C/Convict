# Daksh handoff

1. Clone the shared repository after the foundation PR is merged, or check out
   the foundation branch explicitly while it is under review.
2. Follow README setup and install `requirements-features.txt` for live features.
3. Run `make test`, `make lint`, `make demo` before changing code.
4. Read `app/schemas.py`, `agents/protocol.py`, `tests/fakes.py`, and `docs/contracts.md`.
5. Read low-level reference sections 5, 8 and 9.
6. Create `feat/daksh-claim-extraction`; edit only your paths in `docs/ownership.md`.

## Stable replacement points

| File | Export |
|---|---|
| `ingest/pdf_parser.py` | async `parse_deck(path: str) -> DeckContent` |
| `ingest/claim_extractor.py` | async `extract_claims(deck: DeckContent) -> ClaimSet` |
| `ingest/normalize.py` | async `normalize_claims(claims: ClaimSet) -> ClaimSet` |
| `agents/identity.py` | `IdentityAgent` |
| `agents/scale_team.py` | `ScaleTeamAgent` |
| `agents/funding.py` | `FundingAgent` |
| `agents/traction.py` | `TractionAgent` |
| `leo/adjudicator.py` | async `adjudicate(run_id: str) -> AdjudicationResult` |

Every specialist implements the three methods in SpecialistAgent. The registry
does not change. Remove stub markers only when a path actually performs real work.
Analysts are pure Python; their HTTP dependencies go through scoped tools outside
the analyst module. Use the approved fixed ingestion exchange rate from config
when normalization is implemented; do not silently assume a rate.

## What is unfinished

LedgerRepository, bound LedgerWriter and authentication are implemented. Read
`docs/ledger.md` for phase/ownership rules and the additive `get_evidence` helper.
Crustdata and shared Gemini clients are implemented; see `docs/providers.md` for
native response shapes, cache provenance and usage. Scoped provider tools, the timeout runner and persistent
orchestration remain unfinished. Their methods intentionally raise
NotImplementedError. API run stubs return 501 after successful authentication.
The current FakeLedger supports specialist stub writes only; do not mistake it
for complete challenge/evidence test support. Extend support through Ayush as
your feature tests require it. Default dependencies include Google GenAI and
PyMuPDF. Live smoke checks passed for Crustdata and configured Gemini FAST;
other model choices still need their own validation.

The standalone offline harness is for smoke checks, not a submitted live pipeline.
