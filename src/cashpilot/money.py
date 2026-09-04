"""Money and date primitives.

Everything monetary in Cashpilot is an `int` number of paise. Floats are only ever
used for display and for statistical quantities. This is a deliberate choice: the
first version of this repo compared reconciliation amounts as float rupees and
produced ~600 phantom "amount mismatch" exceptions on a 2000-line statement, all of
them 0.01 paise rounding artefacts of binary floating point. See docs/FAILURES.md.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

PAISE_PER_RUPEE = 100
_NUMERIC = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$")


def parse_money_paise(raw: object) -> int | None:
    """Parse a bank/ledger amount string into integer paise.

    Handles: "1,23,456.78", "₹45,000.00", "(2,500.50)" (debit style),
    "45000", "", None, 45000.0. Returns None when there is nothing numeric.
    """
    if raw is None:
        return None
    if isinstance(raw, (int,)) and not isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, float):
        return int((Decimal(str(raw)) * PAISE_PER_RUPEE).quantize(Decimal("1"), ROUND_HALF_UP))
    if isinstance(raw, Decimal):
        return int((raw * PAISE_PER_RUPEE).quantize(Decimal("1"), ROUND_HALF_UP))

    text = str(raw).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[₹INR\s,]", "", text, flags=re.IGNORECASE)
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if not text or not _NUMERIC.match(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    paise = int((value * PAISE_PER_RUPEE).quantize(Decimal("1"), ROUND_HALF_UP))
    return -paise if negative else paise


def paise_to_decimal(paise: int) -> Decimal:
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def fmt_inr(paise: int | None, *, sign: bool = False) -> str:
    """Format paise as Indian-grouped rupees, e.g. ₹1,23,456.78."""
    if paise is None:
        return "-"
    value = paise_to_decimal(paise)
    sign_str = "-" if value < 0 else ("+" if sign and value > 0 else "")
    value = abs(value)
    rupees, paise_part = str(value).split(".")
    # Indian digit grouping: last group 3 digits, then pairs.
    if len(rupees) > 3:
        head, tail = rupees[:-3], rupees[-3:]
        chunks = []
        while len(head) > 2:
            chunks.insert(0, head[-2:])
            head = head[:-2]
        if head:
            chunks.insert(0, head)
        rupees = ",".join(chunks + [tail])
    return f"{sign_str}₹{rupees}.{paise_part}"


def money_or_zero(raw: object) -> int:
    return parse_money_paise(raw) or 0


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d %B %Y",
    "%Y/%m/%d",
    "%d%b%y",
)


def parse_date(raw: object) -> date | None:
    """Deterministic multi-format date parser (no fuzzy LLM, no dateutil relative dates).

    Indian bank statements use DD/MM/YYYY; invoices use YYYY-MM-DD; the Razorpay
    export uses ISO timestamps. All three must land on the same `datetime.date`.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # ISO timestamp such as 2026-08-30T14:03:22+05:30 (the gateway export) - date part only
    head = re.split(r"[T ]", text, maxsplit=1)[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})$", head)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    iso = iso_date_head(text)
    return iso.date() if iso else None


def iso_date_head(text: str) -> datetime | None:
    """Accept a leading YYYY-MM-DD of any timestamp-ish string (ISO with or without tz)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def date_range(start: date, end: date):
    d = start
    step = 1 if end >= start else -1
    delta = timedelta_days(step)
    while (step > 0 and d <= end) or (step < 0 and d >= end):
        yield d
        d += delta


def timedelta_days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def days_between(a: date, b: date) -> int:
    """Signed day count b - a."""
    return (b - a).days
