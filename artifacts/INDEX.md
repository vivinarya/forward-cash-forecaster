# Cashpilot run - 2026-09-05

Finance back-office agent: reconcile the bank feed against AR/AP, verify gateway settlements
to the paisa, forecast cash 7-30 days ahead, and hand a human a typed exception list.

## Headline

| result | value |
|---|---|
| records reconciled | 1709 |
| matched correctly | 1629 (99.7% of everything matched) |
| auto-posted without a human | 1535 at 100.0% precision |
| exceptions needing a human | 266 |
| settlement batches flagged | 26 |
| forecast horizon | 30 days, P10/P50/P90 over 2000 paths |
| end-to-end wall time | 1532.25 ms |
| LLM | off (no key) - deterministic fallback used |

## Outputs

| file | contents |
|---|---|
| matches.csv | 1634 rows |
| exceptions.csv | 266 rows |
| settlements.csv | 174 rows |
| forecast.csv | 30 rows |
| party_behaviour.csv | 56 rows |
| aged_receivables.csv | 909 rows |
| unresolved.csv | 43 rows |
| run_manifest.json | stage timings, versions, llm usage |
| accuracy.json | measured score card |


## Stage timings

| stage | ms |
|---|---|
| ingest_ms | 751.12 |
| reconcile_ms | {'prepare_ms': 26.35, 't0_duplicates_ms': 68.66, 't1_settlement_ms': 1 |
| reconcile_total_ms | 484.34 |
| settlement_verify_ms | 5.03 |
| triage_ms | 0.64 |
| forecast_ms | 277.87 |
| total_ms | 1532.25 |


Reproduce with `make demo` (or `python -m cashpilot run --data data/synthetic --out artifacts`).
