# Cashpilot run - 2026-09-04

Finance back-office agent: reconcile the bank feed against AR/AP, verify gateway settlements
to the paisa, forecast cash 7-30 days ahead, and hand a human a typed exception list.

## Headline

| result | value |
|---|---|
| records reconciled | 725 |
| matched correctly | 701 (99.2% of everything matched) |
| auto-posted without a human | 670 at 100.0% precision |
| exceptions needing a human | 110 |
| settlement batches flagged | 18 |
| forecast horizon | 30 days, P10/P50/P90 over 1200 paths |
| end-to-end wall time | 753.48 ms |
| LLM | off (no key) - deterministic fallback used |

## Outputs

| file | contents |
|---|---|
| matches.csv | 707 rows |
| exceptions.csv | 110 rows |
| settlements.csv | 115 rows |
| forecast.csv | 30 rows |
| party_behaviour.csv | 28 rows |
| aged_receivables.csv | 382 rows |
| unresolved.csv | 16 rows |
| run_manifest.json | stage timings, versions, llm usage |
| accuracy.json | measured score card |


## Stage timings

| stage | ms |
|---|---|
| ingest_ms | 436.09 |
| reconcile_ms | {'prepare_ms': 14.59, 't0_duplicates_ms': 29.14, 't1_settlement_ms': 3 |
| reconcile_total_ms | 138.47 |
| settlement_verify_ms | 3.38 |
| triage_ms | 0.28 |
| forecast_ms | 169.29 |
| total_ms | 753.48 |


Reproduce with `make demo` (or `python -m cashpilot run --data data/synthetic --out artifacts`).
