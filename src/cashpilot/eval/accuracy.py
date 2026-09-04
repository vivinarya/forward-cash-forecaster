"""Accuracy harness: score a reconciliation run against planted ground truth.

Scoring rules, stated up front because "84% matched" means nothing without them:

 * A bank line is **matchable** if the generator says some document (or gateway settlement)
   explains it. Lines with no document - bank charges, interest, a reversal - are
   **expected_unmatched**: leaving them unmatched is *correct behaviour*, not a miss.
 * A match is **correct** only if the set of documents equals the truth set exactly.
   Matching one of three invoices in a lumpsum receipt is *partial*, not correct: in the ledger
   it posts ₹9 of a ₹31 receipt and silently leaves a fake ₹22 receivable.
 * **precision** is over everything the engine claimed; **recall** is over what was claimable.
 * **auto_post_precision** is the operational number: of the entries the engine wrote to the
   books with no human, how many were right. This must be ~1.0 or the feature is a liability,
   which is why review-queue routing exists as a separate state from "wrong".
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Match

MATCHABLE = {"matchable", "matchable_amount_mismatch", "matchable_lumpsum", "gateway_settlement"}
QUARANTINE = {
    "expected_unmatched_charge",
    "expected_unmatched_interest",
    "expected_unmatched_unknown",
    "expected_unmatched_duplicate",
}


def load_truth(path: str | Path) -> dict[str, dict[str, object]]:
    truth: dict[str, dict[str, object]] = {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"ground truth file not found: {p}")
    with p.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            docs = tuple(x for x in (row.get("doc_ids") or "").split(";") if x)
            truth[row["line_id"]] = {
                "docs": frozenset(docs),
                "kind": (row.get("truth_kind") or "").strip(),
                "amount_paise": int(row.get("amount_paise") or 0),
                "narration": row.get("narration") or "",
                "txn_date": row.get("txn_date") or "",
            }
    return truth


@dataclass
class ScoreCard:
    strategy: str = "full"
    lines_total: int = 0
    lines_matchable: int = 0
    lines_quarantine: int = 0
    lines_matched: int = 0
    correct: int = 0
    partial: int = 0
    wrong: int = 0
    unmatched_but_matchable: int = 0
    correctly_quarantined: int = 0
    wrongly_claimed_quarantine: int = 0
    auto_post_count: int = 0
    auto_post_correct: int = 0
    amount_error_paise: list[int] = field(default_factory=list)
    by_tier: dict[str, dict[str, int]] = field(default_factory=dict)
    by_kind: dict[str, dict[str, object]] = field(default_factory=dict)
    rupees_correct_paise: int = 0
    rupees_matchable_paise: int = 0
    false_doc_assignments: int = 0
    timing_ms: dict[str, float] = field(default_factory=dict)
    throughput_lines_per_s: float = 0.0
    unresolved: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ derived
    @property
    def match_rate(self) -> float:
        return round(self.lines_matched / self.lines_total, 4) if self.lines_total else 0.0

    @property
    def precision(self) -> float:
        return round(self.correct / self.lines_matched, 4) if self.lines_matched else 0.0

    @property
    def recall(self) -> float:
        return round(self.correct / self.lines_matchable, 4) if self.lines_matchable else 0.0

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return round(2 * self.precision * self.recall / (self.precision + self.recall), 4)

    @property
    def auto_post_precision(self) -> float:
        return round(self.auto_post_correct / self.auto_post_count, 4) if self.auto_post_count else 1.0

    @property
    def quarantine_accuracy(self) -> float:
        return round(self.correctly_quarantined / self.lines_quarantine, 4) if self.lines_quarantine else 1.0

    @property
    def rupee_accuracy(self) -> float:
        return round(self.rupees_correct_paise / self.rupees_matchable_paise, 4) if self.rupees_matchable_paise else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "lines_total": self.lines_total,
            "lines_matchable": self.lines_matchable,
            "lines_quarantine_expected": self.lines_quarantine,
            "lines_matched": self.lines_matched,
            "match_rate": self.match_rate,
            "correct": self.correct,
            "partial": self.partial,
            "wrong": self.wrong,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "unmatched_but_matchable": self.unmatched_but_matchable,
            "correctly_quarantined": self.correctly_quarantined,
            "wrongly_claimed_quarantine": self.wrongly_claimed_quarantine,
            "quarantine_accuracy": self.quarantine_accuracy,
            "auto_post_count": self.auto_post_count,
            "auto_post_correct": self.auto_post_correct,
            "auto_post_precision": self.auto_post_precision,
            "rupee_accuracy": self.rupee_accuracy,
            "false_document_assignments": self.false_doc_assignments,
            "mean_abs_amount_error_paise": (
                round(sum(self.amount_error_paise) / len(self.amount_error_paise), 1) if self.amount_error_paise else 0.0
            ),
            "max_abs_amount_error_paise": max(self.amount_error_paise, default=0),
            "by_tier": self.by_tier,
            "by_kind": self.by_kind,
            "timing_ms": self.timing_ms,
            "throughput_lines_per_s": self.throughput_lines_per_s,
            "unresolved_count": len(self.unresolved),
            "notes": self.notes,
        }


def score(
    matches: list[Match],
    truth: dict[str, dict[str, object]],
    *,
    lines: list | None = None,
    stats: dict[str, object] | None = None,
    strategy: str = "full",
    unresolved_cap: int | None = None,
) -> ScoreCard:
    """Compare engine output to ground truth. `matches` may contain >1 match per line (it should not)."""
    card = ScoreCard(strategy=strategy)
    card.lines_total = len(truth)
    by_line: dict[str, list[Match]] = {}
    for m in matches:
        by_line.setdefault(m.line_id, []).append(m)

    def bump(kind: str, key: str) -> None:
        """Per-class bookkeeping. A single aggregate recall hides that lumpsum and settlement
        groups are hard while invoice-number lines are trivial; this table is what we actually
        report when asked how the ladder behaves on each kind of line."""
        row = card.by_kind.setdefault(
            kind,
            {"lines": 0, "correct": 0, "partial": 0, "wrong": 0, "unmatched": 0, "refused_correctly": 0},
        )
        row[key] = int(row[key]) + 1

    for line_id, t in truth.items():
        kind = str(t["kind"])
        matchable = kind in MATCHABLE
        bump(kind, "lines")
        card.rupees_matchable_paise += abs(int(t["amount_paise"])) if matchable else 0
        if matchable:
            card.lines_matchable += 1
        elif kind in QUARANTINE:
            card.lines_quarantine += 1
        got = by_line.get(line_id)
        if not got:
            if matchable and (unresolved_cap is None or len(card.unresolved) < unresolved_cap):
                card.unmatched_but_matchable += 1
                card.unresolved.append(
                    {
                        "line_id": line_id,
                        "why": "no_match",
                        "truth_docs": sorted(t["docs"]),  # type: ignore[arg-type]
                        "amount_paise": t["amount_paise"],
                        "txn_date": t["txn_date"],
                        "narration": str(t["narration"])[:120],
                    }
                )
            elif matchable:
                card.unmatched_but_matchable += 1
            bump(kind, "unmatched" if matchable else "refused_correctly")
            continue

        card.lines_matched += 1
        # multiple matches for one line is itself a defect we want visible, not averaged away
        claimed = frozenset(d for m in got for d in m.doc_ids)
        first = got[0]
        doc_truth = t["docs"]
        if claimed == doc_truth and len(got) == 1:
            bump(kind, "correct")
            card.correct += 1
            card.rupees_correct_paise += abs(int(t["amount_paise"]))
            tier = card.by_tier.setdefault(first.tier, {"matches": 0, "correct": 0, "wrong": 0})
            tier["matches"] += 1
            tier["correct"] += 1
            if kind == "matchable_amount_mismatch":
                card.amount_error_paise.append(abs(first.amount_diff_paise))
        elif claimed & doc_truth:
            bump(kind, "partial")
            card.partial += 1
            tier = card.by_tier.setdefault(first.tier, {"matches": 0, "correct": 0, "wrong": 0})
            tier["matches"] += 1
            tier["wrong"] += 1
            if unresolved_cap is None or len(card.unresolved) < unresolved_cap:
                card.unresolved.append(
                    {
                        "line_id": line_id,
                        "why": "partial_set",
                        "claimed": sorted(claimed),
                        "truth_docs": sorted(doc_truth),
                        "amount_paise": t["amount_paise"],
                        "txn_date": t["txn_date"],
                        "narration": str(t["narration"])[:120],
                    }
                )
        else:
            bump(kind, "wrong")
            card.wrong += 1
            card.false_doc_assignments += len(claimed - doc_truth)
            tier = card.by_tier.setdefault(first.tier, {"matches": 0, "correct": 0, "wrong": 0})
            tier["matches"] += 1
            tier["wrong"] += 1
            if kind in QUARANTINE:
                card.wrongly_claimed_quarantine += 1
            if unresolved_cap is None or len(card.unresolved) < unresolved_cap:
                card.unresolved.append(
                    {
                        "line_id": line_id,
                        "why": "wrong_document" if matchable else "posted_a_line_that_should_not_be_posted",
                        "claimed": sorted(claimed),
                        "truth_docs": sorted(doc_truth),
                        "amount_paise": t["amount_paise"],
                        "txn_date": t["txn_date"],
                        "narration": str(t["narration"])[:120],
                    }
                )
        if any(m.auto_post for m in got):
            card.auto_post_count += 1
            if claimed == doc_truth and len(got) == 1:
                card.auto_post_correct += 1

    for k, row in card.by_kind.items():
        n = int(row["lines"])
        if k in QUARANTINE:
            row["resolution"] = "left unmatched"
            row["correct_pct"] = round(100.0 * int(row["refused_correctly"]) / n, 2) if n else 100.0
        elif k == "matchable_lumpsum":
            row["resolution"] = "one line, several documents"
            row["correct_pct"] = round(100.0 * int(row["correct"]) / n, 2) if n else 0.0
        elif k in MATCHABLE:
            row["resolution"] = "matched to the exact document set"
            row["correct_pct"] = round(100.0 * int(row["correct"]) / n, 2) if n else 0.0
        else:
            row["resolution"] = "unexplained by any document"
            row["correct_pct"] = round(100.0 * (int(row["refused_correctly"]) + int(row["correct"])) / n, 2) if n else 0.0

    # quarantine score: expected-unmatched lines the engine correctly refused to post
    claimed_ids = set(by_line)
    card.correctly_quarantined = sum(1 for lid, t in truth.items() if str(t["kind"]) in QUARANTINE and lid not in claimed_ids)

    if stats:
        timing = stats.get("timing_ms") or stats.get("stage_ms") or {}
        if isinstance(timing, dict):
            card.timing_ms = {k: float(v) for k, v in timing.items()}
        card.notes.append(f"strategy={strategy} tiers={stats.get('tier_counts')}")
        total = sum(v for k, v in card.timing_ms.items() if k.endswith("_ms")) or 1.0
        if total:
            card.throughput_lines_per_s = round(card.lines_total / (total / 1000.0), 1)
    return card
