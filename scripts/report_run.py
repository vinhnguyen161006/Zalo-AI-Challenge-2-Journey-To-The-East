"""Summarize completed question traces without reading data or reference answers."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def summarize(directory: Path) -> dict[str, Any]:
    traces = []
    for path in sorted(directory.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if "question_id" in item and "final_answer" in item:
            traces.append(item)
    ids = [t["question_id"] for t in traces]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate question traces; summarize a single run directory")
    latencies = [float(t["latency_ms"]) / 1000 for t in traces if "latency_ms" in t]
    reasons: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    for trace in traces:
        reason = trace.get("decision_reason", [])
        reasons.update([reason] if isinstance(reason, str) else reason)
        for report in trace.get("verification", []):
            failures.update(report.get("hard_failures", []))
    return {
        "kind": "LOCAL_TRACE_SUMMARY_NOT_ACCURACY",
        "completed_questions": len(traces),
        "decisions": dict(Counter(t.get("decision", "unknown") for t in traces)),
        "routes": dict(Counter(t.get("route", "unrouted") for t in traces)),
        "decision_reasons": dict(reasons.most_common()),
        "verification_failures": dict(failures.most_common()),
        "question_seconds": {
            "total": round(sum(latencies), 3),
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
            "maximum": max(latencies, default=None),
        },
        "model_calls": sum(t.get("model_calls", 0) for t in traces),
        "runtime_signatures": sorted(
            {t["runtime_signature"] for t in traces if "runtime_signature" in t}
        ),
        "warning": "Completed traces only; excludes startup and unfinished queries. No labels were read; this is not an accuracy or target-hardware benchmark.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.logs.is_dir():
        parser.error("--logs must be an existing run log directory")
    report = json.dumps(summarize(args.logs), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
