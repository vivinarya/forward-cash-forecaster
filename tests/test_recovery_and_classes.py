"""Money at stake + per-class behaviour. Every number here is arithmetic on purpose-built objects,
so the claims in docs/ACCURACY.md about recovery can be re-derived without a corpus."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from cashpilot.eval.accuracy import load_truth, score
from cashpilot.eval.recovery import recovery_report
from cashpilot.models import Match
from cashpilot.verify.settlements import BatchCheck

TOL = 200  # paise, the same per-batch tolerance the checker publishes


def _check(
    sid: str,
    *,
    gross: int = 100_000_00,
    fee: int = 2_00_00,
    expected_fee: int = 2_00_00,
    tmn: int = 0,
    gst: int = 36_00,
    tds: int = 4_00,
    expected_gst: int = 36_00,
    expected_tds: int = 4_00,
    deductions: int = 0,
    credited: int | None = None,
    flags: list[str] | None = None,
) -> BatchCheck:
    """A batch whose declared arithmetic is internally consistent unless `credited` says otherwise."""
    declared_net = gross - fee - tmn - gst - tds - deductions
    expected_net = gross - expected_fee - tmn - expected_gst - expected_tds - deductions
    return BatchCheck(
        settlement_id=sid,
        settled_on="2026-09-01",
        bank_line_id=None,
        gross_paise=gross,
        declared_fee_paise=fee,
        expected_fee_paise=expected_fee,
        declared_net_paise=declared_net,
        expected_net_paise=expected_net,
        credited_paise=declared_net if credited is None else credited,
        refund_paise=0,
        txn_count=1,
        declared_tmn_paise=tmn,
        declared_gst_paise=gst,
        declared_tds_paise=tds,
        expected_gst_paise=expected_gst,
        expected_tds_paise=expected_tds,
        flags=flags if flags is not None else [],
    )


# --------------------------------------------------------------------- batch-level rupee maths
def test_fee_overbilling_is_the_rate_card_gap_not_the_whole_fee():
    r = _check("setl_a", fee=3_00_00, expected_fee=2_00_00, flags=["FEE_TIER_MISMATCH"])
    assert r.overbilled_paise == 1_00_00  # ₹1,000.00 billed above the slab that applies


def test_a_credit_that_matches_the_wrong_declaration_is_still_a_shortfall():
    r = _check("setl_b", fee=3_00_00, expected_fee=2_00_00, flags=["BATCH_ARITHMETIC"])
    assert r.undercredited_paise == 1_00_00
    # and the claim is counted once: max(rate card, cash shortfall), not the sum of two names for it
    assert r.recoverable_paise == 1_00_00


def test_deductions_with_no_evidence_behind_them_are_their_own_bucket():
    # declared net is ₹5,000 lower than gross minus every deduction on file: money went out, no row says why
    r = _check("setl_c", credited=None, flags=["BATCH_ARITHMETIC"])
    declared_net = r.gross_paise - r.declared_fee_paise - r.declared_gst_paise - r.declared_tds_paise
    short = declared_net - 5_00_00
    r.declared_net_paise = short
    r.expected_net_paise = short
    r.credited_paise = short
    assert r.unexplained_deduction_paise == 5_00_00  # positive = money out, no row explains it
    assert r.recoverable_paise == 5_00_00
    assert r.undercredited_paise == 0


def test_recovery_rate_is_rupee_weighted_and_cannot_exceed_100():
    ok = _check("setl_clean")
    assert ok.recovery_rate_pct == 100.0
    short = _check("setl_half", credited=ok.expected_net_paise // 2)
    assert short.recovery_rate_pct == 50.0
    over = _check("setl_over", credited=ok.expected_net_paise + 10_00_00)
    assert over.recovery_rate_pct == 100.0, "an over-credited batch is not negative recovery"


def test_credit_gap_compares_the_bank_line_with_the_declaration():
    r = _check("setl_gap", credited=50_00_00)
    assert r.credit_gap_paise == 50_00_00 - r.declared_net_paise


# ------------------------------------------------------------------------- the recovery report
def _recon(matches=(), exceptions=()):
    return SimpleNamespace(matches=list(matches), exceptions=list(exceptions))


def test_runtime_block_needs_no_ground_truth(tmp_path: Path):
    rows = [_check("setl_1"), _check("setl_2", fee=4_00_00, expected_fee=2_00_00, flags=["FEE_TIER_MISMATCH"])]
    summary = {"tolerance_paise_per_batch": TOL, "recovery_rate_pct": 99.0, "gross_at_stake_pct": 1.0}
    rep = recovery_report(tmp_path, _recon(), rows, summary)
    assert rep.runtime["batches"] == 2
    assert rep.runtime["recoverable_paise"] == 2_00_00
    assert rep.runtime["batches_with_rupee_stake"] == 1
    assert rep.runtime["claim_value"] == "₹200.00"  # 20,000 paise
    assert rep.batch_defects["measured"] is False and "reason" in rep.batch_defects
    assert any("not measured" in n for n in rep.notes), "silence would read like a zero"


def test_missing_corruption_is_never_reported_as_a_zero_catch_rate(tmp_path: Path):
    rep = recovery_report(tmp_path, _recon(), [], {"tolerance_paise_per_batch": TOL})
    assert rep.runtime["batches"] == 0
    assert rep.runtime["recoverable_paise"] == 0
    assert rep.batch_defects["measured"] is False
    assert "detection_rate_pct" not in rep.runtime


def test_catch_rate_is_capped_at_the_planted_rupees(tmp_path: Path):
    """A flagged batch carrying a big generic claim must not push the catch rate over 100%."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"planted": {"drift_by_batch": {"setl_x": 1_00_00}, "mis_tier_by_batch": {}, "dropped_refund_by_batch": {}}})
    )
    rows = [_check("setl_x", credited=40_00_00, flags=["BATCH_ARITHMETIC"])]  # ₹22k claimed, ₹1k planted
    rep = recovery_report(tmp_path, _recon(), rows, {"tolerance_paise_per_batch": TOL})
    bd = rep.batch_defects
    assert bd["measured"] is True and bd["planted_batches"] == 1 and bd["flagged_batches"] == 1
    assert bd["identified_paise"] == 1_00_00 <= bd["planted_paise"] == 1_00_00
    assert bd["rupee_catch_rate_pct"] == 100.0


