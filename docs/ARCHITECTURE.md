# Architecture

## The problem, restated as a data-flow problem

A business has three records of the same money and they never agree:

1. **the bank feed** — signed rupee movements, thin narration (90 characters, uppercase, typos),
   occasional duplicate postings, plus charges and interest that belong to no invoice;
2. **the books** — receivables and payables with amounts, due dates, partial payments, disputes;
3. **the gateway** — Razorpay captures, refunds, and a settlement batch per payout with a fee, GST on
   the fee, TDS on the commission and a country payout charge.

Reconciliation answers *"which document does this movement settle, and how sure are you?"*.
Verification answers *"was the money the gateway owes us actually credited, to the paisa?"*.
Forecasting answers *"what is in the account on the 5th of next month, and what could go wrong?"*.

All three are the same ledger viewed at different times, so they share one ingest path and one
`Dataset`. Cashpilot's shape falls out of that:

```
             ┌────────────┐   ┌────────────┐   ┌────────────────┐
 CSVs ──────►│   ingest   │──►│recon.engine│──►│verify.settlements│
 (or exports)│ typed recs +│   │ tier ladder│   │ integer recompute│
             │ parse fail.│   └──────┬─────┘   └───────┬─────────┘
             └────────────┘          │                 │
                                     ▼                 ▼
                            ┌─────────────────────────────────┐
                            │ ai.triage  (advisory, budgeted)  │
                            └────────────────┬────────────────┘
                                             ▼
                            ┌─────────────────────────────────┐
                            │ forecast.engine                  │
                            │ learned delay pmf → survival →   │
                            │ weekday seasonality → churn →    │
                            │ gateway cadence → Monte Carlo    │
                            └────────────────┬────────────────┘
                                             ▼
                            ┌─────────────────────────────────┐
                            │ eval.recovery (₹ at stake, and   │
                            │ what was planted vs what we got) │
                            └────────────────┬────────────────┘
                                             ▼
                            ┌─────────────────────────────────┐
                            │ report.py (8 CSVs, 6 md, svg)   │
                            │ eval/accuracy.py (scoring)       │
                            └─────────────────────────────────┘
```

## ingest — where every format problem is absorbed

`Dataset` holds `BankLine`, `LedgerDoc` (AR from `invoices.csv`, AP from `bills.csv`),
`PaymentAdvice`, `Settlement`, `GatewayPayment`, `Refund`, plus `opening_balance_paise`, `as_of` and
`parse_failures`.

Rules:

* money is `int` paise everywhere. `Decimal` at the boundary, never `float`. `money.parse_money_paise`
  handles `₹1,23,456.78`, `(2,500.50)`, `45000.0`, blank. A `None` return becomes a `PARSE_FAILURE`
  exception, which is *why the file has a row for a row we could not read*.
* `money_or_zero` is used only where zero is genuinely the answer (missing `amount_out` on a credit).
* `as_of` comes from `meta.json`, else the latest line date. Every date-window in the engine is
  relative to it, so the same code runs on a live feed and on a historical slice.
* Column aliases are resolved here and nowhere else. The Razorpay-shaped files are read as-is; another
  aggregator is a new ~30-line loader.
* `counterparty_code` → `LedgerDoc.extra["counterparty_code"]`. When that mapping was wrong, every
  party collapsed to `GENERIC` and the forecaster's per-party learning had nothing to learn — a silent
  accuracy loss of the kind that looks like model weakness.

## recon.engine — the ladder, and why it is a ladder

`DocIndex` holds candidate documents with a normalised name, a token inverted index, amount buckets,
and a `consumed` set so one document can never be posted twice. Tiers run in this order:

| tier | evidence | why it sits here |
|---|---|---|
| `t0_duplicates` | same UTR + amount (quarantine); same amount + payee within 3 days (flag) | before anything can consume a copy |
| `t1_settlement` | bank credit whose narration names a settlement id (or payout UTR) | a payout is not a receipt from a customer |
| `t2_advice_utr` | UTR from a payment-advice row | the customer told us which invoices; near-certain |
| `t3_doc_number` | invoice/bill number extracted by `config` regexes | explicit reference in the narration |
| `t4_amount_exact` | unique document with exactly this amount | strong but name-blind: never auto-posted alone below the floor |
| `t5_amount_name` | amount + name similarity above a floor | the "40% of names are mangled" tier |
| `t6_lumpsum` | subset-sum over the party's open documents, blocked by an inverted token index | one credit, many invoices |
| `t7_fuzzy` | global assignment (highest total score, one-to-one) | residue only, small candidate sets |
| `t8_single_inference` | exactly one open document left for this party in this window | last safe inference |

Three properties that matter more than the tier list:

* **Settlement pseudo-documents live outside `DocIndex`** (`Reconciler.settlement_docs`), so the amount
  tiers structurally cannot steal a gateway credit. Fixing this was worth more precision than any
  scoring change.
