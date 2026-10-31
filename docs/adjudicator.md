# Lead analyst (adjudicator)

`leo/adjudicator.py` runs once, in the `ADJUDICATE` phase, after every specialist has written its
verdict. It has three jobs, and a model is trusted with the first two only after code has checked it.

| Job | Model? | What it does |
|---|---|---|
| Confirm gates | No | `confirm_gates` turns analyst-proposed gates into confirmed ones |
| Rule on disputes | Yes, validated | Open disputes become `leo`-authored severity revisions in the ledger |
| Narrative | Yes, with a code fallback | A short, grounded summary for the report |

## How the orchestrator calls it

```python
# phase ADJUDICATE
result = await adjudicate(run_id, repo=repo, llm=llm, model=settings.model_pro)

gates = await confirmed_gates_for_run(run_id, repo)   # after adjudicate, so rulings are counted

# phase SCORING
decision = compute(claims, findings, verdicts, policy, confirmed_gates=gates)
await repo.save_decision(run_id, decision, result)
```

- `adjudicate(run_id)` with no `repo` returns the visibly stubbed result the offline demos use.
- With a `repo` it must run in `ADJUDICATE` (otherwise `LedgerStateError`). `llm=None` skips the model:
  no rulings and a deterministic narrative.
- It writes its revisions to the ledger itself and returns every `leo` revision for the run, so a retried
  call is safe and `save_decision` finds the revisions it checks for.

## Gates (read this)

Analysts never confirm a gate. They write a metric named `gate:<name>` that carries evidence ids. The
adjudicator confirms a gate only when that evidence backs a CONTRADICTED finding whose effective severity
is still at least 4. **If `confirmed_gates` is not passed to `compute`, a fabricated headline claim is not
capped**: on the live Retool test a deck claiming $200M ARR against an estimated $20M to $50M scored
`INVEST_WITH_CONDITIONS` ($43,436 of $100,000) without it and `PASS` ($0) with it.

Known gates: `arr_ratio_over_3`, `funding_round_absent`, `entity_not_active`, `founder_not_found`.

## Guardrails on rulings

- A dispute is a challenge that was rebutted or never answered; at most six are put to the model, most
  severe first.
- A ruling moves a severity at most two steps, and a finding gets one revision.
- A finding that backs a gate can be raised by a ruling but never lowered.
- Reasons and the narrative are length-limited; everything interpolated into the prompt is marked as
  untrusted data.

## Known limit

Agent verdict scores are written before adjudication and are append-only. A ruling changes a finding's
effective severity (and so the credibility inputs and the severity-5 cap in scoring) but not the score
the agent already gave.
