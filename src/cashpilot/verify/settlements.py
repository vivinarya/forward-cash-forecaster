"""Settlement verification: recompute the gateway's arithmetic and diff it against the bank feed.

This is the part of the "AI finance agent" that should contain no AI. A settlement either
adds up or it does not: gross - MDR - TMN - GST on fees - TDS - refunds = net credited, and the
bank must show a credit of exactly that net. Every check below is integer arithmetic on paise
against the rate card in config/fee_schedule.json, so a flag is always actionable: the diff is
shown component by component, and `recoverable_paise` totals what the business can dispute.

Checks
  FEE_TIER_MISMATCH      commission billed != sum of per-transaction rates from the rate card
  FEE_COMPONENT_MISMATCH GST on fees / TDS on commission does not follow the schedule
  BATCH_ARITHMETIC       gross - deductions != net as declared by the gateway
  NOT_CREDITED           settlement declared settled, no bank credit found
  CREDIT_AMOUNT_MISMATCH bank credit != declared net (refunds usually; drift sometimes)
  UNSETTLED_PAYMENT      captured payment older than the T+n window with no settlement
  SETTLEMENT_GAP         no settlement for > threshold business days (hold/rollback signal)
  ORPHAN_REFUND          refund debited with no settlement or payment reference
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..config import REPO_ROOT
from ..ingest import Dataset
from ..models import ReconException, Settlement
from ..money import fmt_inr

P = Decimal(100)


def _rupees_to_paise(value: Decimal | int | str) -> int:
    return int((Decimal(str(value)) * P).quantize(Decimal("1"), ROUND_HALF_UP))


@dataclass(slots=True)
class FeeSchedule:
    modes: dict[str, float]
    default_mode: str
    gst_rate: float
    tds_rate: float
    tmn_rate: float
    tolerance_paise: int
    expect_days: int
    gap_alert_days: int

    @classmethod
    def load(cls, path=None) -> "FeeSchedule":
        raw = json.loads((path or (REPO_ROOT / "config" / "fee_schedule.json")).read_text())
        return cls(
            modes={k: float(v["rate"]) for k, v in raw.get("modes", {}).items()},
            default_mode=raw.get("default_mode", "card"),
            gst_rate=float(raw.get("gst_rate_on_fees", 0.18)),
            tds_rate=float(raw.get("tds_rate_on_commission", 0.02)),
            tmn_rate=float(raw.get("tmn_rate", 0.0)),
            tolerance_paise=int(raw.get("tolerance_paise_per_batch", 200)),
            expect_days=int(raw.get("settlement_expectation_days", 2)),
            gap_alert_days=int(raw.get("settlement_gap_alert_days", 4)),
        )

    def rate_for(self, method: str | None) -> float:
        return self.modes.get((method or "").lower(), self.modes[self.default_mode])

    def expected(self, gross_paise: int, fee_components: list[tuple[int, str]] | None, refunds_paise: int = 0) -> dict[str, int]:
        """Recompute deductions for a batch. `fee_components` = [(amount, method), ...]."""
        if fee_components:
            fee = sum(int(a * self.rate_for(m)) for a, m in fee_components)
        else:
            fee = int(gross_paise * self.rate_for(self.default_mode))
        tmn = int(gross_paise * self.tmn_rate)
        gst = int((fee + tmn) * self.gst_rate)
        tds = int(fee * self.tds_rate)
        net = gross_paise - fee - tmn - gst - tds - refunds_paise
        return {"fee": fee, "tmn": tmn, "gst": gst, "tds": tds, "refunds": refunds_paise, "net": net}


@dataclass
class BatchCheck:
    settlement_id: str
    settled_on: str
    bank_line_id: str | None
    gross_paise: int
    declared_fee_paise: int
    expected_fee_paise: int
    declared_net_paise: int
    expected_net_paise: int
    credited_paise: int | None
    refund_paise: int
    txn_count: int
    declared_tmn_paise: int = 0
    declared_gst_paise: int = 0
    declared_tds_paise: int = 0
    expected_gst_paise: int = 0
    expected_tds_paise: int = 0
    flags: list[str] = field(default_factory=list)

    @property
    def overbilled_paise(self) -> int:
        return max(0, self.declared_fee_paise - self.expected_fee_paise)

    @property
    def unexplained_deduction_paise(self) -> int:
        """Rupees taken out of this batch that no deduction on file explains.

        gross − commission − TMN − GST − TDS − refunds = net. Any residue is money the gateway kept
        without evidence: a dropped refund row, a silent breakage adjustment, a manual correction.
        It is the single most actionable number in this report because it is directly claimable.
        """
        residue = (
            self.gross_paise
            - self.declared_fee_paise
            - self.declared_tmn_paise
            - self.declared_gst_paise
            - self.declared_tds_paise
            - self.refund_paise
            - self.declared_net_paise
        )
        return residue

    @property
    def component_overbill_paise(self) -> int:
        """GST/TDS billed above what the batch's own settlement classes imply (TDS at source is 0/1/2 %)."""
        return max(0, (self.declared_gst_paise + self.declared_tds_paise) - (self.expected_gst_paise + self.expected_tds_paise))

    @property
    def undercredited_paise(self) -> int:
        if self.credited_paise is None:
            return 0
        return max(0, self.expected_net_paise - self.credited_paise)

    @property
    def rate_card_claim_paise(self) -> int:
        """Rupees the gateway billed above the rate card it applies to this batch's own mix."""
        return self.overbilled_paise + self.component_overbill_paise

    @property
    def recoverable_paise(self) -> int:
        """What this batch is worth chasing for, in paise.

        Three orthogonal buckets, added once each:

        * `unexplained_deduction` - the batch's own declared numbers do not add up, so money left the
          batch with no deduction on file behind it. Claimable as is.
        * `rate_card_claim` vs `undercredited` - combined with **max(), not +**. A fee that is too high
          makes the declared net too low, and if the credit followed that declaration then the cash
          shortfall *is* the overbilling; adding both would bill the same rupees to the client twice.
          They only separate when the credit did not follow the declaration, and max() keeps whichever
          claim is larger. Under-counting is not possible: at least one of the two equals the gap.
        """
        return abs(self.unexplained_deduction_paise) + max(self.rate_card_claim_paise, self.undercredited_paise)

    @property
    def recovery_rate_pct(self) -> float:
        """Share of the money this batch owed us that actually reached the account."""
        if self.expected_net_paise <= 0:
            return 100.0
        got = self.credited_paise if self.credited_paise is not None else 0
        return round(100.0 * min(got, self.expected_net_paise) / self.expected_net_paise, 2)

    @property
    def credit_gap_paise(self) -> int:
        if self.credited_paise is None:
            return 0
        return self.credited_paise - self.declared_net_paise

    def as_row(self) -> dict[str, object]:
        return {
            "settlement_id": self.settlement_id,
            "settled_on": self.settled_on,
            "bank_line_id": self.bank_line_id or "",
            "txn_count": self.txn_count,
            "gross_paise": self.gross_paise,
            "declared_fee_paise": self.declared_fee_paise,
            "expected_fee_paise": self.expected_fee_paise,
            "fee_diff_paise": self.declared_fee_paise - self.expected_fee_paise,
            "refund_paise": self.refund_paise,
            "declared_net_paise": self.declared_net_paise,
            "expected_net_paise": self.expected_net_paise,
            "credited_paise": self.credited_paise if self.credited_paise is not None else "",
            "credit_gap_paise": self.credit_gap_paise,
            "unexplained_deduction_paise": self.unexplained_deduction_paise,
            "overbilled_paise": self.overbilled_paise,
            "component_overbill_paise": self.component_overbill_paise,
            "undercredited_paise": self.undercredited_paise,
            "recoverable_paise": self.recoverable_paise,
            "recovery_rate_pct": self.recovery_rate_pct,
            "flags": ";".join(self.flags),
        }


