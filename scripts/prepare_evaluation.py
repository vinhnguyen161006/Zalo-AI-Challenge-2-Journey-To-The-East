"""Prepare balanced train-only development/calibration/holdout question files, no labels."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from datamate.adapters.official_kit import TrainKitAdapter
from datamate.adapters.submission_writer import json_line
from datamate.config import load_config
from datamate.data.ingest import sha256_file
from datamate.eval.scorer import split_businesses


def prepare(config: Path, destination: Path, per_business: int) -> dict[str, object]:
    if per_business < 1:
        raise ValueError("per-business must be positive")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a new empty evaluation directory")
    cfg = load_config(config)
    cfg.preflight()
    records = list(TrainKitAdapter(cfg.data_root).iter_questions(cfg.questions))
    split = split_businesses([q.business_id for q in records], cfg.seed)
    grouped = {}
    rng = random.Random(cfg.seed)
    for business in sorted({q.business_id for q in records}):
        questions = [q for q in records if q.business_id == business]
        rng.shuffle(questions)
        grouped[business] = questions[:per_business]
    destination.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, businesses in split.items():
        interleaved = [
            grouped[b][i] for i in range(per_business) for b in businesses if i < len(grouped[b])
        ]
        (destination / f"{name}.jsonl").write_text(
            "".join(
                json_line(q.model_dump(exclude={"schema_version", "extra"})) + "\n"
                for q in interleaved
            ),
            encoding="utf-8",
        )
        counts[name] = len(interleaved)
    report = {
        "business_split": split,
        "seed": cfg.seed,
        "questions_sha256": sha256_file(cfg.questions),
        "per_business": per_business,
        "counts": counts,
        "labels_read": False,
    }
    (destination / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/qwen35.yaml"))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--per-business", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config, args.destination, args.per_business)))
