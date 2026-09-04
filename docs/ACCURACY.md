# How every number in this repo is produced

Nothing here is a claim about quality in the abstract; each line is the output of a command you can
run. If a number cannot be reproduced from a command in this file, it does not belong in the README.

## The scoring contract

`eval/accuracy.py` compares engine output to `truth_matches.csv`, written by the generator and **never
read by the reconciler** (only `load_dataset` for the evaluation pass opens it).

| ground-truth kind | meaning | what counts as success |
|---|---|---|
| `matchable` | one document, resolvable | matched set == truth set |
| `matchable_lumpsum` | several documents, one credit | matched set == truth set (a subset is `partial`) |
| `matchable_amount_mismatch` | paid short / with a deduction | matched set == truth set; the paisa gap is measured separately |
| `gateway_settlement` | a payout credit, verified not posted | matched to the settlement pseudo-document |
| `expected_unmatched_{charge,interest,unknown,duplicate}` | lines that must not be posted | **no** match, and an exception raised |
| (anything else) | — | reported, not scored |

Definitions the table in README uses:

```
precision = correct / lines_matched            # of what we posted, how much was right
recall    = correct / lines_matchable          # of what was available, how much we got
auto_post_precision = correct_and_auto / auto  # the only number that must be ~1.0
rupee_accuracy      = rupees_correct / rupees_matchable   # money-weighted, not line-weighted
quarantine_accuracy = correctly_left_alone / quarantine_lines
```

Two deliberate choices that make the numbers *worse*, and are the reason they mean something:

* matching 1 of the 3 invoices a lumpsum covers is `partial`, not `correct`;
* a line the engine matched but did not need to match (a bank charge attributed to an invoice) is
  `wrong` and also decrements `quarantine_accuracy`.

`unresolved.csv` lists every refusal with the reason, the truth documents it should have found, the
amount and the verbatim narration. That file *is* the exception list; it is not capped in the CSV (the
`--unresolved-cap` in the score card only bounds the in-memory report table).

## Reproduce the reconciliation numbers

```bash
# the corpus in data/synthetic is reproducible to the byte; regenerate it to see for yourself
python -m cashpilot generate --out data/synthetic --scale medium --seed 20260905 --as-of 2026-09-05
python -m cashpilot bench --data data/synthetic --reps 3 --out-dir artifacts --json artifacts/bench.json
```

`--as-of` matters for reproduction: without it the world is generated against today's date, the ledger
gets a different cut, and every number below moves by a few tenths of a point.

`--reps 3` reports the median of three runs (times vary with page cache; accuracy does not vary at
all — the engine is deterministic, and if it ever stops being so, that is a bug worth catching).

### Published corpus — `data/synthetic`, medium scale (1,709 bank lines, 5,853 gateway payments, 174 batches)

| strategy | precision | recall | F1 | auto-post precision | rupee acc | quarantine | engine ms | lines/s engine |
|---|---|---|---|---|---|---|---|---|
| `exact` | 0.9978 | 0.5341 | 0.6958 | 1.000 | 0.4725 | 1.000 | ~160 | ~10,600 |
| `fuzzy_only` | 0.9957 | 0.8391 | 0.9107 | 1.000 | 0.9047 | 1.000 | ~745 | ~2,300 |
| `full` | 0.9969 | 0.9743 | 0.9855 | 1.000 | 0.9657 | 1.000 | ~475 | ~3,500 |

By tier (from `accuracy.json` in the run output) — where the matches come from and how good each rung
is on its own:

| tier | matches | correct | wrong |
|---|---|---|---|
| `t3_doc_number` | 515 | 515 | 0 |
| `t4_amount_exact` | 719 | 719 | 0 |
| `t2_advice_utr` | 208 | 206 | 2 |
| `t1_settlement` | 172 | 172 | 0 |
| `t6_lumpsum` | 5 | 5 | 0 |
| `t7_fuzzy` | 13 | 11 | 2 |
| `t5_amount_name` | 2 | 1 | 1 |

Residue, i.e. the honest exception list for the month: **266 exceptions** —
`OVERDUE_UNRECONCILED_AR` 118, `SHORT_DEDUCTION` 45, `DUPLICATE_BANK_LINE` 31,
`UNALLOCATED_CREDIT` 24, `BATCH_ARITHMETIC` 19, `UNMATCHED_DEBIT` 13, `FEE_TIER_MISMATCH` 7,
`BANK_CHARGE_NO_DOCUMENT` 5, `RESIDUAL_UNALLOCATED` 2, `BANK_INTEREST_NO_DOCUMENT` 1,
`REVERSAL_OR_RETURN` 1. Plus 38 matchable lines refused outright, 2 partial and 3 wrong.

End-to-end wall time for the whole run — ingest, reconcile, verify, triage, 2,000-path Monte Carlo,
scoring, seven CSVs and five markdown reports — **1,532 ms** (ingest 751, reconcile 484, verify 5,
triage 0.6, forecast 278) in the run that produced the committed `artifacts/`. Engine-only throughput
~3,500 lines/s with the ladder, ~10,600 without. Times move ~10% run to run on a shared box, which is
why they are rounded in the README; accuracy is deterministic and moves not at all.

### Small (demo) corpus — 725 bank lines

`python -m cashpilot bench --data data/sample --reps 1 --forecast --seeded`, the corpus `make demo`
builds (725 bank lines, 904 documents, 120 days):

