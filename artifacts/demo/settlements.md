# Razorpay settlement verification

- batches checked: **115**, flagged: **18** (15.7%)
- gross captured: ₹1,14,44,150.00, commission billed: ₹2,12,735.02 (effective MDR 1.859%)
- commission re-derived from the rate card: ₹2,11,996.15
- **recoverable overbilling: ₹738.87**
- unexplained credit gaps vs bank feed: ₹0.00
- refunds matched into batches: ₹2,32,010.78
- payments older than the settlement window with no batch: 0

## Flagged batches

| settlement | date | gross | fee_diff | credit_gap | flags |
|---|---|---|---|---|---|
| setl_00000027 | 2026-06-05 | ₹1,00,828.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000029 | 2026-06-08 | ₹1,19,794.00 | ₹105.76 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000032 | 2026-06-11 | ₹1,59,399.00 | ₹115.79 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000034 | 2026-06-13 | ₹1,58,429.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000037 | 2026-06-16 | ₹9,341.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000047 | 2026-06-27 | ₹1,43,173.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000053 | 2026-07-04 | ₹1,11,477.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000059 | 2026-07-10 | ₹61,467.00 | ₹191.74 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000066 | 2026-07-17 | ₹1,25,885.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000071 | 2026-07-22 | ₹1,43,885.00 | ₹146.72 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH;BATCH_ARITHMETIC |
| setl_00000081 | 2026-08-01 | ₹1,08,185.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000082 | 2026-08-03 | ₹1,40,787.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000089 | 2026-08-10 | ₹1,54,876.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000093 | 2026-08-14 | ₹1,27,258.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000098 | 2026-08-19 | ₹1,52,529.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000101 | 2026-08-22 | ₹1,23,969.00 | ₹178.86 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000105 | 2026-08-26 | ₹1,42,023.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000107 | 2026-08-28 | ₹1,36,434.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |


Arithmetic only: gross - MDR - TMN - GST on fees - TDS - refunds = net credited, per batch,
against `config/fee_schedule.json`. No model involvement, by design.
