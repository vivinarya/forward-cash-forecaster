"""Indexes used for candidate blocking.

Naive reconciliation compares every bank line against every document with a fuzzy
string metric: 1,255 x 1,651 = ~2.1M SequenceMatcher calls, minutes of CPU. Blocking on
(a) exact document key, (b) amount (exact or within tolerance), (c) counterparty name and
(d) UTR drops the average candidate set to under 3 and the full run to well under a second.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date

from ..models import BankLine, LedgerDoc
from ..norm import doc_key, name_tokens, norm_name


@dataclass(slots=True)
class IndexedDoc:
    idx: int
    doc: LedgerDoc
    name_norm: str
    tokens: frozenset[str]
    key: str
    party_code: str

    @property
    def amount(self) -> int:
        return self.doc.net_amount_paise or self.doc.amount_paise

    @property
    def is_open(self) -> bool:
        return self.doc.status not in {"paid", "cancelled"}

    @property
    def label(self) -> str:
        return f"{self.doc.number}/{self.doc.counterparty}"


@dataclass
class DocIndex:
    """Documents (AR + AP + settlement pseudo-documents) keyed for blocking."""

    items: list[IndexedDoc] = field(default_factory=list)
    by_key: dict[str, list[int]] = field(default_factory=dict)
    by_amount: dict[int, list[int]] = field(default_factory=dict)
    by_party: dict[str, list[int]] = field(default_factory=dict)
    by_name_head: dict[str, list[int]] = field(default_factory=dict)
    amounts_sorted: list[int] = field(default_factory=list)
    consumed: set[int] = field(default_factory=set)

    @classmethod
    def build(cls, docs: list[LedgerDoc]) -> "DocIndex":
        ix = cls()
        for i, d in enumerate(docs):
            name_norm = norm_name(d.counterparty)
            item = IndexedDoc(
                idx=i,
                doc=d,
                name_norm=name_norm,
                tokens=name_tokens(name_norm),
                key=doc_key(d.number),
                party_code=str(d.extra.get("customer_code") or d.extra.get("vendor_code") or ""),
            )
            ix.items.append(item)
            ix.by_key.setdefault(item.key, []).append(i)
            ix.by_amount.setdefault(item.amount, []).append(i)
            if item.party_code:
                ix.by_party.setdefault(item.party_code, []).append(i)
            head = " ".join(name_norm.split()[:2])
            if head:
                ix.by_name_head.setdefault(head, []).append(i)
        ix.amounts_sorted = sorted(ix.by_amount)
        return ix

    # -- candidate pools (never include a document consumed by an earlier tier) --
    def pool(self, idxs: list[int]) -> list[int]:
        return [i for i in idxs if i not in self.consumed]

    def amount_bucket(self, amount_paise: int) -> list[int]:
        return self.pool(self.by_amount.get(amount_paise, []))

    def amounts_near(self, amount_paise: int, tol_paise: int) -> list[int]:
        lo = bisect.bisect_left(self.amounts_sorted, amount_paise - tol_paise)
        hi = bisect.bisect_right(self.amounts_sorted, amount_paise + tol_paise)
        out: list[int] = []
        for amt in self.amounts_sorted[lo:hi]:
            out.extend(self.by_amount[amt])
        return self.pool(out)

    def name_bucket(self, name_norm: str) -> list[int]:
        head = " ".join(name_norm.split()[:2])
        return self.pool(self.by_name_head.get(head, []))

    def within_window(
        self,
        idxs: list[int],
        *,
        txn_date: date,
        early: int,
        late: int,
        doc_floor: int,
        direction: str,
    ) -> list[int]:
        """Date sanity check relative to due date, hard-floored at document date.

        `early` / `late` are days before/after the due date. A line can also land right
        after the document was raised (immediate payment on short terms), which is why the
        window is not simply "around the due date".
        """
        keep: list[int] = []
        for i in idxs:
            if i in self.consumed:
                continue
            d = self.items[i].doc
            if d.kind != direction:
                continue
            rel_due = (txn_date - d.due_date).days
            if rel_due > late or rel_due < -early:
                continue
            rel_doc = (txn_date - d.doc_date).days
            if rel_doc < -2:  # money cannot move before the document exists
                continue
            if rel_due > late and rel_doc > doc_floor:
                continue
            keep.append(i)
        return keep

    def label(self, i: int) -> str:
        return self.items[i].label


@dataclass
class LineIndex:
    """Bank lines with the same blocking idea, plus the set of still-unresolved lines."""

    lines: list[BankLine] = field(default_factory=list)
    unresolved: set[int] = field(default_factory=set)
    by_utr: dict[str, list[int]] = field(default_factory=dict)
    by_amount: dict[int, list[int]] = field(default_factory=dict)

    @classmethod
    def build(cls, lines: list[BankLine]) -> "LineIndex":
        ix = cls(lines=lines, unresolved=set(range(len(lines))))
        for i, ln in enumerate(lines):
            if ln.utr:
                ix.by_utr.setdefault(ln.utr, []).append(i)
            ix.by_amount.setdefault(ln.amount_paise, []).append(i)
        return ix

    def pending(self) -> list[int]:
        return sorted(self.unresolved)

    def pending_for(self, idxs: list[int]) -> list[int]:
        return [i for i in idxs if i in self.unresolved]

    def take(self, idxs: int | list[int]) -> None:
        if isinstance(idxs, int):
            self.unresolved.discard(idxs)
        else:
            self.unresolved.difference_update(idxs)

    def amount_bucket(self, amount_paise: int) -> list[int]:
        return self.pending_for(self.by_amount.get(amount_paise, []))