* **Partial coverage is extended only where the line names the document.** `extend_residual()` runs
  after t1/t2/t3 (advice/UTR/doc-number) and never after a bare subset-sum: an unexplained remainder
  on a lumpsum stays an exception (`RESIDUAL_UNALLOCATED`), because inventing a split is how books get
  cooked.
* **Every tier records `evidence` and `confidence`.** A match is `auto_post` only if
  `confidence ≥ auto_post_min_confidence`, the amount is under `max_amount_paise_for_auto`, the tier
  is not a pure-similarity one, and the document set is unambiguous. `t7_fuzzy` is excluded from
  auto-posting by name: similarity is a hypothesis.

Cost profile on the 1,709-line corpus (from `run_manifest.json` → `stages_ms.reconcile_ms`):
t6 294 ms, t0 69 ms, t3 39 ms, t4 28 ms, t1 13 ms, t2 7 ms, t7 5 ms, t5 1.6 ms (t8 runs inside the
classification pass and is not timed separately - worth fixing if the ladder grows another rung). That is the
argument for the ordering *and* the argument for dropping the subset-sum tier on a 50-line day.

## verify.settlements — arithmetic, audited

`FeeSchedule.load("config/fee_schedule.json")` supplies per-mode rates, GST on fees, TDS on
commission, payout charge, and a per-batch tolerance in paise. For every batch, and for every captured
payment, the expected deductions are recomputed and compared to the declared ones:

```
fee = Σ (component_gross × rate[mode])          tmn = gross × tmn_rate
gst = (fee + tmn) × gst_rate_on_fees            tds = fee × tds_rate_on_commission
net = gross − fee − tmn − gst − tds − refunds   credit_gap = bank_credit − net
```

Flags: `FEE_TIER_MISMATCH`, `FEE_COMPONENT_MISMATCH`, `BATCH_ARITHMETIC`, `NOT_CREDITED`,
`CREDIT_AMOUNT_MISMATCH`, plus `SETTLEMENT_OVERDUE` when a captured payment is older than
`settlement_expectation_days` (T+2) without a batch. The rate card is a placeholder: the point is the
machine-auditable arithmetic, and that a wrong slab on 174 batches is caught and priced.

### What each batch is worth

`BatchCheck` carries declared and recomputed values side by side, so the derived numbers are audit
lines rather than vibes:

```
overbilled      = max(0, declared_fee - expected_fee)          # fee overbilling, per batch
rate_card_claim = overbilled + max(0, (gst+tds) declared - expected)
unexplained     = gross - fee - tmn - gst - tds - refunds - declared_net   # money out, no row for it
undercredited   = max(0, expected_net - bank_credit)           # credit short of what was owed
recoverable     = abs(unexplained) + max(rate_card_claim, undercredited)
recovery_rate   = min(credit, expected_net) / expected_net     # rupee-weighted over the corpus
```

`max()` in `recoverable` is the interesting line. A fee that is too high *causes* the credit to be too
low, so on the batch where the gateway both overbilled and under-credited, the two numbers are the same
rupees; adding them would inflate the claim. The unexplained bucket stays additive because it measures a
different failure — money that left the batch with no deduction record behind it at all. `NOT_CREDITED`
batches are deliberately excluded from `undercredited` (no credit to compare) and still raise a
high-severity exception, so a payout the gateway promised and never sent is visible without pretending
we know what it was short by.

On the published corpus this prices 26 of 174 batches at ₹41,074.20 of claim value, of which
₹1,187.11 is fee overbilling, ₹19,824.82 unexplained deductions and ₹21,249.38 under-credit.

## forecast.engine — a book model, not a time series

The forecast is not `ARIMA on daily net cash`. It is the sum of *when each known document moves*, plus
the parts the ledger cannot see:

1. **Per-party delay distribution.** `learn_ledger` collects, for every (party, kind), the observed
   delay from due date to settlement for documents cleared *inside the slice*, and smooths it with a
   Gaussian kernel (`_density`, support −45…+120 days: early payers and the long tail both survive).
   The party curve is shrunk towards the global curve with weight `n/(n+8)`.
