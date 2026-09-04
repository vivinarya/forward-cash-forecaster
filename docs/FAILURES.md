# What did not work, and what it cost

Every entry is a real change we made, measured on a real run, kept even when the outcome was
embarrassing. Where a number came from a corpus we later invalidated, that is said out loud.

Two corpora appear below:

* **`t3`** — the current one: 1,709 bank lines, 2,029 documents, seed `20260905`, `as_of` 2026-09-05,
  180 days of history, no future information in the ledger files. All headline numbers.
* **`t2`** — an earlier corpus that leaked 316 future-dated documents into `invoices.csv`/`bills.csv`.
  Numbers from it are *directionally* useful and are labelled. Never quoted as accuracy.

---

## 1. Exact reference matching resolved barely half the feed

The first working engine matched on invoice number in the narration, UTR, and settlement id — the
"obvious" regex answer — and was scored on `t3`:

| approach | precision | recall | rupee accuracy |
|---|---|---|---|
| regex + UTR + settlement id (`bench --strategies exact`) | 0.9978 | **0.5341** | 0.4725 |
| + amount, name similarity, subset-sum, inference (`full`) | 0.9969 | **0.9743** | 0.9657 |

**53.4% → 97.4% recall, precision held at 0.997, +44 points.** Cost: engine time 166 ms → 490 ms on
1,709 lines — 1.40× total wall time including ingest, 2.95× the matching work itself. Note the rupee accuracy swing (0.47 → 0.97): the missing half
was not small lines, it was the *big* ones — customers pay in lumpsums and put the reference in a
truncated 90-character narration.

The specific things that broke the naive pass, with the tier written to fix each:
`norm.invoice_tokens` (t3), truncated-name similarity with token containment (t5/t7), subset-sum over
an inverted token index (t6), duplicate-UTR quarantine (t0), single-candidate inference (t8).

---

## 2. A "run-rate for everything else" term double-counted unledgered movement

`forecast/engine.py` once had three buckets for inflow: booked documents, same-window churn (documents
raised *and* settled inside the horizon), and `_misc_projection` — a flat "money moved that we cannot
attribute to a document" run-rate. The second and third describe the same money.

Deleting `_misc_projection` fixed it, and the temptation is to call that the end of the story. Instead
we re-added the term to the current build and measured what it costs, because a number is the only
argument that survives a rewrite:

| 30-day forecast outflow (₹ lakh, `t3`) | value | error vs actual (455.9) |
|---|---|---|
| shipped model | 510.9 | **+12.1%** |
| with the run-rate term re-added | 557.5 | **+22.3%** |

The term would add ₹46.7 L of outflow and ₹172.4 L of inflow — 9.1% and 15.6% of the totals, on top of
churn that is already in there. It is now a no-op function with a comment pointing here, and
`tests/test_forecast_engine.py::test_every_rupee_is_attributed_to_exactly_one_component` fails if the
forecast total stops closing against `book + prospective + gateway` to within one paise per day.

**The related trap:** the first version of the churn component used "receipts in the window that no
document explains", which counted bills raised since the last reference date — *already in the ledger*.
The rewrite measures only documents raised **and** settled inside the same window.

---

## 3. Three forecast statistics, in the order we got them wrong

### 3a. Laplace-smoothed histogram pinned the prior at two-thirds weight

`_pmf` originally smoothed a party's observed payment delays with a 256-bin Laplace histogram whose
pseudo-count `alpha` grew with `n`. For a party with 6 observations that put ~2/3 of the weight on the
global prior and smeared money across the whole window: ~12% of every invoice expected per window
regardless of who owed it. On `t2`, origin 2026-08-05, the 30-day net movement came out
**+₹3,27 L vs an actual +₹382 L (−14%, a 2× under-call)**.

Replaced by a Gaussian kernel density (`_density`, support −45…+120 days so early payers and the long
tail both survive) shrunk towards the global density with `n/(n+k)`, `k=8`. Same corpus, same origin:
net under-call shrank to single-digit percent; on `t3` today the 30-day inflow error is −2.4%.

### 3b. A blunt aging haircut over-corrected

Because the fix in 3a over-called 31–120 day receivables, we next discounted **every** past-due
document by the recovery curve: `expected = outstanding × recovery(age)`. That threw away the
information in the learned distribution, which already contains the late payers. Result on `t2`:
**−32% at the latest origin** — worse than the bug it fixed. Reverted.

What is in the shipped model instead: the recovery curve is a *prior for documents with no evidence
left*, applied only when the survival path has nothing to say (branch `hazard`). The
`profile_branch_counts` in `run_manifest.json` shows how many documents landed on each branch, which
is how we knew 3b was applying a blanket penalty where 92% of documents had real evidence.

### 3c. Survival renormalisation without a cure fraction invented ₹18 M of collections

Next attempt: condition on "unpaid so far" by dividing by the survival function,
`q(window)/S(age)`. For an invoice 200 days overdue, the learned density there is ~0, so
`0.0001/0.0002` → "it must be imminent". The model booked ₹18 M of collections from
invoices that were, in the generator's own words, never going to be paid.

The fix is a **cure fraction**: a party with probability `p_paid` ever pays, otherwise it never does.

```
placed = p_paid · q(window) / (p_paid · S(age) + 1 − p_paid)
```

`placed` now decays for old documents instead of exploding, and the "never paid" mass is honoured.
On `t3` the book projection for the 30-day window came to ₹91.3 L expected against ₹92.8 L actual for
AR and ₹43.0 L against ₹41.0 L for AP — within 1.6% and 4.9%.

