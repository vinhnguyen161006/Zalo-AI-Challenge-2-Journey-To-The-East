from datetime import date
from decimal import Decimal

import pytest

from datamate.data.normalization import (
    date_day_first,
    identifier,
    normalized_text,
    number_style,
    parse_date,
    parse_number,
)


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
        ("1,234,567", "1234567"),
        ("1,234,567.89", "1234567.89"),
        ("0.125", "0.125"),
        ("0,125", "0.125"),
        ("12345.678", "12345.678"),
    ],
)
def test_numbers_preserve_raw_and_precision(raw: str, expected: str) -> None:
    result = parse_number(raw)
    assert result.value == Decimal(expected)
    assert result.raw == raw


@pytest.mark.parametrize("raw", ["1,234", "1.234", "1,23,456", "NaN", "inf", "1..2", "abc"])
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


@pytest.mark.parametrize(("raw", "expected"), [(0.125, "0.125"), (1.234, "1.234"), (7, "7")])
def test_native_numbers_are_never_ambiguous(raw: float, expected: str) -> None:
    result = parse_number(raw)
    assert result.value == Decimal(expected)
    assert result.warning is None


def test_non_finite_native_number_is_flagged() -> None:
    assert parse_number(float("inf")).warning == "nonfinite_numeric"


@pytest.mark.parametrize(
    ("column", "style"),
    [
        (["560,000", "1,056,000"], "en"),
        (["1.234.567", "12"], "vi"),
        (["12,5", "3"], "vi"),
        (["1.234.567", "1,234,567"], None),
        (["560,000", "4,000"], None),
    ],
)
def test_column_cells_can_prove_the_separator_style(column: list[str], style: str | None) -> None:
    assert number_style(column) == style


def test_style_resolves_single_group_cells() -> None:
    assert parse_number("560,000", style="en").value == Decimal("560000")
    assert parse_number("1.234", style="en").value == Decimal("1.234")
    assert parse_number("1,234", style="vi").value == Decimal("1.234")
    assert parse_number("1.234", style="vi").value == Decimal("1234")
    assert parse_number("560,000").warning == "ambiguous_numeric"


def test_column_date_order_comes_from_any_proving_cell() -> None:
    assert date_day_first(["03/04/2026", "25/04/2026"]) is True
    assert date_day_first(["03/04/2026", "04/25/2026"]) is False
    assert date_day_first(["25/04/2026", "04/25/2026"]) is None
    assert date_day_first(["03/04/2026"]) is None
