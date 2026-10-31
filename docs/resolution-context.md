# Company resolution and context

`resolve_company(name, domain, claims, client)` identifies by domain when supplied,
otherwise by name. It returns `Resolution`; operational provider failures raise
typed errors rather than becoming a claim that the company does not exist.

- One unique highest-confidence candidate at >=0.9 can resolve automatically.
  Known domain conflicts and a known conflicting founding year block this shortcut.
- Medium-confidence candidates (>=0.5) and ties require a lead of at least two
  independent matching attributes: domain, founding year, HQ country/location,
  employee range. Repeated claims add no votes; contradictory claim values remove
  that attribute from comparison. Missing fields give no credit.
- Low/empty results allow one web search and one Identify batch of at most three
  discovered domains. Search URLs are discovery hints only. A fallback match needs
  a consistent company name, a matching provider domain and a clear score >=0.9.
  An explicitly supplied domain cannot be replaced with a different search result.
- Unresolved ambiguity returns `cannot_assess`, retaining candidate summaries.
  The default whole-operation deadline is 60 seconds, configurable up to 180.

No candidate enrichment or LLM guessing is performed during disambiguation. Sparse
Identify data can therefore leave a real company unresolved; this is intentional.
HQ comparison uses a small explicit country alias table, and employee ranges use
numeric lower/upper bounds. These comparisons do not establish fraud or score claims.

`LedgerRepository.save_resolution` stores the selected ID and audit event once,
only during RESOLVE. An unresolved result atomically ends the run as CANNOT_ASSESS.
The caller then enriches a resolved company and appends its timestamped dossier
evidence before saving packets. No schema migration is needed.

`build_context_packets(claims, dossier_or_slices, policy)` produces four frozen
packets in fixed specialist order, preserving primary/secondary routing and claim
order. `product_other` is shared background with `testable=False`. Dossier groups:

| Specialist | Groups |
|---|---|
| Identity | basic_info, locations, people, news |
| Scale/team | basic_info, headcount, employee_reviews |
| Funding | basic_info, funding, revenue, headcount |
| Traction | basic_info, hiring, web_traffic, followers |

Provider group names/nulls are preserved. Each slice is independent finite JSON;
unknown groups, malformed JSON and duplicate claim IDs are rejected. Stub status
from either claims or dossier propagates. Full evidence timestamps remain in the
ledger; packets do not fabricate new retrieval dates. Existing explicit slice
callers remain supported. Saved packets are append-only.

Manual offline verification, requiring no server, `.env` or API credits:

```sh
make context-demo
python -m scripts.context_demo --timeout-agent
python -m pytest tests/test_entity_resolution.py tests/test_context.py tests/test_resolution_integration.py
```

The demo uses synthetic responses and stub specialists, and deliberately stops at
ROUND1. Production orchestration and the upload/run API are subsequent work.
