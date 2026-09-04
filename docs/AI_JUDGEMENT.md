# Where the model is used, and where it is forbidden

The instruction we took seriously: *no AI everywhere*. So this file is an inventory, not a pitch. Every
row is checkable in code, and the last section is the list of things the LLM provably cannot do.

## The two uses

| # | job | why a model | blast radius if it is wrong |
|---|---|---|---|
| 1 | **Triage of ambiguous exceptions** (`ai/triage.py`) | reading `"NEFT CR MERIDIAN ROOF PVT LT 4,50,000/-"` and deciding that this looks like a partial payment against two invoices rather than a new advance is a judgement over messy text; the rule ladder already tried and refused | one line of advice in `exceptions.csv` and a suggested owner/action. Nothing is posted |
| 2 | **The morning brief** (`ai/narrative.py`) | a 90-word summary of five figures, three themes and one risk is prose, and prose is what a language model is for | a paragraph a human reads. Numbers it emits are checked against the evidence block |

That is all. Two call sites, one file each.

## Everything else is deterministic, on purpose

| task | method | why not a model |
|---|---|---|
| amount equality, paisa | integer `int` paise, `Decimal` at the boundary | `0.1 + 0.2` is how reconciliation bugs are born |
| fee / GST / TDS / TMN recompute | arithmetic from `config/fee_schedule.json` | an auditable formula beats a plausible guess; the whole point is to price the discrepancy |
| document reference extraction | configurable regexes in `config/recon_rules.json` | `INV-2026-00123` has a shape; a regex has 100% recall on a shape and no hallucination |
| date parsing | format list + explicit Indian DD/MM/YYYY fallback | a model that "fixes" `05/09/2026` into September 5 or May 9 is a liability |
| name matching | normalise → token containment + `SequenceMatcher` edit ratio | must be reproducible when someone asks "why did you post this?" |
| lumpsum allocation | subset-sum over an inverted token index | arithmetic on a bounded candidate set; also 5 ms and 289 ms of tuning went into making it fast |
| payment timing | learned empirical delay distribution + survival with cure fraction | a distribution you can plot per customer is a distribution a credit manager can argue with |
| bands | 2,000-path Monte Carlo, `numpy` percentiles | needs a sampler, not a language |
| scoring | ground-truth set equality | the measurement must not be model-dependent, or nothing is measured |

## The safety envelope

`ai/triage.py`, in order:

1. **Eligibility.** A code allow-list (`AMBIGUOUS_CANDIDATES`, `AMBIGUOUS_LUMPSUM`, `UNALLOCATED_CREDIT`,
   `UNMATCHED_DEBIT`, `DOC_REF_OUTSIDE_WINDOW`, `SHORT_DEDUCTION`, `SUSPECTED_DUPLICATE`,
   `CREDIT_AMOUNT_MISMATCH`). A bank charge does not need a language model, and asking for one costs
   money and adds risk for zero information.
2. **Deterministic first.** `deterministic_triage` classifies by code and severity before any prompt is
   built; the model only adds to a table that already has an answer.
3. **Budget.** `CASHPILOT_LLM_MAX_CALLS` (default 200) is enforced by `budget()` *and* decremented at
   the transport layer; `run_manifest.json` reports `calls / ok / failed / invalid_json /
   budget_remaining / approx_tokens / wall_ms`, so "we used 41 calls and 3 answers were thrown out" is
   a fact in the artifact, not a claim in a README.
4. **Validation, then discard.** `_validate` requires a category from a fixed taxonomy, confidence in
   [0.4, 1.0], a non-empty explanation, a known owner, a known action, and cross-references that point
   at ids the batch actually contained. Failures increment `invalid_json` and the row keeps the
   deterministic classification.
5. **Prompt discipline.** The narration is passed **verbatim** (`narration (verbatim, may be truncated
   by the bank)`) so the model reads the bank's own text, not our normalisation of it. The system
   prompt is two sentences of scope: *"You classify unresolved bank-statement lines into a fixed
   taxonomy. You never invent document numbers. You answer with a single JSON object and no prose."*
   We do not trust that instruction — step 4 enforces it — but it stops the model being rewarded for
   inventing, and the prompt also asks for one field no rule can produce: `question_to_ask`, the
   sentence to send the counterparty, which lands in `exceptions.csv`.
6. **Nothing to answer when there is nothing to judge.** `build_brief` is given an evidence block whose
   every figure was computed deterministically, and its accuracy line reads `not measured` until a
   backtest has actually run — the brief cannot flatter the model that produced it.
7. **No digits it did not see.** `build_brief` rejects any number in the generated text that is not in
   the evidence block and falls back to `template_brief`, which is written by string interpolation from
   the same figures. `tests/test_ai_scope.py::test_a_brief_may_not_invent_a_number`.

`tests/test_ai_scope.py` also pins three structural facts, because prose about scope decays:

* with `CASHPILOT_LLM_ENABLED=0`, `urlopen` is monkeypatched to explode and no test dies — i.e. nothing
  tried to call out;
* the call budget is hard at the transport boundary (4th call is refused, not queued);
* running triage cannot mutate a match, an auto-post flag, or the document count.

## Offline by default

No key, no network, no failure: `llm.enabled=false` ⇒ deterministic judge + template brief, and
`run_manifest.json` says so. The demo command passes `--llm off` explicitly, so the numbers a panel
sees were produced with the model switched off. Enable it with

```bash
cp .env.example .env   # then: CASHPILOT_LLM_ENABLED=1 and a key
set -a; . ./.env; set +a
python -m cashpilot run --data data/synthetic --out artifacts
```

Any OpenAI-compatible `/chat/completions` endpoint works (`CASHPILOT_LLM_BASE_URL`), including a local
one. `temperature=0`, `response_format={"type":"json_object"}`, `max_tokens=500`, timeout
`CASHPILOT_LLM_TIMEOUT_S`, and every transport error is counted and swallowed — a dead endpoint degrades
the run to its offline shape, it does not fail the books.

## What the model can never do in this codebase

* move, post, or unpost money (no write path to `Dataset.docs`, no ledger file is rewritten);
* create or close a document;
* change a `Match.doc_ids`, its `confidence`, or `auto_post` — the triage table is keyed by exception
  `ref_id` and consumed only by the renderer;
* approve a payment or touch a gateway credential (there is no payment code path at all);
* be the only source of a number in the brief (the digit check above);
* run unbounded (budget) or unseen (`usage()` in the manifest).

If a future change violates that list, `test_no_llm_output_reaches_a_posting_or_a_payment` should fail.
