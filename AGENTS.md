# Project instructions

Read `CODEX_MASTER_SPEC.md`, `architecture.mmd` and `docs/IMPLEMENTATION_STATUS.md`
before modifying the system. Organizer evidence takes precedence over assumptions.

Preserve source data and existing user changes. Never commit train/data, weights,
snapshots, secrets or outputs. Use synthetic fixtures for regression tests. Keep
CPU development independent of torch/CUDA. Do not add cloud inference or raw
generated Python execution. Retain AST allowlisting and isolated SQL execution.

Update status honestly: synthetic smoke, local format validation and stored-tensor
counting are not official evaluator/eligibility or GPU results. `predict` currently
abstains deliberately until semantic verification and calibration are implemented.

Run pytest, ruff check/format-check and mypy for changes to the runtime. See README
for exact commands. Do not create commits or push changes unless asked.
