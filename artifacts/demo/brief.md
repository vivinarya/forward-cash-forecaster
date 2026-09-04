# Daily cash brief

_generated <built-in method isoformat of datetime.date object at 0x7fc08337beb0>; source: template (deterministic numbers, language model used only to phrase them)_

> Cash is ₹2,20,00,000.00 today and the expected path ends at ₹5,00,47,205.77 in 30 days; the P10 path of ₹4,16,89,548.39 stays above your ₹30,00,000.00 minimum. 711 of 742 bank lines cleared themselves (662 auto-posted) and 111 need a human, dominated by unknown (87), partial_or_short_payment (16), duplicate_bank_posting (15). No funding action needed inside the window - use the slack to clear the exception queue. Gateway verification flagged ₹29,772.80 overbilling risk, 27 batches flagged. Run `cashpilot bench --forecast` for measured forecast accuracy.

## Evidence behind the sentences

| figure | value |
|---|---|
| today | ₹2,20,00,000.00 |
| h7 | 7 |
| h30 | 30 |
| h7c | ₹3,00,72,011.11 |
| h30c | ₹5,00,47,205.77 |
| h30lo | ₹4,16,89,548.39 |
| h30hi | ₹5,22,55,281.63 |
| floor | ₹30,00,000.00 |
| breach | none inside the window |
| ar | ₹8,43,23,641.00 |
| ar_n | 165 |
| ap | ₹3,92,02,701.00 |
| ap_n | 139 |
| pend | ₹29,772.80 overbilling risk, 27 batches flagged |
| matched | 711 |
| lines | 742 |
| auto | 662 |
| exc | 111 |
| themes | unknown (87), partial_or_short_payment (16), duplicate_bank_posting (1 |
| worst_day | 2026-09-06 |
| worst | ₹2,13,64,681.00 |
| mape7 | not measured |
| mape30 | not measured |


The prompt and the validation rule (no digit may appear that is not in the evidence block) are
in `src/cashpilot/ai/narrative.py`; the brief is discarded, not edited, if that check fails.
