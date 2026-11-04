# Development runbook

Use Python 3.12 and the README commands. Source-checkout configs/fixtures are
explicit filesystem resources; a wheel packages prompts but does not implicitly
install personal data/config. Pass a config path in deployed environments.

1. Install CPU requirements and project.
2. Run preflight on dev.yaml.
3. Inspect all businesses; review outputs/ingestion_report.json and catalogs.
4. Run test-fixtures, pytest, ruff and mypy.
5. Connect local models only after checking revisions, eligibility, parameter
   accounting, chat templates and constrained-JSON compatibility.

Predict with disabled models writes explicit abstentions. Evaluate reads labels
only when explicitly requested and computes a local exact-match diagnostic.
It never supplies labels to inference or creates an answer lookup.

Use --resume with identical input/config/output. Checkpoint fingerprints bind
data, rules, calendar, config, question bytes and pipeline version. Completed
lines are fsynced and final output atomically replaces .partial. Resume discards
only an interrupted final line; corrupt complete lines, mismatched IDs or changed
fingerprints fail loudly. A new path starts a fresh run. Concurrent writers to
the same prediction path/cache key are not supported.

Official preflight returns nonzero deliberately. All-business ingestion accumulates
failures and returns nonzero if any business fails. Per-query model/execution
errors produce abstain; invalid inputs fail before writing predictions. Unexpected
infrastructure bugs may interrupt a run; resume retains completed records.

GPU accuracy, calibration, full semantics and official submission validation are
separate milestones. Do not interpret a valid JSONL file as a validated solution.
