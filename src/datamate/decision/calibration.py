"""Empirical, business-disjoint calibration; no model-reported confidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamate.adapters.submission_writer import json_line
from datamate.eval.matching import matches
from datamate.eval.scorer import read_labels, split_businesses

if TYPE_CHECKING:
    from datamate.config import Config


def inference_settings(config: Config) -> dict[str, Any]:
    return {
        "models": config.models.model_dump(mode="json"),
        "seed": config.seed,
        "limits": config.limits.model_dump(mode="json"),
        "require_independent_agreement": config.require_independent_agreement,
        "differential_verification": config.differential_verification,
        "use_verified_templates": config.use_verified_templates,
        "pandas_first": config.pandas_first,
        "adaptive_candidates": config.adaptive_candidates,
        "planner_critic": config.planner_critic,
    }


def runtime_signature(models: dict[str, Any]) -> str:
    root = Path(__file__).resolve().parents[1]
    files = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.suffix in {".py", ".md"}
    }
    return hashlib.sha256(json.dumps([files, models], sort_keys=True).encode()).hexdigest()


def evidence_bucket(trace: dict[str, Any]) -> str:
    plan = trace.get("validated_plan", {})
    kind = (
        "scalar"
        if plan.get("answer_type") in {"number", "percent", "boolean", "date", "string"}
        else "collection"
    )
    tables = "single" if len(plan.get("required_tables", [])) == 1 else "multi"
    reports = trace.get("verification", [])
    eligible_reports = [r for r in reports if not r.get("hard_failures")]
    proof = bool(eligible_reports) and all(
        bool(r.get("semantic_checks"))
        and all(r["semantic_checks"].values())
        and not any("semantic_proof" in w for w in r.get("soft_warnings", []))
        for r in eligible_reports
    )
    return ":".join([kind, tables, "declared_proof" if proof else "execution_only"])


def wilson_lower(correct: int, total: int, z: float = 1.96) -> float:
    if not total or not 0 <= correct <= total:
        return 0.0
    p = correct / total
    return (
        p + z * z / (2 * total) - z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    ) / (1 + z * z / total)


def fit_calibration(
    questions: Path,
    labels: Path,
    trace_directory: Path,
    models: dict[str, Any],
    *,
    min_samples: int = 20,
    min_businesses: int = 3,
    seed: int = 7,
) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in questions.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len({q["id"] for q in rows}) != len(rows):
        raise ValueError("Duplicate question ids")
    assignments = split_businesses([q["business_id"] for q in rows], seed)
    calibration_businesses = set(assignments["calibration"])
    eligible = {q["id"]: q for q in rows if q["business_id"] in calibration_businesses}
    gold = {r["id"]: r for r in read_labels(labels)}
    groups: dict[str, list[tuple[bool, str]]] = defaultdict(list)
    seen = set()
    signature = runtime_signature(models)
    for path in sorted(trace_directory.glob("*.json")):
        trace = json.loads(path.read_text(encoding="utf-8"))
        key = trace.get("question_id")
        if key not in eligible:
            continue
        if key in seen or key not in gold:
            raise ValueError("Duplicate trace or missing calibration label")
        seen.add(key)
        if trace.get("runtime_signature") != signature:
            raise ValueError(
                "Calibration traces must match the exact runtime and model configuration"
            )
        answer = trace.get("final_answer", {})
        if answer.get("type", "abstain") == "abstain":
            continue
        if answer.get("type") == "unanswerable":
            continue
        if trace.get("cross_paradigm_agreement") is not True:
            raise ValueError("Calibration requires independently agreed executed candidates")
        if trace.get("business_id") != eligible[key]["business_id"]:
            raise ValueError("Trace business mismatch")
        groups[evidence_bucket(trace)].append(
            (matches(answer, gold[key]), eligible[key]["business_id"])
        )
    buckets = {}
    for name, examples in groups.items():
        correct = sum(v for v, _ in examples)
        businesses = sorted({b for _, b in examples})
        buckets[name] = {
            "correct": correct,
            "total": len(examples),
            "businesses": businesses,
            "lower_bound": wilson_lower(correct, len(examples)),
            "sufficient": len(examples) >= min_samples and len(businesses) >= min_businesses,
        }
    return {
        "version": 1,
        "method": "wilson_95_lower_bound",
        "scoring": {"correct": 1, "wrong": -1, "abstain": 0},
        "runtime_signature": runtime_signature(models),
        "business_split": assignments,
        "min_samples": min_samples,
        "min_businesses": min_businesses,
        "traces_seen": len(seen),
        "buckets": buckets,
        "labels_sha256": hashlib.sha256(labels.read_bytes()).hexdigest(),
        "questions_sha256": hashlib.sha256(questions.read_bytes()).hexdigest(),
    }


def save_calibration(artifact: dict[str, Any], output: Path) -> None:
    if output.exists():
        raise ValueError("Use a new calibration artifact path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json_line(artifact) + "\n", encoding="utf-8")


def calibrated_confidence(artifact: dict[str, Any], trace: dict[str, Any]) -> float | None:
    if artifact.get("version") != 1 or artifact.get("method") != "wilson_95_lower_bound":
        raise ValueError("Unsupported calibration artifact")
    if artifact.get("runtime_signature") != trace.get("runtime_signature"):
        raise ValueError("Calibration/runtime fingerprint mismatch")
    if trace.get("cross_paradigm_agreement") is not True:
        return None
    bucket = artifact.get("buckets", {}).get(evidence_bucket(trace), {})
    if not bucket.get("sufficient"):
        return None
    total, correct = bucket.get("total", 0), bucket.get("correct", 0)
    if total < max(20, artifact.get("min_samples", 20)) or len(bucket.get("businesses", [])) < max(
        3, artifact.get("min_businesses", 3)
    ):
        return None
    return wilson_lower(correct, total)
