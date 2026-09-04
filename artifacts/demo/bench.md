# Benchmark - data/sample

_1 repetitions per strategy; times include CSV ingest._

| strategy | records | matched | correct | partial | wrong | unmatched_but_matchable | precision | recall | f1 | auto_post | auto_precision | rupee_acc | quarantine | ingest ms | engine ms | lines/s (engine) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exact | 725 | 417 | 411.0 | 6.0 | 0.0 |  | 0.9856 | 0.5732 | 0.7248 | 392 | 1.0 | 0.4943 | 1.0 | 433.81 | 68.31 | 10613.0 |
| fuzzy_only | 725 | 575 | 573.0 | 0.0 | 2.0 |  | 0.9965 | 0.7992 | 0.887 | 554 | 1.0 | 0.8895 | 1.0 | 426.36 | 190.23 | 3811.3 |
| full | 725 | 707 | 701.0 | 6.0 | 0.0 |  | 0.9915 | 0.9777 | 0.9846 | 670 | 1.0 | 0.9655 | 1.0 | 427.7 | 136.65 | 5305.7 |

## What the extra tiers buy

- recall +0.4045 and +290.0 more correct lines vs the regex-only baseline
- cost: 1.12x the wall time of the naive pass

## Forecast accuracy (rolling-origin backtest)

Error of the **cumulative net cash movement** over the horizon, expressed as a share of the money that
actually moved in the window (a percentage of *net* change explodes on weeks where inflow and outflow
nearly cancel, which is why it is not the headline). Lower is better; the baselines run on the same
origins and the same truncated history.

- 3 origins ending 2026-08-04, 2026-07-25, 2026-07-15
- backtest wall time 591.4 ms (includes re-running reconciliation at every origin)

| model | err% gross @7 | err% gross @14 | err% gross @30 | MAE net @30 (INR) | bias @30 (INR) | skill vs naive | direction right |
|---|---|---|---|---|---|---|---|
| cashpilot | 16.0 | 13.8 | 3.1 | 3,533,877 | -387,438 | 65.46% | 100.0% |
| seasonal_naive | 9.3 | 19.8 | 8.9 | 10,231,769 | -3,264,098 | 0.0% | 100.0% |
| moving_avg | 15.0 | 22.9 | 18.9 | 20,472,836 | 10,897,284 | -100.09% | 100.0% |
| due_date_sum | 20.6 | 10.6 | 9.8 | 10,772,462 | -10,772,462 | -5.28% | 100.0% |


- P10-P90 band hit rate: band_hit_7d 100.0%, band_hit_14d 33.3%, band_hit_30d 0.0%
- and the part no naive baseline can do at all: of the 25 documents the model expects to settle inside a 30-day window, 84.0% really did, against 16.0% when the same open book is ranked by size alone (3 origins)


### Secondary check against the generator's own plan

The seeded world knows what it intends to happen next. This is an upper bound, not the headline:
it shares assumptions with the generator, so it flatters the model.

| horizon | cumulative error | err as share of gross | mean daily MAE (INR) | days within 20% of plan (daily path) | inflow, pred vs actual (INR) |
|---|---|---|---|---|---|
| 7d | 64.905% | 25.04% | 870,304 | 0/7 | 15,095,443 vs 8,574,577 |
| 14d | 8.695% | 3.58% | 1,472,401 | 0/14 | 28,805,949 vs 26,973,109 |
| 30d | 33.317% | 8.9% | 1,457,602 | 2/30 | 63,820,890 vs 55,001,388 |


- caveat: Shares structural assumptions with the generator, so this is an upper bound on real-world accuracy. Use the rolling-origin numbers as the headline.


## Caveats, before anyone asks

- accuracy is measured on synthetic data whose messiness (truncated narrations, missing refs, short deductions, duplicates, lumpsums) was planted deliberately by src/cashpilot/synth/world.py; tools/generate_synthetic.py is a thin wrapper around it
- the forecaster is scored on rolling origins of the same synthetic world it was tuned on, so treat absolute errors as optimistic: skill vs the seasonal naive baseline, the top-25 settlement ranking and the band hit rate are the numbers that should survive contact with a real ledger
- daily MAPE on net movement is not a headline metric here - net change is a small difference of two large numbers and is zero on many days, which once made it read 3.3e9%
- timings are one core of a shared CI box; treat them as ratios, not as SLAs