def verify_settlements(
    ds: Dataset,
    schedule: FeeSchedule | None = None,
    settlement_to_line: dict[str, str] | None = None,
) -> tuple[list[BatchCheck], list[ReconException], dict[str, object]]:
    """Verify every declared settlement and every captured payment. Returns rows, exceptions, summary."""
    sched = schedule or FeeSchedule.load()
    link = settlement_to_line or {}
    lines_by_id = {ln.line_id: ln for ln in ds.lines}
    payments_by_settlement: dict[str, list] = {}
    for p in ds.payments:
        if p.settlement_id:
            payments_by_settlement.setdefault(p.settlement_id, []).append(p)
    refunds_by_settlement: dict[str, int] = {}
    known_refund_ids = set()
    for r in ds.refunds:
        if r.settlement_id:
            refunds_by_settlement[r.settlement_id] = refunds_by_settlement.get(r.settlement_id, 0) + r.amount_paise
        known_refund_ids.add(r.refund_id)

    by_id: dict[str, Settlement] = {s.settlement_id: s for s in ds.settlements}
    rows: list[BatchCheck] = []
    exceptions: list[ReconException] = []
    as_of = ds.as_of

    for s in sorted(ds.settlements, key=lambda x: x.settled_on):
        comps = [(p.amount_paise, p.method) for p in payments_by_settlement.get(s.settlement_id, [])]
        refund_amt = refunds_by_settlement.get(s.settlement_id, 0)
        exp = sched.expected(s.gross_paise, comps or None, refund_amt)
        line_id = link.get(s.settlement_id)
        credited = abs(lines_by_id[line_id].amount_paise) if line_id in lines_by_id else None
        flags: list[str] = []

        if abs(s.fee_paise - exp["fee"]) > max(sched.tolerance_paise, int(exp["fee"] * 0.005)):
            flags.append("FEE_TIER_MISMATCH")
        if abs(s.gst_paise - exp["gst"]) > sched.tolerance_paise or abs(s.tds_paise - exp["tds"]) > sched.tolerance_paise:
            flags.append("FEE_COMPONENT_MISMATCH")
        arith = s.gross_paise - s.fee_paise - s.tmn_paise - s.gst_paise - s.tds_paise - refund_amt
        if abs(arith - s.net_paise) > sched.tolerance_paise:
            flags.append("BATCH_ARITHMETIC")
        if credited is None and s.settled_on <= as_of:
            flags.append("NOT_CREDITED")
        elif credited is not None and abs(credited - s.net_paise) > sched.tolerance_paise:
            flags.append("CREDIT_AMOUNT_MISMATCH")

        row = BatchCheck(
            settlement_id=s.settlement_id,
            settled_on=s.settled_on.isoformat(),
            bank_line_id=line_id,
            gross_paise=s.gross_paise,
            declared_fee_paise=s.fee_paise,
            expected_fee_paise=exp["fee"],
            declared_net_paise=s.net_paise,
            expected_net_paise=exp["net"],
            credited_paise=credited,
            refund_paise=refund_amt,
            txn_count=s.txn_count or len(comps),
            declared_tmn_paise=s.tmn_paise,
            declared_gst_paise=s.gst_paise,
            declared_tds_paise=s.tds_paise,
            expected_gst_paise=exp["gst"],
            expected_tds_paise=exp["tds"],
            flags=flags,
        )
        rows.append(row)

        if "FEE_TIER_MISMATCH" in flags:
            exceptions.append(
                ReconException(
                    s.settlement_id,
                    "settlement",
                    "FEE_TIER_MISMATCH",
                    "high",
                    row.overbilled_paise,
                    (
                        f"Batch {s.settlement_id} billed {fmt_inr(s.fee_paise)} commission but the rate card gives "
                        f"{fmt_inr(exp['fee'])} for its {len(comps)} transactions "
                        f"(diff {fmt_inr(s.fee_paise - exp['fee'])})."
                    ),
                    (s.settlement_id,),
                    "dispute_with_gateway_and_attach_batch_breakdown",
                )
            )
        if "CREDIT_AMOUNT_MISMATCH" in flags:
            gap = row.credit_gap_paise
            exceptions.append(
                ReconException(
                    s.settlement_id,
                    "settlement",
                    "CREDIT_AMOUNT_MISMATCH",
                    "high" if abs(gap) > 10_000_000 else "medium",
                    gap,
                    (
                        f"Bank credited {fmt_inr(credited or 0)} against a declared net of {fmt_inr(s.net_paise)} "
                        f"(gap {fmt_inr(gap)}). Refund evidence on file covers {fmt_inr(refund_amt)}."
                    ),
                    (line_id or "-",),
                    "request_settlement_breakdown_for_unexplained_gap",
                )
            )
        if "NOT_CREDITED" in flags:
            exceptions.append(
                ReconException(
                    s.settlement_id,
                    "settlement",
                    "SETTLEMENT_NOT_CREDITED",
                    "high",
                    s.net_paise,
                    f"Gateway marked {s.settlement_id} settled on {row.settled_on} but no bank credit line matches its UTR or amount.",
                    (),
                    "escalate_to_gateway_settlement_team",
                )
            )
        if "BATCH_ARITHMETIC" in flags:
            exceptions.append(
                ReconException(
                    s.settlement_id,
                    "settlement",
                    "BATCH_ARITHMETIC",
                    "medium",
                    arith - s.net_paise,
                    f"Declared gross/fees/refunds do not add up to the declared net for {s.settlement_id} (off by {fmt_inr(arith - s.net_paise)}).",
                    (),
                    "check_for_missing_refund_or_chargeback_rows",
                )
            )

    # captured payments that never reached a settlement
    expect_lag = timedelta(days=sched.expect_days + 2)
    unsettled = [p for p in ds.payments if not p.settlement_id and p.captured_on + expect_lag < as_of]
    if unsettled:
        exceptions.append(
            ReconException(
                f"UNSETTLED_COUNT_{len(unsettled)}",
                "settlement",
                "UNSETTLED_PAYMENT",
                "medium",
                sum(p.amount_paise for p in unsettled),
                f"{len(unsettled)} captured payments are older than T+{sched.expect_days + 2} with no settlement id.",
                tuple(p.payment_id for p in unsettled[:5]),
                "raise_settlement_ticket",
            )
        )

    # payout cadence gaps
    prev = None
    for s in sorted(ds.settlements, key=lambda x: x.settled_on):
        if prev is not None and (s.settled_on - prev).days > sched.gap_alert_days + 1:
            exceptions.append(
                ReconException(
                    f"GAP-{prev.isoformat()}-{s.settled_on.isoformat()}",
                    "settlement",
                    "SETTLEMENT_GAP",
                    "medium",
                    0,
                    f"{(s.settled_on - prev).days} days between payouts on {prev} and {s.settled_on}; a hold/rollback window.",
                    (),
                    "check_gateway_account_status_and_rollbacks",
                )
            )
        prev = s.settled_on

    orphans = [r for r in ds.refunds if not r.settlement_id and r.created_on + timedelta(days=4) < as_of]
    for r in orphans:
        exceptions.append(
            ReconException(
                r.refund_id,
                "settlement",
                "ORPHAN_REFUND",
                "low",
                r.amount_paise,
                f"Refund {r.refund_id} has no settlement reference and is older than 4 days.",
                (r.payment_id or "-",),
                "tie_refund_to_a_batch",
            )
        )

    summary = {
        "batches": len(rows),
        "batches_flagged": sum(1 for r in rows if r.flags),
        "flag_rate": round(sum(1 for r in rows if r.flags) / max(1, len(rows)), 4),
        "gross_paise": sum(r.gross_paise for r in rows),
        "declared_fee_paise": sum(r.declared_fee_paise for r in rows),
        "expected_fee_paise": sum(r.expected_fee_paise for r in rows),
        "recoverable_paise": sum(r.recoverable_paise for r in rows),
        "fee_overbill_paise": sum(r.overbilled_paise for r in rows),
        "component_overbill_paise": sum(r.component_overbill_paise for r in rows),
        "unexplained_deduction_paise": sum(r.unexplained_deduction_paise for r in rows),
        "undercredited_paise": sum(r.undercredited_paise for r in rows),
        "expected_net_paise": sum(r.expected_net_paise for r in rows),
        "credited_paise": sum(r.credited_paise or 0 for r in rows),
        "recovery_rate_pct": round(
            100.0 * sum(min(r.credited_paise or 0, r.expected_net_paise) for r in rows) / max(1, sum(r.expected_net_paise for r in rows)),
            3,
        ),
        "batches_with_rupee_stake": sum(1 for r in rows if r.recoverable_paise > sched.tolerance_paise),
        "batch_stake_rate": round(sum(1 for r in rows if r.recoverable_paise > sched.tolerance_paise) / max(1, len(rows)), 4),
        "gross_at_stake_pct": round(
            100.0 * sum(r.gross_paise for r in rows if r.recoverable_paise > sched.tolerance_paise) / max(1, sum(r.gross_paise for r in rows)),
            2,
        ),
        "credit_gap_paise": sum(r.credit_gap_paise for r in rows),
        "refunds_matched_paise": sum(r.refund_paise for r in rows),
        "effective_mdr_pct": round(sum(r.declared_fee_paise for r in rows) / max(1, sum(r.gross_paise for r in rows)) * 100, 3),
        "unsettled_payments": len(unsettled),
    }
    return rows, exceptions, summary