2. **Survival with a cure fraction.** For a document unpaid at `as_of`,
   `placed = p_paid · q(window) / (p_paid · S(age) + 1 − p_paid)`, where `p_paid` is that party's
   historical share of invoices ever cleared. The `1 − p_paid` mass is what stops a near-zero density
   tail being read as "so it must be due now" ([FAILURES.md #3c](FAILURES.md)). Documents past the
   distribution's support fall to a daily hazard (0.012/day) and only there does the AR aging-recovery
   curve apply.
3. **Weekday redistribution.** The placement is re-spread across the window by the book's weekday
   index, renormalised so each document's total is untouched. Measured effect: small (2.6% MAE at 7d,
   none at 30d) — kept because it makes the daily path respect a business calendar.
4. **Same-window churn.** Documents raised *and* settled inside the horizon (a ₹20 L invoice paid by
   card and settled T+2 never appears in the ledger at `as_of`). Estimated from four recency-weighted
   windows (0.50/0.25/0.15/0.10) of the same-length look-back, and applied to both directions.
5. **Gateway cadence.** Captured volume projected forward from the recent daily gross with its own
   lag distribution, so the settlement side does not depend on the ledger being complete.
6. **Point vs band.** The expectation is computed analytically, day by day (that is the number you
   see in the table). P10/P50/P90 come from a Monte Carlo over the same components with per-document
   Bernoulli pay/no-pay draws and signed amounts for AP — 2,000 paths, ~290 ms on 2,029 documents.
   The band is *not* a symmetric haircut on the point forecast; that is why it can be narrow at 7d
   where the book is nearly certain and wide at a month end.

Deliberately absent: an "unexplained residual flow" term. See
[FAILURES.md #2](FAILURES.md) for what it cost when it was there.

## ai/ — the two judgement calls

*`triage.py`* — only exceptions whose code says *"we could not choose"*
(`AMBIGUOUS_CANDIDATES`, `AMBIGUOUS_LUMPSUM`, `UNALLOCATED_CREDIT`, `UNMATCHED_DEBIT`,
`DOC_REF_OUTSIDE_WINDOW`, `SHORT_DEDUCTION`, `SUSPECTED_DUPLICATE`, `CREDIT_AMOUNT_MISMATCH`) are
offered to the model. The prompt shows the verbatim narration, the candidate documents with their
amounts and ages, and a fixed `CATEGORIES` taxonomy; the answer is JSON validated against it
(`_validate`) — bad category, out-of-range or over-confident, missing explanation, invented
cross-references, unknown owner or action ⇒ **the answer is dropped and counted in
`llm.invalid`**, never coerced into the report. Everything else is `deterministic_triage`: a code map
that already knows a bank charge is a bank charge.

*`narrative.py`* — one call, writing the morning brief from an evidence block of figures. Then
`_numbers_allowed(text, evidence)` rejects any digit group not present in the evidence, and the whole
brief falls back to `template_brief` if it fails. An LLM that invents a balance is worse than no LLM,
so the *only* freedom it has is prose.

`LlmClient` is stdlib `urllib`, temperature 0, `response_format: json_object`, a hard call budget, and
no import of any provider SDK. `llm.enabled=false` changes nothing else in the pipeline: same
artifacts, deterministic judge, template brief.

## eval/, report/, cli.py

* `eval/accuracy.py` — the scoring contract (see [ACCURACY.md](ACCURACY.md)).
* `eval/bench.py` — strategies × repetitions, ingest/engine timing split (the first benchmark lumped
  them, which made the engine look 4× slower than it is and hid where the time actually goes), and the
  forecast backtest, writing `bench.md` + `bench.json`. Takes no settings argument at all when called
  as a library (`cashpilot sweep` does) — it loads its own, and a test pins that.
* `eval/recovery.py` — the money page, in two halves that must not be confused. *Runtime* (per-batch
  ₹ at stake, recovery rate, share of gross) is computed from the input files alone and works on a
  customer's own CSVs. *Detection* (how many planted defects we caught, what share of the planted ₹ we
  identified) divides by `meta.json`, which only the generator writes; if it is absent the section
  reports "not measured" and `run_manifest.recovery.batch_defects.measured = false` — never 0%, because
  0% is a claim about the engine and "not measured" is a claim about the data.
* `eval/sweep.py` (`cashpilot sweep`) — the same measurements at five corpus sizes from 90 to 5,610
  lines, one seed each, into `artifacts/scale_sweep.{md,json}`. It exists because a number measured at
  the size you tuned on is not a number. `sample` in that table is generated with seed 4242, the same
  as `data/sample`, so the row can be checked against `artifacts/demo/`.
* `pipeline.run_books()` — the one function that does the whole thing; `report.write_all` renders it.
  `dashboard.html` is inline SVG with no external references so it renders inside a sandboxed iframe
  and from `file://`.
* `cli.py` — `generate | run | bench | forecast | sweep | demo | doctor`. Sub-command namespaces inside
  `demo` are built by re-parsing through the real parser, so a new flag cannot silently break the
  demo path (it did, once).

## What we deliberately did not build

* **No database, no service, no queue, no scheduler.** The panel runs it in ten minutes; a CSV in,
  reports out. `cron` + `cashpilot run` is the deployment story.
* **No ML frameworks.** `numpy` only for the Monte-Carlo path matrix and its percentiles; stdlib
  `difflib.SequenceMatcher` for edit-ratio name similarity; pure-Python kernel smoothing for the delay
  density; a hand-written subset-sum with inverted-index blocking for lumpsums. The reason is not
  purity — it is that a rule the finance team can read is a rule they can overrule.
* **No agent loop.** Nothing here decides to fetch another document; the pipeline is a straight line
  and every branch is a config value.
* **No payment initiation, no ledger mutation.** The output is a proposal plus a list of refusals.
