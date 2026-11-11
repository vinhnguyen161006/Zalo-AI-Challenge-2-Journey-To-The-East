import json
from pathlib import Path

import pytest

from datamate.decision.calibration import (
    calibrated_confidence,
    evidence_bucket,
    fit_calibration,
    runtime_signature,
    wilson_lower,
)
from datamate.eval.scorer import split_businesses


def test_confidence_is_a_sample_bound_and_rejects_stale_runtime() -> None:
    trace = {
        "runtime_signature": "current",
        "business_id": "holdout",
        "cross_paradigm_agreement": True,
        "validated_plan": {"answer_type": "number", "required_tables": ["sales"]},
    }
    bucket = {"sufficient": True, "correct": 90, "total": 100, "businesses": ["a", "b", "c"]}
    artifact = {
        "version": 1,
        "method": "wilson_95_lower_bound",
        "runtime_signature": "current",
        "buckets": {evidence_bucket(trace): bucket},
        "business_split": {"calibration": ["a", "b", "c"]},
    }
    p = calibrated_confidence(artifact, trace)
    assert p is not None and 0.8 < p < 0.9
    bucket["total"] = 10
    assert calibrated_confidence(artifact, trace) is None
    trace["runtime_signature"] = "changed"
    with pytest.raises(ValueError, match="fingerprint"):
        calibrated_confidence(artifact, trace)
    assert wilson_lower(0, 0) == 0
    assert wilson_lower(5, 10) < 0.5


def test_fitting_uses_only_calibration_businesses(tmp_path: Path) -> None:
    businesses = [f"synthetic_{i}" for i in range(20)]
    split = split_businesses(businesses)
    questions = tmp_path / "questions.jsonl"
    labels = tmp_path / "labels.jsonl"
    traces = tmp_path / "traces"
    traces.mkdir()
    questions.write_text(
        "\n".join(json.dumps({"id": b, "business_id": b}) for b in businesses), encoding="utf-8"
    )
    labels.write_text(
        "\n".join(json.dumps({"id": b, "type": "number", "answer": 7}) for b in businesses),
        encoding="utf-8",
    )
    for b in businesses:
        trace = {
            "question_id": b,
            "business_id": b,
            "runtime_signature": runtime_signature({}),
            "final_answer": {"id": b, "type": "number", "answer": 7},
            "cross_paradigm_agreement": True,
        }
        (traces / (b + ".json")).write_text(json.dumps(trace), encoding="utf-8")
    artifact = fit_calibration(questions, labels, traces, {})
    assert artifact["traces_seen"] == len(split["calibration"])
    assert all(not v["sufficient"] for v in artifact["buckets"].values())
    bucket = next(iter(artifact["buckets"].values()))
    assert set(bucket["businesses"]) == set(split["calibration"])
