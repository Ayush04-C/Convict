# Lead analyst (adjudicator)

`leo/adjudicator.py` runs once, in the `ADJUDICATE` phase, after every specialist has written its
verdict. A model is trusted with the first two jobs only after code has checked it.

| Job | Model? | What it does |
|---|---|---|
| Rule on disputes | Yes, validated | Open disputes become `leo`-authored severity revisions in the ledger |
| Narrative | Yes, with a code fallback | A short, grounded summary for the report |
| Gate bookkeeping | No | `confirm_gates` identifies findings that back a proposed hard gate |

## How the pipeline calls it

`leo/orchestrator.py` does this in the `ADJUDICATE` phase:

```python
adjudication = await deps.adjudicate(run_id, repo=repo, llm=deps.llm, model=settings.model_pro or None)
stored = {r.revision_id for r in await repo.get_revisions(run_id)}
for revision in adjudication.revisions:
    if revision.revision_id not in stored:      # the adjudicator already wrote its own rulings
        await repo.add_revision(run_id, revision)
```

- `adjudicate(run_id)` with no `repo` returns the visibly stubbed result the offline demos use.
- With a `repo` it must run in `ADJUDICATE` (otherwise `LedgerStateError`). `llm=None`, a failing model or
  malformed output means no rulings and a deterministic narrative; the run still completes.
- It writes its revisions to the ledger itself and returns every `leo` revision for the run, so a retried
  call is safe and `save_decision` finds the revisions it checks for.

## Hard gates

Analysts never confirm a gate. They write a metric named `gate:<name>` that carries evidence ids.

**Scoring uses `leo/gates.py`, not this module.** The orchestrator confirms gates there by re-running the
analyst rule against the trusted dossier and checking every citation against it, and passes the result to
`compute(..., confirmed_gates=...)`. That is the check that turns a fabricated headline claim into a `PASS`:
on the live Retool test a deck claiming $200M ARR against an estimated $20M to $50M scored
`INVEST_WITH_CONDITIONS` ($43,436 of $100,000) without a confirmed gate and `PASS` ($0) with it.

`confirm_gates` here is a second, severity-based view: a gate counts when its evidence backs a CONTRADICTED
finding whose effective severity is at least 4. The adjudicator uses it to mark those findings PROTECTED in
the prompt and to refuse rulings that would lower them. `confirmed_gates_for_run` is a convenience wrapper
for callers outside the pipeline.

Known gates: `arr_ratio_over_3`, `funding_round_absent`, `entity_not_active`, `founder_not_found`.

## Guardrails on rulings

- A dispute is a challenge that was rebutted or never answered; at most six are put to the model, most
  severe first.
- A ruling moves a severity at most two steps, and a finding gets one revision.
- A finding that backs a gate can be raised by a ruling but never lowered.
- Reasons and the narrative are length-limited; everything interpolated into the prompt is marked as
  untrusted data.

## Known limits

- Agent verdict scores are written before adjudication and are append-only. A ruling changes a finding's
  effective severity (and so the credibility inputs and the severity-5 cap in scoring) but not the score the
  agent already gave.
- The narrative is model-written and can paraphrase loosely; it cites finding ids but is not a verified quote.
- The orchestrator wraps the call in the phase timeout (at most 60 seconds). A model that outlives it fails
  the run, so keep `GEMINI_TIMEOUT` (default 45 seconds) below `PHASE_TIMEOUT`.
