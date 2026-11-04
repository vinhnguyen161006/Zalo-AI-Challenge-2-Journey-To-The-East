"""Conservative normalization. Ambiguous input is evidence, not a guessed value."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


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


def parse_number(value: Any) -> Parsed:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Parsed(value, None)
    if isinstance(value, bool):
        return Parsed(value, None, "not_numeric")
    text = str(value).strip().casefold().replace("\u00a0", "")
    if not text or text[0] not in "+-0123456789":
        return Parsed(value, None, "not_numeric")
    multiplier = Decimal(1)
    for suffix, factor in (("triệu", "1000000"), ("tr", "1000000"), ("k", "1000")):
        if text.endswith(suffix):
            multiplier = Decimal(factor)
            text = text[: -len(suffix)].strip()
            break
    text = re.sub(r"\s*(vnd|₫|đ|%)\s*$", "", text).strip()
    if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)*", text):
        return Parsed(value, None, "not_numeric")
    if "." in text and "," in text:
        if not re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+,\d+", text):
            return Parsed(value, None, "ambiguous_numeric")
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        if not re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", text):
            return Parsed(value, None, "ambiguous_numeric")
        text = text.replace(".", "")
    elif text.count(",") > 1:
        return Parsed(value, None, "ambiguous_numeric")
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        if len(text.split(sep)[1]) == 3:
            return Parsed(value, None, "ambiguous_numeric")
        text = text.replace(",", ".")
    try:
        with localcontext() as context:
            context.prec = max(80, len(text) + 10)
            number = Decimal(text) * multiplier
        if not number.is_finite():
            return Parsed(value, None, "nonfinite_numeric")
        return Parsed(value, number)
    except InvalidOperation:
        return Parsed(value, None, "not_numeric")


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
