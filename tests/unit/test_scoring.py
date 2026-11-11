import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from datamate.adapters.submission_writer import json_line
from datamate.cli import app
from datamate.errors import OutputValidationError
from datamate.eval.matching import matches, normalize_name
from datamate.eval.scorer import diagnostic_score


def test_normalization_preserves_accents_and_uses_lowercase() -> None:
    assert normalize_name(unicodedata.normalize("NFD", "  CÀ-PHÊ\tSỮA! ")) == "cà phê sữa"
    assert normalize_name("dâu") != normalize_name("dầu")
    assert normalize_name("Straße") != normalize_name("STRASSE")


@pytest.mark.parametrize(
    ("kind", "actual", "expected", "correct"),
    [
        ("number", Decimal("9007199254740993.12"), Decimal("9007199254740993.13"), False),
        ("number", Decimal("1.001"), 1, False),
        ("number", Decimal("1.00"), 1, True),
        ("percent", Decimal("12.454"), Decimal("12.45"), True),
        ("percent", Decimal("12.455"), Decimal("12.46"), True),
        ("percent", Decimal("-0.001"), Decimal("0.001"), False),
        ("list", ["CÀ-PHÊ", 2], ["cà phê", 2], True),
        ("list", [2, "cà phê"], ["cà phê", 2], False),
        ("list", [True], [1], False),
        ("date", "2026-08", "2026-08-01", False),
        ("boolean", False, True, False),
    ],
)
def test_typed_matching(kind: str, actual: Any, expected: Any, correct: bool) -> None:
    assert matches({"type": kind, "answer": actual}, {"type": kind, "answer": expected}) is correct


def test_table_column_alignment_and_unordered_rows() -> None:
    label = {
        "type": "table",
        "answer": {"columns": ["Tên", "Nợ"], "rows": [["An", 1], ["Bình", 2]]},
    }
    guess = {
        "type": "table",
        "answer": {"columns": ["NỢ!", "TÊN"], "rows": [[2, "BÌNH"], [1, "An"]]},
    }
    assert matches(guess, label)
    guess["answer"] = {"columns": ["customer", "amount"], "rows": [["Bình", 2], ["An", 1]]}
    assert matches(guess, label)
    guess["answer"]["rows"] = [["Bình", 1], ["An", 2]]
    assert not matches(guess, label)


def test_nested_aliases_preserve_list_positions_and_table_row_values() -> None:
    label = {"type": "list", "answer": ["Apple", "Pear"], "alias": [["P1"], ["P2"]]}
    assert matches({"type": "list", "answer": ["p1", "P2"]}, label)
    assert not matches({"type": "list", "answer": ["P2", "P1"]}, label)
    table = {
        "type": "table",
        "answer": {"columns": ["product", "quantity"], "rows": [["Apple", 2], ["Pear", 5]]},
        "alias": [["P1"], ["P2"]],
    }
    actual = {
        "type": "table",
        "answer": {
            "columns": ["quantity", "product"],
            "rows": [[5, "P2"], [2, "P1"]],
        },
    }
    assert matches(actual, table)
    actual["answer"]["rows"] = [[2, "P2"], [5, "P1"]]
    assert not matches(actual, table)


def test_reader_accepts_nested_training_alias_shape(tmp_path: Path) -> None:
    labels, predictions = tmp_path / "labels", tmp_path / "predictions"
    labels.write_text(
        json_line(
            {
                "id": "q",
                "type": "list",
                "answer": ["Apple"],
                "alias": [["P1"]],
            }
        ),
        encoding="utf-8",
    )
    predictions.write_text(
        json_line(
            {
                "id": "q",
                "type": "list",
                "answer": ["P1"],
            }
        ),
        encoding="utf-8",
    )
    assert diagnostic_score(predictions, labels)["correct"] == 1


def test_score_alias_null_reference_missing_and_reordered_ids(tmp_path: Path) -> None:
    labels = tmp_path / "labels.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    labels.write_text(
        "\n".join(
            json_line(r)
            for r in [
                {"id": "a", "type": "string", "answer": "Cà phê", "alias": ["SP01"]},
                {"id": "b", "type": "unanswerable", "answer": None},
                {"id": "c", "type": "number", "answer": 3},
                {"id": "d", "type": "number", "answer": 4},
            ]
        ),
        encoding="utf-8",
    )
    predictions.write_text(
        "\n".join(
            json_line(r)
            for r in [
                {"id": "d", "type": "number", "answer": 5},
                {"id": "b", "type": "unanswerable", "reason": "Missing source"},
                {"id": "a", "type": "string", "answer": "sp01"},
            ]
        ),
        encoding="utf-8",
    )
    result = diagnostic_score(predictions, labels)
    assert (result["correct"], result["wrong"], result["abstentions"]) == (2, 1, 1)
    assert result["penalized_accuracy"] == 0.25
    assert result["official_score"] is None
    assert (
        CliRunner()
        .invoke(app, ["score", "--predictions", str(predictions), "--labels", str(labels)])
        .exit_code
        == 0
    )


@pytest.mark.parametrize(
    "records",
    [
        [{"id": "unknown", "type": "abstain"}],
        [{"id": "q", "type": "abstain"}, {"id": "q", "type": "abstain"}],
    ],
)
def test_score_rejects_unknown_or_duplicate_ids(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    labels, predictions = tmp_path / "labels", tmp_path / "predictions"
    labels.write_text(json_line({"id": "q", "type": "number", "answer": 1}), encoding="utf-8")
    predictions.write_text("\n".join(json_line(r) for r in records), encoding="utf-8")
    with pytest.raises(OutputValidationError):
        diagnostic_score(predictions, labels)
