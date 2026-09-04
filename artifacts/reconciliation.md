# Reconciliation run

_as of 2026-09-05 - strategy `full` - 1709 bank lines vs 2029 documents_

## Measured result

| metric | value | note |
|---|---|---|
| records | 1709 | bank statement lines in the run |
| matched | 1634 | match rate 95.6% |
| correct (exact doc set) | 1629 | precision 99.7%, recall 97.4%, F1 0.9855 |
| partial / wrong | 2 / 3 | partial = subset of the true doc set |
| auto-posted | 1535 | auto-post precision 100.0% |
| exceptions raised | 240 | every unresolved line, typed |
| quarantine accuracy | 100.0% | charges/interest/duplicates correctly left unposted |
| rupee accuracy | 96.6% | share of matchable rupees posted to the right document |

## Where the matches came from

| tier | matches | correct | wrong |
|---|---|---|---|
| t4_amount_exact | 719 | 719 | 0 |
| t3_doc_number | 515 | 515 | 0 |
| t2_advice_utr | 208 | 206 | 2 |
| t1_settlement | 172 | 172 | 0 |
| t7_fuzzy | 13 | 11 | 2 |
| t6_lumpsum | 5 | 5 | 0 |
| t5_amount_name | 2 | 1 | 1 |

## Behaviour by class of line

Aggregate recall averages a class that works with a class that does not. This is the same run,
split by what the line was:

| class of line | lines | exact | partial | wrong | refused | left alone | what right means here | rate |
|---|---|---|---|---|---|---|---|---|
| matchable | 1372 | 1371 | 0 | 0 | 1 | 0 | matched to the exact document set | 99.93% |
| gateway_settlement | 172 | 172 | 0 | 0 | 0 | 0 | matched to the exact document set | 100.0% |
| matchable_amount_mismatch | 108 | 70 | 0 | 3 | 35 | 0 | matched to the exact document set | 64.81% |
| expected_unmatched_duplicate | 31 | 0 | 0 | 0 | 0 | 31 | left unmatched | 100.0% |
| matchable_lumpsum | 20 | 16 | 2 | 0 | 2 | 0 | one line, several documents | 80.0% |
| expected_unmatched_charge | 3 | 0 | 0 | 0 | 0 | 3 | left unmatched | 100.0% |
| expected_unmatched_unknown | 2 | 0 | 0 | 0 | 0 | 2 | left unmatched | 100.0% |
| expected_unmatched_interest | 1 | 0 | 0 | 0 | 0 | 1 | left unmatched | 100.0% |

## Exception mix

| code | count |
|---|---|
| OVERDUE_UNRECONCILED_AR | 118 |
| SHORT_DEDUCTION | 45 |
| DUPLICATE_BANK_LINE | 31 |
| UNALLOCATED_CREDIT | 24 |
| BATCH_ARITHMETIC | 19 |
| UNMATCHED_DEBIT | 13 |
| FEE_TIER_MISMATCH | 7 |
| BANK_CHARGE_NO_DOCUMENT | 5 |
| RESIDUAL_UNALLOCATED | 2 |
| BANK_INTEREST_NO_DOCUMENT | 1 |
| REVERSAL_OR_RETURN | 1 |

## Triage (the only AI step)

- deterministic pre-classification: 266 exceptions
- LLM attempted / accepted / discarded: 0 / 0 / 0 _(skipped: llm_disabled_or_no_key)_
- duplicate-root-cause groupings: 0
- LLM wall time: 0.07 ms; usage: `{"enabled": false, "model": null, "calls": 0, "ok": 0, "failed": 0, "invalid_json": 0, "budget_remaining": 200, "prompt_chars": 0, "completion_chars": 0, "approx_tokens": 0, "wall_ms": 0.0, "errors": []}`

## Top unresolved bank lines

| line | why | amount | claimed | truth | narration |
|---|---|---|---|---|---|
| BL-000037 | no_match | ₹9,41,206.00 | - | AR-INV-2026-000185 | NEFT CR HDFC0001234 900000017066 FROM GOLDCREST WAREHOUSING  |
| BL-000107 | no_match | ₹8,35,655.00 | - | AR-INV-2026-000324 | NEFT CR SBIN0000921 900000023042 FROM ORBITEX PAPER MILLS PV |
| BL-000207 | no_match | ₹8,29,861.00 | - | AR-INV-2026-000423 | NEFT CR UTIB0002233 900000027311 FROM PUSHPA METALS AGRI INP |
| BL-000231 | wrong_document | ₹8,46,102.00 | AR-INV-2026-000423 | AR-INV-2026-000427 | NEFT CR KKBK0000812 900000027614 FROM PUSHPA METALS AGRI INP |
| BL-000303 | no_match | -₹6,39,205.00 | - | AP-RT-2026-000268 | UPI/HDFC0001234/636249098836/pragcon@paytm/PRAGATI CONV |
| BL-000393 | no_match | ₹5,17,298.00 | - | AR-INV-2026-000232 | RTGS SBIN0000921 900000018990 SHREE BALAJI HARDIARE INR 5172 |
| BL-000400 | no_match | -₹1,49,431.00 | - | AP-VB-2026-000193 | UPI/HDFC0001234/773443858908/eversta@paytm/EVERSTONE STAFFIN |
| BL-000545 | no_match | ₹4,13,742.00 | - | AR-INV-2026-000527 | UPI/HDFC0001234/604675279208/anancon@paytm/ANANT INFRA CONVE |
| BL-000593 | no_match | ₹5,18,057.00 | - | AR-INV-2026-000702 | NEFT CR ICIC0000429 900000039840 FROM ASHWINI INTERIORS LTD  |
| BL-000712 | no_match | -₹5,74,798.00 | - | AP-VB-2026-000710 | UPI/HDFC0001234/820212317556/eversta@okhdfcbank/EVERSTONE ST |
| BL-000743 | no_match | -₹2,35,244.00 | - | AP-VB-2026-000634 | UPI/HDFC0001234/442030737713/bluecol@okaxis/BLUECOL CH |
| BL-000746 | no_match | ₹1,97,932.00 | - | AR-INV-2026-000757 | NEFT CR HDFC0001234 900000041761 FROM SAHYADRI POLYMERS PVT  |
| BL-000795 | no_match | -₹5,32,258.00 | - | AP-VB-2026-000791 | NEFT DR ICIC0000429 900000043309 FROM TRINETRA AGRI INPUTS a |
| BL-000806 | no_match | -₹4,43,148.00 | - | AP-RT-2026-000566 | NEFT DR SBIN0000921 900000034095 FROM TRINETRA SOLAR securit |
| BL-000817 | no_match | -₹3,32,658.00 | - | AP-VB-2026-000651 | UPI/HDFC0001234/859565451834/ashwmac@paytm/ASHWINI MACHINE P |


Full list: `unresolved.csv` / `exceptions.csv`.
