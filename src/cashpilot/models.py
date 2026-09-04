"""Dataclasses shared by every module. Deliberately dumb: no behaviour, no ORM, no pydantic.

Field names mirror the CSV headers in data/ so the loader is a thin, auditable mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class BankLine:
    line_id: str
    txn_date: date
    narration: str
    amount_paise: int  # signed: positive = money in, negative = money out
    utr: str | None = None
    value_date: date | None = None
    account: str = "PRIMARY"
    source_row: int = 0

    @property
    def is_credit(self) -> bool:
        return self.amount_paise > 0

    @property
    def is_debit(self) -> bool:
        return self.amount_paise < 0


@dataclass(slots=True)
class LedgerDoc:
    """A reconcilable document: an AR invoice, an AP bill, or a recurring cash item."""

    doc_id: str
    kind: str  # "AR" | "AP" | "RECUR"
    number: str  # human document number (invoice_no / bill_no)
    counterparty: str  # customer / vendor legal name
    amount_paise: int  # gross amount (positive)
    doc_date: date
    due_date: date
    gst_rate: float = 0.18
    status: str = "open"  # open | partial | paid | overdue | disputed
    currency: str = "INR"
    net_amount_paise: int = 0  # amount expected to hit the bank
    paid_amount_paise: int = 0
    extra: dict = field(default_factory=dict)
    source_row: int = 0

    @property
    def outstanding_paise(self) -> int:
        return max(0, self.net_amount_paise or self.amount_paise) - self.paid_amount_paise

    @property
    def direction(self) -> int:
        return 1 if self.kind == "AR" else -1


@dataclass(slots=True)
class PaymentAdvice:
    """Remittance advice / proof-of-payment sent by a customer. Optional input, big win."""

    advice_id: str
    payer_name: str
    amount_paise: int
    notified_on: date
    invoice_number: str | None = None
    utr: str | None = None
    source_row: int = 0


@dataclass(slots=True)
class Settlement:
    """A Razorpay settlement/payout as described by the gateway export."""

    settlement_id: str
    settled_on: date
    payout_utr: str | None
    batch_type: str
    txn_count: int
    gross_paise: int
    fee_paise: int
    tmn_paise: int
    gst_paise: int
    tds_paise: int
    net_paise: int
    source_row: int = 0


@dataclass(slots=True)
class GatewayPayment:
    payment_id: str
    order_id: str | None
    invoice_number: str | None
    amount_paise: int
    captured_on: date
    fee_paise: int
    settlement_id: str | None
    status: str = "captured"
    method: str = ""
    source_row: int = 0


@dataclass(slots=True)
class Refund:
    """Refunds/chargebacks are debited from a settlement batch - the usual cause of breaks."""

    refund_id: str
    payment_id: str | None
    amount_paise: int
    created_on: date
    settlement_id: str | None
    status: str = "processed"
    source_row: int = 0


@dataclass(slots=True)
class Match:
    line_id: str
    doc_ids: tuple[str, ...]
    tier: str
    score: float
    confidence: float
    amount_diff_paise: int = 0
    auto_post: bool = False
    evidence: str = ""

    @property
    def is_multi(self) -> bool:
        return len(self.doc_ids) > 1


@dataclass(slots=True)
class ReconException:
    """A record the engine could not resolve with confidence. These are *outputs*, not errors."""

    ref_id: str
    ref_type: str  # bank_line | invoice | bill | settlement
    code: str
    severity: str  # high | medium | low
    amount_paise: int
    detail: str
    candidates: tuple[str, ...] = ()
    suggested_action: str = ""
    engine: str = "deterministic"
    llm_category: str | None = None
    llm_action: str | None = None
    txn_date: str = ""
    narration: str = ""

    def as_row(self) -> dict[str, object]:
        return {
            "ref_id": self.ref_id,
            "ref_type": self.ref_type,
            "code": self.code,
            "severity": self.severity,
            "amount_paise": self.amount_paise,
            "detail": self.detail,
            "candidates": ";".join(self.candidates),
            "suggested_action": self.suggested_action,
            "engine": self.engine,
            "llm_category": self.llm_category or "",
            "llm_action": self.llm_action or "",
        }


@dataclass(slots=True)
class ReconResult:
    matches: list[Match]
    exceptions: list[ReconException]
    stats: dict[str, object]
    matched_line_ids: set[str] = field(default_factory=set)

    @property
    def auto_posted(self) -> list[Match]:
        return [m for m in self.matches if m.auto_post]


@dataclass(slots=True)
class DayFlow:
    """One day of forecast cash flow."""

    day: date
    expected_in_paise: int
    expected_out_paise: int
    band_lo_paise: int
    band_hi_paise: int
    closing_paise: int
    closing_lo_paise: int = 0
    closing_hi_paise: int = 0
    closing_p50_paise: int = 0
    note: str = ""
