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


def test_vietnamese_short_month_date_is_parsed() -> None:
    assert parse_date("1 thg 7 2025").value == "2025-07-01"
    assert parse_date("15 thg 12 2025").value == "2025-12-15"


def test_iso_datetime_text_keeps_its_time() -> None:
    assert parse_date("2025-02-01 10:30:00").value == "2025-02-01T10:30:00"


def test_excel_serial_is_parsed_only_when_requested() -> None:
    assert parse_date(45839, excel_serial=True).value == "2025-07-01"
    assert parse_date("45839", excel_serial=True).value == "2025-07-01"
    assert parse_date(45839).value is None
    assert parse_date(3, excel_serial=True).value is None


def test_money_markers_select_the_vnd_style() -> None:
    assert number_style(["1.600đ", "9,200", "69k"]) == "vnd"
    assert number_style(["560,000", "1,056,000"]) == "en"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9,200", "9200"),
        ("1.600đ", "1600"),
        ("20,1tr", "20100000"),
        ("19,29tr", "19290000"),
        ("1,500tr", "1500000"),
        ("19tr990", "19990000"),
        ("19tr2", "19200000"),
        ("2k5", "2500"),
        ("19300k", "19300000"),
    ],
)
def test_vnd_amounts_parse_to_whole_dong(raw: str, expected: str) -> None:
    assert parse_number(raw, style="vnd").value == Decimal(expected)


def test_compound_scale_amounts_need_no_style() -> None:
    assert parse_number("19tr990").value == Decimal("19990000")


@pytest.mark.parametrize(("raw", "expected"), [("2 cái", "2"), ("5 viên", "5"), ("1,5 kg", "1.5")])
def test_quantity_with_one_unit_word_is_numeric(raw: str, expected: str) -> None:
    assert parse_number(raw).value == Decimal(expected)


def test_text_with_several_words_is_not_a_quantity() -> None:
    assert parse_number("1 thg 7 2025").value is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2 m3", "2"), ("1 xe 5m3", "1"), ("1 cuộn 2x50m", "1"), ("-1 m3", "-1")],
)
def test_quantity_with_a_descriptive_unit_is_numeric(raw: str, expected: str) -> None:
    assert parse_number(raw).value == Decimal(expected)


def test_negative_compound_amount() -> None:
    assert parse_number("-9tr999", style="vnd").value == Decimal("-9999000")


def test_space_grouped_amount() -> None:
    assert parse_number("129 000").value == Decimal("129000")
    assert parse_number("1 234 567").value == Decimal("1234567")
