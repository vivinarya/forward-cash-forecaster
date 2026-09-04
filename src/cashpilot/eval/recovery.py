"""Money at stake: what a run actually found, in rupees, against what was there to find.

Two deliberately separate jobs live here.

**Runtime numbers** - per-batch recoverable rupees, credit recovery rate, share of gross at stake -
come only from the files a business would have (bank statement, gateway exports, ledger). They are
what `settlements.csv` carries and they work on a customer's own data.

**Detection numbers** - "of the batches we deliberately corrupted, this many were flagged, and ₹X of
the ₹Y planted was identified" - need a denominator, and a denominator only exists when the data was
generated. They are read from `meta.json`, which the engine never opens for any other purpose. On a
real corpus this section says "not measured" instead of printing a percentage, because a recovery
rate you cannot audit is a marketing number.

The rupee ledger is kept per defect and per batch by the generator (`planted.*_by_batch`) so the
numerator can be capped at the planted amount: identifying ₹40 on a batch where ₹30 was planted is
reported as ₹30, not ₹40. That cap is the difference between a catch rate and an inflated one.

See docs/ACCURACY.md for the tables this module produces.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any



@dataclass
class RecoveryReport:
    """What the run could put a rupee value on, and how much of that was there to find."""

    runtime: dict[str, object] = field(default_factory=dict)
    batch_defects: dict[str, object] = field(default_factory=dict)
    classes: list[dict[str, object]] = field(default_factory=list)
    receivables: dict[str, object] = field(default_factory=dict)
    batches: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "batch_defects": self.batch_defects,
            "classes": self.classes,
            "receivables": self.receivables,
            "batches": self.batches,
            "notes": self.notes,
        }


def _inr(paise: object) -> str:
    from ..money import fmt_inr

    return fmt_inr(int(paise or 0))


def _load_planted(data_dir: Path) -> dict[str, Any] | None:
    meta = data_dir / "meta.json"
    if not meta.exists():
        return None
    try:
        raw = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return None
    planted = raw.get("planted")
    return planted if isinstance(planted, dict) else None


# --------------------------------------------------------------------------- runtime section
def runtime_summary(verify_rows: list, verify_summary: dict[str, object], *, tol: int) -> dict[str, object]:
    """Everything measurable without ground truth. Rows are `BatchCheck` objects.

    The two rate keys are taken from the verifier's summary when present and derived here otherwise,
    so this block works on a hand-built row list too (the tests do exactly that)."""
    owed = sum(int(r.expected_net_paise) for r in verify_rows)
    arrived = sum(min(int(r.credited_paise or 0), int(r.expected_net_paise)) for r in verify_rows)
    at_stake_gross = sum(int(r.gross_paise) for r in verify_rows if r.recoverable_paise > tol)
    total_gross = sum(int(r.gross_paise) for r in verify_rows) or 1
    out = {
        "batches": len(verify_rows),
        "batches_with_rupee_stake": sum(1 for r in verify_rows if r.recoverable_paise > tol),
        "recoverable_paise": int(sum(r.recoverable_paise for r in verify_rows)),
        "fee_overbill_paise": int(sum(r.overbilled_paise for r in verify_rows)),
        "gst_tds_overbill_paise": int(sum(r.component_overbill_paise for r in verify_rows)),
        "unexplained_deduction_paise": int(sum(r.unexplained_deduction_paise for r in verify_rows)),
        "undercredited_paise": int(sum(r.undercredited_paise for r in verify_rows)),
        "gross_paise": int(sum(r.gross_paise for r in verify_rows)),
        "expected_net_paise": int(sum(r.expected_net_paise for r in verify_rows)),
        "credited_paise": int(sum(r.credited_paise or 0 for r in verify_rows)),
        "recovery_rate_pct": verify_summary.get(
            "recovery_rate_pct", round(100.0 * arrived / max(1, owed), 3)
        ),
        "gross_at_stake_pct": verify_summary.get(
            "gross_at_stake_pct", round(100.0 * at_stake_gross / total_gross, 1)
        ),
        "claim_value": _inr(sum(r.recoverable_paise for r in verify_rows)),
    }
    return out


# ------------------------------------------------------------------- receivables section
def receivables_summary(data_dir: Path, recon, *, tol: int, as_of: str | None = None) -> dict[str, object]:
    """How much of the money customers short-paid ended up on a list someone can chase.

    Denominator: `truth_schedule.csv` (generated only), restricted to documents the bank had actually
    received payment for by the corpus as-of date. A short payment scheduled for next week is not
    something this run could have surfaced, so it is not counted as a miss.

    Numerator: engine output alone. Two strengths of "surfaced" are reported, because they cost a
    business different amounts:

    * **attributed** - the deduction is tied to the document: a committed match carrying that amount
      gap, or a SHORT_DEDUCTION exception naming the document. Someone can raise the credit note.
    * **in the queue** - the exact money sits on an unresolved bank line (an UNMATCHED_DEBIT whose
      amount equals the document's expected short receipt). A human will see it, but it is not tied
      to the invoice yet, so it cannot be chased as a claim.
    * **silently missed** - neither. That is the only number worth calling a failure.
    """
    out: dict[str, object] = {
        "planted_docs": 0,
        "planted_paise": 0,
        "surfaced_docs": 0,
        "surfaced_paise": 0,
        "queued_docs": 0,
        "queued_paise": 0,
        "missed_docs": 0,
        "detection_rate_pct": None,
        "queue_rate_pct": None,
        "rupee_catch_rate_pct": None,
        "note": "no truth_schedule.csv: short-payment coverage is not measured on this corpus",
    }
    sched = Path(data_dir) / "truth_schedule.csv"
    if not sched.exists():
        return out

    planted: dict[str, int] = {}
    expect_paise: dict[str, int] = {}  # doc -> the cash figure a bank line carries when paid short
    scheduled = 0
    with sched.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        # a schedule without the payment columns is an older export: no window filtering is possible
        tracks = "paid" in (reader.fieldnames or []) and "scheduled_pay" in (reader.fieldnames or [])
        out["window_filter"] = tracks
        for row in reader:
            ded = int(row.get("short_deduction_paise") or 0)
            if ded <= 0:
                continue
            scheduled += 1
            if not tracks:
                pass
            elif str(row.get("paid") or "0") != "1":
                continue
            elif as_of and str(row.get("scheduled_pay") or "") > as_of:
                continue  # money has not moved yet; nothing in the feed to read it from
            planted[row["doc_id"]] = ded
            expect_paise[row["doc_id"]] = abs(int(row.get("net_paise") or 0)) - ded
    out["planted_in_plan"] = scheduled
    if not planted:
        out["note"] = "no short payment reached the bank inside the corpus window (or none was planted)"
        return out

    surfaced: dict[str, int] = {}
    for m in getattr(recon, "matches", []):
        gap = abs(int(m.amount_diff_paise or 0))
        if gap > tol:
            for d in m.doc_ids:
                surfaced[d] = max(surfaced.get(d, 0), gap)
    for e in getattr(recon, "exceptions", []):
        if e.code == "SHORT_DEDUCTION":
            for d in e.candidates:
                surfaced[d] = max(surfaced.get(d, 0), abs(int(e.amount_paise or 0)))

    unresolved_amounts = [
        abs(int(e.amount_paise or 0))
        for e in getattr(recon, "exceptions", [])
        if e.ref_type == "bank_line" and e.code != "SHORT_DEDUCTION"
    ]

    found = 0
    found_paise = 0
    queued = 0
    queued_paise = 0
    for did, ded in planted.items():
        got = surfaced.get(did, 0)
        if got:
            found += 1
            found_paise += min(got, ded)  # never claim more than was withheld
            continue
        want = expect_paise.get(did, 0)
        # the money counts as seen if it is sitting in the queue, even unattributed
        if want and any(abs(a - want) <= tol for a in unresolved_amounts):
            queued += 1
            queued_paise += ded
    planted_paise = sum(planted.values())
    out.update(
        {
            "planted_docs": len(planted),
            "planted_paise": planted_paise,
            "surfaced_docs": found,
            "surfaced_paise": found_paise,
            "queued_docs": queued,
            "queued_paise": queued_paise,
            "missed_docs": len(planted) - found - queued,
            "detection_rate_pct": round(100.0 * found / len(planted), 2),
            "queue_rate_pct": round(100.0 * (found + queued) / len(planted), 2),
            "rupee_catch_rate_pct": round(100.0 * found_paise / planted_paise, 1) if planted_paise else None,
            "chased_value": _inr(found_paise),
            "queued_value": _inr(queued_paise),
            "planted_value": _inr(planted_paise),
        }
    )
    out.pop("note", None)
    return out

def recovery_report(
    data_dir: str | Path,
    recon,
    verify_rows: list,
    verify_summary: dict[str, object],
    *,
    cash=None,
    tolerance_paise: int | None = None,
    as_of: str | None = None,
) -> RecoveryReport:
    """Build the recovery view. `recon` is a `ReconResult`, `verify_rows` the `BatchCheck` list."""
    data = Path(data_dir)
    # same tolerance the checker used, so "is this batch's gap noise" means one thing repo-wide
    tol = max(int(tolerance_paise if tolerance_paise is not None else verify_summary.get("tolerance_paise_per_batch", 200)), 1)
    rep = RecoveryReport()
    by_sid = {r.settlement_id: r for r in verify_rows}
    rep.runtime = runtime_summary(verify_rows, verify_summary, tol=tol)
    if as_of is None:
        try:
            as_of = str(json.loads((data / "meta.json").read_text()).get("as_of") or "") or None
        except (OSError, json.JSONDecodeError):
            as_of = None
    rep.receivables = receivables_summary(data, recon, tol=tol, as_of=as_of)
    rep.batches = [
        {
            "settlement_id": r.settlement_id,
            "settled_on": r.settled_on,
            "gross_paise": r.gross_paise,
            "credited_paise": r.credited_paise,
            "expected_net_paise": r.expected_net_paise,
            "recoverable_paise": r.recoverable_paise,
            "fee_overbill_paise": r.overbilled_paise,
            "gst_tds_overbill_paise": r.component_overbill_paise,
            "unexplained_deduction_paise": r.unexplained_deduction_paise,
            "undercredited_paise": r.undercredited_paise,
            "recovery_rate_pct": r.recovery_rate_pct,
            "planted_paise": 0,
            "flags": ";".join(r.flags),
        }
        for r in sorted(verify_rows, key=lambda x: -x.recoverable_paise)[:50]
        if r.recoverable_paise > tol or r.flags
    ]

    planted = _load_planted(data) or {}
    out_of_scope = 0
    per_batch: dict[str, dict[str, int]] = {}
    for key, comp in (
        ("drift_by_batch", "unexplained"),
        ("dropped_refund_by_batch", "unexplained"),
        ("mis_tier_by_batch", "fee"),
    ):
        for sid, paise in (planted.get(key) or {}).items():
            if sid not in by_sid:
                out_of_scope += 1  # settled after the corpus date, or absent from the export
                continue
            row = per_batch.setdefault(sid, {"planted_paise": 0, "fee_paise": 0, "unexplained_paise": 0})
            row["planted_paise"] += int(paise)
            row[f"{comp}_paise"] += int(paise)

    if per_batch:
        flagged = 0
        identified = 0
        planted_paise = 0
        for sid, row in per_batch.items():
            check = by_sid.get(sid)
            planted_paise += row["planted_paise"]
            if check is None or not check.flags:
                continue
            flagged += 1
            # one number per batch, capped by what was planted on it: a fee-tiering defect may surface
            # as "under-credited" rather than "fee overbilled", and both are the same rupees
            identified += min(row["planted_paise"], int(check.recoverable_paise))
        rep.batch_defects = {
            "measured": True,
            "planted_batches": len(per_batch),
            "flagged_batches": flagged,
            "planted_paise": planted_paise,
            "identified_paise": identified,
            "planted_value": _inr(planted_paise),
            "identified_value": _inr(identified),
            "detection_rate_pct": round(100.0 * flagged / len(per_batch), 2),
            "rupee_catch_rate_pct": round(100.0 * identified / planted_paise, 1) if planted_paise else None,
            "batches_flagged_with_no_planted_defect": sum(
                1 for r in verify_rows if r.flags and r.settlement_id not in per_batch
            ),
            "planted_batches_out_of_scope": out_of_scope,
        }
    else:
        rep.batch_defects = {
            "measured": False,
            "reason": "no meta.json planted ledger on this corpus",
        }
        rep.notes.append(
            "no meta.json planted ledger: the rupee columns are what this run found; batch-defect "
            "detection has no denominator on a real corpus, so it is not measured and not claimed"
        )

    for row in rep.batches:  # what was planted on this batch, so a claim can be read against it
        row["planted_paise"] = int((per_batch.get(row["settlement_id"]) or {}).get("planted_paise", 0))

    def _row(short: str, label: str, code: str, key: str, ids_key: str) -> dict[str, object]:
        ids = list((planted.get(key) or {}).keys()) or [str(x) for x in (planted.get(ids_key) or [])]
        n = len(ids)
        hit = sum(1 for sid in ids if sid in by_sid and by_sid[sid].flags)
        rupees = sum(int((planted.get(key) or {}).get(sid, 0)) for sid in ids) if planted.get(key) else 0
        got = 0
        for sid in ids:
            check = by_sid.get(sid)
            if check is None or not check.flags:
                continue
            got += min(int((planted.get(key) or {}).get(sid, 0)), int(check.recoverable_paise))
        return {
            "class": label,
            "label_short": short,
            "exception": code,
            "planted": n,
            "flagged": hit,
            "detection_rate_pct": round(100.0 * hit / n, 2) if n else None,
            "planted_paise": rupees,
            "identified_paise": got,
            "planted_value": _inr(rupees),
            "identified_value": _inr(got),
            "rupee_catch_rate_pct": round(100.0 * got / rupees, 1) if rupees else None,
        }

    if per_batch:
        rep.classes += [
            _row(
                "fee_wrong_slab",
                "Fee billed on the wrong slab (commission ≠ the rate card for the batch's own mix)",
                "FEE_TIER_MISMATCH",
                "mis_tier_by_batch",
                "mis_tiered_batches",
            ),
            _row(
                "unexplained_shortfall",
                "Net credited short with no deduction on file to explain it",
                "BATCH_ARITHMETIC",
                "drift_by_batch",
                "drifted_batches",
            ),
            _row(
                "missing_refund_evidence",
                "Refund evidence missing from the export (the batch debits a refund we cannot see)",
                "BATCH_ARITHMETIC",
                "dropped_refund_by_batch",
                "dropped_refund_batches",
            ),
        ]
    if rep.receivables.get("planted_docs"):
        ar = rep.receivables
        rep.classes.append(
            {
                "class": "Customer paid short (TDS / damage claim / part payment against an invoice)",
                "label_short": "customer_paid_short",
                "exception": "SHORT_DEDUCTION",
                "planted": ar["planted_docs"],
                "flagged": ar["surfaced_docs"],
                "queued": ar["queued_docs"],
                "detection_rate_pct": ar["detection_rate_pct"],
                "planted_paise": ar["planted_paise"],
                "identified_paise": ar["surfaced_paise"],
                "planted_value": ar["planted_value"],
                "identified_value": ar["chased_value"],
                "rupee_catch_rate_pct": ar["rupee_catch_rate_pct"],
            }
        )

    if cash is not None:
        at_risk = int(cash.stats.get("open_ar_paise", 0))
        discounted = int(cash.stats.get("recovery_discounted_paise", 0))
        rep.runtime["ar_open_paise"] = at_risk
        rep.runtime["ar_expected_haircut_paise"] = discounted
        rep.runtime["ar_expected_recovery_pct"] = (
            round(100.0 * (at_risk - discounted) / at_risk, 2) if at_risk else None
        )

    rep.notes += [
        "recoverable_paise per batch = fee overbilling + GST/TDS overbilling + unexplained deduction + shortfall in the credit",
        "recovery_rate_pct = money that arrived ÷ money the batches owed, rupee-weighted, not batch-count weighted",
        "identified_paise is capped at the planted amount per batch, so a catch rate cannot exceed 100%",
        "class rows share a batch when two defects hit the same one; the de-duplicated total is batch_defects",
        "detection and catch rates read the generator's meta.json; the runtime block needs no ground truth",
        "batches_flagged_with_no_planted_defect is a false-positive count: flagged here, nothing planted here",
    ]
    return rep
