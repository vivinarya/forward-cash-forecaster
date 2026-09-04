# Daily cash brief

_generated <built-in method isoformat of datetime.date object at 0x7fb572f20a10>; source: template (deterministic numbers, language model used only to phrase them)_

> Cash is ₹2,20,00,000.00 today and the expected path ends at ₹8,12,31,446.98 in 30 days; the P10 path of ₹6,88,71,082.54 stays above your ₹30,00,000.00 minimum. 1634 of 1709 bank lines cleared themselves (1535 auto-posted) and 240 need a human, dominated by unknown (146), partial_or_short_payment (45), duplicate_bank_posting (31). No funding action needed inside the window - use the slack to clear the exception queue. Gateway verification flagged ₹41,074.20 overbilling risk, 26 batches flagged. Run `cashpilot bench --forecast` for measured forecast accuracy.

## Evidence behind the sentences

| figure | value |
|---|---|
| today | ₹2,20,00,000.00 |
| h7 | 7 |
| h30 | 30 |
| h7c | ₹3,80,89,754.70 |
| h30c | ₹8,12,31,446.98 |
| h30lo | ₹6,88,71,082.54 |
| h30hi | ₹8,32,73,751.47 |
| floor | ₹30,00,000.00 |
| breach | none inside the window |
| ar | ₹21,53,90,724.00 |
| ar_n | 327 |
| ap | ₹7,29,45,142.00 |
| ap_n | 208 |
| pend | ₹41,074.20 overbilling risk, 26 batches flagged |
| matched | 1634 |
| lines | 1709 |
| auto | 1535 |
| exc | 240 |
| themes | unknown (146), partial_or_short_payment (45), duplicate_bank_posting ( |
| worst_day | 2026-09-06 |
| worst | ₹2,18,25,230.10 |
| mape7 | not measured |
| mape30 | not measured |


The prompt and the validation rule (no digit may appear that is not in the evidence block) are
in `src/cashpilot/ai/narrative.py`; the brief is discarded, not edited, if that check fails.
