# Reconciliation run

_as of 2026-09-04 - strategy `full` - 725 bank lines vs 904 documents_

## Measured result

| metric | value | note |
|---|---|---|
| records | 725 | bank statement lines in the run |
| matched | 707 | match rate 97.5% |
| correct (exact doc set) | 701 | precision 99.2%, recall 97.8%, F1 0.9846 |
| partial / wrong | 6 / 0 | partial = subset of the true doc set |
| auto-posted | 670 | auto-post precision 100.0% |
| exceptions raised | 91 | every unresolved line, typed |
| quarantine accuracy | 100.0% | charges/interest/duplicates correctly left unposted |
| rupee accuracy | 96.5% | share of matchable rupees posted to the right document |

## Where the matches came from

| tier | matches | correct | wrong |
|---|---|---|---|
| t4_amount_exact | 282 | 282 | 0 |
| t3_doc_number | 215 | 215 | 0 |
| t1_settlement | 113 | 113 | 0 |
| t2_advice_utr | 89 | 83 | 6 |
| t7_fuzzy | 6 | 6 | 0 |
| t6_lumpsum | 2 | 2 | 0 |

## Exception mix

| code | count |
|---|---|
| OVERDUE_UNRECONCILED_AR | 55 |
| BATCH_ARITHMETIC | 14 |
| SHORT_DEDUCTION | 12 |
| RESIDUAL_UNALLOCATED | 6 |
| UNALLOCATED_CREDIT | 6 |
| UNMATCHED_DEBIT | 5 |
| FEE_TIER_MISMATCH | 5 |
| BANK_CHARGE_NO_DOCUMENT | 3 |
| DUPLICATE_BANK_LINE | 2 |
| BANK_INTEREST_NO_DOCUMENT | 1 |
| REVERSAL_OR_RETURN | 1 |

## Triage (the only AI step)

- deterministic pre-classification: 109 exceptions
- LLM attempted / accepted / discarded: 0 / 0 / 0 _(skipped: llm_disabled_or_no_key)_
- duplicate-root-cause groupings: 0
- LLM wall time: 0.04 ms; usage: `{"enabled": false, "model": null, "calls": 0, "ok": 0, "failed": 0, "invalid_json": 0, "budget_remaining": 200, "prompt_chars": 0, "completion_chars": 0, "approx_tokens": 0, "wall_ms": 0.0, "errors": []}`

## Top unresolved bank lines

| line | why | amount | claimed | truth | narration |
|---|---|---|---|---|---|
| BL-000027 | no_match | ₹3,49,008.00 | - | AR-INV-2026-000113 | UPI/HDFC0001234/636221896668/zeniele@paytm/ZENITH ONE  |
| BL-000055 | no_match | ₹11,42,705.00 | - | AR-INV-2026-000067 | NEFT CR SBIN0000921 900000008460 FROM SAPTAGIRI WATER TREATM |
| BL-000131 | no_match | -₹2,72,967.00 | - | AP-RT-2026-000215 | NEFT DR HDFC0001234 900000015029 FROM KARMAN CONVEYANCES PVT |
| BL-000200 | partial_set | ₹9,96,949.00 | AR-INV-2026-000168 | AR-INV-2026-000152,AR-INV-2026-000168,AR-INV-2026-000186 | NEFT CR KKBK0000812 900000034198 FROM INDUSLOOM ROOFING PROD |
| BL-000268 | no_match | -₹3,72,174.00 | - | AP-RT-2026-000047 | NEFT DR SBIN0000921 900000007533 FROM RAMAN DEEP HARDWARE PR |
| BL-000275 | partial_set | ₹7,68,664.00 | AR-INV-2026-000068 | AR-INV-2026-000035,AR-INV-2026-000066,AR-INV-2026-000068 | UPI/HDFC0001234/261546371422/INV-2026-000035,INV-2026-000066 |
| BL-000291 | partial_set | ₹20,98,399.00 | AR-INV-2026-000233 | AR-INV-2026-000154,AR-INV-2026-000233,AR-INV-2026-000264 | RTGS UTIB0002233 900000034259 VAISHNAVI TEXTILES JAI INR 209 |
| BL-000325 | no_match | -₹3,49,678.00 | - | AP-RT-2026-000458 | NEFT DR ICIC0000429 900000025545 FROM KARMAN CONVEYANCES FAR |
| BL-000468 | partial_set | ₹12,23,762.00 | AR-INV-2026-000327 | AR-INV-2026-000327,AR-INV-2026-000398,AR-INV-2026-000431 | INV-2026-000327,INV-2026-000398,INV-2026-000431 NEFT CR SBIN |
| BL-000498 | no_match | -₹36,891.00 | - | AP-RT-2026-000331 | NEFT DR ICIC0000429 900000019986 FROM MERIDIAN WATER TREAT a |
| BL-000527 | partial_set | ₹17,57,659.00 | AR-INV-2026-000467 | AR-INV-2026-000465,AR-INV-2026-000467,AR-INV-2026-000504 | UPI/HDFC0001234/767401190263/INV-2026-000465,INV-2026-000467 |
| BL-000545 | no_match | -₹2,26,647.00 | - | AP-VB-2026-000459 | CHQ CLG SAPTAGIRI MACHINE PARTS & CO  |
| BL-000562 | no_match | ₹4,41,211.00 | - | AR-INV-2026-000482 | NEFT CR HDFC0001234 900000026449 FROM INDUSLOOM ROOFING adva |
| BL-000591 | partial_set | ₹11,94,274.00 | AR-INV-2026-000532 | AR-INV-2026-000516,AR-INV-2026-000532,AR-INV-2026-000547 | INV-2026-000516,INV-2026-000532,INV-2026-000547 NEFT CR UTIB |
| BL-000599 | no_match | ₹1,77,936.00 | - | AR-INV-2026-000517 | NEFT CR ICIC0000429 900000027624 FROM EVERSTONE LOGISTICS ma |


Full list: `unresolved.csv` / `exceptions.csv`.
