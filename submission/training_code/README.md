# Training code: calibration only

No model was trained or fine-tuned and no data was generated. The only fitted artifact is
the decision calibration (`/code/calibration/calibration-artifact.json`): per evidence
bucket, the count of correct answers and the Wilson 95% lower bound on the provided train
split. Inference publishes an answer only for buckets whose lower bound exceeds 0.5.

## Contents

| Path | Content |
|---|---|
| `train.sh` | reproduces the question plan, calibration fit and holdout evaluation |
| `configs/calibration.yaml` | calibration run config (extends `/code/configs/sales.yaml`) |
| `data/train/` | the organizer train split, unchanged (data, rulebook, calendar, questions, answers) |
| `logs/question-plan/` | business split and question partitions (seed 12, 100 per business) |
| `logs/calibration.jsonl`, `logs/holdout.jsonl` | predictions of the submitted runtime |
| `logs/calibration-artifact.json` | fitted artifact, identical to the deployed one |
| `logs/final-config.yaml` | calibrated config used for the holdout run (local paths) |
| `data_manifest.json` | every file here with path, size and SHA-256 |
| `requirements.txt` | pinned dependencies (same as inference) |

## Reproduce

```bash
bash /code/training_code/train.sh
```

Writes to `${DATAMATE_WORK:-/tmp/datamate-calibration}`. Steps:

1. `scripts/prepare_evaluation.py`: split the 30 train businesses with seed 12 into
   development (18), calibration (6) and holdout (6); no labels are read.
2. `datamate evaluate` on the calibration questions with the uncalibrated
   `execution_baseline` policy; labels are read only after each answer is written.
3. `datamate calibrate`: fit the per-bucket Wilson lower bounds; requires at least 20
   answers from at least 3 businesses for a bucket to be usable.
4. `datamate evaluate` on the holdout questions with the calibrated policy.

Hardware: any x86-64 CPU; no GPU. Runtime: a few minutes after snapshot ingestion.
Everything is deterministic: there is no sampling and no model call. The runtime
signature in the artifact binds the exact source files and settings; the image build
fails if they differ.

## Recorded result (submitted runtime)

Calibration: 600 questions, 48 correct, 0 wrong. Buckets: `sales_model:revenue`
38/38 over 5 businesses, lower bound 0.908 (usable); `sales_model:order_count` 9/9,
fewer than 20 samples (not usable). Holdout: 480 questions, 41 correct, 0 wrong.
