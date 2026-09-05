# Benchmark - data\sample

_1 repetitions per strategy; times include CSV ingest._

| strategy | records | matched | correct | partial | wrong | refused (matchable) | precision | recall | f1 | auto_post | auto_precision | rupee_acc | quarantine | ingest ms | engine ms | lines/s (engine) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exact | 742 | 417 | 413.0 | 4.0 | 0.0 | 304.0 | 0.9904 | 0.5728 | 0.7258 | 387 | 1.0 | 0.5362 | 1.0 | 400.35 | 71.58 | 10366.7 |
| fuzzy_only | 742 | 574 | 570.0 | 0.0 | 4.0 | 147.0 | 0.993 | 0.7906 | 0.8803 | 542 | 1.0 | 0.861 | 1.0 | 438.73 | 196.19 | 3782.1 |
| full | 742 | 711 | 706.0 | 4.0 | 1.0 | 10.0 | 0.993 | 0.9792 | 0.9861 | 662 | 1.0 | 0.9808 | 1.0 | 547.3 | 136.2 | 5447.7 |

## What the extra tiers buy

- recall +0.4064 and +293.0 more correct lines vs the regex-only baseline
- cost: 1.45x the wall time of the naive pass

## Forecast accuracy (rolling-origin backtest)

Error of the **cumulative net cash movement** over the horizon, expressed as a share of the money that
actually moved in the window (a percentage of *net* change explodes on weeks where inflow and outflow
nearly cancel, which is why it is not the headline). Lower is better; the baselines run on the same
origins and the same truncated history.

- 3 origins ending 2026-08-05, 2026-07-26, 2026-07-16
- backtest wall time 560.2 ms (includes re-running reconciliation at every origin)

| model | err% gross @7 | err% gross @14 | err% gross @30 | MAE net @30 (INR) | bias @30 (INR) | skill vs naive | direction right |
|---|---|---|---|---|---|---|---|
| cashpilot | 14.4 | 4.0 | 5.0 | 5,396,621 | -3,938,979 | 55.03% | 100.0% |
| seasonal_naive | 12.7 | 19.2 | 10.1 | 11,999,737 | -11,999,737 | 0.0% | 100.0% |
| moving_avg | 19.1 | 20.0 | 16.3 | 18,195,439 | -6,312,906 | -51.63% | 100.0% |
| due_date_sum | 20.5 | 12.2 | 15.4 | 17,514,487 | -17,514,487 | -45.96% | 100.0% |


- P10-P90 band hit rate: band_hit_7d 100.0%, band_hit_14d 66.7%, band_hit_30d 33.3%
- and the part no naive baseline can do at all: of the 25 documents the model expects to settle inside a 30-day window, 93.3% really did, against 20.0% when the same open book is ranked by size alone (3 origins)


### Secondary check against the generator's own plan

The seeded world knows what it intends to happen next. This is an upper bound, not the headline:
it shares assumptions with the generator, so it flatters the model.

| horizon | cumulative error | err as share of gross | mean daily MAE (INR) | days within 20% of plan (daily path) | inflow, pred vs actual (INR) |
|---|---|---|---|---|---|
| 7d | 47.356% | 8.35% | 1,339,817 | 0/7 | 13,364,055 vs 10,893,051 |
| 14d | 32.708% | 7.43% | 1,102,220 | 0/14 | 26,623,584 vs 22,197,034 |
| 30d | 44.205% | 9.59% | 1,403,117 | 1/30 | 61,962,934 vs 54,562,112 |


- caveat: Shares structural assumptions with the generator, so this is an upper bound on real-world accuracy. Use the rolling-origin numbers as the headline.


## Caveats, before anyone asks

- accuracy is measured on synthetic data whose messiness (truncated narrations, missing refs, short deductions, duplicates, lumpsums) was planted deliberately by src/cashpilot/synth/world.py; tools/generate_synthetic.py is a thin wrapper around it
- the forecaster is scored on rolling origins of the same synthetic world it was tuned on, so treat absolute errors as optimistic: skill vs the seasonal naive baseline, the top-25 settlement ranking and the band hit rate are the numbers that should survive contact with a real ledger
- daily MAPE on net movement is not a headline metric here - net change is a small difference of two large numbers and is zero on many days, which once made it read 3.3e9%
- timings are one core of a shared CI box; treat them as ratios, not as SLAs
