# Development runbook

Use Python 3.12 and the README commands. Source-checkout configs/fixtures are
explicit filesystem resources; a wheel packages prompts but does not implicitly
install personal data/config. Pass a config path in deployed environments.

Follow `architecture.mmd`: the SLOW branches are SQLCoder and independent
PandasCoder. PandasCoder emits typed JSON operations; the isolated project
interpreter calls pandas. Code/config still use legacy names such as
`DataOpsCandidate`, `dsl_coder.md` and `max_dsl_source_rows`. These names do not
authorize generated Python execution or access to the SQL branch's PlanIR/results.

1. Install CPU requirements and project.
2. Run preflight on dev.yaml.
3. Inspect all businesses; review outputs/ingestion_report.json and catalogs.
4. Run test-fixtures, pytest, ruff and mypy.
5. Connect local models only after checking revisions, eligibility, parameter
   accounting, chat templates and constrained-JSON compatibility.

Predict with disabled models writes explicit abstentions. Evaluate reads labels
only when explicitly requested and computes a local rule-text diagnostic.
It never supplies labels to inference or creates an answer lookup.

Use `datamate score --predictions PATH --labels PATH` to score existing predictions
without inference. See EVALUATION_CONTRACT.md for matching rules and limitations.
`configs/public.yaml` selects the public split's own rules and calendar; it does
not enable training or lift the production abstention gate.

Use --resume with identical input/config/output. Checkpoint fingerprints bind
data, rules, calendar, config, question bytes and runtime source/prompt hashes.
The current CLI publishes and fsyncs each completed line directly to the submission
path; the writer also retains its atomic `.partial` mode for other callers. Resume discards
only an interrupted final line; corrupt complete lines, mismatched IDs or changed
fingerprints fail loudly. A new path starts a fresh run. Concurrent writers to
the same prediction path/cache key are not supported.

Official preflight returns nonzero deliberately. All-business ingestion accumulates
failures and returns nonzero if any business fails. Per-query model/execution
errors produce abstain; invalid inputs fail before writing predictions. Unexpected
infrastructure bugs may interrupt a run; resume retains completed records.

GPU accuracy, calibration, full semantics and official submission validation are
separate milestones. Do not interpret a valid JSONL file as a validated solution.
