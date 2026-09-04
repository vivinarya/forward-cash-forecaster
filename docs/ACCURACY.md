# How every number in this repo is produced

Nothing here is a claim about quality in the abstract; each line is the output of a command you can
run. If a number cannot be reproduced from a command in this file, it does not belong in the README.

Corpora used below: `data/synthetic` (medium scale, 1,709 bank lines, 2,365 documents of which 2,029
have a bank-visible movement, 5,853 gateway payments, 174 settlement batches, seed `20260905`, as-of
2026-09-05) and `data/sample` (the demo corpus: 742 lines, 1,157 documents of which 916 are
bank-visible, 113 batches, seed `4242`,
same as-of date). Both are committed, so nothing has to be generated to check a number.

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

Definitions the tables use:

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
`unresolved_cap` in the score card only bounds the in-memory report table).

## Reproduce the reconciliation numbers

```bash
# the corpus in data/synthetic is reproducible to the byte; regenerate it to see for yourself
python -m cashpilot generate --out data/synthetic --scale medium --seed 20260905 --as-of 2026-09-05
python -m cashpilot bench --data data/synthetic --reps 3 --out-dir artifacts --json artifacts/bench.json
python -m cashpilot bench --data data/sample --reps 1 --forecast --seeded --out-dir artifacts/demo
```

`--as-of` matters for reproduction: without it the world is generated against today's date, the ledger
gets a different cut, and every number below moves by a few tenths of a point. That is why the
`Makefile` passes `$(AS_OF)` (default `2026-09-05`) to every generating target, and why `make demo`
pins the demo corpus to seed `4242` / as-of `2026-09-05` even when it has to build `data/sample`
itself.

`--reps 3` reports the median of three runs (times vary with page cache; accuracy does not vary at
all — the engine is deterministic, and if it ever stops being so, that is a bug worth catching).

### Published corpus — `data/synthetic`

| strategy | precision | recall | F1 | auto-post precision | rupee acc | quarantine | engine ms | lines/s engine |
|---|---|---|---|---|---|---|---|---|
| `exact` | 0.9978 | 0.5341 | 0.6958 | 1.000 | 0.4725 | 1.000 | ~165 | ~10,400 |
| `fuzzy_only` | 0.9957 | 0.8391 | 0.9107 | 1.000 | 0.9047 | 1.000 | ~750 | ~2,300 |
| `full` | 0.9969 | 0.9743 | 0.9855 | 1.000 | 0.9657 | 1.000 | ~485 | ~3,500 |

`exact` leaves **777** of the 1,672 matchable lines with nothing on them at all; `full` refuses **38**.
Uplift as published in `bench.md`: recall `+0.4402`, `+736` correct lines, for `1.38x` the wall time.

By tier (from `accuracy.json` in the run output) — where the matches come from and how good each rung
is on its own:

| tier | matches | correct | wrong |
|---|---|---|---|
| `t4_amount_exact` | 719 | 719 | 0 |
| `t3_doc_number` | 515 | 515 | 0 |
| `t2_advice_utr` | 208 | 206 | 2 |
| `t1_settlement` | 172 | 172 | 0 |
| `t7_fuzzy` | 13 | 11 | 2 |
| `t6_lumpsum` | 5 | 5 | 0 |
| `t5_amount_name` | 2 | 1 | 1 |

By class of bank line (`by_kind`, also printed by `cashpilot run` and written into
`reconciliation.md`). Same run, split by what the line was — this is the table that shows *where* the
ladder works and where it does not:

| class | lines | exact doc set | partial | wrong | refused | rate |
|---|---|---|---|---|---|---|
| `matchable` | 1,372 | 1,371 | 0 | 0 | 1 | 99.93% |
| `gateway_settlement` | 172 | 172 | 0 | 0 | 0 | 100.0% |
| `matchable_amount_mismatch` | 108 | 70 | 0 | 3 | 35 | 64.81% |
| `matchable_lumpsum` | 20 | 16 | 2 | 0 | 2 | 80.0% |
| `expected_unmatched_duplicate` | 31 | 0 | 0 | 0 | — | 100.0% left alone |
| `expected_unmatched_charge` | 3 | 0 | 0 | 0 | — | 100.0% left alone |
| `expected_unmatched_interest` | 1 | 0 | 0 | 0 | — | 100.0% left alone |
| `expected_unmatched_unknown` | 2 | 0 | 0 | 0 | — | 100.0% left alone |

