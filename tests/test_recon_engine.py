"""Reconciliation tiers, tested on hand-built ledgers where the expected answer is unambiguous.

Each test isolates one rung of the ladder in `src/cashpilot/recon/engine.py` so a regression names
the tier that broke instead of just "recall went down".
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cashpilot.ingest import Dataset
from cashpilot.models import BankLine, LedgerDoc, PaymentAdvice, Settlement
from cashpilot.recon.engine import Reconciler

AS_OF = date(2026, 9, 5)


def ar(number: str, amount: int, *, doc_date: date = AS_OF - timedelta(days=20), due: date | None = None, **kw) -> LedgerDoc:
    return LedgerDoc(
        doc_id=f"INV-{number}",
        kind="AR",
        number=number,
        counterparty=kw.pop("counterparty", "Acme Toolworks Pvt Ltd"),
        amount_paise=amount,
        net_amount_paise=amount,
        doc_date=doc_date,
        due_date=due or (doc_date + timedelta(days=14)),
        **kw,
    )


def ap(number: str, amount: int, *, doc_date: date = AS_OF - timedelta(days=10), **kw) -> LedgerDoc:
    return LedgerDoc(
        doc_id=f"BILL-{number}",
        kind="AP",
        number=number,
        counterparty=kw.pop("counterparty", "Bengaluru Spices Pvt Ltd"),
        amount_paise=amount,
        net_amount_paise=amount,
        doc_date=doc_date,
        due_date=kw.pop("due", doc_date + timedelta(days=21)),
        **kw,
    )


def line(line_id: str, amount: int, narration: str, *, txn: date = AS_OF - timedelta(days=1), utr: str | None = None) -> BankLine:
    return BankLine(line_id=line_id, txn_date=txn, narration=narration, amount_paise=amount, utr=utr)


def run(ds: Dataset, settings, strategy: str = "full"):
    return Reconciler(ds, settings, strategy=strategy).run()


def match_for(rec, line_id: str):
    for m in rec.matches:
        if m.line_id == line_id:
            return m
    return None


def test_tier3_document_number_in_the_narration_beats_everything_else(settings):
    inv = ar("INV-4711", 500_0000)
    ds = Dataset(lines=[line("L1", 500_0000, "NEFT CR ACME 450000 REF INV-4711 CLOSING")], invoices=[inv])
    rec = run(ds, settings)
    m = match_for(rec, "L1")
    assert m is not None and m.doc_ids == (inv.doc_id,)
    assert m.tier == "t3_doc_number"
    assert m.auto_post is True


def test_tier4_and_5_amount_matching_does_not_need_a_reference(settings):
    inv = ar("INV-5000", 300_0000, counterparty="Zenith Fabricators Pvt Ltd")
    ds = Dataset(lines=[line("L1", 300_0000, "NEFT CR ZENITH FABRICATORS PVT LT")], invoices=[inv])
    rec = run(ds, settings)
    m = match_for(rec, "L1")
    assert m is not None and m.doc_ids == (inv.doc_id,)
    assert m.tier in {"t4_amount_exact", "t5_amount_name"}


def test_amount_only_match_is_never_auto_posted(settings):
    """The right rupee figure against the wrong name may be *proposed*, never *posted*.

    This is the contract that keeps a 99.7%-precision engine safe: t4 can see one candidate with
    the exact amount and will surface it as evidence, but confidence sits below the auto-post floor
    so a human decides. A future change that lets a name mismatch through must fail here.
    """
    inv = ar("INV-5001", 300_0000, counterparty="Zenith Fabricators Pvt Ltd")
    ds = Dataset(lines=[line("L1", 300_0000, "NEFT CR SOMETHING ELSE TRADERS")], invoices=[inv])
    rec = run(ds, settings)
    m = match_for(rec, "L1")
    if m is not None:
        assert m.doc_ids == (inv.doc_id,) and m.auto_post is False
        assert m.confidence < settings.auto_post_min_confidence
    else:  # refusing outright is also acceptable; posting silently is not
        assert any(e.code in {"UNALLOCATED_CREDIT", "AMBIGUOUS_CANDIDATES", "TOO_MANY_CANDIDATES"} for e in rec.exceptions)


def test_tier6_lumpsum_single_credit_covers_several_invoices(settings):
    a, b, c = ar("INV-6001", 100_0000), ar("INV-6002", 150_0000), ar("INV-6003", 250_0000)
    ds = Dataset(
        lines=[line("L1", 500_0000, "NEFT CR ACME TOOLWORKS PVT LTD AGAINST MULTIPLE INVOICES SEP")],
        invoices=[a, b, c],
        as_of=AS_OF,
    )
    rec = run(ds, settings)
    m = match_for(rec, "L1")
    assert m is not None
    assert set(m.doc_ids) == {a.doc_id, b.doc_id, c.doc_id}
    assert m.tier == "t6_lumpsum"


def test_tier6_refuses_to_invent_a_split_that_does_not_add_up(settings):
    """A credit that matches no subset of the open book is refused, not half-posted.

    `extend_residual()` deliberately only runs after the document-reference and advice tiers, where
    the bank line *names* the invoice. On a bare lumpsum with no exact subset the engine must say
    "I don't know" - inventing a ₹2,00,000 + ₹50,000 + "the rest" split is how books get cooked.
    """
    a, b = ar("INV-6101", 200_0000), ar("INV-6102", 50_0000)
    other = ar("INV-6103", 900_0000)
    ds = Dataset(
        lines=[line("L1", 300_0000, "NEFT CR ACME TOOLWORKS PVT LTD PARTIAL SETTLEMENT OF DUES")],
        invoices=[a, b, other],
        as_of=AS_OF,
    )
    rec = run(ds, settings)
    assert match_for(rec, "L1") is None
    assert any(e.code == "UNALLOCATED_CREDIT" and e.ref_id == "L1" for e in rec.exceptions)
    # and none of the invoices were marked settled
    posted = {d for m in rec.matches for d in m.doc_ids}
    assert not posted & {a.doc_id, b.doc_id, other.doc_id}


def test_tier0_same_utr_twice_is_quarantined_not_double_posted(settings):
    inv = ar("INV-7000", 400_0000)
    ds = Dataset(
        lines=[
            line("L1", 400_0000, "NEFT CR ACME REF INV-7000", utr="UTR777"),
            line("L2", 400_0000, "NEFT CR ACME REF INV-7000", utr="UTR777"),
        ],
        invoices=[inv],
    )
    rec = run(ds, settings)
    assert len([m for m in rec.matches if m.line_id in {"L1", "L2"}]) == 1, "the second copy must not hit the invoice again"
    dup = [e for e in rec.exceptions if e.code == "DUPLICATE_BANK_LINE"]
    assert dup and dup[0].ref_id in {"L1", "L2"}


def test_tier0_same_amount_and_payee_without_a_utr_is_only_suspected(settings):
    """No UTR means no proof: the copy is flagged for a human, never silently dropped or posted."""
    inv = ar("INV-7001", 400_0000)
    ds = Dataset(
        lines=[
            line("L1", 400_0000, "NEFT CR ACME REF INV-7001"),
            line("L2", 400_0000, "NEFT CR ACME REF INV-7001"),
        ],
        invoices=[inv],
    )
    rec = run(ds, settings)
    assert len([m for m in rec.matches if m.line_id in {"L1", "L2"}]) == 1
    codes = {e.code for e in rec.exceptions}
    assert "SUSPECTED_DUPLICATE" in codes or "DUPLICATE_BANK_LINE" in codes


def test_settlement_credit_is_never_stolen_by_an_invoice_of_the_same_amount(settings):
    """The bug that caused this test: gateway payout credits are not customer receipts.

    Settlement pseudo-docs live outside DocIndex on purpose, so the amount tiers cannot see them.
    """
    inv = ar("INV-8000", 1000_0000)
    st = Settlement(
        settlement_id="SETL-1",
        settled_on=AS_OF - timedelta(days=1),
        payout_utr=None,
        batch_type="PAYOUT",
        txn_count=3,
        gross_paise=1040_0000,
        fee_paise=20_800,
        tmn_paise=104,
        gst_paise=3763,
        tds_paise=416,
        net_paise=1000_0000,
    )
    ds = Dataset(
        lines=[line("L1", 1000_0000, "IMPS CREDIT RAZORPAY PAYMENT GATEWAY SETTLEMENT SETL-1")],
        invoices=[inv],
        settlements=[st],
    )
    rec = run(ds, settings)
    m = match_for(rec, "L1")
    assert m is not None
    assert inv.doc_id not in m.doc_ids
    assert "SETL-1" in m.doc_ids[0] or m.tier in {"t1_settlement", "t2_advice_utr"}


def test_gateway_payment_and_advice_route(settings):
    from cashpilot.models import GatewayPayment

    inv = ar("INV-9000", 250_0000)
    pay = GatewayPayment(
        payment_id="pay_A",
        order_id="order_A",
        invoice_number="INV-9000",
        amount_paise=250_0000,
        captured_on=AS_OF - timedelta(days=3),
        fee_paise=5000,
        settlement_id="SETL-9",
        method="card",
    )
    st = Settlement(
        settlement_id="SETL-9",
        settled_on=AS_OF - timedelta(days=1),
        payout_utr="UTR12345",
        batch_type="PAYOUT",
        txn_count=1,
        gross_paise=250_0000,
        fee_paise=5000,
        tmn_paise=25,
        gst_paise=900,
        tds_paise=100,
        net_paise=244_0000,
    )
    ds = Dataset(
        lines=[line("L1", 244_0000, "IMPS CREDIT RAZORPAY PAYMENT GATEWAY SETTLEMENT SETL-9", utr="UTR12345")],
        invoices=[inv],
        payments=[pay],
        settlements=[st],
    )
    rec = run(ds, settings)
    assert match_for(rec, "L1") is not None
    # the payout credit is verified by the settlement verifier, not posted against a customer
    assert inv.doc_id not in (match_for(rec, "L1").doc_ids or ())


def test_payment_advice_with_utr_links_before_amount_guessing(settings):
    inv = ar("INV-9100", 600_0000)
    adv = PaymentAdvice(
        advice_id="ADV-1",
        payer_name="Acme Toolworks",
        amount_paise=598_5000,
        notified_on=AS_OF - timedelta(days=2),
        invoice_number="INV-9100",
        utr="UTR99999999",
    )
    ds = Dataset(
        lines=[line("L1", 598_5000, "NEFT DR ACME TOOLWORKS UTR99999999 CHQ RET", utr="UTR99999999")],
        invoices=[inv],
        advices=[adv],
    )
    rec = run(ds, settings)
    m = match_for(rec, "L1")
    assert m is not None and m.doc_ids == (inv.doc_id,)


def test_auto_post_flag_always_respects_the_confidence_gate(run_result, settings):
    """No match may be auto-posted below the configured floor - this is the whole safety story."""
    floor = settings.auto_post_min_confidence
    assert floor > 0.85
    bad = [m for m in run_result.recon.matches if m.auto_post and m.confidence < floor]
    assert not bad, f"{len(bad)} matches auto-posted below {floor}"


def test_engine_never_invents_a_document_and_never_double_consumes_it(run_result):
    seen: dict[str, str] = {}
    for m in run_result.recon.matches:
        for doc_id in m.doc_ids:
            assert doc_id not in seen, f"{doc_id} claimed by {seen.get(doc_id)} and {m.line_id}"
            seen[doc_id] = m.line_id


def test_every_unmatched_bank_line_produces_an_exception(run_result):
    matched = {m.line_id for m in run_result.recon.matches}
    excepted = {e.ref_id for e in run_result.recon.exceptions if e.ref_type == "bank_line"}
    unexplained = [ln.line_id for ln in run_result.dataset.lines if ln.line_id not in matched and ln.line_id not in excepted]
    assert not unexplained, f"{len(unexplained)} lines vanished without a match or an exception: {unexplained[:5]}"
