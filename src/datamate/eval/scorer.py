from __future__ import annotations

import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

from datamate.adapters.submission_writer import read_predictions, validate_record
from datamate.errors import OutputValidationError
from datamate.eval.matching import matches


def read_labels(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            try:
                record = json.loads(line, parse_float=Decimal)
                if not isinstance(record, dict):
                    raise ValueError("Expected reference object")
                submission = {k: v for k, v in record.items() if k != "alias"}
                if submission.get("type") == "unanswerable" and submission.get("answer") is None:
                    submission.pop("answer", None)
                validate_record(submission)
                aliases = record.get("alias", [])
                if not isinstance(aliases, list) or any(
                    not isinstance(v, str)
                    and not (isinstance(v, list) and all(isinstance(a, str) for a in v))
                    for v in aliases
                ):
                    raise ValueError("Invalid reference aliases")
                if record["type"] == "string" and any(not isinstance(v, str) for v in aliases):
                    raise ValueError("Scalar aliases must be strings")
                if record["type"] == "abstain" or record["id"] in seen:
                    raise ValueError("Abstaining or duplicate reference")
            except (ValueError, TypeError) as exc:
                raise OutputValidationError("Malformed reference record") from exc
            seen.add(record["id"])
            records.append(record)
    return records


def diagnostic_score(
    predictions: Path, labels: Path, question_ids: list[str] | None = None
) -> dict[str, Any]:
    gold = read_labels(labels)
    if question_ids is not None:
        if len(question_ids) != len(set(question_ids)) or set(question_ids) - {
            r["id"] for r in gold
        }:
            raise OutputValidationError("Evaluation subset has duplicate or unknown reference ids")
        selected = set(question_ids)
        gold = [r for r in gold if r["id"] in selected]
    guesses = {r["id"]: r for r in read_predictions(predictions)}
    if set(guesses) - {r["id"] for r in gold}:
        raise OutputValidationError("Prediction contains unknown ids")
    correct = wrong = abstentions = 0
    for label in gold:
        prediction = guesses.get(label["id"], {"type": "abstain"})
        if prediction["type"] == "abstain":
            abstentions += 1
        elif matches(prediction, label):
            correct += 1
        else:
            wrong += 1
    total = len(gold)
    return {
        "kind": "LOCAL_RULE_TEXT_DIAGNOSTIC",
        "subset": question_ids is not None,
        "official_score": None,
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "abstentions": abstentions,
        "penalized_accuracy": (correct - wrong) / total if total else 0,
        "answered_accuracy": correct / (correct + wrong) if correct + wrong else 0,
        "coverage": (total - abstentions) / total if total else 0,
        "warning": "Not organizer-certified. Exact numeric comparison; percent half-ties use ROUND_HALF_UP. Nested aliases are positional for lists and per-row for tables with one string cell, following the released train shape.",
    }


def split_businesses(ids: list[str], seed: int = 7) -> dict[str, list[str]]:
    groups = sorted(set(ids))
    if len(groups) < 3:
        raise ValueError("At least three businesses required for disjoint splits")
    random.Random(seed).shuffle(groups)
    n = max(1, len(groups) // 5)
    return {"train": groups[2 * n :], "calibration": groups[n : 2 * n], "holdout": groups[:n]}
