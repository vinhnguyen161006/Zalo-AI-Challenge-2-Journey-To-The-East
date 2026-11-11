"""Local interpretation of the supplied evaluation text, not the organizer evaluator."""

from __future__ import annotations

import unicodedata
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in text).split())


def cell_key(value: Any) -> tuple[str, Any]:
    if isinstance(value, str):
        return "string", normalize_name(value)
    if value is None or isinstance(value, bool):
        return type(value).__name__, value
    return "number", Decimal(str(value))


def rounded_percent(value: Any) -> Decimal:
    number = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(80, len(number.as_tuple().digits), number.adjusted() + 4)
        return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def named_cell_matches(actual: Any, expected: Any, aliases: list[str]) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return normalize_name(actual) in {normalize_name(v) for v in [expected, *aliases]}
    return cell_key(actual) == cell_key(expected)


def tables_match(
    predicted: dict[str, Any], reference: dict[str, Any], aliases: list[Any] | None = None
) -> bool:
    actual_names = [normalize_name(c) for c in predicted["columns"]]
    expected_names = [normalize_name(c) for c in reference["columns"]]
    if len(actual_names) != len(expected_names):
        return False
    positions = list(range(len(expected_names)))
    if (
        len(set(actual_names)) == len(actual_names)
        and len(set(expected_names)) == len(expected_names)
        and set(actual_names) == set(expected_names)
    ):
        positions = [actual_names.index(name) for name in expected_names]
    actual_rows = {tuple(cell_key(row[i]) for i in positions) for row in predicted["rows"]}
    expected_rows = {tuple(cell_key(cell) for cell in row) for row in reference["rows"]}
    if (
        aliases
        and len(aliases) == len(reference["rows"])
        and all(
            isinstance(a, list) and sum(isinstance(c, str) for c in row) == 1
            for a, row in zip(aliases, reference["rows"], strict=True)
        )
    ):

        def row_matches(actual: list[Any], row_index: int) -> bool:
            return all(
                named_cell_matches(actual[position], expected, aliases[row_index])
                for position, expected in zip(positions, reference["rows"][row_index], strict=True)
            )

        return all(
            any(row_matches(row, i) for i in range(len(reference["rows"])))
            for row in predicted["rows"]
        ) and all(
            any(row_matches(row, i) for row in predicted["rows"])
            for i in range(len(reference["rows"]))
        )
    return actual_rows == expected_rows


def matches(prediction: dict[str, Any], label: dict[str, Any]) -> bool:
    kind = label["type"]
    if prediction["type"] != kind:
        return False
    if kind == "unanswerable":
        return True
    actual, expected = prediction["answer"], label["answer"]
    if kind == "string":
        return normalize_name(actual) in {
            normalize_name(value) for value in [expected, *label.get("alias", [])]
        }
    if kind == "percent":
        return rounded_percent(actual) == rounded_percent(expected) and (
            (actual > 0) - (actual < 0) == (expected > 0) - (expected < 0)
        )
    if kind == "list":
        aliases = label.get("alias", [])
        if aliases and len(aliases) == len(expected) and all(isinstance(a, list) for a in aliases):
            return len(actual) == len(expected) and all(
                named_cell_matches(a, e, names)
                for a, e, names in zip(actual, expected, aliases, strict=True)
            )
        return [cell_key(v) for v in actual] == [cell_key(v) for v in expected]
    if kind == "table":
        return tables_match(actual, expected, label.get("alias"))
    return actual == expected
