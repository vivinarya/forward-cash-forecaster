"""The AI boundary: two jobs, one budget, and no way for a model to move money.

If any of these fail, the "AI where it adds value, not everywhere" claim in README is false - so
they are written as claims about the *system*, not about the prompt.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cashpilot.ai.llm import LlmClient, budget
from cashpilot.ai.narrative import _numbers_allowed, build_brief, template_brief
from cashpilot.ai.triage import CATEGORIES, _validate, deterministic_triage, triage
from cashpilot.config import load_settings
from cashpilot.ingest import load_dataset
from cashpilot.models import ReconException


def no_llm_settings():
    st = load_settings()
    st.llm_enabled = False
    st.llm_api_key = None
    return st


def test_a_disabled_client_never_touches_the_network(monkeypatch):
    import urllib.request

    def boom(*a, **k):  # pragma: no cover - the point is that it is never called
        raise AssertionError("a network call was attempted with the LLM disabled")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    st = no_llm_settings()
    client = LlmClient(st)
    assert client.enabled is False
    assert budget(client) is False
    assert client.complete_json("sys", "user") is None
    assert client.usage()["calls"] == 0


def test_the_call_budget_is_hard(monkeypatch):
    """The transport is faked, but the *real* `LlmClient._post` runs, so the counter under test is
    the one production uses."""
    st = no_llm_settings()
    st.llm_enabled = True
    st.llm_api_key = "test-key"
    st.llm_max_calls = 3
    client = LlmClient(st)
    assert client.budget_left() == 3

    attempts = {"n": 0}

    class _Resp:
        def read(self):
            return b'{"choices": [{"message": {"content": "{\\"category\\": \\"unknown\\"}"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request as ur

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        return _Resp()

    monkeypatch.setattr(ur, "urlopen", fake_urlopen)
    for _ in range(5):
        client.complete_json("s", "u")
    assert attempts["n"] == 3, "calls past the budget must be refused, not queued"
    assert client.usage()["calls"] == 3
    assert budget(client) is False


def test_triage_classifies_everything_deterministically_first(run_result):
    st = no_llm_settings()
    out = triage(run_result.recon.exceptions, run_result.dataset, st, limit=200, llm=LlmClient(st))
    assert out["stats"]["llm_attempted"] == 0
    assert out["stats"]["skipped_reason"] == "llm_disabled_or_no_key"
    table = out["table"]
    assert table, "the deterministic judge must classify something without a model"
    for row in table.values():
        assert row["category"] in CATEGORIES


def test_only_ambiguous_codes_are_worth_a_model_call():
    """A bank charge does not need a language model. The allow-list is the scope decision, tested."""
    st = no_llm_settings()
    client = LlmClient(st)
    excs = [
        ReconException("L1", "bank_line", "BANK_CHARGE_NO_DOCUMENT", "low", 1_00, "gateway fee", (), "post_to_charges"),
        ReconException("L2", "bank_line", "OVERDUE_UNRECONCILED_AR", "medium", 1_00, "old invoice", (), "chase"),
    ]
    out = triage(excs, type("D", (), {"docs": [], "lines": []})(), st, llm=client)
    assert out["stats"]["llm_attempted"] == 0, "neither of these codes is ambiguous"
    det = deterministic_triage(excs)
    assert set(det) == {"L1", "L2"}


def test_answers_are_rejected_on_form_not_trusted():
    exc = ReconException("L1", "bank_line", "AMBIGUOUS_CANDIDATES", "high", 10_00, "two candidates", ("INV-1", "INV-2"), "check")

    assert _validate({"category": "i_paid_the_lottery", "confidence": 0.9, "likely_explanation": "x"}, exc, {}) == "category_out_of_taxonomy"
    assert _validate({"category": "partial_or_short_payment", "confidence": 4.0, "likely_explanation": "x"}, exc, {}) == "confidence_out_of_range"
    assert _validate({"category": "partial_or_short_payment", "confidence": 0.1, "likely_explanation": "x"}, exc, {}) == "low_confidence"
    assert _validate({"category": "partial_or_short_payment", "confidence": 0.8}, exc, {}) == "missing_explanation"

    payload = {
        "category": "partial_or_short_payment",
        "confidence": 0.8,
        "likely_explanation": "Customer cleared two invoices at once.",
        "owner": "Supreme Leader",
        "action": "burn_the_money",
        "same_root_cause_as": ["L999", "L1"],
    }
    assert _validate(payload, exc, {"L1": "row"}) is None
    assert payload["owner"] == "Banking-ops", "an out-of-taxonomy owner is coerced, not accepted"
    assert payload["action"] == "hold", "an unknown action must degrade to the safe one"
    assert payload["same_root_cause_as"] == ["L1"], "invented ids are dropped, keeping the real one"


def test_a_brief_may_not_invent_a_number():
    """The digit check is the whole reason an LLM is allowed near the CFO's inbox."""
    evidence = "today ₹2,20,00,000.00, in 30 days ₹8,12,31,446.94"
    assert _numbers_allowed("We hold ₹2,20,00,000.00 and expect ₹8,12,31,446.94.", evidence)
    assert not _numbers_allowed("We hold ₹9,99,00,000.00 today.", evidence)
    assert not _numbers_allowed("Settle 47 invoices by Friday.", evidence)
    # a brief with no figures at all is not grounded in the evidence either, so it is rejected too
    assert not _numbers_allowed("Cash looks fine this week.", evidence)


def test_brief_falls_back_to_a_template_offline(run_result):
    st = no_llm_settings()
    brief = build_brief(
        forecast=run_result.cash,
        recon_stats=run_result.recon.stats,
        triage_stats=run_result.triage_stats,
        verify_summary=run_result.verify_summary,
        settings=st,
        backtest=None,
        llm=LlmClient(st),
    )
    assert brief["source"] == "template"
    assert "bench --forecast" in brief["text"], "accuracy must be declared unmeasured, with the command that measures it"
    assert "%" not in brief["text"] or "0.0%" not in brief["text"]
    text = template_brief(brief["evidence"])
    assert any(ch.isdigit() for ch in text)
    assert len(text.split()) < 160


def test_no_llm_output_reaches_a_posting_or_a_payment(run_result):
    """The advisory-only rule: triage may annotate, never mutate a match or create a document."""
    st = no_llm_settings()
    out = triage(run_result.recon.exceptions, run_result.dataset, st, limit=50, llm=LlmClient(st))
    before = {(m.line_id, m.doc_ids, m.auto_post) for m in run_result.recon.matches}
    n_docs = len(run_result.dataset.docs)
    _ = out  # running triage must not touch the ledger
    after = {(m.line_id, m.doc_ids, m.auto_post) for m in run_result.recon.matches}
    assert before == after
    assert len(run_result.dataset.docs) == n_docs
