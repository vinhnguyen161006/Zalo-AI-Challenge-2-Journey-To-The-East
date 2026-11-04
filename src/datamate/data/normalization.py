"""Conservative normalization. Ambiguous input is evidence, not a guessed value."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal


def normalized_text(value: str, *, accents: bool = False) -> str:
    value = " ".join(unicodedata.normalize("NFC", value).casefold().split())
    if accents:
        value = "".join(
            c for c in unicodedata.normalize("NFD", value) if not unicodedata.combining(c)
        ).replace("đ", "d")
    return value


def identifier(value: str, fallback: str = "column") -> str:
    result = re.sub(r"[^a-z0-9_]+", "_", normalized_text(value, accents=True)).strip("_")
    if not result or result[0].isdigit():
        result = f"{fallback}_{result}"
    return result[:80]


@dataclass(frozen=True)
class Parsed:
    raw: Any
    value: Any
    warning: str | None = None


NumberStyle = Literal["vi", "en"]
GROUP_DIGITS = 3
NUMBER_BODY = re.compile(r"[+-]?\d+(?:[.,]\d+)*")
DOT_GROUPS = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})+")
COMMA_GROUPS = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+")
VI_MIXED = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})+,\d+")
EN_MIXED = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+\.\d+")
UNIT_SUFFIX = re.compile(r"\s*(vnd|₫|đ|%)\s*$")
SCALE_SUFFIXES = (("triệu", "1000000"), ("tr", "1000000"), ("k", "1000"))
SLASH_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/\d{4}")
MAX_MONTH = 12


def _split_suffix(value: Any) -> tuple[str, Decimal] | None:
    text = str(value).strip().casefold().replace("\u00a0", "")
    if not text or text[0] not in "+-0123456789":
        return None
    multiplier = Decimal(1)
    for suffix, factor in SCALE_SUFFIXES:
        if text.endswith(suffix):
            multiplier = Decimal(factor)
            text = text[: -len(suffix)].strip()
            break
    return UNIT_SUFFIX.sub("", text).strip(), multiplier


def _single_separator(text: str) -> tuple[str, str, str] | None:
    separators = [c for c in text if c in ".,"]
    if len(separators) != 1:
        return None
    integer_part, fraction = re.split(r"[.,]", text.lstrip("+-"))
    return separators[0], integer_part, fraction


def _is_group_separator_candidate(integer_part: str, fraction: str) -> bool:
    return (
        len(fraction) == GROUP_DIGITS
        and len(integer_part) <= GROUP_DIGITS
        and not integer_part.startswith("0")
    )


def number_style_evidence(text: str) -> NumberStyle | None:
    """Convention proven by one cell: vi uses dot groups and a decimal comma, en the reverse."""
    if EN_MIXED.fullmatch(text) or (text.count(",") > 1 and COMMA_GROUPS.fullmatch(text)):
        return "en"
    if VI_MIXED.fullmatch(text) or (text.count(".") > 1 and DOT_GROUPS.fullmatch(text)):
        return "vi"
    single = _single_separator(text)
    if single is None or _is_group_separator_candidate(single[1], single[2]):
        return None
    return "vi" if single[0] == "," else "en"


def number_style(values: Iterable[Any]) -> NumberStyle | None:
    """The one convention that a column's cells prove, or None when absent or contradictory."""
    proven = set()
    for value in values:
        parts = _split_suffix(value) if isinstance(value, str) else None
        if parts is not None and NUMBER_BODY.fullmatch(parts[0]):
            style = number_style_evidence(parts[0])
            if style is not None:
                proven.add(style)
    return proven.pop() if len(proven) == 1 else None


def _plain_decimal_text(text: str, style: NumberStyle | None) -> str | None:
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        if VI_MIXED.fullmatch(text):
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "") if EN_MIXED.fullmatch(text) else None
    if dots > 1:
        return text.replace(".", "") if DOT_GROUPS.fullmatch(text) else None
    if commas > 1:
        return text.replace(",", "") if COMMA_GROUPS.fullmatch(text) else None
    single = _single_separator(text)
    if single is None:
        return text
    separator, integer_part, fraction = single
    if not _is_group_separator_candidate(integer_part, fraction):
        return text.replace(",", ".")
    if style is None:
        return None
    groups_are_commas = style == "en"
    if (separator == ",") == groups_are_commas:
        return text.replace(separator, "")
    return text.replace(",", ".")


def _native_number(value: int | float | Decimal) -> Parsed:
    number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    if not number.is_finite():
        return Parsed(value, None, "nonfinite_numeric")
    return Parsed(value, number)


def parse_number(value: Any, style: NumberStyle | None = None) -> Parsed:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Parsed(value, None)
    if isinstance(value, bool):
        return Parsed(value, None, "not_numeric")
    if isinstance(value, (int, float, Decimal)):
        return _native_number(value)
    parts = _split_suffix(value)
    if parts is None or not NUMBER_BODY.fullmatch(parts[0]):
        return Parsed(value, None, "not_numeric")
    text, multiplier = parts
    plain = _plain_decimal_text(text, style)
    if plain is None:
        return Parsed(value, None, "ambiguous_numeric")
    try:
        with localcontext() as context:
            context.prec = max(80, len(plain) + 10)
            number = Decimal(plain) * multiplier
        if not number.is_finite():
            return Parsed(value, None, "nonfinite_numeric")
        return Parsed(value, number)
    except InvalidOperation:
        return Parsed(value, None, "not_numeric")


def date_day_first(values: Iterable[Any]) -> bool | None:
    """True when a cell proves day-first, False when it proves month-first, else None."""
    day_first = month_first = False
    for value in values:
        match = SLASH_DATE.fullmatch(value.strip()) if isinstance(value, str) else None
        if match:
            day_first = day_first or int(match[1]) > MAX_MONTH
            month_first = month_first or int(match[2]) > MAX_MONTH
    if day_first == month_first:
        return None
    return day_first


def parse_date(value: Any, *, day_first: bool | None = None, excel_serial: bool = False) -> Parsed:
    if isinstance(value, datetime):
        return Parsed(value, value.isoformat())
    if isinstance(value, date):
        return Parsed(value, value.isoformat())
    if excel_serial and isinstance(value, (int, float)):
        return Parsed(value, (datetime(1899, 12, 30) + timedelta(days=value)).date().isoformat())
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return Parsed(value, date.fromisoformat(text).isoformat())
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
            a, b, year = map(int, text.split("/"))
            if day_first is None and a <= 12 and b <= 12 and a != b:
                return Parsed(value, None, "ambiguous_date")
            day, month = (a, b) if day_first is not False else (b, a)
            return Parsed(value, date(year, month, day).isoformat())
    except ValueError:
        return Parsed(value, None, "invalid_date")
    return Parsed(value, None, "not_date")
