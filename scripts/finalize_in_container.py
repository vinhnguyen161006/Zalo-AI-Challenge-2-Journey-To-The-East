"""Train-only calibration and held-out evaluation inside the frozen offline image."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from datamate.config import load_config
from datamate.data.ingest import sha256_file


def finalize(config: Path, split: Path, work: Path, per_business: int, prepare_only: bool) -> None:
    config, split, work = config.resolve(), split.resolve(), work.resolve()
    if per_business < 1:
        raise ValueError("per-business must be positive")
    if not prepare_only and not config.is_relative_to(Path("/app/configs")):
        raise ValueError("Run finalization inside the frozen image using a config in /app/configs")
    if not (split / "answers.jsonl").is_file():
        raise ValueError("Calibration requires the training split with answers.jsonl")
    work.mkdir(parents=True, exist_ok=True)
    phase = "prepare"
    started = datetime.now(UTC).isoformat()

    def status(state: str, message: str = "") -> None:
        payload = {
            "state": state,
            "phase": phase,
            "message": message,
            "process_id": os.getpid(),
            "started_at": started,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        temporary = work / "status.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(work / "status.json")

    def run(*arguments: str) -> None:
        subprocess.run([sys.executable, *arguments], check=True)

    def write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    try:
        status("running")
        runtime_config = work / "runtime-config.yaml"
        runtime = {
            "extends": str(config),
            "adapter": "train",
            "data_root": str(split / "data"),
            "questions": str(split / "questions.jsonl"),
            "rulebook": str(split / "rulebook.md"),
            "calendar": str(split / "calendar.csv"),
            "output_root": str(work / "runtime"),
        }
        if (
            runtime_config.exists()
            and json.loads(runtime_config.read_text(encoding="utf-8")) != runtime
        ):
            raise ValueError("Different runtime configuration; use a new work directory")
        write_json(runtime_config, runtime)
        cfg = load_config(runtime_config)
        cfg.preflight()
        if cfg.decision_policy != "execution_baseline":
            raise ValueError("Calibration needs an uncalibrated execution_baseline configuration")
        plan = work / "question-plan"
        manifest = plan / "manifest.json"
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            if (
                previous["seed"] != cfg.seed
                or previous["per_business"] != per_business
                or previous["questions_sha256"] != sha256_file(cfg.questions)
            ):
                raise ValueError("Existing question plan differs; use a new work directory")
        else:
            run(
                str(Path(__file__).with_name("prepare_evaluation.py")),
                "--config",
                str(runtime_config),
                "--destination",
                str(plan),
                "--per-business",
                str(per_business),
            )
        if prepare_only:
            status(
                "prepared", "Question partitions created; no model inference or label contents read"
            )
            return
        phase = "calibration_inference"
        status("running")
        run(
            "-m",
            "datamate",
            "evaluate",
            "--config",
            str(runtime_config),
            "--questions",
            str(plan / "calibration.jsonl"),
            "--labels",
            str(split / "answers.jsonl"),
            "--output",
            str(work / "calibration.jsonl"),
            "--resume",
        )
        phase = "fit_calibration"
        status("running")
        artifact_path = work / "calibration-artifact.json"
        if not artifact_path.exists():
            run(
                "-m",
                "datamate",
                "calibrate",
                "--config",
                str(runtime_config),
                "--labels",
                str(split / "answers.jsonl"),
                "--traces",
                str(work / "calibration.logs"),
                "--output",
                str(artifact_path),
            )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        if not any(
            b["sufficient"] and b["lower_bound"] > 0.5 for b in artifact["buckets"].values()
        ):
            raise ValueError("Insufficient calibration evidence; no release was created")
        calibrated_config = work / "final-config.yaml"
        write_json(
            calibrated_config,
            {
                "extends": str(runtime_config),
                "decision_policy": "calibrated",
                "calibration_artifact": str(artifact_path),
                "output_root": str(work / "calibrated-runtime"),
            },
        )
        phase = "holdout_evaluation"
        status("running")
        run(
            "-m",
            "datamate",
            "evaluate",
            "--config",
            str(calibrated_config),
            "--questions",
            str(plan / "holdout.jsonl"),
            "--labels",
            str(split / "answers.jsonl"),
            "--output",
            str(work / "holdout.jsonl"),
            "--resume",
        )
        release = work / "release"
        release.mkdir(exist_ok=True)
        shutil.copyfile(artifact_path, release / "calibration-artifact.json")
        write_json(
            release / "deployment-config.yaml",
            {
                "extends": str(config),
                "decision_policy": "calibrated",
                "calibration_artifact": "/app/calibration/calibration-artifact.json",
                "output_root": "/result",
            },
        )
        phase = "complete"
        status(
            "complete",
            "Calibration and holdout finished; release contains no source data or labels",
        )
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        status("failed", str(exc))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/app/configs/qwen35-docker.yaml"))
    parser.add_argument("--split", type=Path, default=Path("/train"))
    parser.add_argument("--work", type=Path, default=Path("/result"))
    parser.add_argument("--per-business", type=int, default=20)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    finalize(args.config, args.split, args.work, args.per_business, args.prepare_only)
