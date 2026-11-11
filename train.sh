#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p "${DATAMATE_RESULT:-/result}/logs"
ollama serve > "${DATAMATE_RESULT:-/result}/logs/ollama.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT INT TERM
python scripts/wait_ollama.py
python scripts/finalize_in_container.py \
  --config "${DATAMATE_CONFIG:-/app/configs/qwen35-docker.yaml}" \
  --split "${DATAMATE_TRAIN:-/train}" \
  --work "${DATAMATE_RESULT:-/result}" "$@"