def test_unflagged_planted_batch_lowers_detection_and_is_counted(tmp_path: Path):
    (tmp_path / "meta.json").write_text(
        json.dumps({"planted": {"drift_by_batch": {"setl_miss": 5_00_00, "setl_hit": 5_00_00}}})
    )
    rows = [
        _check("setl_miss"),
        _check("setl_hit", credited=None, flags=["BATCH_ARITHMETIC"]),
    ]
    rows[0].declared_net_paise -= 5_00_00
    rows[0].expected_net_paise = rows[0].declared_net_paise
    rep = recovery_report(tmp_path, _recon(), rows, {"tolerance_paise_per_batch": TOL})
    assert rep.batch_defects["detection_rate_pct"] == 50.0
    assert rep.classes[1]["planted"] == 2 and rep.classes[1]["flagged"] == 1


def test_false_positive_count_is_kept_visible(tmp_path: Path):
    (tmp_path / "meta.json").write_text(json.dumps({"planted": {"drift_by_batch": {"setl_real": 1_00_00}}}))
    rows = [
        _check("setl_real", flags=["BATCH_ARITHMETIC"]),
        _check("setl_noise", fee=9_00_00, expected_fee=2_00_00, flags=["FEE_TIER_MISMATCH"]),
    ]
    rep = recovery_report(tmp_path, _recon(), rows, {"tolerance_paise_per_batch": TOL})
    assert rep.batch_defects["batches_flagged_with_no_planted_defect"] == 1


def test_short_payments_are_traced_from_the_schedule(tmp_path: Path):
    (tmp_path / "truth_schedule.csv").write_text(
        "doc_id,document_no,kind,net_paise,short_deduction_paise\n"
        "INV-1,INV-001,AR,90000,10000\n"  # surfaced through a match carrying the gap
        "INV-2,INV-002,AR,95000,5000\n"  # never surfaced - the engine saw nothing
    )
    matches = [
        Match(
            line_id="BL-1",
            doc_ids=("INV-1",),
            tier="t3_doc_number",
            score=0.9,
            confidence=0.9,
            auto_post=True,
            evidence="doc",
            amount_diff_paise=-10_00,
        )
    ]
    rep = recovery_report(
        tmp_path,
        _recon(matches),
        [],
        {"tolerance_paise_per_batch": TOL},
    )
    ar = rep.receivables
    assert ar["planted_docs"] == 2 and ar["surfaced_docs"] == 1
    assert ar["detection_rate_pct"] == 50.0
    assert ar["planted_paise"] == 15_000 and ar["surfaced_paise"] == 1_000
    assert ar["missed_docs"] == 1
    assert rep.classes[-1]["exception"] == "SHORT_DEDUCTION"


