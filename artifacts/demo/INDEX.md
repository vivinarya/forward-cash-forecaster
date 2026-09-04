# Cashpilot run - 2026-09-05

Finance back-office agent: reconcile the bank feed against AR/AP, verify gateway settlements
to the paisa, forecast cash 7-30 days ahead, and hand a human a typed exception list.

## Headline

| result | value |
|---|---|
| records reconciled | 742 |
| matched correctly | 706 (99.3% of everything matched) |
| auto-posted without a human | 662 at 100.0% precision |
| exceptions needing a human | 138 |
| settlement batches flagged | 27 |
| money identified as recoverable | ₹29,772.80 |
| forecast horizon | 30 days, P10/P50/P90 over 1200 paths |
| end-to-end wall time | 774.4 ms |
| LLM | off (no key) - deterministic fallback used |

## Outputs

| file | contents |
|---|---|
| matches.csv | 711 rows |
| exceptions.csv | 138 rows |
| settlements.csv | 113 rows |
| forecast.csv | 30 rows |
| party_behaviour.csv | 28 rows |
| aged_receivables.csv | 399 rows |
| recovery_batches.csv | 27 rows |
| unresolved.csv | 15 rows |
| run_manifest.json | stage timings, versions, llm usage |
| accuracy.json | measured score card |


## Stage timings

| stage | ms |
|---|---|
| ingest_ms | 435.9 |
| reconcile_ms | {'prepare_ms': 14.96, 't0_duplicates_ms': 30.08, 't1_settlement_ms': 4 |
| reconcile_total_ms | 145.16 |
| settlement_verify_ms | 4.24 |
| triage_ms | 0.51 |
| forecast_ms | 176.93 |
| recovery_ms | 4.84 |
| total_ms | 774.4 |


Reproduce with `make demo` (or `python -m cashpilot run --data data/synthetic --out artifacts`).
