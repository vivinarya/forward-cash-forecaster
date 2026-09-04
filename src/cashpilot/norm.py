"""Name / narration normalisation and token extraction.

This is the module that decides what "the same payment" means. Every rule in here is
a regex or a string transform: deliberately *not* an LLM. See docs/AI_JUDGEMENT.md.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

LEGAL_SUFFIXES = {
    "PVT",
    "PRIVATE",
    "LTD",
    "LIMITED",
    "LLP",
    "INC",
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "INDIA",
    "THE",
}

# Filler tokens that appear in NEFT/IMPS/UPI narrations but carry no identity.
NARRATION_STOPWORDS = {
    "NEFT",
    "IMPS",
    "UPI",
    "RTGS",
    "SAX",
    "CIB",
    "HDFC00",
    "SBIN00",
    "ICIC00",
    "KKBK00",
    "UTIB000",
    "PAYMENT",
    "REC",
    "RECEIPT",
    "SALE",
    "INV",
    "INVOICE",
    "BILL",
    "TAX",
    "REF",
    "TRANSACTION",
    "COLLECTION",
    "DR",
    "CR",
    "VAL",
    "DIFF",
    "FROM",
    "TO",
    "FOR",
    "INR",
}

_UTR_PATTERNS = [
    re.compile(r"\b(?:UTR|NUTR|SICN|CIBN|UTI|OTH)\s*[:\-]?\s*(\d{12,17})\b", re.I),
    re.compile(r"\b(\d{16,17})\b"),  # bare 16/17 digit reference used by UPI/IMPS
]
_UPI_PATTERNE = re.compile(r"\b[\w.\-]{2,64}@[\w]{2,64}\b")
_RAZORPAY_PAY = re.compile(r"\b(?:pay|order|setl|fund|sign|token)_[0-9a-zA-Z]{8,}\b")
_AMOUNT_IN_TEXT = re.compile(r"(?:rs\.?|inr|₹)\s*([0-9,]+(?:\.[0-9]{1,2})?)", re.I)


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def norm_name(text: str | None, *, keep_suffixes: bool = False) -> str:
    """Canonical counterparty key: uppercased, ASCII-folded, punctuation-free, no legal suffix."""
    if not text:
        return ""
    s = strip_accents(str(text)).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\b\d{4,}\b", " ", s)  # stray account/UTR digits inside names
    toks = [t for t in s.split() if t]
    if not keep_suffixes:
        toks = [t for t in toks if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


def name_tokens(norm: str) -> frozenset[str]:
    return frozenset(t for t in norm.split() if t not in NARRATION_STOPWORDS and len(t) > 1)


def token_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard-ish containment: how much of the *shorter* token set is present in the other."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    # Containment-dominant: bank narrations carry extra junk (purpose text, channel words)
    # that the ledger master does not, so the smaller set being present is the strong signal.
    smaller = min(len(a), len(b))
    containment = inter / smaller
    jaccard = inter / len(a | b)
    return 0.85 * containment + 0.15 * jaccard


def edit_ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio in [0,1]; SequenceMatcher already rejects long blocks cheaply."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def similarity_norm(a: str, b: str) -> float:
    """0..1 similarity between two already-normalised names, tolerant of typos + truncation.

    Combines character-level ratio with token containment because bank narrations
    truncate long names ("MERIDIAN ROOFING PVT LT") while invoice masters do not.
    """
    if not a or not b:
        return 0.0
    char = edit_ratio(a, b)
    tok = token_overlap(name_tokens(a), name_tokens(b))
    prefix = 0.0
    if a.startswith(b[: max(6, len(b) // 2)]) or b.startswith(a[: max(6, len(a) // 2)]):
        prefix = 1.0
    blend = 0.50 * char + 0.35 * tok + 0.15 * prefix
    return min(1.0, max(char, blend))


def similarity(a: str, b: str) -> float:
    return similarity_norm(norm_name(a), norm_name(b))


def extract_utr(narration: str | None) -> str | None:
    if not narration:
        return None
    for pat in _UTR_PATTERNS:
        m = pat.search(str(narration))
        if m:
            return m.group(1)
    return None


def extract_upi_payer_handle(narration: str | None) -> str | None:
    if not narration:
        return None
    m = _UPI_PATTERNE.search(str(narration))
    return m.group(0).lower() if m else None


def extract_gateway_refs(narration: str | None) -> list[str]:
    if not narration:
        return []
    return _RAZORPAY_PAY.findall(str(narration))


def extract_amount_hint(narration: str | None) -> int | None:
    """Amounts quoted inside the free text (some banks append 'INR 45000.00')."""
    from .money import parse_money_paise

    if not narration:
        return None
    m = _AMOUNT_IN_TEXT.search(str(narration))
    return parse_money_paise(m.group(1)) if m else None


def classify_narration_kind(narration: str | None) -> str:
    """Coarse channel label - used for blocking and for explanation, never for matching."""
    text = (narration or "").upper()
    if "NEFT" in text or "CIB" in text or "SAX" in text:
        return "NEFT"
    if "IMPS" in text:
        return "IMPS"
    if "UPI" in text or "@YBL" in text or "@OKAXIS" in text or "@AXL" in text:
        return "UPI"
    if "RTGS" in text:
        return "RTGS"
    if "CHQ" in text or "CHEQUE" in text:
        return "CHEQUE"
    if "RZP" in text or "RAZORPAY" in text or "SETL_" in text:
        return "GATEWAY_SETTLEMENT"
    if "INT" in text or "INTEREST" in text:
        return "INTEREST"
    if "CHG" in text or "FEE" in text or "COMMISSION" in text or "AMC" in text:
        return "BANK_CHARGE"
    if "REVERS" in text or "CANCEL" in text or "REFUND" in text:
        return "REVERSAL"
    return "OTHER"


def invoice_tokens(narration: str | None, patterns: list[str]) -> list[str]:
    """Extract document numbers from narration using *configurable* regexes.

    Patterns live in config/recon_rules.json so a finance team can add their own
    invoice scheme without touching code. Matching is performed against the
    normalised (upper, punctuation-light) narration and against a digit-collapsed
    variant, so "INV-2026-00123", "INV202600123" and "INV/2026/00123" all work.
    """
    if not narration:
        return []
    text = str(narration).upper()
    compact = re.sub(r"[^A-Z0-9]", "", text)
    found: list[str] = []
    for pat in patterns:
        regex = re.compile(pat, re.I)
        for source in (text, compact):
            for m in regex.finditer(source):
                tok = m.group(0).upper()
                tok = tok if "-" in tok or len(tok) >= 6 else tok
                if tok not in found:
                    found.append(tok)
    return found


def doc_key(number: str | None) -> str:
    """Canonical join key for an invoice/bill number: uppercase, non-alphanumerics removed."""
    if not number:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(number).upper())


_DOC_TOKEN_RES = [
    re.compile(r"\b[A-Z]{2,6}[-/]?\d{4}[-/]?\d{3,8}\b"),
    re.compile(r"\b(?:INV|RT|VB|PO|BILL|SALARY|RENT|EMI|GST)-[A-Z0-9\-]+\b"),
    re.compile(r"\bSETL_\w+\b", re.I),
]
_CHANNEL_RES = [
    re.compile(r"\b(?:NEFT|RTGS|IMPS|UPI|CHQ|CLG|SAX|CIB|SBIN|HDFC|ICIC|KKBK|UTIB|AXIS)\b[\w./-]*"),
    re.compile(r"\b(?:FROM|TO|CR|DR|REF|VAL|INR|RS\.?|₹)\b"),
    re.compile(r"\b[A-Z0-9._\-]{2,64}@(?:YBL|OK[A-Z]+|AXL|AIRTEL|HDFCBANK|ICICI|PAYTM|SBI)\b"),
    re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\b"),  # embedded amounts
    re.compile(r"\b\d{6,}\b"),  # reference numbers
    re.compile(r"\b(?:MONTHLY|MAINTAINANCE|MAINTENANCE|PROCESSING|FEE|CHG|CHARGES|AMC|INTEREST|SETTLEMENT|GATEWAY|PAYMENT|TRANSACTION|BALANCE|QUARTERLY|REVERSAL|CANCELLED|REFUND|INVALID|SIGNATURE|RETURN)\b"),
]


def counterparty_hint(narration: str | None) -> str:
    """Best-effort counterparty name hidden inside a bank narration.

    Strips channel codes, reference numbers and amounts, then drops stopwords, so the
    remainder can be fuzzy-compared against the ledger's counterparty master. This is a
    text transform, not a model: it is reproducible and it is auditable line by line.
    """
    if not narration:
        return ""
    text = strip_accents(str(narration)).upper()
    for pat in _DOC_TOKEN_RES + _CHANNEL_RES:
        text = pat.sub(" ", text)
    toks = norm_name(text).split()
    keep = [t for t in toks if t not in NARRATION_STOPWORDS and not t.isdigit() and len(t) > 1]
    return " ".join(keep)


def channel_purpose_tags(narration: str | None) -> list[str]:
    """Keywords that identify non-AR/AP lines (bank charges, interest, reversals)."""
    text = (narration or "").upper()
    tags = []
    for tag, needles in {
        "CHARGE": ("FEE", "CHG", "CHARGES", "MAINTAIN", "AMC", "PROCESSING"),
        "INTEREST": ("INTEREST", "INT CR", "BANNER"),
        "REVERSAL": ("REVERS", "CANCEL", "RETURN", "REFUND", "DEBIT ADJ"),
        "GATEWAY": ("RAZORPAY", "GATEWAY SETTLEMENT", "SETL_"),
        "SALARY": ("SALARY", "PAYROLL"),
        "STATUTORY": ("GST", "TDS", "INCOME TAX", "TAX PAYMENT"),
    }.items():
        if any(n in text for n in needles):
            tags.append(tag)
    return tags
