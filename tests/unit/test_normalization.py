from datetime import date
from decimal import Decimal

import pytest

from datamate.data.normalization import identifier, normalized_text, parse_date, parse_number


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234.567", "1234567"),
        ("1.234,50", "1234.50"),
        ("1,25", "1.25"),
        ("1,2tr", "1200000"),
        ("15k", "15000"),
        ("12000 ₫", "12000"),
        ("12,4%", "12.4"),
        ("-42", "-42"),
        ("0", "0"),
        ("1.25", "1.25"),
    ],
)
def test_numbers_preserve_raw_and_precision(raw: str, expected: str) -> None:
    result = parse_number(raw)
    assert result.value == Decimal(expected)
    assert result.raw == raw


@pytest.mark.parametrize("raw", ["1,234", "1.234", "1,234,567", "NaN", "inf", "1..2", "abc"])
def test_ambiguous_or_invalid_numbers(raw: str) -> None:
    assert parse_number(raw).value is None
    assert parse_number(raw).warning


def test_dates_do_not_guess() -> None:
    assert parse_date("03/04/2026").warning == "ambiguous_date"
    assert parse_date("03/04/2026", day_first=True).value == "2026-04-03"
    assert parse_date("31/12/2025").value == "2025-12-31"
    assert parse_date("2025-02-29").warning == "invalid_date"
    assert parse_date(date(2024, 2, 29)).value == "2024-02-29"


def test_vietnamese_identity() -> None:
    assert normalized_text("  CÀ   PHÊ SỮA ", accents=True) == "ca phe sua"
    assert identifier("Mã hóa đơn") == "ma_hoa_don"


def test_decimal_context_never_rounds_during_parse() -> None:
    raw = "1234567890123456789012345678.123456789"
    assert parse_number(raw).value == Decimal(raw)