Residue, i.e. the honest exception list for the month: **266 exceptions** —
`OVERDUE_UNRECONCILED_AR` 118, `SHORT_DEDUCTION` 45, `DUPLICATE_BANK_LINE` 31,
`UNALLOCATED_CREDIT` 24, `BATCH_ARITHMETIC` 19, `UNMATCHED_DEBIT` 13, `FEE_TIER_MISMATCH` 7,
`BANK_CHARGE_NO_DOCUMENT` 5, `RESIDUAL_UNALLOCATED` 2, `BANK_INTEREST_NO_DOCUMENT` 1,
`REVERSAL_OR_RETURN` 1. Plus 38 matchable lines refused outright, 2 partial and 3 wrong.

End-to-end wall time for the whole run — ingest, reconcile, settlement verification, recovery
accounting, triage, 2,000-path Monte Carlo, scoring, 8 CSVs and 6 markdown reports — **1,500 ms**
(ingest 685, reconcile 491, verify 5.9, recovery 9.0, triage 0.7, forecast 293) in the run that
produced the committed `artifacts/`. Engine-only throughput ~3,500 lines/s with the ladder, ~10,400
without. Times move ~10% run to run on a shared box, which is why they are rounded in the README;
accuracy is deterministic and moves not at all.

### Small (demo) corpus — 742 bank lines

The corpus `make demo` builds (742 bank lines, 1,157 documents, 916 bank-visible, 113 batches,
120 days of history from 2026-05-08 to 2026-09-05):

| | value |
|---|---|
| `full` | 706 correct of 721 matchable, precision 0.9930, recall 0.9792, F1 0.9861, auto-post 1.000 (662), rupee acc 0.9808 |
| `exact` | recall 0.5728 (vs 0.5341 on the published corpus), 304 matchable lines left with nothing |
| by class | `matchable` 100.0%, `gateway_settlement` 100.0%, `matchable_amount_mismatch` 73.81%, `matchable_lumpsum` 60.0%, all `expected_unmatched_*` 100.0% left alone |
| exceptions | 138 |
| run wall time | 774 ms (ingest 436, engine 145, forecast 177, recovery 5) - timings move a few
  percent between runs; the 24 numbers above this row do not, and only these are claimed |
| forecast @30d | 5.0% error, +55.0% skill vs naive, top-25 settle ranking 93.3% vs 20.0% by size |
| forecast @7d | **14.4% error against the seasonal naive's 12.7% — at a week, on this corpus, the naive wins** |
| band hit 7/14/30d | 100% / 66.7% / 33.3% (3 origins) |

