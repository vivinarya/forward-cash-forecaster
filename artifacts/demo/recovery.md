# Money at stake

## What this run found (no ground truth needed)

| measure | value |
|---|---|
| batches verified | 113 |
| batches with rupees at stake | 27 |
| claim value (deduplicated) | ₹29,772.80 |
|   fee / GST / TDS billed above the rate card | ₹169.26 |
|   deductions with no evidence on file | ₹14,801.77 |
|   credit short of what the batches owed | ₹14,971.03 |
| credit recovery rate (money in / money owed) | 97.917% |
| share of gateway gross with a rupee at stake | 25.27% |


A batch inside `config/fee_schedule.json`'s tolerance contributes nothing to any column - it is
not a finding. Fee overbilling and cash shortfall are combined with `max()`, not `+`: when the
credit follows an inflated fee declaration, those are the same rupees.

## Receivables: short payments the customers owe us

| measure | value |
|---|---|
| invoices paid short, paid inside the window | 43 |
| more paid short later in the plan | 28 |
| money withheld from us | ₹9,06,548.00 |
| tied to the invoice (a claim can be raised) | 31 of 43 (72.09%) |
| in the exception queue, not yet attributed | 10 (95.35% of short-paid invoices seen) |
| silently missed | 2 |
| rupee value turned into a claim | ₹7,22,983.00 (79.8%) |
| rupee value sitting in the queue | ₹1,73,477.00 |


## Gateway defects: did we catch what was planted?

The corpus was generated with a ledger of every defect we introduced. Only the generator
knows that ledger; a production run has no such column and this section is omitted.

| defect | found as | planted | caught | catch rate | rupees identified |
|---|---|---|---|---|---|
| fee_wrong_slab | FEE_TIER_MISMATCH | 1 | 1 | 100.0% | ₹141.04 of ₹141.04 (100.0%) |
| unexplained_shortfall | BATCH_ARITHMETIC | 21 | 21 | 100.0% | ₹5,470.08 of ₹5,470.08 (100.0%) |
| missing_refund_evidence | BATCH_ARITHMETIC | 5 | 5 | 100.0% | ₹9,331.69 of ₹9,331.69 (100.0%) |
| customer_paid_short | SHORT_DEDUCTION | 43 | 31 | 72.09% | ₹7,22,983.00 of ₹9,06,548.00 (79.8%) |


**De-duplicated over batches**: 27 of 27 corrupted
batches flagged (100.0%), ₹14,942.81 of ₹14,942.81 identified
(100.0%). 0 batches were flagged with
no defect planted on them - on real data those are the false positives to review.

## Batches worth chasing

| settlement | settled_on | gross | recoverable | recovery_rate_% | flags |
|---|---|---|---|---|---|
| setl_00000087 | 2026-08-08 | ₹1,45,195.00 | ₹4,918.50 | 98.27 | BATCH_ARITHMETIC |
| setl_00000035 | 2026-06-15 | ₹1,00,228.00 | ₹4,380.48 | 97.77 | BATCH_ARITHMETIC |
| setl_00000050 | 2026-06-29 | ₹47,026.00 | ₹4,091.60 | 95.55 | BATCH_ARITHMETIC |
| setl_00000090 | 2026-08-12 | ₹91,891.00 | ₹3,683.64 | 97.95 | BATCH_ARITHMETIC |
| setl_00000086 | 2026-08-07 | ₹1,43,375.00 | ₹1,589.16 | 99.43 | BATCH_ARITHMETIC |
| setl_00000108 | 2026-09-02 | ₹1,68,457.00 | ₹1,205.28 | 99.63 | BATCH_ARITHMETIC |
| setl_00000091 | 2026-08-13 | ₹1,29,044.00 | ₹936.88 | 99.61 | BATCH_ARITHMETIC |
| setl_00000009 | 2026-05-18 | ₹1,16,874.00 | ₹726.38 | 99.68 | BATCH_ARITHMETIC |
| setl_00000065 | 2026-07-16 | ₹1,21,824.00 | ₹707.40 | 99.69 | BATCH_ARITHMETIC |
| setl_00000038 | 2026-06-17 | ₹1,49,787.00 | ₹702.00 | 99.75 | BATCH_ARITHMETIC |
| setl_00000019 | 2026-05-29 | ₹1,08,034.00 | ₹640.60 | 99.68 | BATCH_ARITHMETIC |
| setl_00000013 | 2026-05-23 | ₹95,947.00 | ₹634.06 | 99.66 | BATCH_ARITHMETIC |
| setl_00000049 | 2026-06-29 | ₹1,71,744.00 | ₹629.18 | 99.81 | BATCH_ARITHMETIC |
| setl_00000045 | 2026-06-24 | ₹81,501.00 | ₹624.38 | 99.61 | BATCH_ARITHMETIC |
| setl_00000080 | 2026-07-31 | ₹1,14,541.00 | ₹618.62 | 99.71 | BATCH_ARITHMETIC |
| setl_00000097 | 2026-08-20 | ₹78,302.00 | ₹541.90 | 99.6 | BATCH_ARITHMETIC |
| setl_00000099 | 2026-08-22 | ₹86,310.00 | ₹507.24 | 99.7 | BATCH_ARITHMETIC |
| setl_00000008 | 2026-05-18 | ₹1,08,490.00 | ₹497.22 | 99.76 | BATCH_ARITHMETIC |
| setl_00000110 | 2026-09-04 | ₹72,123.00 | ₹443.40 | 99.69 | BATCH_ARITHMETIC |
| setl_00000028 | 2026-06-08 | ₹1,63,159.00 | ₹386.66 | 99.88 | BATCH_ARITHMETIC |
| setl_00000056 | 2026-07-06 | ₹1,51,382.00 | ₹334.54 | 99.88 | BATCH_ARITHMETIC |
| setl_00000032 | 2026-06-11 | ₹1,15,620.00 | ₹232.24 | 99.89 | BATCH_ARITHMETIC |
| setl_00000076 | 2026-07-27 | ₹32,830.00 | ₹230.48 | 99.64 | BATCH_ARITHMETIC |
| setl_00000106 | 2026-08-31 | ₹1,43,451.00 | ₹169.26 | 99.88 | FEE_TIER_MISMATCH;FEE_COMPONENT_MISMATCH |
| setl_00000029 | 2026-06-08 | ₹32,789.00 | ₹167.12 | 99.74 | BATCH_ARITHMETIC |


Full list in `recovery_batches.csv`. Every number on this page is arithmetic on the input files;
no model output appears in it.

## Notes

- recoverable_paise per batch = fee overbilling + GST/TDS overbilling + unexplained deduction + shortfall in the credit
- recovery_rate_pct = money that arrived ÷ money the batches owed, rupee-weighted, not batch-count weighted
- identified_paise is capped at the planted amount per batch, so a catch rate cannot exceed 100%
- class rows share a batch when two defects hit the same one; the de-duplicated total is batch_defects
- detection and catch rates read the generator's meta.json; the runtime block needs no ground truth
- batches_flagged_with_no_planted_defect is a false-positive count: flagged here, nothing planted here
