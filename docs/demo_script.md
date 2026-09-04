# Demo script — ten minutes, start to finish

For whoever is evaluating. Everything below is copy-pasteable; the timings are from the sandbox this
was built in (4 cores shared, cold cache), so treat them as "seconds, not minutes".

## 0. Setup (~60 s)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # numpy only
python -m cashpilot doctor --data data/synthetic
```

Expected: `doctor: ready`, and the data line reports 1,709 lines / 2,029 documents. Nothing needs a key;
`.env` is optional and the LLM stays off unless you turn it on.

## 1. The one command (~25 s)

```bash
python -m cashpilot demo --data data/sample --out artifacts/demo
```

It generates a 725-line company if the directory is missing, runs reconcile → verify → triage →
forecast → reports, then benchmarks three strategies and backtests the forecaster. Read the printed
summary first — it is the whole claim in six lines:

```
reconciled 701/717 matchable bank lines correctly (precision 0.9915, recall 0.9777, F1 0.9846)
auto-posted 670 at 1.000 precision; 10 matchable lines refused + 6 flagged as imperfect
settlement batches flagged: 18 (recoverable ₹738.87)
cash ₹2,20,00,000.00 today -> ₹… in 30d (P10 ₹…)
llm: 0 calls, 0 accepted (disabled: deterministic fallback used)
```

(our run; the generated corpus is dated against "today", so the last digit moves day to day)

The last line is the point: the numbers were produced with the model switched off.

## 2. Open three files

1. `artifacts/demo/dashboard.html` — the cash path with the P10–P90 band, flow by day, exception mix,
   settlement findings. No network, renders from `file://`.
   *Point at the band:* "the grey area is not a decoration; the business cares about the bottom edge."
2. `artifacts/demo/bench.md` — the accuracy table, and the forecast table with its baselines.
   *Point at the `exact` row:* 53% recall. Then at `full`: 97.4%. Same data, same day, no model.
3. `artifacts/demo/unresolved.csv` — the exception list. 10 refused lines with the narration they came
   from and what the truth said. Say it plainly: these are the ones it would not guess.

## 3. Show a real failure mode, live (~20 s)

```bash
python -m cashpilot run --data data/synthetic --out artifacts --runs 2000 --backtest
python - <<'PY'
import csv, collections
rows = list(csv.DictReader(open("artifacts/exceptions.csv")))
print(collections.Counter(r["code"] for r in rows).most_common())
print(next(r for r in rows if r["code"] == "SHORT_DEDUCTION")["detail"][:220])
PY
```

You get the month's residue — 118 overdue receivables, 45 short deductions, 31 duplicate postings,
19 batches whose arithmetic does not close — and, for one of them, the exact sentence a human needs.
Then:

```bash
head -3 artifacts/settlements.csv   # declared vs recomputed, in paise
python -m cashpilot forecast --data data/synthetic --horizon 30
```

`settlements.csv` is per-batch declared-vs-recomputed fee/GST/TDS/TMN in paise: the number the finance
team takes to the gateway. The forecast line is the 7/14/30-day position with P10, the worst day in
the window, and whether the operating minimum gets breached.

## 4. Tests (~12 s)

```bash
PYTHONPATH=src python -m pytest -q          # 93 passed
```

The interesting ones to name, because they are the failures we actually hit:

* `test_accuracy_and_backtest.py::test_sliced_ledger_does_not_know_what_happened_after_the_origin`
  — the backtest was leaking future payment state; fixing it moved 30-day error from 25.4% to 1.8%.
* `test_forecast_engine.py::test_every_rupee_is_attributed_to_exactly_one_component`
  — a "run-rate for everything else" term was double-counted with the churn term; re-adding it now
  costs 10 points of accuracy and fails the test.
* `test_ai_scope.py::test_a_brief_may_not_invent_a_number` and
  `test_no_llm_output_reaches_a_posting_or_a_payment` — the AI cannot move money or invent figures.

## 5. If asked "what happens on 500 random transactions?"

500 lines is *below* the corpus the numbers come from (1,709). Same command, any size:

```bash
python -m cashpilot generate --out /tmp/500 --seed 1234 --as-of 2026-09-05 \
  --history-days 60 && python -m cashpilot bench --data /tmp/500 --reps 1
```

Answer to give: about 97% of *matchable* lines reconciled correctly at 99.7% precision on this class of
data, ~100% auto-post precision, ~0.5 s of engine time, and the residue is a written list of what it
refused rather than a silent guess. The honest caveat: 2.6% of resolvable lines were refused, and on a
real feed the mess distribution would differ — which is exactly why refusals are itemised.

## 6. If asked "where is the AI?"

`src/cashpilot/ai/` — three files, two call sites, both advisory: exception triage and the brief.
Everything else is regex, integer arithmetic and a learned delay distribution.
Enable the two LLM calls to see them work:

```bash
cp .env.example .env    # set CASHPILOT_LLM_ENABLED=1, a base URL and a key
set -a; . ./.env; set +a
python -m cashpilot run --data data/sample --out /tmp/with-llm
python -c "import json;print(json.load(open('/tmp/with-llm/run_manifest.json'))['llm'])"
```

You will see `calls / ok / failed / invalid_json / budget_remaining` — and `brief.md` sourced from the
model, with its digits still validated against the evidence block. Turn it off and nothing else changes.

## 7. If asked "why should we believe any of this?"

`docs/ACCURACY.md` starts with the scoring contract: a line is correct only if the whole document *set*
matches; a subset of a lumpsum is graded as partial; a bank charge that gets posted to an invoice is
graded wrong and also breaks the quarantine score. Then it gives the command that regenerates every
number, and closes with the list of what is optimistic about them. `docs/FAILURES.md` documents five
things that did not work, with the measured cost of each.