def test_short_payment_still_in_the_queue_counts_as_seen_but_unclaimed(tmp_path: Path):
    """A line a human will open is not the same thing as a claim raised on the invoice."""
    (tmp_path / "truth_schedule.csv").write_text(
        "doc_id,document_no,kind,net_paise,short_deduction_paise,paid,scheduled_pay\n"
        "AP-RENT-1,RENT-1,AP,9000000,1738800,1,2026-07-06\n"
    )
    exc = SimpleNamespace(
        code="UNMATCHED_DEBIT",
        ref_type="bank_line",
        ref_id="BL-9",
        amount_paise=-(9_000_000 - 1_738_800),  # net minus the deduction: the money is right there
        candidates=(),
    )
    rep = recovery_report(
        tmp_path,
        _recon(exceptions=[exc]),
        [],
        {"tolerance_paise_per_batch": TOL},
        as_of="2026-09-05",
    )
    ar = rep.receivables
    assert ar["planted_docs"] == 1 and ar["surfaced_docs"] == 0
    assert ar["queued_docs"] == 1 and ar["queued_paise"] == 1_738_800
    assert ar["missed_docs"] == 0
    assert ar["detection_rate_pct"] == 0.0 and ar["queue_rate_pct"] == 100.0


def test_payments_after_the_asof_date_are_not_counted_as_misses(tmp_path: Path):
    (tmp_path / "truth_schedule.csv").write_text(
        "doc_id,document_no,kind,net_paise,short_deduction_paise,paid,scheduled_pay\n"
        "INV-1,INV-001,AR,90000,10000,1,2026-09-01\n"
        "INV-2,INV-002,AR,90000,20000,1,2026-10-11\n"  # not in the feed yet
        "INV-3,INV-003,AR,90000,30000,0,1970-01-01\n"  # never paid
    )
    rep = recovery_report(tmp_path, _recon(), [], {"tolerance_paise_per_batch": TOL}, as_of="2026-09-05")
    ar = rep.receivables
    assert ar["planted_in_plan"] == 3
    assert ar["planted_docs"] == 1, "only the payment that reached the bank is in scope"
    assert ar["planted_paise"] == 10_000


def test_a_gap_inside_tolerance_is_not_a_finding(tmp_path: Path):
    (tmp_path / "truth_schedule.csv").write_text("doc_id,document_no,kind,net_paise,short_deduction_paise\nINV-1,INV-001,AR,99900,100\n")
    matches = [
        Match(
            line_id="BL-1",
            doc_ids=("INV-1",),
            tier="t3_doc_number",
            score=0.9,
            confidence=0.9,
            auto_post=True,
            evidence="doc",
            amount_diff_paise=-100,
        )
    ]
    rep = recovery_report(tmp_path, _recon(matches), [], {"tolerance_paise_per_batch": TOL})
    assert rep.receivables["surfaced_docs"] == 0, "noise under the tolerance must not be sold as a catch"


# ------------------------------------------------------------------------ accuracy per class
def _truth(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(r["line_id"]): r for r in rows}


def test_by_kind_splits_a_good_class_from_a_bad_one():
    truth = _truth(
        [
            {
                "line_id": "BL-1",
                "docs": frozenset({"INV-1"}),
                "kind": "matchable",
                "amount_paise": 100_00,
                "narration": "RTGS INV-1",
                "txn_date": "2026-09-01",
            },
            {
                "line_id": "BL-2",
                "docs": frozenset({"INV-2", "INV-3"}),
                "kind": "matchable_lumpsum",
                "amount_paise": 200_00,
                "narration": "LUMPSUM",
                "txn_date": "2026-09-01",
            },
            {
                "line_id": "BL-3",
                "docs": frozenset(),
                "kind": "expected_unmatched_charge",
                "amount_paise": -50_00,
                "narration": "BANK CHARGE",
                "txn_date": "2026-09-01",
            },
        ]
    )
    matches = [
        Match(
            line_id="BL-1",
            doc_ids=("INV-1",),
            tier="t3_doc_number",
            score=0.99,
            confidence=0.99,
            auto_post=True,
            evidence="doc",
            amount_diff_paise=0,
        ),
        Match(
            line_id="BL-2",
            doc_ids=("INV-2",),
            tier="t4_party_fuzzy",
            score=0.8,
            confidence=0.8,
            auto_post=False,
            evidence="partial",
            amount_diff_paise=0,
        ),
    ]
    card = score(matches, truth)
    assert card.by_kind["matchable"] == {
        "lines": 1,
        "correct": 1,
        "partial": 0,
        "wrong": 0,
        "unmatched": 0,
        "refused_correctly": 0,
        "resolution": "matched to the exact document set",
        "correct_pct": 100.0,
    }
    lump = card.by_kind["matchable_lumpsum"]
    assert lump["partial"] == 1 and lump["correct_pct"] == 0.0
    assert lump["resolution"] == "one line, several documents"
    chg = card.by_kind["expected_unmatched_charge"]
    assert chg["refused_correctly"] == 1 and chg["correct_pct"] == 100.0
    assert chg["resolution"] == "left unmatched"
    assert card.by_kind["matchable"]["resolution"] == "matched to the exact document set"


