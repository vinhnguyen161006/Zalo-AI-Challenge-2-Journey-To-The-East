"""Assemble the organizer /code layout for the Docker submission from a finished run."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TREES = ("src", "configs", "docs")
CODE_FILES = ("pyproject.toml", "architecture.mmd")
SCRIPTS = ("prepare_evaluation.py", "report_run.py", "stage_submission.py")
RUN_EVIDENCE = (
    "calibration.jsonl",
    "holdout.jsonl",
    "calibration-artifact.json",
    "final-config.yaml",
)
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_yaml(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}: {v}\n" for k, v in values.items()), encoding="utf-8")


def copy_code(destination: Path) -> None:
    for tree in CODE_TREES:
        shutil.copytree(ROOT / tree, destination / tree, ignore=IGNORED)
    for name in CODE_FILES:
        shutil.copy2(ROOT / name, destination / name)
    (destination / "scripts").mkdir()
    for name in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / name, destination / "scripts" / name)
    shutil.copy2(ROOT / "requirements.lock.txt", destination / "requirements.txt")
    for name in ("predict.sh", "Dockerfile", "README.md"):
        shutil.copy2(ROOT / "submission" / name, destination / name)


def copy_calibration(destination: Path, run: Path) -> None:
    target = destination / "calibration"
    target.mkdir()
    shutil.copy2(run / "calibration-artifact.json", target / "calibration-artifact.json")
    write_yaml(
        target / "deployment-config.yaml",
        {
            "extends": "/code/configs/sales.yaml",
            "decision_policy": "calibrated",
            "calibration_artifact": "/code/calibration/calibration-artifact.json",
            "output_root": "/result",
        },
    )


def copy_training(destination: Path, run: Path, plan: Path, train: Path) -> Path:
    target = destination / "training_code"
    shutil.copytree(ROOT / "submission" / "training_code", target)
    shutil.copy2(ROOT / "requirements.lock.txt", target / "requirements.txt")
    split = target / "data" / "train"
    shutil.copytree(train, split)
    write_yaml(
        target / "configs" / "calibration.yaml",
        {
            "extends": "/code/configs/sales.yaml",
            "data_root": str(Path("/code/training_code/data/train/data")),
            "rulebook": "/code/training_code/data/train/rulebook.md",
            "calendar": "/code/training_code/data/train/calendar.csv",
            "questions": "/code/training_code/data/train/questions.jsonl",
            "output_root": "/tmp/datamate-calibration/runtime",
        },
    )
    logs = target / "logs"
    logs.mkdir()
    for name in RUN_EVIDENCE:
        shutil.copy2(run / name, logs / name)
    shutil.copytree(plan, logs / "question-plan")
    return target


def manifest(training: Path) -> dict[str, object]:
    files = sorted(p for p in training.rglob("*") if p.is_file())
    plan = json.loads((training / "logs" / "question-plan" / "manifest.json").read_text())
    return {
        "schema_version": "1",
        "training_performed": False,
        "model_weights": "none used at inference; no fine-tuning",
        "data_source": "Zalo AI Challenge 2026 DataMate train split as provided by the organizers",
        "business_split": plan["business_split"],
        "seed": plan["seed"],
        "question_counts": plan["counts"],
        "files": [
            {
                "path": f"/code/training_code/{p.relative_to(training).as_posix()}",
                "bytes": p.stat().st_size,
                "sha256": sha256(p),
            }
            for p in files
        ],
    }


def stage(run: Path, plan: Path, train: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"Destination exists; choose a new directory: {destination}")
    destination.mkdir(parents=True)
    copy_code(destination)
    copy_calibration(destination, run)
    training = copy_training(destination, run, plan, train)
    (training / "data_manifest.json").write_text(
        json.dumps(manifest(training), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--train", type=Path, default=ROOT / "data" / "train")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    stage(args.run, args.plan, args.train, args.destination)


if __name__ == "__main__":
    main()
