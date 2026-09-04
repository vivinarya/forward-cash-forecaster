"""The scoring contract and the rolling-origin backtest.

These tests are the reason the numbers in README/artifacts can be trusted: they pin down what
counts as "correct" (whole-document-set equality), and they keep the backtest free of look-ahead.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cashpilot.eval.accuracy import MATCHABLE, QUARANTINE, load_truth, score
from cashpilot.forecast.backtest import _err_share, _slice_dataset, backtest, seeded_future_check
from cashpilot.ingest import load_dataset
from cashpilot.models import BankLine, LedgerDoc, Match


def m(line_id: str, docs: tuple[str, ...], *, tier: str = "t4_amount_exact", conf: float = 0.95, auto: bool = True) -> Match:
    return Match(line_id=line_id, doc_ids=docs, tier=tier, score=0.9, confidence=conf, auto_post=auto)


def truth(kind: str, docs: tuple[str, ...], amount: int = 100_00) -> dict[str, dict[str, object]]:
    return {
        "L1": {
            "docs": frozenset(docs),
            "kind": kind,
            "amount_paise": amount,
            "narration": "x",
            "txn_date": "2026-09-01",
        }
    }


def test_correct_requires_the_whole_document_set_not_just_one_hit():
    card = score([m("L1", ("A", "B"))], truth("matchable", ("A", "B")))
    assert card.correct == 1 and card.partial == 0 and card.wrong == 0
    assert card.precision == pytest.approx(1.0) and card.recall == pytest.approx(1.0)


def test_a_subset_counts_as_partial_not_as_a_match():
    """Matching 1 of the 3 invoices a lumpsum covers is a *wrong answer* we grade honestly."""
    card = score([m("L1", ("A",))], truth("matchable", ("A", "B", "C")))
    assert card.correct == 0 and card.partial == 1
    assert card.precision < 1.0
    assert card.unresolved and card.unresolved[0]["why"] == "partial_set"


def test_an_unrelated_document_is_wrong_not_partial():
    """Claiming a document the line never touched is the expensive error; it must be counted."""
    card = score([m("L1", ("X",))], truth("matchable", ("A",)))
    assert card.wrong == 1 and card.partial == 0 and card.correct == 0
    assert card.false_doc_assignments == 1


def test_two_matches_for_one_line_is_a_defect_not_an_average():
    card = score([m("L1", ("A",)), m("L1", ("B",))], truth("matchable", ("A", "B")))
    assert card.correct == 0 and card.partial == 1, "the union is right but the engine split it - still a finding"


def test_quarantine_lines_must_stay_unmatched():
    card = score([], truth("expected_unmatched_charge", ()))
    assert card.lines_quarantine == 1 and card.quarantine_accuracy == pytest.approx(1.0)
    assert card.recall == 0.0 and card.match_rate == 0.0, "nothing matched, and that is the right answer here"
    assert card.correctly_quarantined == 1

    card2 = score([m("L1", ("A",))], truth("expected_unmatched_charge", ()))
    assert card2.quarantine_accuracy == pytest.approx(0.0)
    assert card2.wrongly_claimed_quarantine == 1
    assert card2.unresolved[0]["why"] == "posted_a_line_that_should_not_be_posted"


def test_auto_post_precision_is_measured_only_on_what_was_auto_posted():
    card = score(
        [m("L1", ("A",), auto=True), m("L2", ("B",), auto=True, conf=0.6)],
        {**truth("matchable", ("A",)), "L2": {"docs": frozenset({"C"}), "kind": "matchable", "amount_paise": 1, "narration": "", "txn_date": ""}},
    )
    assert card.auto_post_count == 2
    assert card.auto_post_precision == pytest.approx(0.5), "one of the two auto-posts is wrong: the number must show it"


def test_rupee_accuracy_weights_the_money_not_the_line_count():
    small = {"docs": frozenset({"A"}), "kind": "matchable", "amount_paise": 100_00, "narration": "", "txn_date": ""}
    big = {"docs": frozenset({"B"}), "kind": "matchable", "amount_paise": 100_00_00, "narration": "", "txn_date": ""}
    card = score([m("L1", ("A",))], {"L1": small, "L2": big})
    assert card.rupee_accuracy < 0.02, "correct on the ₹100 line only must not read as ~50%"


def test_unresolved_list_is_capped_but_the_count_is_not():
    t = {f"L{i}": {"docs": frozenset({f"D{i}"}), "kind": "matchable", "amount_paise": 1, "narration": "", "txn_date": ""} for i in range(50)}
    card = score([], t, unresolved_cap=5)
    assert len(card.unresolved) == 5 and card.unmatched_but_matchable == 50


def test_truth_kinds_partition_the_feed():
    assert {"matchable", "matchable_lumpsum", "gateway_settlement"} <= MATCHABLE
    assert {"expected_unmatched_duplicate", "expected_unmatched_unknown"} <= QUARANTINE
    assert not (MATCHABLE & QUARANTINE)


def test_load_truth_reads_the_generated_file(tiny_corpus):
    t = load_truth(tiny_corpus / "truth_matches.csv")
    assert len(t) > 50
    assert all(x["kind"] for x in t.values()), "every line must be classified, or the score is fiction"
    assert any(x["kind"] == "matchable_lumpsum" for x in t.values())
    assert any(not x["docs"] for x in t.values()), "the corpus must contain unmatchable lines"


def test_err_share_is_finite_when_net_flow_is_zero():
    """A Sunday with no movement is not an infinite percentage error."""
    assert _err_share(500_00, 0, 0) == 0.0
    assert _err_share(500_00, 1000_00, 1000_00) == pytest.approx(25.0)


def test_sliced_ledger_does_not_know_what_happened_after_the_origin(tmp_path):
    """The look-ahead bug that made the first backtest numbers look impossible.

    `status`/`paid_amount` in the CSVs describe *today*; at an earlier origin a document that was
    paid later must be outstanding again, or the model is graded on information it could not have.
    """
    from cashpilot.models import BankLine as _B

    ds_like = type("D", (), {})()
    as_of = date(2026, 9, 5)
    from cashpilot.ingest import Dataset

    doc = LedgerDoc(
        doc_id="BILL-1",
        kind="AP",
        number="BILL-2026-0001",
        counterparty="Vendor",
        amount_paise=500_00_00,
        net_amount_paise=500_00_00,
        doc_date=as_of - timedelta(days=40),
        due_date=as_of - timedelta(days=20),
        status="paid",
        paid_amount_paise=500_00_00,
    )
    ds = Dataset(
        lines=[
            _B("L0", as_of - timedelta(days=30), "NEFT CR OTHER", 10_00_00),
            _B("L1", as_of - timedelta(days=2), "NEFT DR VENDOR", -500_00_00),  # the payment, in the future of the origin
        ],
        bills=[doc],
        as_of=as_of,
    )
    origin = as_of - timedelta(days=10)
    sliced = _slice_dataset(ds, origin)
    assert [ln.line_id for ln in sliced.lines] == ["L0"], "the payment line is after the origin and must be invisible"
    assert sliced.docs and sliced.docs[0].outstanding_paise == 500_00_00, "the payment happens after the origin; it must not be known yet"


def test_backtest_runs_and_reports_everything_it_claims(tiny_corpus, settings):
    ds = load_dataset(tiny_corpus)
    bt = backtest(ds, settings, horizons=[7], n_origins=3, warmup_days=25, runs=60, step_days=10)
    assert bt.origins, "the tiny corpus must be long enough for at least one origin"
    for name in ("cashpilot", "seasonal_naive", "moving_avg", "due_date_sum"):
        assert f"{name}_share_gross_7" in bt.metrics, f"{name} must be scored on the same windows"
    assert bt.metrics["cashpilot_share_gross_7"] >= 0
    assert "band_hit_7" in bt.coverage
    assert all(row["error_paise"] == row["predicted_net_paise"] - row["actual_net_paise"] for row in bt.per_origin)


def test_seeded_check_reports_the_metric_it_names(tiny_corpus, settings):
    ds = load_dataset(tiny_corpus)
    res = seeded_future_check(ds, settings, horizon=14)
    assert res["horizon_days"] == 14
    assert "error_share_of_gross_pct" in res and "cumulative_error_pct" in res
    assert "caveat" in res and "upper bound" in res["caveat"].lower()
