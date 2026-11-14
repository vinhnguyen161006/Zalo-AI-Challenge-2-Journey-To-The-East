#!/usr/bin/env bash
set -euo pipefail
cd /code
export PYTHONPATH=/code/src
work="${DATAMATE_WORK:-/tmp/datamate-calibration}"
split=/code/training_code/data/train
config=/code/training_code/configs/calibration.yaml
mkdir -p "$work"
python /code/scripts/prepare_evaluation.py --config "$config" --destination "$work/plan" --per-business 100
python -m datamate evaluate --config "$config" --questions "$work/plan/calibration.jsonl" \
  --labels "$split/answers.jsonl" --output "$work/calibration.jsonl" --resume
python -m datamate calibrate --config "$config" --labels "$split/answers.jsonl" \
  --traces "$work/calibration.logs" --output "$work/calibration-artifact.json"
printf 'extends: %s\ndecision_policy: calibrated\ncalibration_artifact: %s\noutput_root: %s\n' \
  "$config" "$work/calibration-artifact.json" "$work/calibrated-runtime" > "$work/final-config.yaml"
python -m datamate evaluate --config "$work/final-config.yaml" --questions "$work/plan/holdout.jsonl" \
  --labels "$split/answers.jsonl" --output "$work/holdout.jsonl" --resume
