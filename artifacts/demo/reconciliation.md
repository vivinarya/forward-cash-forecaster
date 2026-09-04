# Reconciliation run

_as of 2026-09-05 - strategy `full` - 742 bank lines vs 916 documents_

## Measured result

| metric | value | note |
|---|---|---|
| records | 742 | bank statement lines in the run |
| matched | 711 | match rate 95.8% |
| correct (exact doc set) | 706 | precision 99.3%, recall 97.9%, F1 0.9861 |
| partial / wrong | 4 / 1 | partial = subset of the true doc set |
| auto-posted | 662 | auto-post precision 100.0% |
| exceptions raised | 111 | every unresolved line, typed |
| quarantine accuracy | 100.0% | charges/interest/duplicates correctly left unposted |
| rupee accuracy | 98.1% | share of matchable rupees posted to the right document |

## Where the matches came from

| tier | matches | correct | wrong |
|---|---|---|---|
| t4_amount_exact | 280 | 280 | 0 |
| t3_doc_number | 211 | 211 | 0 |
| t1_settlement | 111 | 111 | 0 |
| t2_advice_utr | 95 | 91 | 4 |
| t7_fuzzy | 12 | 11 | 1 |
| t6_lumpsum | 2 | 2 | 0 |

## Behaviour by class of line

Aggregate recall averages a class that works with a class that does not. This is the same run,
split by what the line was:

| class of line | lines | exact | partial | wrong | refused | left alone | what right means here | rate |
|---|---|---|---|---|---|---|---|---|
| matchable | 558 | 558 | 0 | 0 | 0 | 0 | matched to the exact document set | 100.0% |
| gateway_settlement | 111 | 111 | 0 | 0 | 0 | 0 | matched to the exact document set | 100.0% |
| matchable_amount_mismatch | 42 | 31 | 0 | 1 | 10 | 0 | matched to the exact document set | 73.81% |
| expected_unmatched_duplicate | 15 | 0 | 0 | 0 | 0 | 15 | left unmatched | 100.0% |
| matchable_lumpsum | 10 | 6 | 4 | 0 | 0 | 0 | one line, several documents | 60.0% |
| expected_unmatched_charge | 3 | 0 | 0 | 0 | 0 | 3 | left unmatched | 100.0% |
| expected_unmatched_unknown | 2 | 0 | 0 | 0 | 0 | 2 | left unmatched | 100.0% |
| expected_unmatched_interest | 1 | 0 | 0 | 0 | 0 | 1 | left unmatched | 100.0% |

## Exception mix

| code | count |
|---|---|
| OVERDUE_UNRECONCILED_AR | 56 |
| BATCH_ARITHMETIC | 26 |
| SHORT_DEDUCTION | 16 |
| DUPLICATE_BANK_LINE | 15 |
| UNMATCHED_DEBIT | 8 |
| RESIDUAL_UNALLOCATED | 4 |
| AMBIGUOUS_CANDIDATES | 4 |
| UNALLOCATED_CREDIT | 4 |
| BANK_CHARGE_NO_DOCUMENT | 2 |
| BANK_INTEREST_NO_DOCUMENT | 1 |
| REVERSAL_OR_RETURN | 1 |
| FEE_TIER_MISMATCH | 1 |

## Triage (the only AI step)

- deterministic pre-classification: 137 exceptions
- LLM attempted / accepted / discarded: 0 / 0 / 0 _(skipped: llm_disabled_or_no_key)_
- duplicate-root-cause groupings: 0
- LLM wall time: 0.06 ms; usage: `{"enabled": false, "model": null, "calls": 0, "ok": 0, "failed": 0, "invalid_json": 0, "budget_remaining": 200, "prompt_chars": 0, "completion_chars": 0, "approx_tokens": 0, "wall_ms": 0.0, "errors": []}`

## Top unresolved bank lines

| line | why | amount | claimed | truth | narration |
|---|---|---|---|---|---|
| BL-000125 | partial_set | ₹5,02,807.00 | AR-INV-2026-000067 | AR-INV-2026-000067,AR-INV-2026-000103,AR-INV-2026-000120 | UPI/HDFC0001234/333328504733/INV-2026-000067,INV-2026-000103 |
| BL-000132 | no_match | -₹3,69,849.00 | - | AP-VB-2026-000091 | UPI/HDFC0001234/582319438841/adissol@okaxis/ADISHAKTI SOLAR  |
| BL-000140 | no_match | -₹6,28,112.00 | - | AP-RT-2026-000090 | RTGS SBIN0000921 900000010092 SHREE BALAJI ELECTRICALS COMPA |
| BL-000157 | partial_set | ₹6,36,606.00 | AR-INV-2026-000236 | AR-INV-2026-000236,AR-INV-2026-000249,AR-INV-2026-000252 | UPI/HDFC0001234/952362474639/INV-2026-000236,INV-2026-000249 |
| BL-000213 | no_match | -₹11,83,928.00 | - | AP-EMI-202607-0 | NEFT DR ICIC0000429 900000035667 FROM STATE BANK EQUIP LOAN  |
| BL-000277 | no_match | -₹4,31,028.00 | - | AP-RT-2026-000290 | NEFT DR ICIC0000429 900000018939 FROM NALANDA RECYCLERS PVT  |
| BL-000372 | partial_set | ₹6,78,411.00 | AR-INV-2026-000354 | AR-INV-2026-000354,AR-INV-2026-000405,AR-INV-2026-000417 | UPI/HDFC0001234/780653100517/INV-2026-000354,INV-2026-000405 |
| BL-000454 | no_match | -₹3,24,255.00 | - | AP-VB-2026-000358 | NEFT DR SBIN0000921 900000021892 FROM VAISHNAVI LOGISTICS PV |
| BL-000471 | wrong_document | -₹1,54,739.00 | AP-RT-2026-000482 | AP-VB-2026-000442 | UPI/HDFC0001234/610044769070/adissol@okhdfcbank/ADISBAKTI SO |
| BL-000473 | no_match | -₹33,843.00 | - | AP-VB-2026-000176 | NEFT DR UTIB0002233 900000014467 FROM MERIWAT TREATMENT invo |
| BL-000495 | no_match | ₹2,35,977.00 | - | AR-INV-2026-000363 | NEFT CR SBIN0000921 900000022066 FROM KARMAN PRINT SOLUTIONS |
| BL-000649 | no_match | -₹1,93,710.00 | - | AP-VB-2026-000641 | NEFT DR KKBK0000812 900000033116 FROM NALAREC RECY material  |
| BL-000659 | no_match | ₹5,45,020.00 | - | AR-INV-2026-000609 | NEFT CR ICIC0000429 900000032202 FROM TANSH MACHINE PARTS LT |
| BL-000671 | no_match | -₹1,02,689.00 | - | AP-RT-2026-000396 | NEFT DR ICIC0000429 900000023424 FROM RAMAN DEEP HARDWARE PV |
| BL-000735 | partial_set | ₹4,84,083.00 | AR-INV-2026-000701 | AR-INV-2026-000701,AR-INV-2026-000708,AR-INV-2026-000740 | INV-2026-000701,INV-2026-000708,INV-2026-000740 NEFT CR ICIC |


Full list: `unresolved.csv` / `exceptions.csv`.
