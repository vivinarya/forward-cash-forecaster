"""Synthetic but structurally realistic Indian SMB treasury world.

Why a generator instead of a hand-written fixture: the brief demands 50+ records and a
measured match rate, and a hand-written fixture can only contain the messiness I think of.
This generator plants *known* failure modes (truncated names in bank narrations, missing
invoice references, short deductions, duplicate bank postings, aggregate lumpsum
receipts, charges with no document) and records the ground truth for each so that
`cashpilot bench` can score the engine honestly.

The forecaster is never shown anything after `as_of`; the scheduled future is written to
`truth_future_cash.csv` for evaluation only.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .names import CITIES, FIRST, LEGAL, PURPOSE, SECOND

INR = 100  # paise per rupee
BANK_CODES = ["HDFC0001234", "SBIN0000921", "ICIC0000429", "KKBK0000812", "UTIB0002233"]
WEEKEND_RECEIPT_FACTOR = {0: 1.15, 1: 1.05, 2: 1.0, 3: 1.05, 4: 1.22, 5: 0.35, 6: 0.06}
MONTH_FACTOR = {1: 0.95, 2: 0.95, 3: 1.15, 4: 0.92, 5: 0.95, 6: 1.0, 7: 0.95, 8: 1.0, 9: 1.02, 10: 1.12, 11: 1.18, 12: 1.08}
REPUBLIC_DAYS = {(1, 26), (8, 15), (10, 2), (11, 1), (12, 25)}


def rupees(paise: int) -> str:
    return str((Decimal(paise) / INR).quantize(Decimal("0.01"), ROUND_HALF_UP))


@dataclass(slots=True)
class Party:
    code: str
    name: str
    legal: str
    aliases: list[str]
    terms: int
    delay_mu: float
    delay_sd: float
    pay_prob: float
    size_mu: float  # rupees
    size_sd: float
    channel: str
    kind: str  # AR / AP
    region: str


@dataclass(slots=True)
class Doc:
    number: str
    kind: str
    party: Party
    doc_date: date
    due: date
    gross_paise: int
    gst_paise: int
    net_paise: int
    notes: str = ""
    # resolution written into the future plan
    paid_on: date | None = None
    paid_amount_paise: int = 0
    short_deduction_paise: int = 0
    lumpsum_group: int | None = None
    doc_id: str = ""
    status_at_asof: str = "open"


@dataclass(slots=True)
class BankEvent:
    when: date
    amount_paise: int
    narration: str
    utr: str
    doc_ids: tuple[str, ...]
    truth_kind: str
    duplicate_of: str | None = None


@dataclass
class World:
    seed: int = 20260905
    as_of: date = field(default_factory=date.today)
    history_days: int = 180
    horizon_days: int = 30
    n_customers: int = 30
    n_vendors: int = 22
    invoices_per_day: float = 7.0
    bills_per_day: float = 5.0
    open_balance_rupees: float = 22_000_000.0
    noise: dict[str, float] = field(
        default_factory=lambda: {
            "narration_has_doc": 0.62,
            "short_deduction": 0.06,
            "lumpsum": 0.05,
            "duplicate_line": 0.02,
            "truncated_name": 0.30,
            "typo": 0.12,
            "advice_rate": 0.45,
            "regime_shift_extra_default": 0.06,
        }
    )

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.start = self.as_of - timedelta(days=self.history_days)
        self.customers = [self._make_party("AR", i) for i in range(self.n_customers)]
        self.vendors = [self._make_party("AP", i) for i in range(self.n_vendors)]
        self.docs: list[Doc] = []
        self.events: list[BankEvent] = []
        self.advices: list[dict[str, str]] = []
        self.gateway_payments: list[dict[str, str]] = []
        self.settlements: list[dict[str, str]] = []
        self.refunds: list[dict[str, str]] = []
        self._seq = 0
        self._utr_seq = 900000000000
        self._settle_seq = 0
        self.future_cash: dict[date, dict[str, int]] = {}
        self.holidays = {self._year_date(m, d) for (m, d) in REPUBLIC_DAYS}
        self.festival_window = {  # Diwali-ish surge, day -> (receipt_mult, pay_mult)
            self._year_date(10, 25) + timedelta(days=k): (1.25 if k < 0 else 0.75, 1.3) for k in range(-9, 4)
        }

    # ------------------------------------------------------------------ helpers
    def _year_date(self, month: int, day: int) -> date:
        return date(self.as_of.year, month, day)

    def _next_utr(self) -> str:
        self._utr_seq += self.rng.randint(1, 97)
        return str(self._utr_seq)

    def _is_holiday(self, d: date) -> bool:
        return d.weekday() == 6 or d in self.holidays

    def _roll_forward(self, d: date) -> date:
        while self._is_holiday(d):
            d += timedelta(days=1)
        return d

    def season(self, d: date, receipts: bool) -> float:
        f = WEEKEND_RECEIPT_FACTOR[d.weekday()] if receipts else (0.6 if d.weekday() >= 5 else 1.0)
        f *= MONTH_FACTOR[d.month]
        adj = self.festival_window.get(d)
        if adj:
            f *= adj[0] if receipts else adj[1]
        if d.month in (3, 6, 9, 12) and d.day >= 25:
            f *= 1.35 if receipts else 1.15  # quarter-end collection / accrual push
        return f

    def _make_party(self, kind: str, i: int) -> Party:
        rng = self.rng
        first = rng.choice(FIRST) if i < len(FIRST) else f"{rng.choice(FIRST)} {rng.choice(FIRST)}"
        second = SECOND[(i * 7 + 3) % len(SECOND)]
        legal = rng.choice(LEGAL)
        name = f"{first} {second}"
        city = rng.choice(CITIES)
        aliases = [f"{first[:4].upper()}{second.split()[0][:3].upper()}", f"{first} {second.split()[0]}"]
        if kind == "AR":
            terms = rng.choice([0, 15, 30, 30, 45, 60])
            delay = rng.uniform(-5.0, 12.0)
            pay_prob = rng.uniform(0.86, 0.99)
            size_mu, size_sd = rng.uniform(120_000, 900_000), rng.uniform(0.45, 0.95)
            channel = rng.choices(["NEFT", "RTGS", "UPI", "CHEQUE"], weights=[58, 12, 24, 6])[0]
        else:
            terms = rng.choice([15, 30, 45, 60, 60])
            delay = rng.uniform(-2.0, 9.0)
            pay_prob = rng.uniform(0.97, 1.0)
            size_mu, size_sd = rng.uniform(60_000, 480_000), rng.uniform(0.4, 0.8)
            channel = rng.choices(["NEFT", "RTGS", "UPI", "CHEQUE"], weights=[64, 8, 20, 8])[0]
        return Party(
            code=f"{'CUST' if kind == 'AR' else 'VEND'}-{i + 1:03d}",
            name=name,
            legal=legal,
            aliases=aliases,
            terms=terms,
            delay_mu=delay,
            delay_sd=rng.uniform(2.0, 9.0),
            pay_prob=pay_prob,
            size_mu=size_mu,
            size_sd=size_sd,
            channel=channel,
            kind=kind,
            region=city,
        )

    def _name_variant(self, party: Party) -> str:
        """Render a counterparty name the way a bank narration actually does it."""
        rng = self.rng
        pick = rng.random()
        if pick < 0.55:
            base = f"{party.name} {party.legal}"
        elif pick < 0.75:
            base = f"{party.name} {rng.choice(LEGAL)}"
        elif pick < 0.88:
            base = f"{party.name} {party.region.upper()[:3]}" if hasattr(party, 'region') else party.name
        else:
            base = rng.choice(party.aliases)
        if " " not in base:
            base = f"{base} {party.name.split()[-1]}"
        base = base.upper()
        if rng.random() < self.noise["typo"]:
            pos = rng.randrange(2, max(3, len(base) - 2))
            base = base[:pos] + rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + base[pos + 1 :]
        if rng.random() < self.noise["truncated_name"]:
            base = base[: rng.randrange(10, max(11, len(base) - 4))]
        if rng.random() < 0.25:
            base = " ".join(base.split()[:2]) + " " + " ".join(base.split()[2:])
        return base

    def _narration(self, party: Party, docs: list[Doc], amount_paise: int, direction: int, utr: str) -> str:
        rng = self.rng
        bank = rng.choice(BANK_CODES)
        show_doc = rng.random() < self.noise["narration_has_doc"]
        doc_str = ",".join(d.number for d in docs if show_doc)
        doc_first = bool(doc_str) and rng.random() < 0.5
        name = self._name_variant(party)
        purpose = rng.choice(PURPOSE)
        amt = f"INR {Decimal(amount_paise) / INR:.2f}"
        if party.channel == "UPI":
            ref = str(rng.randrange(10 ** 11, 10 ** 12 - 1))
            vpa = f"{party.aliases[0].lower()[:14]}@{rng.choice(['ybl', 'okaxis', 'okhdfcbank', 'paytm'])}"
            n = f"UPI/HDFC0001234/{ref}/{doc_str}/{vpa}/{name}" if doc_first else f"UPI/HDFC0001234/{ref}/{vpa}/{name}"
            if doc_str and not doc_first:
                n += f"/{doc_str}"
            return n[:90]
        if party.channel == "CHEQUE":
            return f"{doc_str + ' ' if doc_first and doc_str else ''}CHQ CLG {name} {' '.join(doc_str.split(','))}"[:90]
        if party.channel == "RTGS":
            pre = f"{doc_str} " if doc_first and doc_str else ""
            suf = f" {doc_str}" if doc_str and not doc_first else ""
            return f"{pre}RTGS {bank} {utr} {name} {amt} {purpose}{suf}"[:90]
        lead = "NEFT CR" if direction > 0 else "NEFT DR"
        pre = f"{doc_str} {lead}" if doc_first and doc_str else lead
        n = f"{pre} {bank} {utr} FROM {name} {purpose} {amt}"
        if doc_str and not doc_first:
            n += f" {doc_str}"
        return n[:90]

    def _size(self, party: Party) -> int:
        rupee = max(8_000, min(6_000_000, math.exp(self.rng.gauss(math.log(party.size_mu), party.size_sd))))
        return int(round(rupee) * INR)

    # ------------------------------------------------------------------ world build
    def build(self) -> None:
        rng = self.rng
        day = self.start
        while day <= self.as_of + timedelta(days=self.horizon_days):
            if day.weekday() == 6 and rng.random() < 0.6:
                day += timedelta(days=1)
                continue
            for kind, parties, per_day in (
                ("AR", self.customers, self.invoices_per_day),
                ("AP", self.vendors, self.bills_per_day),
            ):
                lam = per_day * self.season(day, receipts=(kind == "AR"))
                n = max(0, int(round(rng.gauss(lam, math.sqrt(lam) * 0.8))))
                for _ in range(n):
                    self.docs.append(self._make_doc(kind, rng.choice(parties), day))
            day += timedelta(days=1)

        self._add_recurring_docs()
        self._assign_lumpsum_groups()
        self._plan_settlements()  # gateway payments before doc->event resolution
        self._resolve_docs()
        self._stamp_status()

    def _next_doc_no(self, kind: str) -> str:
        self._seq += 1
        pre = "INV" if kind == "AR" else ("RT" if self.rng.random() < 0.5 else "VB")
        return f"{pre}-{self.as_of.year}-{self._seq:06d}"

    def _make_doc(self, kind: str, party: Party, when: date) -> Doc:
        gross = self._size(party)
        gst_rate = 0.18 if kind == "AR" else self.rng.choice([0.18, 0.18, 0.12, 0.05])
        gst = int((Decimal(gross) / INR * Decimal(gst_rate)).quantize(Decimal("1"), ROUND_HALF_UP)) * INR
        net = gross
        due = self._roll_forward(when + timedelta(days=party.terms or 7))
        d = Doc(
            number=self._next_doc_no(kind),
            kind=kind,
            party=party,
            doc_date=when,
            due=due,
            gross_paise=gross,
            gst_paise=gst,
            net_paise=net,
            notes="tax invoice" if kind == "AR" else "purchase bill",
            doc_id="",
        )
        d.doc_id = f"{kind}-{d.number}"
        return d

    def _add_recurring_docs(self) -> None:
        """Rent / salary / GST / insurance: real recurring outflows with predictable amounts."""
        day = self.start.replace(day=1)
        seq = 0
        while day <= self.as_of + timedelta(days=self.horizon_days + 5):
            seq += 1
            for offset, label, amount, counterparty in (
                (0, "SALARY", 3_450_000 * INR, "Acme Industries Payroll"),
                (4, "RENT", 780_000 * INR, "Skyline Estates Pvt Ltd"),
                (2, "EMI", 1_250_000 * INR, "State Bank Equip Loan"),
            ):
                when = self._roll_forward(day + timedelta(days=offset))
                for month in range(1):
                    d = Doc(
                        number=f"{label}-{when:%Y%m}",
                        kind="AP",
                        party=Party(
                            code=f"RECUR-{label}",
                            name=counterparty,
                            legal="",
                            aliases=[counterparty.split()[0]],
                            terms=0,
                            delay_mu=0,
                            delay_sd=0.4,
                            pay_prob=1.0,
                            size_mu=amount // INR,
                            size_sd=0.01,
                            channel="NEFT",
                            kind="AP",
                            region="",
                        ),
                        doc_date=when - timedelta(days=2),
                        due=when,
                        gross_paise=amount,
                        gst_paise=0,
                        net_paise=amount,
                        notes=f"recurring {label.lower()}",
                    )
                    d.doc_id = f"AP-{d.number}-{month}"
                    self.docs.append(d)
            if day.month in (4, 7, 10, 1):  # GST + TDS filing month
                when = self._roll_forward(day + timedelta(days=5))
                amt = int(self.rng.uniform(2.4e6, 4.8e6)) * INR
                d = Doc(
                    number=f"GST-{when:%Y%m}",
                    kind="AP",
                    party=Party("RECUR-GST", "Income Tax GST Deposits", "", ["GST"], 0, 0, 0.2, 1.0, amt / INR, 0.02, "NEFT", "AP", ""),
                    doc_date=when - timedelta(days=4),
                    due=when,
                    gross_paise=amt,
                    gst_paise=0,
                    net_paise=amt,
                    notes="statutory",
                )
                d.doc_id = f"AP-{d.number}"
                self.docs.append(d)
            day = (day + timedelta(days=32)).replace(day=1)

    def _assign_lumpsum_groups(self) -> None:
        """Bundle some small AR invoices into single aggregate customer payments."""
        by_party: dict[str, list[Doc]] = {}
        for d in self.docs:
            if d.kind == "AR":
                by_party.setdefault(d.party.code, []).append(d)
        gid = 0
        for docs in by_party.values():
            docs.sort(key=lambda x: x.due)
            i = 0
            while i < len(docs) - 1:
                if self.rng.random() < self.noise["lumpsum"] and docs[i].gross_paise < 900_000 * INR:
                    k = i
                    total = 0
                    group = []
                    while k < len(docs) and len(group) < 3 and total + docs[k].gross_paise <= 4_000_000 * INR:
                        if docs[k].lumpsum_group is None:
                            group.append(docs[k])
                            total += docs[k].gross_paise
                        k += 1
                    if len(group) >= 2:
                        gid += 1
                        for g in group:
                            g.lumpsum_group = gid
                    i = k
                else:
                    i += 1

    def _resolve_docs(self) -> None:
        """Turn documents into scheduled bank events - this creates the ground truth.

        Only payments whose scheduled date is <= as_of become historical bank lines;
        everything later is kept in `future_cash` so the forecaster can be scored against
        a future it could not have seen.
        """
        rng = self.rng
        groups: dict[int, list[Doc]] = {}
        solo: list[Doc] = []
        for d in self.docs:
            if d.lumpsum_group is not None:
                groups.setdefault(d.lumpsum_group, []).append(d)
            else:
                solo.append(d)

        for d in solo:
            self._schedule_single(d)
        for gid, docs in groups.items():
            # a customer settling three invoices in one transfer pays them on ONE day: schedule the
            # group jointly, otherwise the aggregate-receipt case this tier exists for never happens
            lead = max(docs, key=lambda x: x.due)
            if rng.random() < 1.0 - min(d.party.pay_prob for d in docs):
                for d in docs:
                    d.paid_on = None
            else:
                jitter = int(round(rng.gauss(lead.party.delay_mu, lead.party.delay_sd)))
                pay_on = self._roll_forward(lead.due + timedelta(days=jitter))
                for d in docs:
                    amt = d.net_paise
                    if self.rng.random() < self.noise["short_deduction"] * 0.5:
                        ded = int(self.rng.uniform(0.004, 0.03) * amt // INR) * INR
                        d.short_deduction_paise = ded
                        amt -= ded
                        d.notes = (d.notes + " short deduction").strip()
                    d.paid_on = pay_on if pay_on <= self.as_of else pay_on
                    d.paid_amount_paise = amt
            past = [x for x in docs if x.paid_on and x.paid_on <= self.as_of]
            if len(past) < 2:
                continue  # singles already emitted by _schedule_single; partial groups stay honest
            if len(past) < len([x for x in docs if x.paid_on]):
                # part of the group is scheduled after as_of: emit the past ones individually rather
                # than leaking a future document into a historical bank line
                for d in past:
                    self._emit_event([d], d.paid_on, d.paid_amount_paise, "matchable")
                continue
            amount = sum(x.paid_amount_paise for x in past)
            self._emit_event(past, pay_on := past[0].paid_on, amount, "matchable_lumpsum")

    def _schedule_single(self, d: Doc, emit: bool = True) -> None:
        rng = self.rng
        past = d.due <= self.as_of
        default_prob = 1.0 - d.party.pay_prob
        if d.due > self.as_of:  # a future regime shift no model can know
            default_prob += self.noise["regime_shift_extra_default"]
        if rng.random() < default_prob * (1.6 if d.due.month == 4 else 1.0):
            d.paid_on = None  # unpaid / disputed
            return
        jitter = int(round(rng.gauss(d.party.delay_mu, d.party.delay_sd)))
        when = self._roll_forward(d.due + timedelta(days=jitter))
        if when < d.doc_date:
            when = self._roll_forward(d.doc_date + timedelta(days=rng.randint(0, 3)))
        amount = d.net_paise
        if rng.random() < self.noise["short_deduction"]:
            d.short_deduction_paise = int(rng.uniform(0.004, 0.06) * amount // INR) * INR
            amount -= d.short_deduction_paise
            d.notes = (d.notes + " short deduction").strip()
        d.paid_on = when
        d.paid_amount_paise = amount
        if emit and when <= self.as_of:
            truth = "matchable_amount_mismatch" if d.short_deduction_paise else "matchable"
            self._emit_event([d], when, amount, truth)

    def _emit_event(self, docs: list[Doc], when: date, amount_paise: int, truth_kind: str) -> None:
        if amount_paise <= 0 or when is None:
            return
        rng = self.rng
        lead = docs[0]
        utr = self._next_utr()
        direction = 1 if lead.kind == "AR" else -1
        narration = self._narration(lead.party, docs, amount_paise, direction, utr)
        ev = BankEvent(when, direction * amount_paise, narration, utr, tuple(d.doc_id for d in docs), truth_kind)
        self.events.append(ev)
        if truth_kind in ("matchable", "matchable_amount_mismatch", "matchable_lumpsum") and lead.kind == "AR":
            for d in docs:
                if d.paid_on and d.paid_on <= self.as_of:
                    if rng.random() < self.noise["advice_rate"]:
                        self.advices.append(
                            {
                                "advice_id": f"ADV-{len(self.advices) + 1:06d}",
                                "invoice_no": d.number if rng.random() < 0.75 else "",
                                "payer_name": self._name_variant(d.party),
                                "amount": rupees(d.paid_amount_paise or d.net_paise),
                                "notified_on": (d.paid_on - timedelta(days=rng.randint(0, 2))).isoformat(),
                                "utr": utr if rng.random() < 0.6 else "",
                                "narration_hint": "",
                            }
                        )
        if rng.random() < self.noise["duplicate_line"]:
            self.events.append(
                BankEvent(ev.when, ev.amount_paise, ev.narration, ev.utr, tuple(), "expected_unmatched_duplicate", duplicate_of=ev.narration)
            )

    def _plan_settlements(self) -> None:
        """Razorpay collections -> refunds -> T+2 settlement -> one bank credit per payout.

        Fee maths follows the published schedule (2% MDR card/upi, 0.1 paise TMN, 18% GST on
        fee+TMN, TDS 2% under 194H on the commission) and refunds are debited from the batch,
        which is exactly what breaks real settlement reconciliation.
        """
        rng = self.rng
        day = self.start
        tmn_rate, gst_rate, tds_rate = 0.00001, 0.18, 0.02
        method_rates = {"upi_apps": 0.02, "card": 0.02, "netbanking": 0.009, "wallet": 0.02}
        self.refunds: list[dict[str, str]] = []
        settle_seq = 0
        pending_refund_rows: list[dict[str, str]] = []
        while day <= self.as_of:
            n_txn = max(0, int(rng.gauss(38 * self.season(day, receipts=True), 9)))
            gross = 0
            declared_fee = 0
            day_payments: list[dict[str, str]] = []
            for _ in range(n_txn):
                amt = int(round(rng.uniform(450, 5_400))) * INR
                gross += amt
                method = rng.choices(list(method_rates), weights=[46, 30, 14, 10])[0]
                fee = int(amt * method_rates[method])
                declared_fee += fee
                day_payments.append(
                    {
                        "payment_id": f"pay_{day:%y%m%d}{len(self.gateway_payments) + len(day_payments):06d}",
                        "order_id": f"order_{day:%y%m%d}{settle_seq:03d}",
                        "amount": rupees(amt),
                        "method": method,
                        "fee": rupees(fee),
                        "captured_at": f"{day.isoformat()}T{rng.randint(8, 22):02d}:{rng.randint(0, 59):02d}:00+05:30",
                        "status": "captured",
                        "settlement_id": "",
                        "notes_invoice_no": "",
                    }
                )
            self.gateway_payments.extend(day_payments)
            if gross <= 0:
                day += timedelta(days=1)
                continue
            # refunds raised on this day's payments (chargebacks are folded into the same debit)
            refund_total = 0
            for p in day_payments:
                if rng.random() < 0.03:
                    amt = int(float(p["amount"]) * INR * rng.uniform(0.3, 1.0))
                    refund_total += amt
                    pending_refund_rows.append(
                        {
                            "refund_id": f"rfnd_{len(self.refunds) + len(pending_refund_rows):07d}",
                            "payment_id": p["payment_id"],
                            "amount": rupees(amt),
                            "created_at": (day + timedelta(days=1)).isoformat(),
                            "settlement_id": "",
                            "status": "processed",
                        }
                    )
            settle_day = self._roll_forward(day + timedelta(days=2))
            # 3% of batches are mis-tiered by the gateway: commission charged at the flat card
            # rate instead of the per-method blend. Exactly the kind of overbilling a business
            # never finds by hand, and pure arithmetic to find by machine.
            fee = int(gross * 0.02) if rng.random() < 0.03 else declared_fee
            tmn = int(gross * tmn_rate)
            gst = int((fee + tmn) * gst_rate)
            tds = int(fee * tds_rate)
            net = gross - fee - tmn - gst - tds - refund_total
            drift = int(gross * rng.uniform(0.001, 0.004)) if rng.random() < 0.12 else 0  # seeded break
            if drift:
                net -= drift
            settle_seq += 1
            sid = f"setl_{settle_seq:08d}"
            self.settlements.append(
                {
                    "settlement_id": sid,
                    "settled_on": settle_day.isoformat(),
                    "payout_utr": self._next_utr(),
                    "batch_type": "regular",
                    "txn_count": str(n_txn),
                    "gross_amount": rupees(gross),
                    "commission_amount": rupees(fee),
                    "tmn_amount": rupees(tmn),
                    "gst_amount": rupees(gst),
                    "tds_amount": rupees(tds),
                    "net_amount": rupees(net),
                    "scheduled_id": f"stl_sch_{settle_seq:06d}",
                }
            )
            for p in day_payments:
                p["settlement_id"] = sid
            for r in pending_refund_rows:
                r["settlement_id"] = sid
            # 8% of batches: the refund evidence is missing from the export -> an unexplained gap
            if rng.random() < 0.08:
                pending_refund_rows = []
            self.refunds.extend(pending_refund_rows)
            pending_refund_rows = []
            if settle_day <= self.as_of:
                self.events.append(
                    BankEvent(
                        settle_day,
                        net,
                        f"RAZORPAY PAYMENTS GATEWAY SETTLEMENT {sid} UTR {self.settlements[-1]['payout_utr']}",
                        self.settlements[-1]["payout_utr"],
                        (f"SETL-{sid}",),
                        "gateway_settlement",
                    )
                )
            else:
                self.future_cash.setdefault(settle_day, {"in": 0, "out": 0})["in"] += net
            day += timedelta(days=1)

    def _stamp_status(self) -> None:
        """Ledger state as the ERP would actually see it on `as_of`.

        AR receipts are NOT posted yet - posting them from the bank feed is exactly the
        manual work being automated, so paid-in-history invoices stay `open`/`overdue`
        with paid_amount = 0 and remain candidates for the reconciler.
        AP payments ARE initiated from the ERP, so settled bills are `paid` and the
        reconciler's job on those lines is verification (did the payment we booked really
        leave the bank account, at the right amount).
        """
        for d in self.docs:
            settled_in_history = bool(d.paid_on and d.paid_on <= self.as_of)
            if d.kind == "AP" and settled_in_history:
                d.status_at_asof = "partial" if d.short_deduction_paise else "paid"
                d.paid_amount_paise = d.paid_amount_paise or d.net_paise
            elif d.lumpsum_group is not None and settled_in_history:
                d.status_at_asof = "open"  # aggregate receipt: nothing posted per-line yet
                d.paid_amount_paise = 0
            elif d.due < self.as_of:
                d.status_at_asof = "overdue"
                d.paid_amount_paise = 0
            else:
                d.status_at_asof = "open"
                d.paid_amount_paise = 0
            if d.paid_on and d.paid_on > self.as_of:  # future plan -> evaluation only
                bucket = self.future_cash.setdefault(d.paid_on, {"in": 0, "out": 0})
                bucket["in" if d.kind == "AR" else "out"] += d.paid_amount_paise or d.net_paise

    # ------------------------------------------------------------------ emission
    def emit(self, out_dir) -> dict[str, object]:
        from pathlib import Path

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        rng = self.rng

        # 1. bank statement: history only
        hist = [e for e in self.events if e.when <= self.as_of]
        hist.sort(key=lambda e: (e.when, e.amount_paise))
        bank_rows: list[dict[str, object]] = []
        truth_rows: list[dict[str, object]] = []
        for i, e in enumerate(hist, start=1):
            line_id = f"BL-{i:06d}"
            bank_rows.append(
                {
                    "line_id": line_id,
                    "txn_date": e.when.isoformat(),
                    "value_date": e.when.isoformat(),
                    "narration": e.narration,
                    "utr": e.utr,
                    "amount_in": rupees(e.amount_paise) if e.amount_paise > 0 else "",
                    "amount_out": rupees(-e.amount_paise) if e.amount_paise < 0 else "",
                    "account": "HDFC-Current-4421",
                }
            )
            truth_rows.append(
                {
                    "line_id": line_id,
                    "doc_ids": ";".join(e.doc_ids),
                    "truth_kind": e.truth_kind,
                    "txn_date": e.when.isoformat(),
                    "amount_paise": e.amount_paise,
                    "narration": e.narration,
                }
            )
        # noise the truth files explain: bank charges, interest, a reversal, unknown credits
        extras = []
        for j, (label, amt, kind) in enumerate(
            [
                ("NEFT OUTWARD PROCESSING FEE HDFC0001234", -1_500 * INR, "expected_unmatched_charge"),
                ("SERVICE CHG FOR MONTHLY MAINTAINANCE", -2_950 * INR, "expected_unmatched_charge"),
                ("CREDIT INTEREST ON SAVINGS BALANCE QUARTERLY", 184_500 * INR, "expected_unmatched_interest"),
                ("CHEQUE RETURN CHRG INVALID SIGNATURE", -500 * INR, "expected_unmatched_charge"),
                ("UPI CR @SUNRISEMARTS@YBL 128734567890123 RANDOM", 250_000 * INR, "expected_unmatched_unknown"),
                ("NEFT CR HDFC0001234 991234567890123 FROM UNKNOWN PARTY XZ", 1_000_000 * INR, "expected_unmatched_unknown"),
            ],
            start=0,
        ):
            when = self.as_of - timedelta(days=rng.randint(1, 90))
            extras.append(BankEvent(when, amt, label, self._next_utr(), tuple(), kind))
        for j, e in enumerate(extras, start=1):
            line_id = f"BL-{len(bank_rows) + j:06d}"
            bank_rows.append(
                {
                    "line_id": line_id,
                    "txn_date": e.when.isoformat(),
                    "value_date": e.when.isoformat(),
                    "narration": e.narration,
                    "utr": e.utr,
                    "amount_in": rupees(e.amount_paise) if e.amount_paise > 0 else "",
                    "amount_out": rupees(-e.amount_paise) if e.amount_paise < 0 else "",
                    "account": "HDFC-Current-4421",
                }
            )
            truth_rows.append(
                {
                    "line_id": line_id,
                    "doc_ids": "",
                    "truth_kind": e.truth_kind,
                    "txn_date": e.when.isoformat(),
                    "amount_paise": e.amount_paise,
                    "narration": e.narration,
                }
            )
        _write_csv(out / "bank_statement.csv", bank_rows)
        _write_csv(out / "truth_matches.csv", truth_rows)

        # 2. AR / AP masters
        def doc_row(d: Doc, i: int) -> dict[str, str]:
            paid = d.paid_amount_paise if (d.paid_on and d.paid_on <= self.as_of) else 0
            return {
                "document_id": d.doc_id,
                "document_no": d.number,
                "counterparty": f"{d.party.name} {d.party.legal}".strip(),
                "counterparty_code": d.party.code,
                "document_date": d.doc_date.isoformat(),
                "due_date": d.due.isoformat(),
                "gross_amount": rupees(d.gross_paise),
                "gst_rate": f"{0.18 if d.kind == 'AR' else 0.18:.2f}",
                "gst_amount": rupees(d.gst_paise),
                "net_amount": rupees(d.net_paise),
                "paid_amount": rupees(paid),
                "status": d.status_at_asof,
                "currency": "INR",
                "notes": d.notes,
            }

        # Only documents that exist at as_of go into the ledger files. The rest of the world's
        # plan stays in truth_*.csv - emitting future invoices would leak the answer to the model.
        ledger_docs = [d for d in self.docs if d.doc_date <= self.as_of]
        _write_csv(out / "invoices.csv", [doc_row(d, i) for i, d in enumerate(d0 for d0 in ledger_docs if d0.kind == "AR")])
        _write_csv(out / "bills.csv", [doc_row(d, i) for i, d in enumerate(d0 for d0 in ledger_docs if d0.kind == "AP")])
        _write_csv(out / "payment_advices.csv", self.advices)
        _write_csv(out / "razorpay_payments.csv", self.gateway_payments)
        _write_csv(out / "razorpay_settlements.csv", self.settlements)
        _write_csv(out / "razorpay_refunds.csv", self.refunds)
        _write_csv(out / "opening_balance.csv", [{"as_of": self.as_of.isoformat(), "balance": rupees(int(self.open_balance_rupees * INR))}])

        # 3. evaluation-only ground truth
        future_rows = [
            {"day": k.isoformat(), "expected_in_paise": v["in"], "expected_out_paise": v["out"], "net_paise": v["in"] - v["out"]}
            for k, v in sorted(self.future_cash.items())
            if k > self.as_of
        ]
        _write_csv(out / "truth_future_cash.csv", future_rows)
        # per-document expected settlement day (for contract-level scoring)
        _write_csv(
            out / "truth_schedule.csv",
            [
                {
                    "doc_id": d.doc_id,
                    "document_no": d.number,
                    "kind": d.kind,
                    "counterparty_code": d.party.code,
                    "due": d.due.isoformat(),
                    "scheduled_pay": (d.paid_on or date(1970, 1, 1)).isoformat(),
                    "amount_paise": d.paid_amount_paise or d.net_paise,
                    "paid": "1" if d.paid_on else "0",
                }
                for d in self.docs
            ],
        )
        meta = {
            "seed": self.seed,
            "as_of": self.as_of.isoformat(),
            "history_start": self.start.isoformat(),
            "horizon_days": self.horizon_days,
            "customers": len(self.customers),
            "vendors": len(self.vendors),
            "documents": len(self.docs),
            "bank_lines_history": len(bank_rows),
            "bank_lines_future_plan": len(self.future_cash),
            "noise": self.noise,
            "note": (
                "truth_matches.csv / truth_schedule.csv / truth_future_cash.csv are EVALUATION ONLY. "
                "Never pass the data dir containing them to the reconciler as a source of truth."
            ),
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2))
        return meta


def _write_csv(path, rows: list[dict[str, object]]) -> None:
    import csv as _csv

    if not rows:
        path.write_text("")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