Read those last rows as the honest limit of a small corpus, not as a passing grade. 120 days of history
means 3 usable rolling origins and thin per-party delay curves, so short-horizon *timing* is noisy; a
30-day band built from 14 customers is genuinely wider relative to the balance, and three origins is
not enough to certify a tail. Two more small-sample effects: `t2_advice_utr` is 91/95 correct here
(4 wrong) against 206/208 on the published corpus — the advice tier's failure mode is real and is
listed in [FAILURES.md](FAILURES.md#8-still-open-measured) — and `matchable_lumpsum` is 6/10 rather
than 16/20, because with 10 rows one refusal is 10 points. **Quote `data/synthetic` in a review;
`data/sample` is what the demo command runs, and both are labelled everywhere they appear.**

## Reproduce the money numbers

```bash
python -m cashpilot run --data data/synthetic --out artifacts --runs 2000
# artifacts/recovery.md, artifacts/recovery_batches.csv, run_manifest.json -> "recovery"
python -m cashpilot sweep --scales tiny,sample,medium,large,xl   # the same at five sizes
```

`eval/recovery.py` has two sections and the split is the point. The runtime section is arithmetic on
the files a business already has; the detection section divides by what the generator planted and needs
`meta.json`, which the engine never otherwise opens.

### Runtime, no ground truth (`data/synthetic`, 174 batches)

| measure | value | definition |
|---|---|---|
| batches with rupees at stake | 26 (14.9%) | `recoverable_paise` above the rate card's own per-batch tolerance |
| claim value | ₹41,074.20 | `Σ_batch abs(unexplained) + max(rate-card overbilling, credit shortfall)` |
| ├─ fee/GST/TDS overbilling | ₹1,424.56 | `Σ max(0, declared − recomputed)` per component |
| ├─ unexplained deductions | ₹19,824.82 | `gross − commission − TMN − GST − TDS − refunds − declared_net` |
| └─ under-credited | ₹21,249.38 | `Σ max(0, expected_net − credited)` |
| credit recovery rate | 98.892% | `Σ min(credited, expected_net) / Σ expected_net` |
| gross at stake | 14.8% | gross of flagged batches ÷ gross of all batches |
| AR expected haircut | ₹1,73,85,842.70 of ₹21,53,90,724.00 open (91.93% expected recovery) | `run_manifest.json` -> `recovery.runtime.ar_expected_haircut_paise`, the forecast model's own aging curve |

`max()` rather than `+` in the claim line is not a rounding preference: an inflated fee makes the
declared net too low, so when the credit followed that declaration the overbilling **is** the cash
shortfall, and adding both would bill the same rupee to the client's claim list twice. Measured on
this corpus, summing the three components above naively gives ₹42,498.76 against ₹41,074.20
deduplicated - a 3.5% overstatement of what a client could be charged to chase.

### Detection, against the planted ledger

| defect planted | exception | planted | caught | ₹ identified |
|---|---|---|---|---|
| fee billed on the wrong slab | `FEE_TIER_MISMATCH` | 7 batches | 7 | ₹1,187.11 of ₹1,187.11 (100%) |
| net credited short, nothing on file | `BATCH_ARITHMETIC` | 14 batches | 14 | ₹2,550.21 of ₹2,550.21 (100%) |
| refund row missing from the export | `BATCH_ARITHMETIC` | 5 batches | 5 | ₹17,274.61 of ₹17,274.61 (100%) |
| customer paid an invoice short | `SHORT_DEDUCTION` | 111 invoices | 72 (64.86%) | ₹11,71,834 of ₹20,31,865 (57.7%) |
| **de-duplicated across batches** | | **26** | **26 (100%)** | **₹21,011.93 of ₹21,011.93** |
| batches flagged with nothing planted (false positives) | | | 0 | |

Two definitions worth stating because they are where a metric like this is usually cheated:

* the short-payment denominator is restricted to documents **the bank had received payment for by the
  corpus as-of date** — 111 of the 143 planted rows; the other 32 are future receipts in the plan, which
  no run of this feed could have surfaced;
* an invoice counts as *surfaced* only when the deduction is **tied to it** (a committed match carrying
  that paisa gap, or a `SHORT_DEDUCTION` naming it). 34 more have the exact money sitting on an
  unresolved bank line — a human will see them, no claim exists yet — so the report prints 64.86%
  attributed, 95.5% seen, **5 silently missed**. Only the last number is a failure.

### At five sizes (`artifacts/scale_sweep.md`)

| corpus | bank lines | full recall | auto-post precision | refused | `t6` share of reconcile | lines/s | end-to-end ms | planted batch defects caught | short payments attributed |
|---|---|---|---|---|---|---|---|---|---|
| tiny | 90 | 0.9756 | 1.000 | 2 | 32.5% (`t0`) | 9,021 | 248 | 6/6 | 0.0% (1 invoice in scope) |
| sample | 742 | 0.9792 | 1.000 | 10 | 44.6% | 5,161 | 652 | 27/27 | 72.09% (31 of 43) |
| medium | 1,709 | 0.9743 | 1.000 | 38 | 60.6% | 3,473 | 1,317 | 26/26 | 64.86% (72 of 111) |
| large | 2,641 | 0.9733 | 1.000 | 65 | 74.7% | 2,163 | 2,392 | 42/42 | 56.38% |
| xl | 5,610 | 0.9731 | 1.000 | 124 | 87.6% | 937 | 7,895 | 63/64 | 57.0% |

Recall and the refusal rate are flat across a 62× range in size; auto-post precision is 1.000 at every
size; rupee accuracy 0.953–0.981. Throughput is **not** flat — see the `t6` column — and that is
documented as an open item rather than hidden. `sample` is generated with the same seed as
`data/sample`, so that row is checkable against `artifacts/demo/` line for line.

### On a corpus with no ground truth

Delete `meta.json` and `truth_*.csv` from a data directory and re-run: accuracy is skipped, and
`recovery.md` prints *"Detection rate: not measured"* with the reason, `run_manifest.json` says
`recovery.batch_defects.measured = false`, and the CLI prints `defect catch rate: not measured - this
corpus has no ground-truth ledger to divide by`. Every runtime row above still stands, because none of
them needed the truth files. Tests assert this behaviour
(`tests/test_recovery_and_classes.py::test_runtime_block_needs_no_ground_truth`,
`::test_missing_corruption_is_never_reported_as_a_zero_catch_rate`,
`::test_recovery_md_says_not_measured_instead_of_inventing_a_rate`) so the fallback cannot rot into a
zero.

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
| same, on the demo corpus (3 origins) | 93.3% | 20.0% |

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
  not on a real month of rate-card churn. The planted-defect denominators come from the same file the
  defects were recorded in — the *counts* are auditable, the *rupee* values are re-derived from the
  ledger arithmetic rather than trusted from the generator, which is why `identified` is capped at the
  planted amount per batch.
* Timings are one core of a shared sandbox; treat them as ratios (ingest ~1.4× the engine, t6 60.6% of
  the ladder at this size) rather than SLAs.
* 9 origins × 3 horizons is enough to rank models and not enough to certify a tail. If a band number
  matters to you, run `--forecast-origins 30` on a longer corpus.
