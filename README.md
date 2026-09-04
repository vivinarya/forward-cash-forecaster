# Cashpilot

**The books, run by itself.** Cashpilot reconciles a bank feed against the sales ledger and the
purchase ledger, verifies Razorpay settlement batches to the paisa, and forecasts the cash position
7 / 14 / 30 days ahead with a probability band and a funding recommendation.

It is built for the part that actually hurts in a small Indian business: the ₹4,50,000 credit in
the HDFC statement that could be invoice INV-2026-00123, or the two invoices the customer paid in
one lumpsum, or the gateway batch where the fee slab was wrong. Nobody reconciles those by hand
every day, so the treasurer runs the business without knowing what cash arrives next month.

```
1,709 bank lines in 1.5 s   →   97.4% reconciled correctly at 99.7% precision
                                1,535 auto-posted at 100% precision, 266 exceptions listed for a human
₹41,074.20 recoverable      →   26 of 174 gateway batches short-paid us; all 26 planted defects caught,
                                ₹21,011.93 of ₹21,011.93 of it put a name and a number to
30-day cash forecast        →   1.8% error (share of money that moved), 50% better than a seasonal naive
which invoices clear?       →   82.2% of the model's top-25 predictions were right; ranking by size gets 14.7%
90 → 5,610 lines            →   recall 0.976 → 0.973, auto-post precision 1.000 at every size (see the sweep)
```

Three rules shape the whole repo:

1. **Deterministic where arithmetic is enough.** Matching is a tiered rule ladder, settlement
   verification is integer paise arithmetic, the forecast is a learned delay distribution. No model
   touches money.
