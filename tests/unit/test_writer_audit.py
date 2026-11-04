import json
import struct
from decimal import Decimal
from pathlib import Path

import pytest

from datamate.adapters.official_kit import TrainKitAdapter
from datamate.adapters.submission_writer import (
    json_line,
    read_predictions,
    validate_record,
    write_predictions,
)
from datamate.config import Config
from datamate.errors import InvalidInput, OutputValidationError
from datamate.model_audit import audit_checkpoint
from datamate.types import InternalDecision, QuestionRecord


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("number", 0),
        ("number", Decimal("9007199254740993.12")),
        ("percent", 12.4),
        ("string", "Cà phê"),
        ("date", "2026-02"),
        ("date", "2024-02-29"),
        ("boolean", False),
        ("list", ["A", 1]),
        ("table", {"columns": ["name", "amount"], "rows": [["A", 1]]}),
    ],
)
def test_typed_outputs(kind: str, value: object) -> None:
    validate_record({"id": "q", "type": kind, "answer": value})


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("number", True),
        ("number", "120"),
        ("number", None),
        ("number", float("nan")),
        ("percent", "12%"),
        ("boolean", 1),
        ("date", "2026-02-29"),
        ("list", "A"),
        ("table", {"columns": ["a"], "rows": [[1, 2]]}),
    ],
)
def test_invalid_types(kind: str, value: object) -> None:
    with pytest.raises(OutputValidationError):
        validate_record({"id": "q", "type": kind, "answer": value})


def test_decimal_no_float_roundtrip() -> None:
    encoded = json_line({"answer": Decimal("9007199254740993.12")})
    assert json.loads(encoded, parse_float=Decimal)["answer"] == Decimal("9007199254740993.12")


def test_resume_preserves_order_and_fingerprint(config: Config, tmp_path: Path) -> None:
    questions = list(TrainKitAdapter(config.data_root).iter_questions(config.questions))
    output = tmp_path / "predictions.jsonl"

    def interrupted(q: QuestionRecord) -> InternalDecision:
        if q.id == questions[1].id:
            raise RuntimeError("simulated crash")
        return InternalDecision(question_id=q.id, status="abstain")

    with pytest.raises(RuntimeError):
        write_predictions(output, questions, interrupted, "fingerprint")
    assert not output.exists()

    def solve(q: QuestionRecord) -> InternalDecision:
        return InternalDecision(question_id=q.id, status="abstain")

    with pytest.raises(OutputValidationError, match="fingerprint"):
        write_predictions(output, questions, solve, "changed", resume=True)
    assert write_predictions(output, questions, solve, "fingerprint", resume=True) == 3
    assert [r["id"] for r in read_predictions(output)] == [q.id for q in questions]
    assert write_predictions(output, questions, solve, "fingerprint", resume=True) == 3


def test_duplicate_question_ids(config: Config, tmp_path: Path) -> None:
    question = config.questions.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "questions.jsonl"
    path.write_text(question + "\n" + question + "\n", encoding="utf-8")
    with pytest.raises(InvalidInput, match="Duplicate"):
        list(TrainKitAdapter(config.data_root).iter_questions(path))


def shard(path: Path, name: str, shape: list[int]) -> None:
    count = 1
    for value in shape:
        count *= value
    header = json.dumps(
        {name: {"dtype": "F32", "shape": shape, "data_offsets": [0, count * 4]}}
    ).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * count * 4)


def test_model_parameter_audit(tmp_path: Path) -> None:
    shard(tmp_path / "a.safetensors", "weight_a", [2, 3])
    shard(tmp_path / "b.safetensors", "weight_b", [4, 5])
    result = audit_checkpoint(tmp_path)
    assert result["stored_elements"] == 26
    assert result["eligibility"] == "UNVERIFIED"
    assert result == audit_checkpoint(tmp_path)
    shard(tmp_path / "c.safetensors", "weight_a", [2, 3])
    with pytest.raises(InvalidInput, match="Duplicate"):
        audit_checkpoint(tmp_path)