| | value |
|---|---|
| `full` | 701 correct of 717 matchable, precision 0.9915, recall 0.9777, auto-post 1.000, rupee acc 0.9655 |
| `exact` | recall 0.5732 (vs 0.5341 on the published corpus) |
| exceptions | 110 |
| run wall time | 753 ms (ingest 436, engine 138) |
| forecast @30d | 3.1% error, +65.5% skill vs naive, top-25 settle ranking 84.0% vs 16.0% by size |
| forecast @7d | **16.0% error, −67% skill — the seasonal naive wins at a week here** |
| band hit 7/14/30d | 100% / 33% / **0%** |

Read those last two rows as the honest limit of a small corpus, not as a passing grade. 120 days of
history means 3 usable rolling origins and thin per-party delay curves, so short-horizon *timing* is
noisy; a 30-day band built from 14 customers is genuinely wider relative to the balance, and three
origins is not enough to certify a tail. Two more small-sample effects: `t2_advice_utr` is 83/89
correct here (6 wrong) against 206/208 on the published corpus — the advice tier's failure mode is real
and is listed in [FAILURES.md](FAILURES.md#7-still-open-measured) — and `exact` recall is 0.5732 rather
than 0.5341. The demo corpus is for the walkthrough; `data/synthetic` is the number to quote.

## Reproduce the forecast numbers

```bash
python -m cashpilot bench --data data/synthetic --reps 1 --forecast --seeded --forecast-origins 10 --bt-runs 1500
python -m cashpilot forecast --data data/synthetic --horizon 30 --backtest   # the same tables, faster
python -m cashpilot run --data data/synthetic --out artifacts --backtest      # writes artifacts/backtest.json
```

**Design of the evaluation** (`forecast/backtest.py`):

1. Origins are stepped backwards every 10 days from the last observed line (overlapping origins are
   the standard setup; one origin per horizon-length window leaves 3 points in a 180-day series, and
   3 points is not an accuracy claim). 9 usable origins after a 75-day warm-up.
2. At every origin the dataset is truncated to `date <= origin` — lines, documents **and ledger
   status**, which `_slice_dataset` resets to unposted so the origin cannot know about payments made
   later ([FAILURES.md #4](FAILURES.md)).
3. The **real** `Reconciler` runs on the truncated dataset, then `learn_ledger`, then `forecast`. No
   stage is reused from a later date.
4. Actuals come from the full feed for `origin+1 … origin+H`.
5. Metrics per model, cumulative over the window: MAE of net movement (paise), signed bias, error as a
   share of gross movement, MAPE on net (kept for reference, unstable by construction), band hit rate
   for P10–P90, and skill vs the seasonal naive.
6. `top25_hit_rate_pct` is different: it ranks the open book by *what the model expects to settle* and
   checks the top 25 against `doc_clear_dates` (ground truth from the full recon, used only as the
   label). The control is the same book ranked by outstanding amount.

| model | err % of gross @7 | @14 | @30 | MAE net @30 | bias @30 | skill vs naive | direction |
|---|---|---|---|---|---|---|---|
| cashpilot | 9.1 | 5.1 | **1.8** | ₹30.6 L | −₹22.8 L | **+50.0%** | 100% |
| seasonal naive (same weekday, 8 weeks) | 10.1 | 8.7 | 3.6 | ₹61.2 L | +₹4.6 L | — | 100% |
| 4-week moving average | 10.8 | 10.0 | 7.3 | ₹1.21 Cr | +₹1.06 Cr | −97.5% | 100% |
| sum of amounts due in window | 14.0 | 7.8 | 4.6 | ₹77.0 L | −₹49.9 L | −26.0% | 100% |

P10–P90 band hit rate 88.9% / 100% / 100% at 7/14/30 days (9 origins, so one miss is 11 points —
quote it with the origin count, always).

**Which invoice** — the metric no flow-based baseline can express:

| | cashpilot | ranked by size |
|---|---|---|
| top-25 expected settlements that actually happened, 30d | **82.2%** | 14.7% |
| same, on the demo corpus | 84.0% | 16.0% |

### Secondary: the seeded future plan

`--seeded` compares the 30-day forecast against `truth_future_cash.csv`, the generator's own intended
receipts and payments. On the large corpus: inflow ₹11.03 Cr predicted vs ₹11.30 Cr actual (−2.4%),
outflow ₹5.11 Cr vs ₹4.56 Cr (+12.1%), cumulative net error 12.1% of net / **5.15% of gross
movement**, mean daily absolute error ₹20.5 L, and 1 of 30 days within 20% of the planned daily net.

**This is an upper bound on accuracy, not a better number** — the model and the generator share
structural assumptions (who pays whom, the payment-lag distribution, weekend skipping). It is kept
because it isolates the *statistical* error from the *reconciliation* error: if the seeded check is
good and the rolling-origin check is bad, the input data is the problem.

### What is optimistic, and by how much we think

* The delay distributions are learned from the same world they are scored on. A real ledger has
  customers who leave, seasonal shocks, and one festival month; none of that is in this corpus. The
  honest reading of "1.8% at 30 days" is "the machinery is right and the world is smooth".
* The synthetic gateway is self-consistent by construction (a batch's declared fee is the truth in
  97% of cases) so the verifier's false-positive rate is measured on 26 flagged batches out of 174,
  not on a real month of rate-card churn.
* Timings are one core of a shared sandbox; treat them as ratios (ingest 1.5× the engine, t6 is 61% of
  the ladder) rather than SLAs.
* 9 origins × 3 horizons is enough to rank models and not enough to certify a tail. If a band number
  matters to you, run `--forecast-origins 30` on a longer corpus.
