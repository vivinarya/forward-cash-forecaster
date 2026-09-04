# Money at stake

## What this run found (no ground truth needed)

| measure | value |
|---|---|
| batches verified | 174 |
| batches with rupees at stake | 26 |
| claim value (deduplicated) | ₹41,074.20 |
|   fee / GST / TDS billed above the rate card | ₹1,424.56 |
|   deductions with no evidence on file | ₹19,824.82 |
|   credit short of what the batches owed | ₹21,249.38 |
| credit recovery rate (money in / money owed) | 98.892% |
| share of gateway gross with a rupee at stake | 14.8% |


A batch inside `config/fee_schedule.json`'s tolerance contributes nothing to any column - it is
not a finding. Fee overbilling and cash shortfall are combined with `max()`, not `+`: when the
credit follows an inflated fee declaration, those are the same rupees.

## Receivables: short payments the customers owe us

| measure | value |
|---|---|
| invoices paid short, paid inside the window | 111 |
| more paid short later in the plan | 32 |
| money withheld from us | ₹20,31,865.00 |
| tied to the invoice (a claim can be raised) | 72 of 111 (64.86%) |
| in the exception queue, not yet attributed | 34 (95.5% of short-paid invoices seen) |
| silently missed | 5 |
| rupee value turned into a claim | ₹11,71,834.00 (57.7%) |
| rupee value sitting in the queue | ₹7,67,220.00 |


## Gateway defects: did we catch what was planted?

The corpus was generated with a ledger of every defect we introduced. Only the generator
knows that ledger; a production run has no such column and this section is omitted.

| defect | found as | planted | caught | catch rate | rupees identified |
|---|---|---|---|---|---|
| fee_wrong_slab | FEE_TIER_MISMATCH | 7 | 7 | 100.0% | ₹1,187.11 of ₹1,187.11 (100.0%) |
| unexplained_shortfall | BATCH_ARITHMETIC | 14 | 14 | 100.0% | ₹2,550.21 of ₹2,550.21 (100.0%) |
| missing_refund_evidence | BATCH_ARITHMETIC | 5 | 5 | 100.0% | ₹17,274.61 of ₹17,274.61 (100.0%) |
| customer_paid_short | SHORT_DEDUCTION | 111 | 72 | 64.86% | ₹11,71,834.00 of ₹20,31,865.00 (57.7%) |


**De-duplicated over batches**: 26 of 26 corrupted
batches flagged (100.0%), ₹21,011.93 of ₹21,011.93 identified
(100.0%). 0 batches were flagged with
no defect planted on them - on real data those are the false positives to review.

## Batches worth chasing

| settlement | settled_on | gross | recoverable | recovery_rate_% | flags |
|---|---|---|---|---|---|
| setl_00000120 | 2026-07-13 | ₹1,49,638.00 | ₹16,811.14 | 94.25 | BATCH_ARITHMETIC |
| setl_00000004 | 2026-03-14 | ₹1,49,008.00 | ₹7,094.64 | 97.57 | BATCH_ARITHMETIC |
| setl_00000071 | 2026-05-23 | ₹92,993.00 | ₹6,394.00 | 96.48 | BATCH_ARITHMETIC |
| setl_00000105 | 2026-06-27 | ₹1,37,022.00 | ₹2,535.94 | 99.05 | BATCH_ARITHMETIC |
| setl_00000002 | 2026-03-12 | ₹1,07,582.00 | ₹1,713.50 | 99.19 | BATCH_ARITHMETIC |
| setl_00000102 | 2026-06-24 | ₹1,78,853.00 | ₹1,157.54 | 99.67 | BATCH_ARITHMETIC |
| setl_00000078 | 2026-05-30 | ₹1,45,063.00 | ₹857.66 | 99.7 | BATCH_ARITHMETIC |
| setl_00000010 | 2026-03-21 | ₹1,18,561.00 | ₹658.94 | 99.71 | BATCH_ARITHMETIC |
| setl_00000112 | 2026-07-04 | ₹1,24,868.00 | ₹427.58 | 99.82 | BATCH_ARITHMETIC |
| setl_00000138 | 2026-07-31 | ₹1,31,251.00 | ₹416.66 | 99.83 | BATCH_ARITHMETIC |
| setl_00000165 | 2026-08-29 | ₹1,49,818.00 | ₹403.98 | 99.7 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000143 | 2026-08-06 | ₹60,250.00 | ₹399.04 | 99.65 | BATCH_ARITHMETIC |
| setl_00000088 | 2026-06-09 | ₹51,722.00 | ₹364.84 | 99.64 | BATCH_ARITHMETIC |
| setl_00000116 | 2026-07-08 | ₹1,48,002.00 | ₹269.12 | 99.8 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000084 | 2026-06-05 | ₹1,06,880.00 | ₹254.66 | 99.88 | BATCH_ARITHMETIC |
| setl_00000092 | 2026-06-13 | ₹70,573.00 | ₹191.93 | 99.72 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000131 | 2026-07-24 | ₹42,597.00 | ₹186.54 | 99.78 | BATCH_ARITHMETIC |
| setl_00000005 | 2026-03-16 | ₹1,48,021.00 | ₹169.73 | 99.88 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000151 | 2026-08-17 | ₹1,60,279.00 | ₹162.77 | 99.9 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000164 | 2026-08-28 | ₹35,894.00 | ₹136.56 | 99.81 | BATCH_ARITHMETIC |
| setl_00000137 | 2026-07-30 | ₹96,255.00 | ₹134.89 | 99.86 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000107 | 2026-06-29 | ₹22,643.00 | ₹121.56 | 99.73 | BATCH_ARITHMETIC |
| setl_00000150 | 2026-08-14 | ₹71,481.00 | ₹92.14 | 99.86 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000047 | 2026-04-28 | ₹22,106.00 | ₹79.28 | 99.75 | BATCH_ARITHMETIC |
| setl_00000060 | 2026-05-12 | ₹3,952.00 | ₹23.04 | 99.7 | BATCH_ARITHMETIC |


Full list in `recovery_batches.csv`. Every number on this page is arithmetic on the input files;
no model output appears in it.

## Notes

- recoverable_paise per batch = fee overbilling + GST/TDS overbilling + unexplained deduction + shortfall in the credit
- recovery_rate_pct = money that arrived ÷ money the batches owed, rupee-weighted, not batch-count weighted
- identified_paise is capped at the planted amount per batch, so a catch rate cannot exceed 100%
- class rows share a batch when two defects hit the same one; the de-duplicated total is batch_defects
- detection and catch rates read the generator's meta.json; the runtime block needs no ground truth
- batches_flagged_with_no_planted_defect is a false-positive count: flagged here, nothing planted here
