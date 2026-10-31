# Final scoring

Ayush owns `leo/scoring.py`: claim credibility, aggregate score, confidence, final
tier, confirmed gate application and policy-based check sizing. Daksh owns the
specialist dimension scores and their severity penalties, plus lead adjudication.
The coordinator confirms gate proposals against original evidence before scoring.

The scorer preserves the agreed policy:

| Component | Fixed weight |
|---|---|
| Claim credibility | 35% |
| Traction/momentum | 25% |
| Funding/financial | 20% |
| Scale/team | 10% |
| Identity/integrity | 10% |

VERIFIED earns 1, PARTIAL 0.5 and CONTRADICTED 0. Each policy-backed testable claim
counts once, weighted by materiality. Prefer verifiable primary-specialist findings;
use the least credit if multiple exist. If no verifiable primary finding exists,
only the configured secondary specialists may supply fallback credit. Uncited or
UNVERIFIABLE findings provide neither credit nor credibility denominator.

Funding/revenue/team-size claims have materiality 5; growth/traction/hiring 4;
identity 3. Non-testable/background-only claims are excluded from the assessment's
claim count. Unknown policy-backed claims reduce the verifiable share without
being treated as contradicted. No verifiable claims gives null credibility, zero
effective total and DIG_DEEPER/zero sizing unless a confirmed gate forces PASS.

Confidence is 0.5 × verifiable claim share + 0.3 × average specialist confidence
(fixed denominator four) + 0.2 × successful final verdict share (also four).
Missing specialists contribute zero to their original weights and confidence;
weights are never redistributed. Analyst severity penalties are not applied again.

The unchanged tier thresholds are >=80 INVEST, >=65 INVEST_WITH_CONDITIONS,
>=50 DIG_DEEPER, otherwise PASS. A severity-5 finding caps INVEST at conditions.
Missing profit documentation also caps INVEST at conditions. Any confirmed hard
gate forces PASS/zero sizing. No rounding occurs before tier selection.

Check size is max_check_usd × final-tier percentage × confidence. Percentages are
1.0 for INVEST, 0.5 for conditions and zero otherwise. Evidence confirmation and
the scorer's trusted `confirmed_gates` input are separate boundaries: the scorer
does not infer fraud from missing data or treat arbitrary agent text as a gate.

## Interfaces and stored calculation

`compute(...) -> Decision` remains supported. `compute_with_breakdown(...)` returns
that same Decision plus a JSON-compatible calculation: per-claim credit/citations,
materiality, each dimension's fixed contribution, confidence components, raw total,
effective-result overrides, caps and sizing inputs. Inputs are revalidated even
when constructed through Pydantic's validation-bypassing copy methods.

The pipeline calculates once and stores the decision, adjudication and breakdown
atomically in the existing decisions JSON column. The ledger verifies that the
breakdown's result matches the decision. No schema migration is required. Reports
expose `scoring_breakdown`; Markdown/CLI render it without recomputing anything.
Older decisions have a null breakdown and remain unchanged.

```sh
python -m pytest tests/test_scoring.py tests/test_scoring_policy.py tests/test_scoring_integration.py
make api-demo
```

Coverage includes fractional boundaries, gate priority, missing/unknown data,
primary/secondary selection, evidence-free findings, materiality, fixed weights,
confidence, validation bypasses, atomic persistence and legacy report behavior.
