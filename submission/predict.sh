#!/usr/bin/env bash
set -euo pipefail
cd /code
mkdir -p /result/logs
export PYTHONPATH=/code/src
exec python -m datamate submit \
  --config /code/calibration/deployment-config.yaml \
  --split /data \
  --result /result \
  --resume