**Lesson we would have paid to learn faster:** when a model conditions on "not yet happened", it must
carry an explicit "will never happen" mass. `p_paid` is also the single most useful number in the
`party_behaviour.csv` report — a treasurer reads "this customer clears 88% of invoices, median −7
days early" far more readily than a pmf.

---

## 4. The backtest was cheating, in the direction that flattered the baselines

First `t3` backtest: cashpilot 32.1 / 35.2 / 25.4% error at 7/14/30 days, skill **−1,174%** against a
same-weekday seasonal naive that posted 5.4 / 4.3 / 2.1%. A naive baseline beating a ledger model by
50× is not a model problem, it is an evaluation problem.

Cause: `invoices.csv`/`bills.csv` carry `status` and `paid_amount` *as of today*. Slicing a dataset at
an origin 30 days back therefore handed the model "this bill is already paid" for payments made after
the origin — the answer key, used as an input. The model under-called outflows by ~4× and the naive
baseline, driven by flow history, looked brilliant.

Fix: `_slice_dataset` resets every document at the origin to unposted, and the origin's own
reconciliation run decides which were settled by then. Two consequences, both desirable:

| 30-day error (share of gross movement) | before | after |
|---|---|---|
| cashpilot | 25.4% | **1.8%** |
| seasonal naive | 2.1% | 3.6% |
| cashpilot MAE of net change | ₹4.39 Cr | ₹30.6 L |
| skill vs naive | −1,174% | **+50.0%** |

`tests/test_accuracy_and_backtest.py::test_sliced_ledger_does_not_know_what_happened_after_the_origin`
is the guard. Pinned in the same file: `_err_share` returns 0 rather than infinity on a zero-flow day,
because the previous metric (`mean_daily_mape_pct`) reported **3.3e9%** — the arithmetic mean of
per-day percentage errors on a series where Sundays are genuinely zero. **A metric can be the bug.**

---

## 5. Ground-truth files that disagreed with the ledger, and one that disagreed with itself

* The generator wrote `invoices.csv` from the whole simulated world, including the 30 days after
  `as_of` that it had already planned. 316 documents leaked into the ledger view of `t2`. Any accuracy
  number computed on that corpus is unusable, including ones that looked *good*. Now `emit()` filters
  `doc_date <= as_of` for the ledger files and keeps everything in `truth_*.csv`, with a test asserting
  the split.
* Narrations originally put the invoice reference beyond the 90-character truncation the generator
  applies, ~50% of the time, and lumpsum groups had no ground truth at all. Both made tiers t3 and t6
  look broken in a run where the corpus, not the engine, was wrong. Fixed in `synth/world.py`
  (`emit` order for refs; lumpsum truth rows).
* `ingest.py` mapped the counterparty column name wrong (`counterparty_code` is the real header), so
  every party collapsed to `GENERIC` and per-party behaviour learning silently had nothing to learn.
  No crash, no warning, just a worse model — the reason `party_behaviour.csv` is now part of the
  report you are asked to read in the demo.

---

## 6. Bugs a code review should have caught, but a test caught instead

| bug | symptom | guard |
|---|---|---|
| `mat[:, 1:][rows, cols]` in the Monte-Carlo update | fancy indexing returns a **copy**; every one of 2,000 paths was discarded, bands were pure noise and 100% silent | `test_percentiles_are_ordered_and_widen_with_the_horizon` |
| no residual "not paid in window" slot | P10 came out **above** the expected value | same test |
| unsigned AP amounts in the path matrix | band shifted by 2× payables | same test |
| `recon` order: `doc_ix` built after the amount tiers read it | every amount tier saw an empty index | `test_tier4_and_5_…` |
| `self.dataset.as_of` used unguarded in the aged-AR pass | a hand-built or manifest-less dataset died with `TypeError: '<' not supported between date and NoneType` | `test_tier6_…` (a real risk for users pointing at their own CSVs) |
| settlement id searched case-sensitively in a lowercased narration | `SETL-1` in `setl-1 …` never matched, so t1 lost the batch | `test_settlement_credit_is_never_stolen_by_an_invoice_of_the_same_amount` |
| default invoice-number regex demanded a 3–6 digit tail | `INV-4711` and `VB-991122` were never extracted and fell to the fuzzy tiers | same file; **measured effect on `t3`: none** (0.9743 before and after) because that generator only emits long numbers — which is exactly why the bug survived a hundred green runs |
| `similarity_norm` weights summed to 1.45 | similarities above 1.0, fuzzy tier accepted junk | `test_similarity_survives_truncation_and_typos` |
| MC band inversion / AP sign error in the first forecast engine | P10 > P90 on some days | `test_percentiles_are_ordered_and_widen_with_the_horizon` |

---

## 7. Still open, measured

| item | number |
|---|---|
| 30-day outflow over-call (`roll_forward` past Sundays not modelled for payables) | +12.1% on the seeded check; error does not shrink to zero at longer horizons |
| daily-path shape | only 1 of 30 days within 20% of the planned net on the seeded corpus, while the 30-day total is 5.2% off gross |
| `t2_advice_utr` over-claim | 2 of 208 wrong (a payment advice naming two invoices where the amount matches one); `t5_amount_name` 1 of 2 |
| `fuzzy_only` slower than `full` | ~745 ms vs ~475 ms engine time — with the cheap tiers disabled, more lines reach the global assignment |
| `t6_lumpsum` cost | 294 ms of 484 ms reconcile to win 5 lines on this corpus; correct for a month-end, wasteful for a 50-line day |
| empty CSVs read `no_rows` | deliberate, but a pandas reader will choke — see `report._write_csv` |
