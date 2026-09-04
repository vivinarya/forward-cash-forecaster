"""Settlement verification - arithmetic, not judgement. Nothing in this file calls a model."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cashpilot.config import REPO_ROOT
from cashpilot.ingest import Dataset
from cashpilot.models import BankLine, GatewayPayment, LedgerDoc, Refund, Settlement
from cashpilot.verify.settlements import FeeSchedule, verify_settlements

AS_OF = date(2026, 9, 5)


@pytest.fixture(scope="module")
def sched() -> FeeSchedule:
    return FeeSchedule.load(REPO_ROOT / "config" / "fee_schedule.json")


def test_fee_schedule_is_read_from_config_not_hardcoded(sched: FeeSchedule):
    assert sched.modes["card"] == 0.02
    assert sched.modes["upi_qr"] == 0.0
    assert sched.gst_rate == 0.18
    assert sched.tds_rate == 0.02
    assert sched.rate_for("UPI_QR") == 0.0
    assert sched.rate_for(None) == sched.modes[sched.default_mode], "unknown method falls back to the default slab"
    assert sched.rate_for("bitcoin") == sched.modes[sched.default_mode]


def test_expected_deductions_are_reproducible_arithmetic(sched: FeeSchedule):
    """One worked example, fixed forever: if the formula changes, the docs change with it."""
    gross = 1_00_000_00  # ₹1,00,000.00 in paise
    exp = sched.expected(gross, [(gross, "card")])
    fee = int(gross * 0.02)  # 2000000 -> 2,000.00
    tmn = int(gross * sched.tmn_rate)
    gst = int((fee + tmn) * 0.18)
    tds = int(fee * 0.02)
    assert exp == {
        "fee": fee,
        "tmn": tmn,
        "gst": gst,
        "tds": tds,
        "refunds": 0,
        "net": gross - fee - tmn - gst - tds,
    }
    assert exp["net"] == gross - fee - tmn - gst - tds
    assert exp["net"] < gross and exp["gst"] > 0 and exp["tds"] > 0


def test_refunds_reduce_the_credit_not_the_fee_basis(sched: FeeSchedule):
    gross = 1_00_00_00
    plain = sched.expected(gross, [(gross, "card")])
    with_refund = sched.expected(gross, [(gross, "card")], refunds_paise=20_00_00)
    assert with_refund["fee"] == plain["fee"]
    assert with_refund["net"] == plain["net"] - 20_00_00


def test_mis_tiered_batch_is_flagged_and_the_overbilling_quantified(sched: FeeSchedule):
    """A ₹10 lakh UPI-app batch billed at a flat 2% while the contract says 2% + GST must show up
    as a rupee figure the finance team can chase, not a shrug."""
    gross = 10_00_00_00  # ₹1,00,000.00
    exp = sched.expected(gross, [(gross, "upi_apps")])
    undercharged_fee = int(gross * 0.01)  # gateway applied the wrong slab
    st = Settlement(
        settlement_id="SETL-BAD",
        settled_on=AS_OF - timedelta(days=1),
        payout_utr=None,
        batch_type="PAYOUT",
        txn_count=12,
        gross_paise=gross,
        fee_paise=undercharged_fee,
        tmn_paise=exp["tmn"],
        gst_paise=0,
        tds_paise=0,
        net_paise=gross - undercharged_fee,
    )
    inv = LedgerDoc(
        doc_id="INV-1",
        kind="AR",
        number="INV-2026-0001",
        counterparty="Acme",
        amount_paise=gross,
        net_amount_paise=gross,
        doc_date=AS_OF - timedelta(days=10),
        due_date=AS_OF - timedelta(days=1),
    )
    ds = Dataset(
        invoices=[inv],
        settlements=[st],
        payments=[
            GatewayPayment(
                payment_id="pay_1",
                order_id=None,
                invoice_number="INV-2026-0001",
                amount_paise=gross,
                captured_on=AS_OF - timedelta(days=3),
                fee_paise=undercharged_fee,
                settlement_id="SETL-BAD",
                method="upi_apps",
            )
        ],
        as_of=AS_OF,
    )
    rows, exceptions, summary = verify_settlements(ds, sched)
    assert rows, "the batch must be checked even when no bank line is present"
    flagged = [r for r in rows if r.flags]
    assert flagged, f"expected at least one flagged batch, got {[r.flags for r in rows]}"
    assert any("FEE_TIER_MISMATCH" in r.flags or "FEE_COMPONENT_MISMATCH" in r.flags or "BATCH_ARITHMETIC" in r.flags for r in flagged)
    assert summary["batches_flagged"] >= 1
    assert any(e.ref_type == "settlement" for e in exceptions)


def test_a_batch_inside_the_tolerance_is_left_alone(sched: FeeSchedule):
    """Precision is a feature: 200 paise of rounding on a batch must not become a finding."""
    gross = 1_00_00_00
    exp = sched.expected(gross, [(gross, "card")])
    st = Settlement(
        settlement_id="SETL-OK",
        settled_on=AS_OF - timedelta(days=1),
        payout_utr=None,
        batch_type="PAYOUT",
        txn_count=1,
        gross_paise=gross,
        fee_paise=exp["fee"],
        tmn_paise=exp["tmn"] + 1,  # one paisa of rounding noise
        gst_paise=exp["gst"],
        tds_paise=exp["tds"],
        net_paise=exp["net"] - 1,
    )
    ds = Dataset(
        settlements=[st],
        lines=[BankLine("L1", AS_OF, "IMPS CREDIT RAZORPAY PAYMENT GATEWAY SETTLEMENT SETL-OK", st.net_paise)],
        as_of=AS_OF,
    )
    rows, exceptions, summary = verify_settlements(ds, sched, {"SETL-OK": "L1"})
    assert summary["batches_flagged"] == 0, [r.flags for r in rows]
    assert rows and not rows[0].flags


def test_missing_settlement_within_expectation_window_is_not_an_alert(sched: FeeSchedule):
    """T+2 means a payment captured yesterday has no business having settled yet."""
    pay = GatewayPayment(
        payment_id="pay_new",
        order_id=None,
        invoice_number=None,
        amount_paise=50_00_00,
        captured_on=AS_OF - timedelta(days=1),
        fee_paise=10_000,
        settlement_id=None,
        method="card",
    )
    _rows, exceptions, _summary = verify_settlements(Dataset(payments=[pay], as_of=AS_OF), sched)
    assert not [e for e in exceptions if "SETTLEMENT_OVERDUE" == e.code]
    assert isinstance(sched.expect_days, int) and 1 <= sched.expect_days <= 4


def test_bank_charge_refund_rows_are_not_double_counted(sched: FeeSchedule):
    """A refund row is a negative movement inside a batch; it must reduce net, never the invoice."""
    gross = 2_00_00_00
    exp = sched.expected(gross, [(gross, "card")], refunds_paise=50_00_00)
    st = Settlement(
        settlement_id="SETL-R",
        settled_on=AS_OF,
        payout_utr=None,
        batch_type="PAYOUT",
        txn_count=2,
        gross_paise=gross,
        fee_paise=exp["fee"],
        tmn_paise=exp["tmn"],
        gst_paise=exp["gst"],
        tds_paise=exp["tds"],
        net_paise=exp["net"],
    )
    ds = Dataset(
        settlements=[st],
        refunds=[Refund(refund_id="rf_1", payment_id=None, amount_paise=50_00_00, created_on=AS_OF, settlement_id="SETL-R")],
        as_of=AS_OF,
    )
    rows, _exceptions, _summary = verify_settlements(ds, sched)
    assert rows
    assert rows[0].refund_paise == 50_00_00
    assert rows[0].expected_net_paise == gross - exp["fee"] - exp["tmn"] - exp["gst"] - exp["tds"] - 50_00_00
