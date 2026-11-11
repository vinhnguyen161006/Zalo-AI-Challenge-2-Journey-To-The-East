#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "${DATAMATE_START_OLLAMA:-0}" = "1" ]; then
    mkdir -p "${DATAMATE_RESULT:-/result}/logs"
    ollama serve > "${DATAMATE_RESULT:-/result}/logs/ollama.log" 2>&1 &
    server_pid=$!
    trap 'kill "$server_pid" 2>/dev/null || true' EXIT INT TERM
    python scripts/wait_ollama.py
fi
python -m datamate submit --config "${DATAMATE_CONFIG:-configs/ollama.yaml}" --split "${DATAMATE_SPLIT:-/data}" --result "${DATAMATE_RESULT:-/result}" --resume
