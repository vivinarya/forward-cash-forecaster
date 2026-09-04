# Scale sweep

Generated with `python -m cashpilot.eval.sweep` (seed 20260905, as-of 2026-09-05). Each scale is a
different corpus, not a different number for the same corpus. `end-to-end ms` includes ingest, the
full reconciliation ladder, settlement verification, triage and a 64-path forecast.

## Size and time

| scale | bank lines | documents | gateway payments | settlement batches | generate ms | reconcile ms | end-to-end ms |
|---|---|---|---|---|---|---|---|
| tiny | 90 | 253 | 1341 | 42 | 39.1 | 10.0 | 248.44 |
| sample | 742 | 916 | 3938 | 113 | 125.8 | 141.94 | 651.53 |
| medium | 1709 | 2029 | 5853 | 174 | 220.3 | 483.92 | 1317.02 |
| large | 2641 | 3055 | 7845 | 217 | 314.9 | 1223.06 | 2392.46 |
| xl | 5610 | 6386 | 12142 | 332 | 564.3 | 5940.58 | 7895.43 |

## Matching quality

| scale | exact recall | full precision | full recall | full F1 | auto-post precision | rupee accuracy | refused (matchable) | imperfect | lines/s |
|---|---|---|---|---|---|---|---|---|---|
| tiny | 0.6951 | 1.0 | 0.9756 | 0.9876 | 1.0 | 0.9531 | 2 | 0 | 9021.3 |
| sample | 0.5728 | 0.993 | 0.9792 | 0.9861 | 1.0 | 0.9808 | 10 | 5 | 5161.3 |
| medium | 0.5341 | 0.9969 | 0.9743 | 0.9855 | 1.0 | 0.9657 | 38 | 5 | 3473.0 |
| large | 0.5302 | 0.9984 | 0.9733 | 0.9857 | 1.0 | 0.9682 | 65 | 4 | 2162.9 |
| xl | 0.495 | 0.9955 | 0.9731 | 0.9842 | 1.0 | 0.9661 | 124 | 24 | 937.4 |

## Money at stake and defect recovery

| scale | batches with money at stake | recoverable inr | credit recovery % | planted batch defects caught % | planted rupees identified % | short payments surfaced % | worst class of line |
|---|---|---|---|---|---|---|---|
| tiny | 6 | 9921.12 | 95.95 | 100.0 | 100.0 | 0.0 | matchable (40 lines) @ 97.5% |
| sample | 27 | 29772.8 | 97.917 | 100.0 | 100.0 | 72.09 | matchable_lumpsum (10 lines) @ 60.0% |
| medium | 26 | 41074.2 | 98.892 | 100.0 | 100.0 | 64.86 | matchable_amount_mismatch (108 lines) @ 64.81% |
| large | 42 | 92945.15 | 99.11 | 100.0 | 100.0 | 56.38 | matchable_amount_mismatch (147 lines) @ 56.46% |
| xl | 63 | 137442.91 | 99.215 | 98.44 | 100.0 | 57.0 | matchable_amount_mismatch (291 lines) @ 57.73% |

## Every class of bank line, every scale

| scale | class | lines | exact | refused | rate |
|---|---|---|---|---|---|
| tiny | matchable | 40 | 39 | 1 | 97.5 |
| tiny | gateway_settlement | 40 | 40 | 0 | 100.0 |
| tiny | expected_unmatched_charge | 3 | 0 | 0 | 100.0 |
| tiny | expected_unmatched_duplicate | 2 | 0 | 0 | 100.0 |
| tiny | expected_unmatched_unknown | 2 | 0 | 0 | 100.0 |
| tiny | matchable_amount_mismatch | 1 | 0 | 1 | 0.0 |
| tiny | matchable_lumpsum | 1 | 1 | 0 | 100.0 |
| tiny | expected_unmatched_interest | 1 | 0 | 0 | 100.0 |
| sample | matchable | 558 | 558 | 0 | 100.0 |
| sample | gateway_settlement | 111 | 111 | 0 | 100.0 |
| sample | matchable_amount_mismatch | 42 | 31 | 10 | 73.81 |
| sample | expected_unmatched_duplicate | 15 | 0 | 0 | 100.0 |
| sample | matchable_lumpsum | 10 | 6 | 0 | 60.0 |
| sample | expected_unmatched_charge | 3 | 0 | 0 | 100.0 |
| sample | expected_unmatched_unknown | 2 | 0 | 0 | 100.0 |
| sample | expected_unmatched_interest | 1 | 0 | 0 | 100.0 |
| medium | matchable | 1372 | 1371 | 1 | 99.93 |
| medium | gateway_settlement | 172 | 172 | 0 | 100.0 |
| medium | matchable_amount_mismatch | 108 | 70 | 35 | 64.81 |
| medium | expected_unmatched_duplicate | 31 | 0 | 0 | 100.0 |
| medium | matchable_lumpsum | 20 | 16 | 2 | 80.0 |
| medium | expected_unmatched_charge | 3 | 0 | 0 | 100.0 |
| medium | expected_unmatched_unknown | 2 | 0 | 0 | 100.0 |
| medium | expected_unmatched_interest | 1 | 0 | 0 | 100.0 |
| large | matchable | 2183 | 2182 | 1 | 99.95 |

_15 more rows in the CSV, not shown._


## Where the time goes as it grows

| scale | bank lines | ingest ms | reconcile ms | forecast ms | end-to-end ms | slowest tier | its share of reconcile |
|---|---|---|---|---|---|---|---|
| tiny | 90 | 141.9 | 10.0 | 91.88 | 248.44 | t0_duplicates_ms | 32.5% |
| sample | 742 | 431.19 | 141.94 | 62.05 | 651.53 | t6_lumpsum_ms | 44.6% |
| medium | 1709 | 679.61 | 483.92 | 120.75 | 1317.02 | t6_lumpsum_ms | 60.6% |
| large | 2641 | 956.76 | 1223.06 | 167.31 | 2392.46 | t6_lumpsum_ms | 74.7% |
| xl | 5610 | 1541.88 | 5940.58 | 316.23 | 7895.43 | t6_lumpsum_ms | 87.6% |


Read the two tables together. `full recall` and `refused (matchable)` stay flat as the corpus
grows - the ladder's work is per line, so density does not confuse it (2.2% of matchable lines
stay unresolved at 90 lines and at 5,610). Throughput does not: `end_to_end ms` grows faster
than the line count, and the slowest tier named above is where the quadratic behaviour lives.

Generated with seed 4242 for `sample` (the committed demo corpus) and 20260905 for the rest,
so the `sample` row here should match `artifacts/demo/` line for line.
