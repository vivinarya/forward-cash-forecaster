"""CSV ingestion: the only place that knows file column names.

Design rules:
 * every input file is optional except the bank statement; a missing file degrades
   quality (fewer tiers can fire) and records a warning in the run manifest;
 * rows that fail parsing are never silently dropped: they become exceptions with
   code `PARSE_FAILURE` and keep their source line number, so a human can find them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .money import money_or_zero, parse_date
from .models import BankLine, GatewayPayment, LedgerDoc, PaymentAdvice, Refund, Settlement
from .norm import doc_key, extract_utr


@dataclass(slots=True)
class Dataset:
    lines: list[BankLine] = field(default_factory=list)
    invoices: list[LedgerDoc] = field(default_factory=list)
    bills: list[LedgerDoc] = field(default_factory=list)
    advices: list[PaymentAdvice] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    payments: list[GatewayPayment] = field(default_factory=list)
    refunds: list[object] = field(default_factory=list)
    opening_balance_paise: int = 0
    as_of: date | None = None
    parse_failures: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)
    data_dir: str = ""

    @property
    def docs(self) -> list[LedgerDoc]:
        return [*self.invoices, *self.bills]

    @property
    def ar(self) -> list[LedgerDoc]:
        return [d for d in self.invoices if d.outstanding_paise > 0]

    @property
    def ap(self) -> list[LedgerDoc]:
        return [d for d in self.bills if d.outstanding_paise > 0]


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def load_bank_lines(path: Path, ds: Dataset) -> None:
    for i, row in enumerate(_read_rows(path), start=2):
        txn = parse_date(row.get("txn_date") or row.get("date"))
        amt_in = money_or_zero(row.get("amount_in") or row.get("credit"))
        amt_out = money_or_zero(row.get("amount_out") or row.get("debit"))
        narration = row.get("narration") or row.get("description") or ""
        signed = amt_in - amt_out
        if signed == 0:
            # a zero-value row reconciles to nothing; keeping it alive just creates a fake matchable
            # line, so it is reported and dropped whether or not it carries an id
            ds.parse_failures.append({"file": path.name, "row": i, "reason": "zero amount", "raw": str(row)})
            continue
        if txn is None:
            ds.parse_failures.append({"file": path.name, "row": i, "reason": "bad date", "raw": str(row)})
            continue
        ds.lines.append(
            BankLine(
                line_id=row.get("line_id") or f"{path.stem}-{i}",
                txn_date=txn,
                narration=narration,
                amount_paise=signed,
                utr=(row.get("utr") or "").strip() or extract_utr(narration),
                value_date=parse_date(row.get("value_date")),
                account=row.get("account") or "PRIMARY",
                source_row=i,
            )
        )


def _load_docs(path: Path, ds: Dataset, kind: str) -> None:
    for i, row in enumerate(_read_rows(path), start=2):
        number = row.get("document_no") or row.get("invoice_no") or row.get("bill_no") or ""
        gross = money_or_zero(row.get("gross_amount") or row.get("amount"))
        gst_rate = float(row.get("gst_rate") or 0.18)
        net_raw = row.get("net_amount")
        net = money_or_zero(net_raw) if net_raw else gross
        doc_date = parse_date(row.get("document_date") or row.get("invoice_date") or row.get("bill_date"))
        due = parse_date(row.get("due_date")) or doc_date
        if doc_date is None or gross <= 0 or not number:
            ds.parse_failures.append({"file": path.name, "row": i, "reason": "missing number/date/amount", "raw": str(row)})
            continue
        paid = money_or_zero(row.get("paid_amount"))
        (ds.invoices if kind == "AR" else ds.bills).append(
            LedgerDoc(
                doc_id=row.get("document_id") or f"{kind}-{doc_key(number)}",
                kind=kind,
                number=number,
                counterparty=row.get("counterparty") or row.get("customer_name") or row.get("vendor_name") or "",
                amount_paise=gross,
                doc_date=doc_date,
                due_date=due,
                gst_rate=gst_rate,
                status=row.get("status") or "open",
                currency=row.get("currency") or "INR",
                net_amount_paise=net,
                paid_amount_paise=paid,
                extra={
                    "customer_code": row.get("customer_code") or row.get("counterparty_code") or "",
                    "vendor_code": row.get("vendor_code") or row.get("counterparty_code") or "",
                    "gst_amount_paise": money_or_zero(row.get("gst_amount")),
                    "notes": row.get("notes", ""),
                },
                source_row=i,
            )
        )


def load_dataset(data_dir: str | Path) -> Dataset:
    root = Path(data_dir)
    if not root.exists():
        raise FileNotFoundError(
            f"data directory not found: {root}\n"
            "Run `make sample` (fast, committed) or `python -m cashpilot generate --out data/synthetic`."
        )
    ds = Dataset()
    ds.data_dir = str(root)
    plan = [
        ("bank_statement.csv", "bank"),
        ("invoices.csv", "AR"),
        ("bills.csv", "AP"),
        ("payment_advices.csv", "advice"),
        ("razorpay_settlements.csv", "settlements"),
        ("razorpay_payments.csv", "payments"),
        ("razorpay_refunds.csv", "refunds"),
        ("opening_balance.csv", "opening"),
    ]
    for name, kind in plan:
        path = root / name
        if not path.exists():
            if kind != "bank":
                ds.warnings.append(f"optional input missing: {name} (matching tiers that need it are skipped)")
            else:
                raise FileNotFoundError(f"required input missing: {path}")
            continue
        ds.source_files[kind] = str(path.relative_to(root))
        if kind == "bank":
            load_bank_lines(path, ds)
        elif kind in ("AR", "AP"):
            _load_docs(path, ds, kind)
        elif kind == "advice":
            for i, row in enumerate(_read_rows(path), start=2):
                amt = money_or_zero(row.get("amount"))
                notified = parse_date(row.get("notified_on") or row.get("notified_date"))
                if amt <= 0 or notified is None:
                    ds.parse_failures.append({"file": name, "row": i, "reason": "bad advice", "raw": str(row)})
                    continue
                ds.advices.append(
                    PaymentAdvice(
                        advice_id=row.get("advice_id") or f"ADVICE-{i}",
                        payer_name=row.get("payer_name", ""),
                        amount_paise=amt,
                        notified_on=notified,
                        invoice_number=(row.get("invoice_no") or "").strip() or None,
                        utr=(row.get("utr") or "").strip() or extract_utr(row.get("narration_hint", "")),
                        source_row=i,
                    )
                )
        elif kind == "settlements":
            for i, row in enumerate(_read_rows(path), start=2):
                settled = parse_date(row.get("settled_on") or row.get("created_at"))
                if settled is None:
                    ds.parse_failures.append({"file": name, "row": i, "reason": "bad settlement date", "raw": str(row)})
                    continue
                ds.settlements.append(
                    Settlement(
                        settlement_id=row.get("settlement_id", f"setl_{i}"),
                        settled_on=settled,
                        payout_utr=(row.get("payout_utr") or "").strip() or None,
                        batch_type=row.get("batch_type", "regular"),
                        txn_count=int(float(row.get("txn_count") or 0)),
                        gross_paise=money_or_zero(row.get("gross_amount")),
                        fee_paise=money_or_zero(row.get("commission_amount")),
                        tmn_paise=money_or_zero(row.get("tmn_amount")),
                        gst_paise=money_or_zero(row.get("gst_amount")),
                        tds_paise=money_or_zero(row.get("tds_amount")),
                        net_paise=money_or_zero(row.get("net_amount")),
                        source_row=i,
                    )
                )
        elif kind == "payments":
            for i, row in enumerate(_read_rows(path), start=2):
                captured = parse_date(row.get("captured_at") or row.get("created_at"))
                if captured is None:
                    ds.parse_failures.append({"file": name, "row": i, "reason": "bad payment date", "raw": str(row)})
                    continue
                ds.payments.append(
                    GatewayPayment(
                        payment_id=row.get("payment_id", f"pay_{i}"),
                        order_id=row.get("order_id") or None,
                        invoice_number=(row.get("notes_invoice_no") or "").strip() or None,
                        amount_paise=money_or_zero(row.get("amount")),
                        captured_on=captured,
                        fee_paise=money_or_zero(row.get("fee")),
                        settlement_id=(row.get("settlement_id") or "").strip() or None,
                        status=row.get("status", "captured"),
                        method=(row.get("method") or "").strip(),
                        source_row=i,
                    )
                )
        elif kind == "refunds":
            for j, row in enumerate(_read_rows(path), start=2):
                when = parse_date(row.get("created_at") or row.get("refund_date"))
                if when is None:
                    ds.parse_failures.append({"file": name, "row": j, "reason": "bad refund date", "raw": str(row)})
                    continue
                ds.refunds.append(
                    Refund(
                        refund_id=row.get("refund_id", f"rfnd_{j}"),
                        payment_id=(row.get("payment_id") or "").strip() or None,
                        amount_paise=money_or_zero(row.get("amount")),
                        created_on=when,
                        settlement_id=(row.get("settlement_id") or "").strip() or None,
                        status=row.get("status", "processed"),
                        source_row=j,
                    )
                )
        elif kind == "opening":
            rows = _read_rows(path)
            if rows:
                row = rows[0]
                ds.opening_balance_paise = money_or_zero(row.get("balance"))
                ds.as_of = parse_date(row.get("as_of"))
    if ds.as_of is None and ds.lines:
        ds.as_of = max(l.txn_date for l in ds.lines)
    if ds.as_of is None:
        ds.as_of = date.today()
    return ds
