"""Inspect the supplied train release without claiming official evaluator parity."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from datamate.data.ingest import safe_child
from datamate.errors import InvalidInput, OfficialRulesUnverified
from datamate.types import QuestionRecord


class TrainKitAdapter:
    def __init__(self, businesses_root: Path):
        self.businesses_root = businesses_root

    def iter_questions(self, path: Path) -> Iterator[QuestionRecord]:
        seen = set()
        with path.open(encoding="utf-8-sig") as stream:
            for number, line in enumerate(stream, 1):
                if len(line) > 1000000:
                    raise InvalidInput(f"Oversized question line {number}")
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise ValueError("Expected object")
                    fields = {
                        k: raw.pop(k) for k in ("id", "business_id", "question", "as_of_date")
                    }
                    question = QuestionRecord.model_validate({**fields, "extra": raw}, strict=True)
                except (ValueError, KeyError, TypeError, ValidationError) as exc:
                    raise InvalidInput(f"Invalid question record at line {number}") from exc
                if question.id in seen:
                    raise InvalidInput(f"Duplicate question id at line {number}")
                seen.add(question.id)
                self.locate_business(question.business_id)
                yield question

    def locate_business(self, business_id: str) -> Path:
        return safe_child(self.businesses_root, business_id)

    def compute_official_score(self, predictions: Path, labels: Path) -> dict[str, Any]:
        raise OfficialRulesUnverified(
            "Authoritative evaluator not supplied; local exact-match is diagnostic only"
        )