2. **AI only where judgement is the task** — two calls, both advisory: adjudicating ambiguous
   exceptions and writing the daily brief. No key, no network, same pipeline. Every section below
   that carries a number has a deterministic fallback, listed in
   [Deterministic fallbacks](#deterministic-fallbacks).
3. **Measured, not asserted.** Every accuracy number here comes from a command in
   [docs/ACCURACY.md](docs/ACCURACY.md), scored against planted ground truth on synthetic books that
   were deliberately messed up. [docs/FAILURES.md](docs/FAILURES.md) documents what did not work, with
   before/after numbers.

---

## Run it in ten minutes

Python 3.10+ (3.13 used here), one runtime dependency (numpy), nothing to configure.

```bash
git clone <this repo> && cd cashpilot
python3 -m venv .venv && source .venv/bin/activate      # optional but recommended
pip install -r requirements.txt   # numpy==2.3.5, plus `-e .` so `cashpilot` is on PATH
cp .env.example .env              # optional: everything works with no keys at all

python -m cashpilot demo --data data/sample --out artifacts/demo
```

`requirements.txt` ends in `-e .` on purpose: the packaged CLI (`cashpilot ...`) and the module form
(`python -m cashpilot ...`) have to behave the same inside the ten minutes a reviewer has. If you
would rather install nothing, every command also runs as `PYTHONPATH=src python -m cashpilot ...`,
which is what the `Makefile` does.

That single command generates a small synthetic company (120 days of trading, 742 bank lines,
planted errors), runs the whole pipeline, writes the reports, then benchmarks three strategies and
backtests the forecaster. It takes under 30 seconds. It is also the number to read in the tables
below as the "demo corpus"; `data/synthetic` is the larger one the headline uses.

Open:

| file | what it is |
|---|---|
| `artifacts/demo/dashboard.html` | self-contained page: cash path with P10–P90 band, flow by day, exception mix, settlement findings. No CDN, no network — it renders from a file:// URL. |
| `artifacts/demo/brief.md` | the one-paragraph morning brief |
| `artifacts/demo/reconciliation.md` | matches by tier, and every line that was refused, with the reason |
| `artifacts/demo/settlements.md` | per-batch fee/GST/TDS/TMN recompute, over/under-credit |
| `artifacts/demo/recovery.md` | the money page: what was found in ₹, batch by batch, and how much of what was planted it caught |
| `artifacts/demo/forecast.md` | 7/14/30-day position, worst day, funding need, and which invoice the money is expected from |
| `artifacts/demo/aged_receivables.csv` | the collections list: overdue documents ranked by money at risk |
| `artifacts/demo/bench.md` | the accuracy table below, on the same corpus |

### The rest of the commands

```bash
python -m cashpilot run --data data/synthetic --out artifacts --runs 2000 --backtest
python -m cashpilot bench --data data/synthetic --reps 3 --forecast --seeded   # accuracy + speed table
python -m cashpilot forecast --data data/synthetic --horizon 30 --backtest   # forecast only
python -m cashpilot sweep --scales tiny,sample,medium,large,xl              # same metrics at five sizes, ~1 min
python -m cashpilot doctor --data data/synthetic                            # environment + data check

# regenerate the published corpus from scratch (byte-identical to the one in data/synthetic):
python -m cashpilot generate --out data/synthetic --scale medium --seed 20260905 --as-of 2026-09-05
# the same stress sizes, with the numbers published, are in the sweep table further down:
python -m cashpilot sweep --scales large,xl
```

`make demo`, `make test`, `make bench`, `make sweep`, `make doctor`, `make data` do the same things
with the flags we use in CI; `make check` runs tests + demo + doctor + a probe benchmark against a
regenerated corpus. `data/synthetic` (1,709 lines) and `data/sample` (742 lines, the demo corpus) are
committed, so every accuracy command works before you generate anything.

To point it at real exports instead of the generator, drop CSVs with the same columns into a
directory — `src/cashpilot/ingest.py` is the only file that knows about column names, and it accepts
`amount_in|credit` / `amount_out|debit`, `txn_date|date`, `narration|description`, and DD/MM/YYYY or
ISO dates. Rows it cannot read become `PARSE_FAILURE` exceptions rather than being dropped.

---

## Architecture

![pipeline](diagrams/architecture.svg)

```
 CSV / export files                        ┌──────────────────────────────┐
 bank_statement.csv  invoices.csv  ... ──►│ ingest  (pandas-free, per-row  │
                                          │ tolerance: bad row → exception)│
                                          └───────────────┬────────────────┘
                                                          ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │ recon.engine — tiered ladder, each tier consumes only what it     │
        │ t0 duplicates → t1 settlement → t2 advice/UTR → t3 doc number →   │
        │ t4 amount → t5 amount+name → t6 lumpsum (subset-sum) → t7 fuzzy    │
        │ → t8 single-candidate inference      deterministic, integer paise  │
        └───────────────┬─────────────────────────────────┬─────────────────┘
                        ▼                                 ▼
        verify.settlements + eval.recovery      ai.triage  (LLM, advisory only,
        fee/GST/TDS/TMN recompute, then ₹       hard call budget, answers
        per batch: claimed, caught, quantified  validated then discarded)
                        │                                 │
                        └────────────┬────────────────────┘
                                     ▼
                     forecast.engine  — per-party empirical delay
                     distribution + survival/cure model + weekday
                     seasonality + same-window churn + gateway cadence
                     (analytic expectation, Monte-Carlo for P10/P90)
                                     ▼
                     report.py — 8 CSVs, run_manifest.json, accuracy.json,
                     6 markdown reports, inline-SVG dashboard.html
```

Why a ladder instead of one clever matcher: the cheap, unambiguous evidence must be exhausted first
so that the expensive, statistical tiers only ever see real residue. That is what makes the
auto-post precision 1.000 — and it makes the *cost* of each tier measurable
(`t6_lumpsum` is 289 ms of the 481 ms reconcile on the large corpus, and it buys 5 lines; we would
drop it on a 500-line month, and the stage timings in `run_manifest.json` are what tell you that).

Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Measured accuracy and speed

Scored on the committed `data/synthetic` corpus: 1,709 bank lines, 2,365 documents (2,029 with a
bank-visible movement), 5,853 gateway payments, 174 settlement batches — seed `20260905`, as-of
2026-09-05. Reproduce exactly with `python -m cashpilot bench --data data/synthetic --reps 3 --forecast
--seeded`; every figure below is copied out of the `artifacts/bench.md` that command writes.

**Reconciliation** — a line is `correct` only if the *whole set* of documents it covers matches ground
truth; matching one invoice of a three-invoice lumpsum is scored as `partial`, not as a win.

| strategy | what it is | precision | recall | F1 | auto-post precision | rupee accuracy | engine ms | lines/s |
|---|---|---|---|---|---|---|---|---|
| `exact` | regex refs + UTR + settlement id only | 0.9978 | **0.5341** | 0.6958 | 1.000 | 0.4725 | ~165 | ~10,400 |
| `fuzzy_only` | + amount, name-similarity, subset-sum, inference | 0.9957 | 0.8391 | 0.9107 | 1.000 | 0.9047 | ~750 | ~2,300 |
| `full` | the shipped ladder | **0.9969** | **0.9743** | **0.9855** | **1.000** | 0.9657 | ~485 | ~3,500 |

*Exact reference/amount matching resolves 53% of the feed — and leaves 777 of the 1,672 matchable
lines with nothing at all on them. The tiered ladder resolves 97.4%, and refuses the remaining 38
loudly instead of guessing.* Full run: 1,709 lines end-to-end including CSV ingest, ground-truth
scoring, settlement verification, recovery accounting, triage and a 2,000-path Monte-Carlo forecast
in **~1,500 ms** (1,500 ms in the run that produced `artifacts/`), of which CSV ingest alone is 685 ms.
Engine times vary ~10% run to run, so they are rounded here; accuracy does not vary at all.

**Forecast** — rolling origin, every origin re-runs reconciliation on data up to that date (no
look-ahead), scored against what the feed then shows:

| model | err, 7d | err, 14d | err, 30d | MAE of net change @30d | skill vs naive | right direction |
|---|---|---|---|---|---|---|
| cashpilot | 9.1% | 5.1% | **1.8%** | ₹30.6 L | **+50.0%** | 100% |
| same-weekday seasonal naive | 10.1% | 8.7% | 3.6% | ₹61.2 L | — | 100% |
| 4-week moving average | 10.8% | 10.0% | 7.3% | ₹1.21 Cr | −97.5% | 100% |
| sum of amounts due in window | 14.0% | 7.8% | 4.6% | ₹77.0 L | −26.0% | 100% |

Error is |forecast − actual| net movement as a share of the money that actually moved in the window;
a percentage of *net* change is unstable (on a zero-flow Sunday it is either 100% or infinite), which
is why it is not the headline. P10–P90 band hit rate: 88.9% / 100% / 100% at 7/14/30 days.

The part a flow-based baseline cannot do at all: **of the 25 documents the model expected to settle
inside a 30-day window, 82.2% really did** — against 14.7% for the same open book ranked by size
alone. [docs/ACCURACY.md](docs/ACCURACY.md) has the per-tier breakdown, the exception list and the
three forecast failures that got us here.

---

## What the run put a price on

Reconciliation is only half the job: a flagged batch is worth nothing until someone can say how much.
Every rupee below is integer arithmetic on the files themselves — no model, no estimate — and each
one lands in `artifacts/recovery.md` plus `artifacts/recovery_batches.csv`, sorted by what it is worth.

| measure (needs no ground truth) | value on `data/synthetic` | how it is computed |
|---|---|---|
| batches verified | 174 | every gateway batch in the export |
| batches with rupees at stake | 26 (14.9%) | a gap wider than the rate card's own per-batch tolerance |
| **claim value** | **₹41,074.20** | unexplained deductions + `max(rate-card overbilling, credit shortfall)` — `max`, not `+`, so one rupee is never claimed twice |
| ├─ fee/GST/TDS above the slab | ₹1,424.56 | recomputed from each batch's own payment mix |
| ├─ deductions with no evidence on file | ₹19,824.82 | gross − commission − TMN − GST − TDS − refunds ≠ net |
| └─ credit short of what the batch owed | ₹21,249.38 | what landed in the account vs what the ledger says should have |
| credit recovery rate | 98.892% | money that arrived ÷ money owed, **rupee-weighted**, not batch-count weighted |
| share of gateway gross at stake | 14.8% | gross of flagged batches ÷ gross of all batches |
| customer short payments turned into claims | ₹11,71,834 of ₹20,31,865 (57.7%) | a gap tied to the invoice it belongs to |
| …plus money visible but not yet attributed | ₹7,67,220 | sitting on an unresolved bank line: a human sees it, no claim exists yet |

Then the part only a generated corpus can answer — *of the money we deliberately hid, how much came
back out* (`meta.json`, which the engine never opens for anything else):

| defect planted | what surfaces it | planted | caught | rupees identified |
|---|---|---|---|---|
| fee billed on the wrong slab | `FEE_TIER_MISMATCH` | 7 batches | 7 | ₹1,187.11 of ₹1,187.11 (100%) |
| net credited short, nothing on file | `BATCH_ARITHMETIC` | 14 batches | 14 | ₹2,550.21 of ₹2,550.21 (100%) |
| refund row missing from the export | `BATCH_ARITHMETIC` | 5 batches | 5 | ₹17,274.61 of ₹17,274.61 (100%) |
| customer paid an invoice short | `SHORT_DEDUCTION` | 111 invoices | 72 | ₹11,71,834 of ₹20,31,865 (57.7%) |

**On a customer's own data the second table cannot exist**, and the repo is designed to say so: with no
`meta.json` defect ledger there is no denominator, so `recovery.md` prints *"Detection rate: not
measured"*, `run_manifest.json` carries `recovery.batch_defects.measured = false`, and the CLI prints
`defect catch rate: not measured - this corpus has no ground-truth ledger to divide by`. A recovery
rate you cannot audit is a marketing number, so the engine refuses to invent one. Same rule for the
AR row above: 5 silently-missed short payments out of 111 are stated as a failure, not rounded away.

Reproduce with `python -m cashpilot run --data data/synthetic --out artifacts --runs 2000` (or
`python -m cashpilot demo` for the small corpus: 113 batches, 27 with money at stake, ₹29,772.80,
27/27 planted defects caught, ₹14,942.81 of ₹14,942.81 identified).

---

## How it behaves per class of record

One aggregate recall averages a class that works with a class that does not, so both views are
published on every run (`reconciliation.md`, `accuracy.json`, `docs/ACCURACY.md`):

| class of bank line | lines | exact doc set | refused | what "right" means here | rate |
|---|---|---|---|---|---|
| `matchable` (one document, amount agrees) | 1,372 | 1,371 | 1 | matched to the exact document set | 99.93% |
| `gateway_settlement` (payout covers a whole batch) | 172 | 172 | 0 | matched to the exact document set | 100.0% |
| `matchable_amount_mismatch` (paid short) | 108 | 70 | 35 | matched to the exact document set | 64.81% |
| `matchable_lumpsum` (one line, several invoices) | 20 | 16 | 2 (+2 partial) | the *whole* set, not one of them | 80.0% |
| `expected_unmatched_duplicate` | 31 | 0 | — | left alone, typed as `DUPLICATE_BANK_LINE` | 100.0% |
| `expected_unmatched_charge` / `interest` / `unknown` | 6 | 0 | — | left alone, typed per cause | 100.0% |

The two hard classes are hard for arithmetic reasons, not laziness: a short payment is *allowed* to be
within the 0.5% tolerance (matching it silently would invent a receivable), and a lumpsum needs a
subset-sum over the party's open documents — the engine commits only when exactly one subset fits and
routes the ambiguity to a human, which is why `t6_lumpsum` is the most expensive tier and the one worth
keeping. The refused 35 and 2 are on the exception list with a reason, not wrong.

---

## Tested at five sizes, not one

`cashpilot sweep` (≈1 minute) generates five corpora with the same seed and runs the whole pipeline on
each; `artifacts/scale_sweep.md` is its output. This is the honest answer to "what happens on 500
random transactions" — and on 5,610:

| corpus | bank lines | full recall | auto-post precision | refused | slowest tier, its share of reconcile | end-to-end ms |
|---|---|---|---|---|---|---|
| tiny | 90 | 0.9756 | 1.000 | 2 | `t0_duplicates` 32.5% | 248 |
| sample (the demo corpus) | 742 | 0.9792 | 1.000 | 10 | `t6_lumpsum` 44.6% | 652 |
| medium (published) | 1,709 | 0.9743 | 1.000 | 38 | `t6_lumpsum` 60.6% | 1,317 |
| large | 2,641 | 0.9733 | 1.000 | 65 | `t6_lumpsum` 74.7% | 2,392 |
| xl | 5,610 | 0.9731 | 1.000 | 124 | `t6_lumpsum` 87.6% | 7,895 |

Recall and the refusal *rate* are flat (2.2% of matchable lines stay unresolved at 90 lines and at
5,610) — density does not confuse the ladder. Throughput is not flat, and the last column says why:
the lumpsum subset-sum tier, which enumerates combinations of up to `max_candidates: 12` open
documents for every line that reaches it — it is not even the slowest rung at 90 lines (there `t0`
is, at 32.5%) and is 87.6% of all reconcile time at 5,610 (937 lines/s there, against 3,473 at
1,709). The cap that exists bounds the *candidate set*, not
the enumeration, so the growth is real; the fix is a meet-in-the-middle search with a per-line time
budget that raises a `LUMPSUM_SEARCH_GAVE_UP` exception when it runs out. That is written up as an open
item in [docs/FAILURES.md](docs/FAILURES.md) rather than quietly left out. Recovery holds too:
26/26, 27/27, 42/42 planted batch defects caught at the three middle sizes, 63/64 at xl, ₹ recovered
₹9,921 → ₹1,37,442 as the corpus grows.

---

## Deterministic fallbacks

The LLM is an optional garnish; nothing in the money path depends on it. What runs instead, per
missing input — every row is a real branch in the code and most are asserted in `tests/`:

| situation | what runs instead | where you can see it |
|---|---|---|
| no API key, or `--llm off` | `deterministic_triage()` (regex/keyword classifier) + `template_brief()` for the narrative | every number in this README was produced on this path; `llm: 0 calls` |
| LLM replies, but the JSON is not an object, `category` is outside the taxonomy, `confidence` is missing / out of range / below 0.4, or the explanation is blank | the reply is rejected field by field (`_validate`) and the deterministic label is kept; free-text `owner` and `action` are snapped back to safe values | `exceptions.csv` → `llm_status = "discarded:<reason>"` |
| per-run LLM call budget exhausted | remaining exceptions keep their deterministic labels | `triage_stats.skipped_reason = "llm_budget_exhausted"` |
| `CASHPILOT_LLM_ENABLED=1` but no key | heuristic judge, and a warning that says so | `run_manifest.config_warnings` |
| optional input absent (`payment_advices.csv`, the three gateway CSVs, `opening_balance.csv`) | ingest continues with an empty table; the tiers that would have used it simply find nothing | `run_manifest.warnings`: `optional input missing: …` |
| `--rules` path missing or invalid JSON | built-in `DEFAULT_RULES`, no crash | `run_manifest.config_warnings` |
| a row that cannot be parsed | it becomes a `PARSE_FAILURE` exception instead of disappearing | `exceptions.csv`, `run_manifest.counts.parse_failures` |
| gateway batch declared settled with no matching credit | `NOT_CREDITED` flag → `SETTLEMENT_NOT_CREDITED` exception, and its money is excluded from `recoverable` rather than guessed at | `settlements.csv`, `exceptions.csv` |
| settlement carries an unknown payment method | the rate card's `default_mode` slab is used | `tests/test_verify_settlements.py` |
| corpus has no ground-truth files | accuracy is skipped and detection rates print as "not measured" | `recovery.md`, `run_manifest.recovery.batch_defects.measured` |

---

## Known limitations, in three buckets

### What it cannot do, by design

* **It is not a book of record.** Cashpilot reads CSV exports and writes reports. It never posts a
  journal entry, never edits an invoice, never initiates a payment. "Auto-posted" means *matched with
  enough confidence that a human can accept it in one click*; the ledger files on disk are untouched.
* **It will not guess money.** If a gateway batch is declared settled but no credit is in the feed,
  the ₹net is raised as a high-severity `SETTLEMENT_NOT_CREDITED` exception — it is *not* added to
  `recoverable`, because a claim needs a bank line to point at. Only the rate-card difference on that
  batch is claimed. (And a future-dated batch is not flagged at all.)
* **Razorpay-shaped gateway coverage.** The column names of the payment/refund/settlement exports
  follow the Razorpay export. Another aggregator needs a new loader in `ingest.py`, not a new engine.
* **No live API fetching.** `RAZORPAY_KEY_ID/SECRET` appear in `.env.example` only to show where they
  would go; nothing in this repo calls that API. The only outbound call is the LLM, and it is off by
  default.
* **Synthetic world, real arithmetic.** All messiness (truncated narrations, missing refs, short
  deductions, duplicate postings, lumpsums, mis-tiered fees, dropped refund rows) is planted by
  `src/cashpilot/synth/world.py` from a fixed seed. It is not your bank's format, and
  `config/fee_schedule.json` is a *placeholder* rate card — replace it with your contracted slab before
  believing any ₹ figure on a real corpus.

### Where it fails, by class of record — measured, not feared

| class | what goes wrong | measured | what the run does about it |
|---|---|---|---|
| `matchable_amount_mismatch` (paid short) | gaps inside `0.5% of the line, ≥₹1, ≤₹5,000` are treated as rounding and matched, so part of the short payment never becomes a claim | 64.81% of that class resolved to the exact doc set; 57.7% of the planted ₹ tied to an invoice, a further 37.8% of ₹ (34 of 111) visible in the queue, 5 invoices silently missed | `SHORT_DEDUCTION` exceptions with the exact paise gap; the unattributed money is counted as "in the queue", never as "recovered" |
| `matchable_lumpsum` | subset-sum runs only over one party's open documents (≤12 candidates) and refuses the moment two subsets fit | 80.0% at 1,709 lines, 82.6% at 5,610, 50–60% on the 742-line demo corpus | `AMBIGUOUS_LUMPSUM` listing the candidate documents and `allocate_by_oldest_invoice_first` as the suggested human move |
| AP side (bills, rent, salaries) | the narration rarely names a vendor bill, so debits often cannot be tied to a document at all | the single in-scope short-paid **rent** line on the tiny corpus surfaced as `UNMATCHED_DEBIT`, not as a claim: `short_pay_detection = 0.0%` there, 57.7% on the big corpus | the money still lands on the exception list, typed, with the amount; it just is not a claim yet |
| whole-corpus growth | the lumpsum tier is super-linear in the open-book size | reconcile ms 10 → 142 → 484 → 1,223 → 5,941 for 90 → 5,610 lines; `t6` is 87.6% of reconcile at xl | stage timings are in `run_manifest.json`, and the fix is scoped in [docs/FAILURES.md](docs/FAILURES.md) |
| day-level forecast shape | cumulative position is right, the daily path is smooth while reality clusters at month ends | 30-day cumulative error 1.8% of gross movement, but only 1 day in 30 lands within 20% of the planned daily net | the P10–P90 band and the worst-day figure are the decision inputs, not a single day's number |
| short-history books | the delay distribution and the gateway cadence need history; a 120-day corpus gives both too little to learn from | on `data/sample`: 7-day forecast **loses** to the seasonal naive (14.4% vs 12.7%), 30-day P10–P90 band hit 33.3% with 3 origins, lumpsum class 60.0% | documented in [docs/ACCURACY.md](docs/ACCURACY.md) as a small-corpus limit, with the published numbers kept separate from the demo ones |
| `fuzzy_only` is slower than `full` | with the cheap tiers removed, far more lines reach the global name-assignment step | ~750 ms vs ~485 ms engine time | a measurement, and the argument for the ladder's ordering |

### What we would do with another week

1. Meet-in-the-middle subset-sum with a per-line time budget, so `t6` stops dominating above ~2,000
   lines, and a `LUMPSUM_SEARCH_GAVE_UP` exception when the budget runs out (today's alternative is
   silently refusing).
2. A `SETTLEMENT_GAP` → claim bridge: money a batch owes but the bank never received is currently
   excluded from `recoverable` on purpose; a real claim needs the UTR + the payout row attached, which
   means reading the Razorpay payouts API, which this repo deliberately does not call.
3. TDS-style deduction *recognition*: the short-payment gaps we cannot tie to an invoice are usually a
   1% / 2% / 5% cut on the goods value, which is a regex-plus-arithmetic pattern a rule can learn
   without a model — the class would move from 64.8% to something honest and higher.
4. Per-party candidate windows for `t4`/`t5` so that recall on very dense feeds cannot drift, plus a
   regression that runs the sweep in CI and fails if the refusal rate moves more than 0.5 pp.
5. Loader plug-ins for other statement formats (the column map is the only hard-coded part) and a
   signed `sha256sums` manifest of the committed corpora, so a reviewer can verify what they ran.

## Configuration

Everything the finance team might want to change lives in two JSON files; no rate, window or
threshold is hard-coded in Python.

| file | knobs |
|---|---|
| `config/recon_rules.json` | invoice-number regexes, date windows per tier, amount tolerance, name-similarity floors, lumpsum limits, duplicate rules, auto-post floor, `cash_policy` (horizons, MC runs, operating minimum, hazard, weekday redistribution), AR aging recovery curve |
| `config/fee_schedule.json` | gateway rate per payment mode, GST on fees, TDS on commission, TMN, per-batch tolerance in paise, expected settlement lag and gap-alert days |

```bash
python -m cashpilot run --data <dir> --rules /path/to/our_rules.json
```

The LLM is off unless `CASHPILOT_LLM_ENABLED=1` **and** a key is present; when enabled it is capped at
`CASHPILOT_LLM_MAX_CALLS` calls per run and its answers are validated before use
([docs/AI_JUDGEMENT.md](docs/AI_JUDGEMENT.md)).

---

## Layout

```
src/cashpilot/
  ingest.py          CSV → typed records; unreadable rows become exceptions, not silence
  models.py money.py norm.py config.py
  recon/engine.py    the tier ladder, DocIndex, subset-sum lumpsum, global fuzzy assignment
  verify/            settlement fee/GST/TDS/TMN recompute to the paisa, per-batch claim value
  forecast/          seasonality, per-party delay curves, survival model, Monte Carlo, backtest
  ai/                llm.py (stdlib client), triage.py, narrative.py - advisory only
  eval/              accuracy.py (scoring contract + per-class table), bench.py (strategies x reps),
                     recovery.py (₹ at stake, catch rates against planted defects),
                     sweep.py (the same metrics at five corpus sizes)
  pipeline.py report.py cli.py
  synth/world.py     the generator: the mess it plants, and the ledger of what it planted
tools/generate_synthetic.py  thin wrapper around `cashpilot generate`
tests/               114 tests: tiers, settlement arithmetic, recovery maths, per-class scoring,
                     the sweep's own tables, AI scope limits, CLI end-to-end, every fallback above
config/              recon_rules.json (every threshold), fee_schedule.json (the rate card)
docs/                ARCHITECTURE.md, ACCURACY.md, AI_JUDGEMENT.md, FAILURES.md
diagrams/architecture.svg
data/sample (742 lines, the demo corpus) and data/synthetic (1,709 lines, the published one)
artifacts/           the published run: 6 markdown reports, 8 CSVs, bench.md, scale_sweep.md, demo/
```


`requirements.txt` pins `numpy==2.3.5` — used for percentile and kernel maths in the forecaster. The
reconciler, verifier and report writer are stdlib-only by design; `pandas`, `scikit-learn`,
`transformers` and every agent framework are deliberately absent.

## Licence

MIT.
