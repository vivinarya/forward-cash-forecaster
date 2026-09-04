"""Reconciliation engine: a ranked ladder of deterministic matching tiers.

Every tier is (1) auditable, (2) cheap, (3) explicit about which evidence it used. The
engine never guesses silently: below the auto-post threshold a match still gets written,
but with a `confidence` that routes it to the review queue, and unresolved lines come out
as typed exceptions rather than vanishing.

Tier ladder (later tiers only see what earlier tiers left unresolved):

  t0  housekeeping   duplicate bank postings (same UTR + amount + date)
  t1  settlement     Razorpay payout UTR / settlement id  -> gateway settlement credit
  t2  advice         customer remittance advice matched on UTR
  t3  doc number     invoice / bill number found in the narration (regex, configurable)
  t4  amount exact   unique open document at the same amount inside the due-date window
  t5  amount+narration name amount within tolerance and counterparty similarity >= review
  t6  lumpsum        subset of open documents of one counterparty summing exactly to the line
  t7  fuzzy          global greedy assignment over the remaining pairs (review-only, never auto-post)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import Settings
from ..ingest import Dataset
from ..models import BankLine, LedgerDoc, Match, ReconException, ReconResult
from ..money import fmt_inr as fmt
from ..norm import (
    channel_purpose_tags,
    name_tokens,
    classify_narration_kind,
    counterparty_hint,
    doc_key,
    invoice_tokens,
    norm_name,
    similarity_norm,
)
from .index import DocIndex, LineIndex

TIER_BASE_CONFIDENCE = {
    "t1_settlement": 0.985,
    "t2_advice_utr": 0.965,
    "t3_doc_number": 0.955,
    "t4_amount_exact": 0.925,
    "t5_amount_name": 0.860,
    "t6_lumpsum": 0.830,
    "t7_fuzzy": 0.700,
    "t8_single_inference": 0.620,
}

STRATEGY_TIERS = {
    "exact": {"t1_settlement", "t2_advice_utr", "t3_doc_number"},
    "fuzzy_only": {"t4_amount_exact", "t5_amount_name", "t6_lumpsum", "t7_fuzzy", "t8_single_inference"},
    "full": set(TIER_BASE_CONFIDENCE),
}


@dataclass
class Reconciler:
    dataset: Dataset
    settings: Settings
    strategy: str = "full"

    doc_ix: DocIndex = field(init=False)
    line_ix: LineIndex = field(init=False)
    matches: list[Match] = field(init=False)
    exceptions: list[ReconException] = field(init=False)
    stage_ms: dict[str, float] = field(init=False)
    tier_counts: dict[str, int] = field(init=False)
    settlement_docs: dict[str, LedgerDoc] = field(init=False, default_factory=dict)
    _idx_by_doc_id: dict[str, int] = field(init=False, default_factory=dict)
    _by_line_id: dict[str, BankLine] = field(init=False, default_factory=dict)
    _doc_by_id: dict[str, LedgerDoc] = field(init=False, default_factory=dict)
    as_of: date = field(init=False, default=None)  # type: ignore[assignment]
    _settlement_by_utr: dict[str, str] = field(init=False, default_factory=dict)
    _settlement_by_id: dict[str, str] = field(init=False, default_factory=dict)
    _advice_by_utr: dict[str, list] = field(init=False, default_factory=list)
    _advice_by_key: dict[str, list] = field(init=False, default_factory=dict)
    _hints: dict[str, str] = field(init=False, default_factory=dict)

    # ------------------------------------------------------------------ setup
    def prepare(self) -> None:
        import time

        t0 = time.perf_counter()
        ds = self.dataset
        # Settlement "documents" are kept out of the AR/AP candidate pool on purpose: a
        # settlement credit is only ever evidence-backed by its payout UTR (t1). Letting them
        # into amount-based tiers lets an unallocated customer credit latch onto a settlement.
        self.settlement_docs = {f"SETL-{st.settlement_id}": self._settlement_doc(st) for st in ds.settlements}
        docs = [*ds.invoices, *ds.bills]
        self._doc_by_id = {d.doc_id: d for d in docs}
        for d in docs:
            self._doc_by_id.setdefault(doc_key(d.number), d)
        self.doc_ix = DocIndex.build(docs)
        self._idx_by_doc_id = {item.doc.doc_id: di for di, item in enumerate(self.doc_ix.items)}
        self._by_line_id = {ln.line_id: ln for ln in ds.lines}
        self.line_ix = LineIndex.build(ds.lines)
        # "today" for a hand-built or manifest-less dataset is the last line we can see; without this
        # fallback the aged-receivables pass below compares dates against None and the run dies.
        self.as_of = ds.as_of or (max((ln.txn_date for ln in ds.lines), default=date.today()) if ds.lines else date.today())
        self._settlement_by_utr = {s.payout_utr: s.settlement_id for s in ds.settlements if s.payout_utr}
        self._settlement_by_id = {s.settlement_id: s.payout_utr or "" for s in ds.settlements}
        self._advice_by_utr = {}
        self._advice_by_key = {}
        for a in ds.advices:
            if a.utr:
                self._advice_by_utr.setdefault(a.utr, []).append(a)
            if a.invoice_number:
                self._advice_by_key.setdefault(doc_key(a.invoice_number), []).append(a)
        self.matches = []
        self.exceptions = []
        self.stage_ms = {}
        self.tier_counts = {}
        self._hints = {}
        self.stage_ms["prepare_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    @staticmethod
    def _settlement_doc(s) -> LedgerDoc:
        """A gateway settlement is a first-class ledger object, not an invoice."""
        return LedgerDoc(
            doc_id=f"SETL-{s.settlement_id}",
            kind="AR",
            number=s.settlement_id,
            counterparty="Razorpay Software and Payments Services Pvt Ltd",
            amount_paise=s.gross_paise,
            net_amount_paise=s.net_paise,
            doc_date=s.settled_on,
            due_date=s.settled_on,
            status="open",
            extra={"customer_code": "GATEWAY", "settlement_id": s.settlement_id},
        )

    # ------------------------------------------------------------------ helpers
    def tolerance(self, amount_paise: int) -> int:
        return self.settings.tolerance_paise(amount_paise)

    def confidence(self, tier: str, *, amount_diff: int, amount: int, name_sim: float, ambiguous: bool) -> float:
        base = TIER_BASE_CONFIDENCE[tier]
        amt = 1.0 if amount <= 0 else max(0.0, 1.0 - 0.35 * min(1.0, abs(amount_diff) / max(1, amount)))
        name = 0.94 + 0.06 * min(1.0, name_sim)
        mult = 0.90 if ambiguous else 1.0
        return round(min(0.999, base * amt * name * mult), 3)

    def commit(
        self,
        line_i: int,
        doc_idxs: list[int],
        tier: str,
        *,
        score: float,
        evidence: str,
        amount_diff: int = 0,
        name_sim: float = 0.0,
        ambiguous: bool = False,
        force_review: bool = False,
    ) -> Match:
        line = self.line_ix.lines[line_i]
        conf = self.confidence(tier, amount_diff=amount_diff, amount=abs(line.amount_paise), name_sim=name_sim, ambiguous=ambiguous)
        if force_review:
            conf = min(conf, 0.85)
        auto_min = self.settings.auto_post_min_confidence
        cap = int(self.settings.rules["auto_post"]["max_amount_paise_for_auto"])
        exact_ok = abs(amount_diff) <= self.tolerance(abs(line.amount_paise))
        auto = conf >= auto_min and exact_ok and abs(line.amount_paise) <= cap and tier != "t7_fuzzy"
        m = Match(
            line_id=line.line_id,
            doc_ids=tuple(self.doc_ix.items[i].doc.doc_id for i in doc_idxs if i >= 0),
            tier=tier,
            score=round(score, 3),
            confidence=conf,
            amount_diff_paise=amount_diff,
            auto_post=auto,
            evidence=evidence,
        )
        self.matches.append(m)
        self.line_ix.take(line_i)
        self.doc_ix.consumed.update(doc_idxs)
        self.tier_counts[tier] = self.tier_counts.get(tier, 0) + 1
        if not exact_ok and tier in {"t3_doc_number", "t1_settlement"}:
            self.exceptions.append(
                ReconException(
                    ref_id=line.line_id,
                    ref_type="bank_line",
                    code="SHORT_DEDUCTION",
                    severity="medium",
                    amount_paise=amount_diff,
                    detail=(
                        f"Payment of {line.amount_paise} settles {m.doc_ids[0]} but is off by "
                        f"{amount_diff} paise, beyond the {self.tolerance(abs(line.amount_paise))} paise tolerance. "
                        "Needs a credit note / write-off, not a silent match."
                    ),
                    candidates=m.doc_ids,
                    suggested_action="raise_credit_note_or_writeoff",
                )
            )
        return m

    def add_exception(self, *args, **kwargs) -> None:
        self.exceptions.append(ReconException(*args, **kwargs))

    def hint(self, line: BankLine) -> str:
        if line.line_id not in self._hints:
            self._hints[line.line_id] = counterparty_hint(line.narration)
        return self._hints[line.line_id]

    def name_sim(self, line: BankLine, doc_i: int) -> float:
        return similarity_norm(self.hint(line), self.doc_ix.items[doc_i].name_norm)

    def in_window(self, txn: date, doc: LedgerDoc, tier: str) -> bool:
        early, late, floor = self.settings.window(tier)
        rel_due = (txn - doc.due_date).days
        if -early <= rel_due <= late:
            return True
        return rel_due > late and (txn - doc.doc_date).days <= floor + late + early

    # ------------------------------------------------------------------ t0 housekeeping
    def t0_duplicates(self) -> None:
        """Same UTR + same amount posted twice -> keep one, quarantine the rest.

        A second copy of a receipt is a real, dangerous failure mode (it becomes a double
        customer credit or a double vendor payment), so it is detected before any matching
        tier can consume one of the copies.
        """
        dup_days = int(self.settings.rules["duplicate_detection"].get("same_amount_name_within_days", 3))
        first_by_utr: dict[tuple, int] = {}
        first_by_sig: dict[tuple, int] = {}
        for i, line in enumerate(self.line_ix.lines):
            utr_key = (line.utr or "", abs(line.amount_paise))
            sig_key = (norm_name(self.hint(line)), abs(line.amount_paise), line.amount_paise > 0)
            clash_utr = first_by_utr.get(utr_key) if line.utr else None
            near = first_by_sig.get(sig_key)
            near_ok = near is not None and near != i and abs((line.txn_date - self.line_ix.lines[near].txn_date).days) <= dup_days and self.line_ix.lines[near].utr == line.utr
            if clash_utr is not None and clash_utr != i:
                self.line_ix.take(i)
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "DUPLICATE_BANK_LINE",
                    "high",
                    line.amount_paise,
                    f"UTR {line.utr} and amount already posted on line {self.line_ix.lines[clash_utr].line_id}. "
                    "Two ledger entries for one bank movement - confirm the statement itself is not duplicated before reposting.",
                    (self.line_ix.lines[clash_utr].line_id,),
                    "verify_with_bank_then_de_dup",
                )
                continue
            if near_ok:
                self.line_ix.take(i)
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "SUSPECTED_DUPLICATE",
                    "medium",
                    line.amount_paise,
                    f"Identical payee and amount as {self.line_ix.lines[near].line_id} within {dup_days} days but a different UTR.",
                    (self.line_ix.lines[near].line_id,),
                    "ask_ap_team_whether_this_is_a_second_payment",
                )
                continue
            if line.utr:
                first_by_utr[utr_key] = i
            first_by_sig[sig_key] = i

    # ------------------------------------------------------------------ t1 settlement
    def t1_settlement(self) -> None:
        consumed_settl: set[str] = set()
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            if not line.is_credit:
                continue
            sid = None
            evidence = ""
            if line.utr and line.utr in self._settlement_by_utr:
                sid, evidence = self._settlement_by_utr[line.utr], f"payout_utr={line.utr}"
            else:
                text = (line.narration or "").lower()
                for cand in self._settlement_by_id:
                    # narrations are upper/mixed case unpredictably; "SETL-1" in a lowercased string
                    # must still find the settlement it names
                    if cand.lower() in text:
                        sid, evidence = cand, f"settlement_id={cand}"
                        break
            if not sid:
                continue
            doc_id = f"SETL-{sid}"
            if sid in consumed_settl or doc_id not in self.settlement_docs:
                continue
            consumed_settl.add(sid)
            net = self.settlement_docs[doc_id].net_amount_paise
            diff = abs(line.amount_paise) - net
            self.commit(i, [], "t1_settlement", score=1.0, evidence=evidence, amount_diff=diff, name_sim=1.0)
            self.matches[-1].doc_ids = (doc_id,)

    # ------------------------------------------------------------------ t2 advice UTR
    def t2_advice_utr(self) -> None:
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            utr = line.utr
            if not utr or utr not in self._advice_by_utr:
                continue
            advices = self._advice_by_utr[utr]
            target = None
            for a in advices:
                if a.invoice_number and doc_key(a.invoice_number) in self.doc_ix.by_key:
                    target = self.doc_ix.by_key[doc_key(a.invoice_number)]
                    break
            if target is None:  # advice without a usable invoice number: match on amount + payer
                amt_bucket = [di for di in self.doc_ix.amount_bucket(a.amount_paise) if self.doc_ix.items[di].doc.kind == "AR"]
                named = [di for di in amt_bucket if self.name_sim(line, di) >= self.settings.sim_thresholds()[1]]
                target = named or amt_bucket
            if not target:
                continue
            usable = [di for di in target if self.in_window(line.txn_date, self.doc_ix.items[di].doc, "tier_utr")]
            if not usable:
                continue
            diff = line.amount_paise - sum(self.doc_ix.items[di].amount for di in usable)
            m = self.commit(
                i,
                usable[:4],
                "t2_advice_utr",
                score=1.0,
                evidence=f"advice_utr={utr}",
                amount_diff=diff,
                name_sim=0.95,
                ambiguous=len(usable) > 1,
            )
            self.extend_residual(i, m)

    # ------------------------------------------------------------------ t3 doc number in narration
    def t3_doc_number(self) -> None:
        patterns = self.settings.patterns
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            toks = invoice_tokens(line.narration, patterns)
            if not toks:
                continue
            hits: list[int] = []
            missing: list[str] = []
            for t in toks:
                idxs = [di for di in self.doc_ix.by_key.get(doc_key(t), []) if di not in self.doc_ix.consumed]
                if idxs:
                    hits.extend(idxs)
                else:
                    missing.append(t)
            if not hits:
                continue  # reference not in our book: leave pending, later tiers / exceptions handle it
            direction = "AR" if line.is_credit else "AP"
            hits = self.doc_ix.within_window(
                sorted(set(hits)),
                txn_date=line.txn_date,
                early=self.settings.window("tier_doc_number")[0],
                late=self.settings.window("tier_doc_number")[1],
                doc_floor=self.settings.window("tier_doc_number")[2],
                direction=direction,
            )
            if not hits:
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "DOC_REF_OUTSIDE_WINDOW",
                    "medium",
                    line.amount_paise,
                    f"Narration references {','.join(toks)} but that document is not inside the reconciliation window.",
                    tuple(toks),
                    "check_if_payment_is_against_a_different_invoice",
                )
                self.line_ix.take(i)
                continue
            total = sum(self.doc_ix.items[di].amount for di in hits)
            diff = abs(line.amount_paise) - total
            name_sim = max((self.name_sim(line, di) for di in hits), default=0.0)
            # An explicit document reference is strong evidence even when the amount is off:
            # we match it, but the deviation is escalated as a SHORT_DEDUCTION exception.
            m3 = self.commit(
                i,
                hits[:8],
                "t3_doc_number",
                score=min(1.0, 0.7 + 0.3 * name_sim),
                evidence=f"doc_ref={','.join(toks)}",
                amount_diff=diff,
                name_sim=name_sim,
                ambiguous=len(hits) > 1,
            )
            if len(hits) == 1:
                self.extend_residual(i, m3)

    # ------------------------------------------------------------------ t4 amount exact
    def t4_amount_exact(self) -> None:
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            direction = "AR" if line.is_credit else "AP"
            bucket = [
                di
                for di in self.doc_ix.amount_bucket(abs(line.amount_paise))
                if self.doc_ix.items[di].doc.kind == direction and self.in_window(line.txn_date, self.doc_ix.items[di].doc, "tier_amount_exact")
            ]
            if not bucket:
                continue
            scored = sorted(((self.name_sim(line, di), di) for di in bucket), reverse=True)
            best_sim, best = scored[0]
            if len(scored) == 1:
                self.commit(i, [best], "t4_amount_exact", score=0.9, evidence="amount_exact_unique", amount_diff=0, name_sim=best_sim)
            elif best_sim >= self.settings.sim_thresholds()[0]:
                tied = [di for s, di in scored if s >= self.settings.sim_thresholds()[0]]
                if len(tied) == 1:
                    self.commit(i, [best], "t4_amount_exact", score=0.95, evidence=f"amount_exact+name({best_sim:.2f})", amount_diff=0, name_sim=best_sim)
                else:
                    self.add_exception(
                        line.line_id,
                        "bank_line",
                        "AMBIGUOUS_CANDIDATES",
                        "medium",
                        line.amount_paise,
                        f"{len(tied)} open documents share this exact amount and this counterparty; amount alone cannot disambiguate.",
                        tuple(self.doc_ix.label(di) for di in tied[:5]),
                        "ask_customer_for_invoice_breakup",
                    )
            else:
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "AMBIGUOUS_CANDIDATES",
                    "medium",
                    line.amount_paise,
                    f"Exact amount hits {len(scored)} documents with no name evidence in the narration (best name score {best_sim:.2f}).",
                    tuple(self.doc_ix.label(di) for di in [di for _, di in scored[:4]]),
                    "human_pick_then_post",
                )

    # ------------------------------------------------------------------ t5 amount tolerance + name
    def t5_amount_name(self) -> None:
        review_min, _ = self.settings.sim_thresholds()
        review_min = float(review_min)
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            direction = "AR" if line.is_credit else "AP"
            tol = self.tolerance(abs(line.amount_paise))
            bucket = self.doc_ix.amounts_near(abs(line.amount_paise), tol)
            bucket = [
                di
                for di in bucket
                if self.doc_ix.items[di].doc.kind == direction and self.in_window(line.txn_date, self.doc_ix.items[di].doc, "tier_amount_plus_name")
            ]
            if not bucket:
                continue
            scored = sorted(((self.name_sim(line, di), di) for di in bucket), reverse=True)
            top = [di for s, di in scored if s >= review_min]
            if not top:
                continue
            if len(top) > 1 and scored[0][0] - scored[1][0] < 0.02:
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "AMBIGUOUS_CANDIDATES",
                    "medium",
                    line.amount_paise,
                    f"{len(top)} documents within {tol} paise and equally plausible by name.",
                    tuple(self.doc_ix.label(di) for di in top[:5]),
                    "human_pick_then_post",
                )
                continue
            di = top[0]
            diff = abs(line.amount_paise) - self.doc_ix.items[di].amount
            self.commit(
                i,
                [di],
                "t5_amount_name",
                score=scored[0][0],
                evidence=f"amount_within_tolerance({diff}p)+name({scored[0][0]:.2f})",
                amount_diff=diff,
                name_sim=scored[0][0],
            )

    # ------------------------------------------------------------------ t6 lumpsum
    def t6_lumpsum(self) -> None:
        """Aggregate receipt = exact subset of one counterparty's open documents.

        Blocking: an inverted index from counterparty name-token to documents, so a line only
        ever looks at documents that share a name token with it. The naive version (every line x
        every party x subset-sum) was 250 ms on 1.2k lines for one aggregate payment; this is
        linear in shared tokens and finds more of them.
        """
        from collections import Counter, defaultdict

        cfg = self.settings.rules["lumpsum"]
        if not cfg.get("enabled", True):
            return
        max_c = int(cfg["max_candidates"])
        min_parts = int(cfg["min_parts"])
        review_min = float(self.settings.sim_thresholds()[1])
        token_index: dict[str, list[int]] = defaultdict(list)
        for di, item in enumerate(self.doc_ix.items):
            for tok in item.tokens:
                token_index[tok].append(di)

        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            direction = "AR" if line.is_credit else "AP"
            hint = self.hint(line)
            hint_toks = name_tokens(hint)
            if not hint_toks:
                continue
            counts = Counter()
            for tok in hint_toks:
                for di in token_index.get(tok, ()):
                    counts[di] += 1
            if not counts:
                continue
            best_sim = {di: max(similarity_norm(hint, self.doc_ix.items[di].name_norm), 0.0) for di in counts}
            cands = [
                di
                for di in counts
                if best_sim[di] >= review_min
                and di not in self.doc_ix.consumed
                and self.doc_ix.items[di].doc.kind == direction
                and self.doc_ix.items[di].amount <= abs(line.amount_paise)
                and self.in_window(line.txn_date, self.doc_ix.items[di].doc, "tier_lumpsum")
            ]
            if len(cands) < min_parts:
                continue
            if len(cands) > max_c:
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "TOO_MANY_CANDIDATES",
                    "low",
                    line.amount_paise,
                    f"{len(cands)} open documents for this counterparty sit in the window; subset allocation "
                    f"is not attempted past {max_c} (it stops being a proof and starts being a guess).",
                    tuple(self.doc_ix.label(di) for di in cands[:6]),
                    "allocate_by_oldest_invoice_first",
                )
                continue
            target = abs(line.amount_paise)
            combos = subset_sums([self.doc_ix.items[di].amount for di in cands], target, min_parts=min_parts)
            if len(combos) == 0:
                continue
            if len(combos) > 1:
                self.add_exception(
                    line.line_id,
                    "bank_line",
                    "AMBIGUOUS_LUMPSUM",
                    "medium",
                    line.amount_paise,
                    f"{len(combos)} different subsets of {len(cands)} open documents for this counterparty sum to the line amount.",
                    tuple(self.doc_ix.label(di) for di in cands[:6]),
                    "allocate_by_oldest_invoice_first",
                )
                continue
            chosen = [cands[k] for k in combos[0]]
            diff = target - sum(self.doc_ix.items[di].amount for di in chosen)
            self.commit(
                i,
                chosen,
                "t6_lumpsum",
                score=0.99,
                evidence=f"subset_sum({len(chosen)} docs of {len(cands)} candidates, shared tokens {sorted(hint_toks & set(self.doc_ix.items[chosen[0]].tokens))})",
                amount_diff=diff,
                name_sim=min(best_sim[di] for di in chosen),
                ambiguous=True,
            )

    # ------------------------------------------------------------------ t7 fuzzy global assignment
    def t7_fuzzy(self) -> None:
        review_min = float(self.settings.sim_thresholds()[1])
        pairs: list[tuple[float, int, int, int]] = []
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            direction = "AR" if line.is_credit else "AP"
            hint = self.hint(line)
            if not hint:
                continue
            tol = max(self.tolerance(abs(line.amount_paise)), int(abs(line.amount_paise) * 0.02))
            for di in self.doc_ix.amounts_near(abs(line.amount_paise), tol):
                item = self.doc_ix.items[di]
                if item.doc.kind != direction:
                    continue
                if not self.in_window(line.txn_date, item.doc, "tier_fuzzy"):
                    continue
                sim = similarity_norm(hint, item.name_norm)
                if sim < review_min:
                    continue
                diff = abs(line.amount_paise) - item.amount
                pairs.append((round(sim - abs(diff) / max(1, abs(line.amount_paise)) * 0.5, 6), i, di, diff))
        pairs.sort(reverse=True)
        used_lines: set[int] = set()
        used_docs: set[int] = set()
        for score, i, di, diff in pairs:
            if i in used_lines or di in used_docs or i not in self.line_ix.unresolved or di in self.doc_ix.consumed:
                continue
            used_lines.add(i)
            used_docs.add(di)
            self.commit(i, [di], "t7_fuzzy", score=score, evidence=f"fuzzy_assignment_name({score:.2f})", amount_diff=diff, name_sim=score, force_review=True)

    # ------------------------------------------------------------------ residual allocation
    def _open_pool_for(self, line: BankLine, party_codes: set[str], direction: str, target: int, tier: str) -> list[int]:
        early, late, floor = self.settings.window(tier)
        pool: list[int] = []
        for code in party_codes:
            for di in self.doc_ix.by_party.get(code, ()):
                if di in self.doc_ix.consumed:
                    continue
                item = self.doc_ix.items[di]
                if item.doc.kind != direction or item.doc.outstanding_paise <= 0:
                    continue
                if item.amount > target:
                    continue
                if not self.in_window(line.txn_date, item.doc, tier):
                    continue
                pool.append(di)
        return sorted(set(pool), key=lambda di: self.doc_ix.items[di].doc.due_date)

    def extend_residual(self, line_i: int, m: Match) -> None:
        """A receipt that only partly clears what the evidence names: try to allocate the rest.

        This is what a clerk does with a lump-sum remittance: they post the invoice the advice
        mentions, then look at the customer's other open bills for something that ties to the
        leftover. Only an *unambiguous* subset is accepted, and the line stays in the review queue.
        """
        line = self.line_ix.lines[line_i]
        chosen = [self._idx_by_doc_id[d] for d in m.doc_ids if d in self._idx_by_doc_id]
        if not chosen:
            return
        direction = line.amount_paise and ("AR" if line.is_credit else "AP")
        target = abs(line.amount_paise) - sum(self.doc_ix.items[di].amount for di in chosen)
        if abs(target) <= self.tolerance(abs(line.amount_paise)) or target <= 0:
            return
        codes = {self.doc_ix.items[di].party_code for di in chosen}
        pool = self._open_pool_for(line, codes, direction, target, "tier_lumpsum")
        if len(pool) < 1 or len(pool) > 14:
            self.add_exception(
                line.line_id,
                "bank_line",
                "RESIDUAL_UNALLOCATED",
                "medium",
                target,
                f"{fmt(target)} of this line is still unallocated after posting {','.join(m.doc_ids)} "
                f"({len(pool)} open documents could absorb it).",
                tuple(self.doc_ix.label(di) for di in pool[:6]),
                "ask_customer_for_invoice_breakup",
            )
            return
        combos = subset_sums([self.doc_ix.items[di].amount for di in pool], target, min_parts=1, max_parts=4)
        if len(combos) != 1:
            self.add_exception(
                line.line_id,
                "bank_line",
                "RESIDUAL_UNALLOCATED",
                "medium",
                target,
                f"{len(combos)} possible allocations of the residual {fmt(target)}; refusing to guess.",
                tuple(self.doc_ix.label(di) for di in pool[:6]),
                "ask_customer_for_invoice_breakup",
            )
            return
        extra = [pool[k] for k in combos[0]]
        m.doc_ids = tuple(self.doc_ix.items[di].doc.doc_id for di in chosen + extra)
        m.evidence += f" +residual_alloc({len(extra)} docs)"
        m.confidence = round(min(m.confidence, 0.88), 3)
        m.auto_post = False
        self.doc_ix.consumed.update(extra)

    def t8_single_candidate_inference(self) -> None:
        """Last resort: exactly one open document could possibly explain this line.

        Posted as *review-required*, never auto-posted, and always accompanied by an exception so a
        human confirms it. In an Indian SMB's ledger "the only open bill from them" is usually the
        right answer, and it is exactly the case a pure-amount script refuses to touch - but the
        engine is not allowed to be quietly confident about it.
        """
        review_min = float(self.settings.sim_thresholds()[1])
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            hint = self.hint(line)
            hint_toks = name_tokens(hint)
            if not hint_toks:
                continue
            direction = "AR" if line.is_credit else "AP"
            tol = int(abs(line.amount_paise) * 0.18)
            cands: list[int] = []
            for di in self.doc_ix.amounts_near(abs(line.amount_paise), tol):
                item = self.doc_ix.items[di]
                if item.doc.kind != direction or not (item.tokens & hint_toks):
                    continue
                if similarity_norm(hint, item.name_norm) < review_min * 0.88:
                    continue
                if not self.in_window(line.txn_date, item.doc, "tier_fuzzy"):
                    continue
                cands.append(di)
            if len(cands) != 1:
                continue
            di = cands[0]
            doc = self.doc_ix.items[di].doc
            diff = abs(line.amount_paise) - self.doc_ix.items[di].amount
            m = self.commit(
                i,
                [di],
                "t8_single_inference",
                score=0.6,
                evidence=f"only open {doc.kind} document for this counterparty in the window (amount off by {diff} paise)",
                amount_diff=diff,
                name_sim=similarity_norm(hint, self.doc_ix.items[di].name_norm),
                force_review=True,
            )
            self.add_exception(
                line.line_id,
                "bank_line",
                "SINGLE_CANDIDATE_INFERRED",
                "medium",
                line.amount_paise,
                f"No document reference in the narration; matched to {doc.number} because it is the only "
                f"open document for {doc.counterparty} in the window. Amount differs by {fmt(diff)}.",
                (doc.doc_id,),
                "confirm_with_counterparty_then_post",
            )
            self.extend_residual(i, m)

    # ------------------------------------------------------------------ leftovers
    def classify_unresolved(self) -> None:
        """Anything still pending becomes a typed exception. Nothing is silently dropped."""
        for i in self.line_ix.pending():
            line = self.line_ix.lines[i]
            tags = channel_purpose_tags(line.narration)
            kind = classify_narration_kind(line.narration)
            if "CHARGE" in tags or kind == "BANK_CHARGE":
                code, sev, action = "BANK_CHARGE_NO_DOCUMENT", "low", "post_to_bank_charges_gl"
            elif "INTEREST" in tags or kind == "INTEREST":
                code, sev, action = "BANK_INTEREST_NO_DOCUMENT", "low", "post_to_interest_income_gl"
            elif "REVERSAL" in tags or kind == "REVERSAL":
                code, sev, action = "REVERSAL_OR_RETURN", "medium", "match_against_original_then_repost"
            elif line.is_credit:
                code, sev, action = "UNALLOCATED_CREDIT", "high", "contact_customer_for_remap_advice"
            else:
                code, sev, action = "UNMATCHED_DEBIT", "high", "confirm_payee_with_ap_team_before_reposting"
            self.add_exception(
                line.line_id,
                "bank_line",
                code,
                sev,
                line.amount_paise,
                f"narration={line.narration!r} channel={kind} utr={line.utr or '-'}",
                (),
                action,
            )
        # ledger-side: documents the feed never cleared
        for di, item in enumerate(self.doc_ix.items):
            if di in self.doc_ix.consumed or item.party_code == "GATEWAY":
                continue
            d = item.doc
            if d.kind == "AR" and d.outstanding_paise > 0 and d.due_date < self.as_of:
                self.add_exception(
                    d.doc_id,
                    "invoice",
                    "OVERDUE_UNRECONCILED_AR",
                    "high" if (self.as_of - d.due_date).days > 45 else "medium",
                    d.outstanding_paise,
                    f"Invoice {d.number} for {d.counterparty} is {(self.as_of - d.due_date).days} days past due with no matching receipt.",
                    (d.number,),
                    "chase_or_provision",
                )

    # ------------------------------------------------------------------ driver
    def run(self) -> ReconResult:
        import time

        self.prepare()
        allowed = STRATEGY_TIERS[self.strategy]
        order = [
            "t0_duplicates",
            "t1_settlement",
            "t2_advice_utr",
            "t3_doc_number",
            "t4_amount_exact",
            "t5_amount_name",
            "t6_lumpsum",
            "t7_fuzzy",
            "t8_single_candidate_inference",
        ]
        for name in order:
            if name != "t0_duplicates" and name not in allowed:
                continue
            t0 = time.perf_counter()
            getattr(self, name)()
            self.stage_ms[f"{name}_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        t0 = time.perf_counter()
        self.classify_unresolved()
        self.stage_ms["classify_unresolved_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        matched_ids = {m.line_id for m in self.matches}
        stats = {
            "bank_lines": len(self.line_ix.lines),
            "documents": len(self.doc_ix.items),
            "matched_lines": len(matched_ids),
            "auto_posted": sum(1 for m in self.matches if m.auto_post),
            "exceptions": len(self.exceptions),
            "tier_counts": dict(self.tier_counts),
            "stage_ms": dict(self.stage_ms),
            "strategy": self.strategy,
        }
        return ReconResult(matches=self.matches, exceptions=self.exceptions, stats=stats, matched_line_ids=matched_ids)


def subset_sums(values: list[int], target: int, *, min_parts: int = 2, max_parts: int = 6) -> list[tuple[int, ...]]:
    """Exact subset sums of `values` equal to `target`, up to max_parts items.

    Bounded DP over (count, sum) instead of brute force: the candidate set is capped by
    the caller at 12 documents, so the state space stays tiny and the result is complete
    for that pool. Returns *all* solutions, because knowing that the allocation is
    ambiguous is more useful than confidently picking one of three.
    """
    if not values or target <= 0:
        return []
    from collections import defaultdict

    states: dict[int, list[tuple[int, ...]]] = defaultdict(list)  # sum -> combos
    states[0] = [tuple()]
    solutions: list[tuple[int, ...]] = []
    for k, v in enumerate(values):
        nxt: dict[int, list[tuple[int, ...]]] = defaultdict(list)
        for s, combos in states.items():
            ns = s + v
            if ns > target:
                continue
            for c in combos:
                if len(c) + 1 > max_parts:
                    continue
                new = c + (k,)
                if ns == target and len(new) >= min_parts:
                    solutions.append(new)
                nxt[ns].append(new)
        for s, combos in nxt.items():
            states[s].extend(combos)
        if len(solutions) > 8:  # we only need to know that it is ambiguous
            return solutions
    return solutions
