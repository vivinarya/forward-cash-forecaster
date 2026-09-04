# Benchmark - data/synthetic

_3 repetitions per strategy; times include CSV ingest._

| strategy | records | matched | correct | partial | wrong | refused (matchable) | precision | recall | f1 | auto_post | auto_precision | rupee_acc | quarantine | ingest ms | engine ms | lines/s (engine) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exact | 1709 | 895 | 893.0 | 2.0 | 0.0 | 777.0 | 0.9978 | 0.5341 | 0.6958 | 825 | 1.0 | 0.4725 | 1.0 | 682.24 | 163.51 | 10452.1 |
| fuzzy_only | 1709 | 1409 | 1403.0 | 0.0 | 6.0 | 263.0 | 0.9957 | 0.8391 | 0.9107 | 1349 | 1.0 | 0.9047 | 1.0 | 681.14 | 747.78 | 2285.4 |
| full | 1709 | 1634 | 1629.0 | 2.0 | 3.0 | 38.0 | 0.9969 | 0.9743 | 0.9855 | 1535 | 1.0 | 0.9657 | 1.0 | 684.21 | 484.23 | 3529.3 |

## What the extra tiers buy

- recall +0.4402 and +736.0 more correct lines vs the regex-only baseline
- cost: 1.38x the wall time of the naive pass

## Forecast accuracy (rolling-origin backtest)

Error of the **cumulative net cash movement** over the horizon, expressed as a share of the money that
actually moved in the window (a percentage of *net* change explodes on weeks where inflow and outflow
nearly cancel, which is why it is not the headline). Lower is better; the baselines run on the same
origins and the same truncated history.

- 9 origins ending 2026-08-05, 2026-07-26, 2026-07-16, 2026-07-06, 2026-06-26, 2026-06-16, 2026-06-06, 2026-05-27, 2026-05-17
- backtest wall time 3973.8 ms (includes re-running reconciliation at every origin)

| model | err% gross @7 | err% gross @14 | err% gross @30 | MAE net @30 (INR) | bias @30 (INR) | skill vs naive | direction right |
|---|---|---|---|---|---|---|---|
| cashpilot | 9.1 | 5.1 | 1.8 | 3,056,403 | -2,279,671 | 50.02% | 100.0% |
| seasonal_naive | 10.1 | 8.7 | 3.6 | 6,115,451 | 460,836 | 0.0% | 100.0% |
| moving_avg | 10.8 | 10.0 | 7.3 | 12,077,883 | 10,560,052 | -97.5% | 100.0% |
| due_date_sum | 14.0 | 7.8 | 4.6 | 7,703,274 | -4,985,748 | -25.96% | 100.0% |


- P10-P90 band hit rate: band_hit_7d 88.9%, band_hit_14d 100.0%, band_hit_30d 100.0%
- and the part no naive baseline can do at all: of the 25 documents the model expects to settle inside a 30-day window, 82.2% really did, against 14.7% when the same open book is ranked by size alone (9 origins)


### Secondary check against the generator's own plan

The seeded world knows what it intends to happen next. This is an upper bound, not the headline:
it shares assumptions with the generator, so it flatters the model.

| horizon | cumulative error | err as share of gross | mean daily MAE (INR) | days within 20% of plan (daily path) | inflow, pred vs actual (INR) |
|---|---|---|---|---|---|
| 7d | 17.565% | 8.8% | 1,917,913 | 0/7 | 23,554,903 vs 24,829,503 |
| 14d | 27.997% | 13.83% | 2,452,551 | 0/14 | 49,825,016 vs 59,157,595 |
| 30d | 12.109% | 5.15% | 2,048,382 | 1/30 | 110,317,478 vs 112,977,705 |


- caveat: Shares structural assumptions with the generator, so this is an upper bound on real-world accuracy. Use the rolling-origin numbers as the headline.


## Caveats, before anyone asks

- accuracy is measured on synthetic data whose messiness (truncated narrations, missing refs, short deductions, duplicates, lumpsums) was planted deliberately by src/cashpilot/synth/world.py; tools/generate_synthetic.py is a thin wrapper around it
- the forecaster is scored on rolling origins of the same synthetic world it was tuned on, so treat absolute errors as optimistic: skill vs the seasonal naive baseline, the top-25 settlement ranking and the band hit rate are the numbers that should survive contact with a real ledger
- daily MAPE on net movement is not a headline metric here - net change is a small difference of two large numbers and is zero on many days, which once made it read 3.3e9%
- timings are one core of a shared CI box; treat them as ratios, not as SLAs
