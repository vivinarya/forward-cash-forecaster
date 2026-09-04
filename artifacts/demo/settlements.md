# Razorpay settlement verification

- batches checked: **113**, flagged: **27** (23.9%)
- gross captured: ₹1,12,47,685.00, commission billed: ₹2,08,283.77 (effective MDR 1.852%)
- commission re-derived from the rate card: ₹2,08,142.73
- **recoverable (deduplicated claim value): ₹29,772.80** - see `recovery.md`
- unexplained credit gaps vs bank feed: ₹0.00
- refunds matched into batches: ₹2,18,140.58
- payments older than the settlement window with no batch: 0

## Flagged batches

| settlement | date | gross | fee_diff | credit_gap | flags |
|---|---|---|---|---|---|
| setl_00000008 | 2026-05-18 | ₹1,08,490.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000009 | 2026-05-18 | ₹1,16,874.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000013 | 2026-05-23 | ₹95,947.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000016 | 2026-05-26 | ₹12,446.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000019 | 2026-05-29 | ₹1,08,034.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000028 | 2026-06-08 | ₹1,63,159.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000029 | 2026-06-08 | ₹32,789.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000032 | 2026-06-11 | ₹1,15,620.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000035 | 2026-06-15 | ₹1,00,228.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000038 | 2026-06-17 | ₹1,49,787.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000045 | 2026-06-24 | ₹81,501.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000049 | 2026-06-29 | ₹1,71,744.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000050 | 2026-06-29 | ₹47,026.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000056 | 2026-07-06 | ₹1,51,382.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000065 | 2026-07-16 | ₹1,21,824.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000076 | 2026-07-27 | ₹32,830.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000080 | 2026-07-31 | ₹1,14,541.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000086 | 2026-08-07 | ₹1,43,375.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000087 | 2026-08-08 | ₹1,45,195.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000090 | 2026-08-12 | ₹91,891.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000091 | 2026-08-13 | ₹1,29,044.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000095 | 2026-08-17 | ₹60,006.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000097 | 2026-08-20 | ₹78,302.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000099 | 2026-08-22 | ₹86,310.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000106 | 2026-08-31 | ₹1,43,451.00 | ₹141.04 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |

_2 more rows in the CSV, not shown._


Arithmetic only: gross - MDR - TMN - GST on fees - TDS - refunds = net credited, per batch,
against `config/fee_schedule.json`. No model involvement, by design.
