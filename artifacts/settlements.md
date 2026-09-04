# Razorpay settlement verification

- batches checked: **174**, flagged: **26** (14.9%)
- gross captured: ₹1,70,92,746.00, commission billed: ₹3,16,540.76 (effective MDR 1.852%)
- commission re-derived from the rate card: ₹3,15,353.65
- **recoverable overbilling: ₹1,187.11**
- unexplained credit gaps vs bank feed: ₹0.00
- refunds matched into batches: ₹3,09,710.17
- payments older than the settlement window with no batch: 0

## Flagged batches

| settlement | date | gross | fee_diff | credit_gap | flags |
|---|---|---|---|---|---|
| setl_00000002 | 2026-03-12 | ₹1,07,582.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000004 | 2026-03-14 | ₹1,49,008.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000005 | 2026-03-16 | ₹1,48,021.00 | ₹141.44 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000010 | 2026-03-21 | ₹1,18,561.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000047 | 2026-04-28 | ₹22,106.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000060 | 2026-05-12 | ₹3,952.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000071 | 2026-05-23 | ₹92,993.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000078 | 2026-05-30 | ₹1,45,063.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000084 | 2026-06-05 | ₹1,06,880.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000088 | 2026-06-09 | ₹51,722.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000092 | 2026-06-13 | ₹70,573.00 | ₹159.95 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000095 | 2026-06-16 | ₹4,934.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000102 | 2026-06-24 | ₹1,78,853.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000105 | 2026-06-27 | ₹1,37,022.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000107 | 2026-06-29 | ₹22,643.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000112 | 2026-07-04 | ₹1,24,868.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000116 | 2026-07-08 | ₹1,48,002.00 | ₹224.26 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000120 | 2026-07-13 | ₹1,49,638.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000131 | 2026-07-24 | ₹42,597.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000137 | 2026-07-30 | ₹96,255.00 | ₹112.40 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000138 | 2026-07-31 | ₹1,31,251.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000143 | 2026-08-06 | ₹60,250.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |
| setl_00000150 | 2026-08-14 | ₹71,481.00 | ₹76.78 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000151 | 2026-08-17 | ₹1,60,279.00 | ₹135.63 | ₹0.00 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000164 | 2026-08-28 | ₹35,894.00 | ₹0.00 | ₹0.00 | BATCH_ARITHMETIC |

_1 more rows in the CSV, not shown._


Arithmetic only: gross - MDR - TMN - GST on fees - TDS - refunds = net credited, per batch,
against `config/fee_schedule.json`. No model involvement, by design.