def test_load_truth_reads_the_kind_column(tmp_path: Path):
    f = tmp_path / "truth_matches.csv"
    f.write_text(
        "line_id,doc_ids,truth_kind,amount_paise,narration,txn_date\n"
        "BL-1,INV-1;INV-2,matchable_lumpsum,-500000,NEFT RETURN,2026-09-01\n"
    )
    t = load_truth(f)
    assert t["BL-1"]["kind"] == "matchable_lumpsum"
    assert t["BL-1"]["docs"] == frozenset({"INV-1", "INV-2"})
    assert t["BL-1"]["amount_paise"] == -500000


# ------------------------------------------------------------------- reporting the recovery page
def test_recovery_md_says_not_measured_instead_of_inventing_a_rate(tmp_path: Path):
    from cashpilot.report import recovery_md

    rep = recovery_report(tmp_path, _recon(), [], {"tolerance_paise_per_batch": TOL})
    res = SimpleNamespace(recovery=rep, verify_summary={})
    text = recovery_md(res)
    assert "Detection rate: not measured" in text
    assert "does not invent one" in text
    assert "catch rate cannot be claimed" in text


def test_recovery_md_prints_measured_rates_when_a_ledger_exists(tmp_path: Path):
    from cashpilot.report import recovery_md

    (tmp_path / "meta.json").write_text(
        json.dumps({"planted": {"mis_tier_by_batch": {"setl_q": 2_00_00}}})
    )
    rows = [_check("setl_q", fee=4_00_00, expected_fee=2_00_00, flags=["FEE_TIER_MISMATCH"])]
    rep = recovery_report(tmp_path, _recon(), rows, {"tolerance_paise_per_batch": TOL})
    text = recovery_md(SimpleNamespace(recovery=rep, verify_summary={}))
    assert "did we catch what was planted" in text
    assert "₹200.00" in text
    assert "100.0%" in text


# ---------------------------------------------------------------------------- the scale sweep
def test_sweep_marks_a_small_sample_class_as_not_the_headline():
    """`_worst_class` must prefer a class with real volume over a 1-line class at 0%."""
    from cashpilot.eval.sweep import _worst_class

    kinds = {
        "matchable": {"lines": 500, "correct_pct": 99.0},
        "matchable_lumpsum": {"lines": 3, "correct_pct": 0.0},
        "matchable_amount_mismatch": {"lines": 40, "correct_pct": 64.0},
    }
    label, rate = _worst_class(kinds)
    assert "amount_mismatch" in label and rate == "64.0%"
    label, rate = _worst_class({})
    assert (label, rate) == ("n/a", None)


def test_sweep_markdown_renders_every_table_from_one_row():
    from cashpilot.eval.sweep import to_markdown

    row = {
        "scale": "tiny",
        "bank_lines": 90,
        "bank_visible_docs": 253,
        "gateway_payments": 1341,
        "settlement_batches": 42,
        "generate_ms": 39.1,
        "ingest_ms": 141.9,
        "reconcile_ms": 10.0,
        "forecast_ms": 91.9,
        "end_to_end_ms": 248.4,
        "dominant_tier": "t0_duplicates_ms",
        "dominant_tier_share_pct": 32.5,
        "exact_recall": 0.6951,
        "full_precision": 1.0,
        "full_recall": 0.9756,
        "full_f1": 0.9876,
        "auto_post_precision": 1.0,
        "rupee_accuracy": 0.9531,
        "refused_matchable": 2,
        "imperfect": 0,
        "lines_per_s": 9021.3,
        "recoverable_paise": 992112,
        "batches_with_stake": 6,
        "recovery_rate_pct": 95.95,
        "defect_detection_pct": 100.0,
        "rupee_catch_pct": 100.0,
        "short_pay_detection_pct": 0.0,
        "worst_class": "matchable (40 lines)",
        "worst_class_rate": "97.5%",
        "by_kind": {"matchable": {"lines": 40, "correct": 39, "refused": 1, "rate": 97.5}},
    }
    text = to_markdown([row])
    for heading in ("## Size and time", "## Matching quality", "## Money at stake and defect recovery", "## Where the time goes"):
        assert heading in text
    assert "tiny" in text and "97.5%" in text
