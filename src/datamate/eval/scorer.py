from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from datamate.adapters.submission_writer import read_predictions


def diagnostic_score(predictions: Path, labels: Path) -> dict[str, Any]:
    gold = read_predictions(labels)
    guesses = read_predictions(predictions, [r["id"] for r in gold])
    correct = wrong = abstentions = 0
    for prediction, label in zip(guesses, gold, strict=True):
        if prediction["type"] == "abstain":
            abstentions += 1
        elif prediction == label:
            correct += 1
        else:
            wrong += 1
    total = len(gold)
    return {
        "kind": "LOCAL_EXACT_MATCH_DIAGNOSTIC",
        "official_score": None,
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "abstentions": abstentions,
        "coverage": (total - abstentions) / total if total else 0,
        "warning": "No official normalization, aliases, tolerance or partial credit is implemented.",
    }


def split_businesses(ids: list[str], seed: int = 7) -> dict[str, list[str]]:
    groups = sorted(set(ids))
    if len(groups) < 3:
        raise ValueError("At least three businesses required for disjoint splits")
    random.Random(seed).shuffle(groups)
    n = max(1, len(groups) // 5)
    return {"train": groups[2 * n :], "calibration": groups[n : 2 * n], "holdout": groups[:n]}
