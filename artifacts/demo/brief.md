# Daily cash brief

_generated <built-in method isoformat of datetime.date object at 0x7f21a9d65670>; source: template (deterministic numbers, language model used only to phrase them)_

> Cash is ₹2,20,00,000.00 today and the expected path ends at ₹5,29,08,906.93 in 30 days; the P10 path of ₹4,50,39,821.27 stays above your ₹30,00,000.00 minimum. 707 of 725 bank lines cleared themselves (670 auto-posted) and 91 need a human, dominated by unknown (79), partial_or_short_payment (12), customer_overpayment (6). No funding action needed inside the window - use the slack to clear the exception queue. Gateway verification flagged ₹738.87 overbilling risk, 18 batches flagged. Run `cashpilot bench --forecast` for measured forecast accuracy.

## Evidence behind the sentences

| figure | value |
|---|---|
| today | ₹2,20,00,000.00 |
| h7 | 7 |
| h30 | 30 |
| h7c | ₹3,24,88,824.19 |
| h30c | ₹5,29,08,906.93 |
| h30lo | ₹4,50,39,821.27 |
| h30hi | ₹5,74,38,604.16 |
| floor | ₹30,00,000.00 |
| breach | none inside the window |
| ar | ₹9,25,54,617.00 |
| ar_n | 167 |
| ap | ₹3,74,55,924.00 |
| ap_n | 132 |
| pend | ₹738.87 overbilling risk, 18 batches flagged |
| matched | 707 |
| lines | 725 |
| auto | 670 |
| exc | 91 |
| themes | unknown (79), partial_or_short_payment (12), customer_overpayment (6) |
| worst_day | 2026-09-05 |
| worst | ₹2,17,36,463.95 |
| mape7 | not measured |
| mape30 | not measured |


The prompt and the validation rule (no digit may appear that is not in the evidence block) are
in `src/cashpilot/ai/narrative.py`; the brief is discarded, not edited, if that check fails.
