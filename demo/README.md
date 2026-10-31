# Demo decks

Three **synthetic** pitch decks for the demo. Every slide carries a visible disclaimer. The two Retool decks
are test fixtures built from public company data: they are **not issued by Retool**, and the inflated one is
labelled as deliberately false.

| Deck | Company | What it shows |
|---|---|---|
| `decks/01_honest_retool.pdf` | Retool (`retool.com`) | Every figure matches Crustdata's data |
| `decks/02_inflated_retool.pdf` | Retool (`retool.com`) | Same layout, deliberately false figures |
| `decks/03_fake_company_orrery.pdf` | Orrery Vantage Systems GmbH | A company that does not exist |

Regenerate them with `python -m scripts.make_demo_decks` (deterministic; a test fails if the committed
PDFs drift from the generator).

## Run one

Start the API in live mode (`MODE=live`, keys in `.env`, see the main README), then in another terminal:

```sh
python -m scripts.leo_cli run --deck demo/decks/01_honest_retool.pdf --company Retool --domain retool.com --max-check-usd 100000
python -m scripts.leo_cli run --deck demo/decks/02_inflated_retool.pdf --company Retool --domain retool.com --max-check-usd 100000
python -m scripts.leo_cli run --deck demo/decks/03_fake_company_orrery.pdf --company "Orrery Vantage Systems GmbH" --max-check-usd 100000
```

Pass `--domain retool.com` for the Retool decks. A run takes about 90 seconds and spends about 2 Crustdata
credits for the Retool decks and about 1 for the fake company.

## What happens (measured on 31 Oct 2026, real Gemini and Crustdata)

| Deck | Result | Detail |
|---|---|---|
| Honest | `INVEST_WITH_CONDITIONS`, $46,977 of $100,000 | 11 claims; 17 findings verified, 2 flagged, 1 unverifiable; credibility 100, confidence 0.94; no gates. It stops at "with conditions" because EBITDA and profit are never available from Crustdata. |
| Inflated | **`PASS`, $0** | 16 claims; **10 contradicted**, credibility 7.8, confidence 0.83; the `arr_ratio_over_3` gate is confirmed ($200M ARR claimed against an estimated $20M to $50M). |
| Fake | **`cannot_assess`** | No verifiable company, so no agent runs. About 30 seconds. |

The inflated deck's false claims, and what catches each: founded 2012 (it is 2017), headquartered in Austin
(San Francisco), 1,200 employees (413), headcount up 100% (down 10%), $400M raised (141M), a Series D that
Crustdata's funding history does not contain, investors that are not among Crustdata's recorded investors,
$200M ARR, 12M monthly visitors (1.26M) and 120 open roles (1). Customer counts and "revenue grows 3x" stay
unverifiable because Crustdata has no such data.

**Needs PR #19.** Before it, a deck that misstated its founding year was refused as `cannot_assess` even with
the exact domain supplied, so the inflated deck was never assessed.

The honest deck also shows the honest limits: falling traffic and 51 months since the last raise are flagged,
and the deck's customer count is reported as unverifiable.

Numbers shift slightly with live data and model wording; the shape of each outcome is stable.
