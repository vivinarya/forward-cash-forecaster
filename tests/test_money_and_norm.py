"""Money, date and name normalisation - the deterministic floor everything else stands on."""

from __future__ import annotations

from datetime import date

import pytest

from cashpilot.money import days_between, fmt_inr, paise_to_decimal, parse_date, parse_money_paise
from cashpilot.norm import norm_name, similarity, similarity_norm


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,23,456.78", 12345678),
        ("₹45,000.00", 4500000),
        ("INR 45,000", 4500000),
        ("(2,500.50)", -250050),  # debit style in parentheses
        ("-500", -50000),
        ("45000", 4500000),
        ("45,000.5", 4500050),
        (" 12,345.678 ", 1234568),  # ROUND_HALF_UP at the paisa
        (45000.0, 4500000),
        ("0", 0),
    ],
)
def test_parse_money_paise_accepts_the_formats_bank_feeds_actually_use(raw, expected):
    assert parse_money_paise(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "not an amount", "CR/DR", "1,2A3"])
def test_parse_money_paise_returns_none_instead_of_guessing(raw):
    """A silent 0 here would reconcile a line that was never paid. None becomes a PARSE_FAILURE."""
    assert parse_money_paise(raw) is None


def test_int_is_already_paise_and_is_not_rescaled():
    """Internally money is always integer paise; an int input is trusted, not multiplied."""
    assert parse_money_paise(1000) == 1000
    from decimal import Decimal

    assert paise_to_decimal(12345678) == Decimal("123456.78")
    assert paise_to_decimal(-250050) == Decimal("-2500.50")


def test_fmt_inr_uses_indian_grouping():
    assert fmt_inr(12345678) == "₹1,23,456.78"
    assert fmt_inr(1000000000) == "₹1,00,00,000.00"
    assert fmt_inr(-250050) == "-₹2,500.50"
    assert fmt_inr(None) == "-"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-05", date(2026, 9, 5)),
        ("05/09/2026", date(2026, 9, 5)),  # DD/MM/YYYY, the Indian convention
        ("5/9/26", date(2026, 9, 5)),
        ("2026-09-05T14:03:22+05:30", date(2026, 9, 5)),  # gateway ISO timestamp
        ("2026-09-05 00:00:00", date(2026, 9, 5)),
        ("garbage", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_handles_the_three_sources_without_a_library(raw, expected):
    assert parse_date(raw) == expected


def test_days_between_is_signed_and_inclusive_of_direction():
    assert days_between(date(2026, 9, 1), date(2026, 9, 5)) == 4
    assert days_between(date(2026, 9, 5), date(2026, 9, 1)) == -4


def test_name_normalisation_absorbs_punctuation_and_case():
    assert norm_name("  Acme   PVT. Ltd. ") == norm_name("acme pvt ltd")


def test_similarity_survives_truncation_and_typos():
    # narrations truncate; invoice masters do not. This is the exact failure the fuzzy tier exists for.
    assert similarity("MERIDIAN ROOFING PVT LTD", "MERIDIAN ROOFING PVT LT") > 0.85
    assert similarity("Bengaluru Spices Private Limited", "BANGALORE SPICES PVT LTD") > 0.5
    assert similarity("Acme Tools", "Zenith Logistics") < 0.35
    assert similarity_norm("", "") == 0.0
    assert 0.0 <= similarity("x y", "x z") <= 1.0
