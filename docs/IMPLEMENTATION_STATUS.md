# Implementation status

Updated 2026-11-11 (Asia/Bangkok). Deliverable: runnable local baseline; project incomplete.
Overall: **IMPLEMENTED_BUT_NOT_VERIFIED** for competition deployment.

## Current packaging update

GitHub handoff preparation: README now documents a fresh Windows/Linux package
setup, separate data/weights, pinned Ollama models, offline Docker build,
calibration/holdout/public orchestration, resume, progress and submission paths.
Removed 20 generated cache directories only; data/models/outputs and the running
calibration container were retained. Git/Docker ignore rules now also exclude
GGUF weights, image archives, logs and private-key files. Candidate source scan:
155 files, no matched secret signatures, prohibited data paths or files over 5 MB.
Full Windows regression rerun passed **202 tests in 70.54 seconds**; Ruff check,
format-check (92 files), mypy (53 runtime files) and synthetic CLI smoke passed.
The first sandbox test attempt had Temp permission errors; the rerun outside that
restriction passed. This preparation does not complete calibration or public inference.
The staged-file-only checkout also exercised all 202 tests: 201 passed on its
first run; the first test had a setup error because the explicitly chosen
basetemp's parent did not yet exist. After creating `outputs/`, that test passed
on rerun (0.60s), followed by SYNTHETIC_CPU_PASS from that checkout. README now
documents creating the parent when selecting a custom basetemp. No runtime fix.

`scripts/watch_progress.ps1 -Watch` now provides a readonly live host view of
workflow stage, completed calibration/holdout/public record counts, container
state and the latest active question/model role from Docker logs. Tested against
the running container: 2/120 calibration records, 0/120 holdout, 0/1000 public.
Counts include abstentions and are not accuracy. It does not alter the frozen
image or restart inference. `-NoDocker` supports file-only inspection.

Docker Desktop storage is now `E:\DockerDesktopWSL`; the engine responds again.
The existing 3B baseline image retained its original image ID and the two unrelated
containers remain present. The two-model image was built with pinned local
weights. Current workflow image is
`sha256:03b7058324dfda18aa64bfeac795a8aea6f27e921feae20e3eb5364ffa3bafaf`
(`datamate-local:qwen35`). It inherits the audited runtime and weights unchanged.
Both checkpoints passed actual structured generation in an offline CPU container.
The readonly in-image GGUF audit counted 13,949,079,280 stored tensor elements;
this is not official eligibility certification. Linux regression: **202 passed**
in 50.56 seconds. The deployment/artifact mismatch rejection test also passed.

New-image CPU submission smoke (`outputs/docker-qwen35-smoke-01/`): 1 correct,
0 wrong, 2 abstentions; local diagnostic PA 0.3333. The count question abstained
on candidate disagreement. Do not substitute the earlier Windows fixture score.
Docker can access the local Quadro T1000; the 4B checkpoint partially offloaded
(58% GPU), but the real JSON generation check timed out at 120 seconds. This
GPU path has not passed generation and is not selected for the current run.
The host controller now accepts `-UseGpu`, persists it with the run settings,
and passes `--gpus all` to training and public containers when selected.
GPU prepare-only orchestration passed; this does not imply inference passed.
The CPU workflow was launched in `outputs/docker-finalization-cpu12/` with
12 logical CPUs and a 12 GB memory limit. Its status/log files are authoritative;
launching it does not establish calibration, holdout accuracy or a public result.

The native 32K calibration client was deliberately stopped after 12 completed
records to free resources and move final calibration into the Linux deployment
environment. Completed predictions and traces were preserved; no artifact or
holdout result was produced. Its `status.json` reports the forced client exit.

`train.sh` and `scripts/finalize_in_container.py` orchestrate train-only calibration
and business-disjoint holdout inside the frozen image. A prepare-only host check
created the expected 120/120 partitions without reading label contents. The release
directory is created only after sufficient calibration and completed holdout;
`Dockerfile.calibrated` rejects a calibration/runtime signature mismatch at build.
These orchestration additions do not change the frozen inference runtime.
Ruff check/format-check (109 files) and mypy (53 runtime files) passed.
Linux verification above is complete; calibration/holdout and full public inference
remain incomplete. Earlier running/storage notes below are historical.

## Current two-model work (2026-11-11)

Both target checkpoints are now installed: Qwen3.5 4B Planner/PandasCoder and
Qwen3.5 9B SQLCoder/repair, Q4_K_M. Both passed actual structured generation.
The GGUF audit verified hashes and counted 13,949,079,280 stored elements,
including projectors; see MODEL_PARAMETER_REPORT.md for publisher release/license
evidence and the limits of this count. No fine-tuning or extra neural model.

Runtime additions: relational/conditional pandas operations and source preparation;
monthly workbook union views after date-partition checks; README date convention
and rulebook sheet-year parsing; declared SQL predicate/metric/group/order checks;
scope-aware JOIN checks through pre-aggregated CTEs; bounded counterexamples;
typed scalar FAST templates; a narrow cited status-rule compiler; empirical
calibration bins with disjoint businesses and frozen runtime/model/settings hashes.
The independent branch may run before SQL to avoid repeated 4B/9B swapping.
No SQL/PlanIR/result is passed to PandasCoder. AST allowlisting and isolated,
read-only SQL workers remain enabled. Unsupported proofs do not become guarantees.

Real model fixture `outputs/qwen35-fixture/run03.jsonl`: 2 correct, 0 wrong,
1 abstention; local PA 0.6667 and LOCAL_FORMAT_PASS. Question processing was
63.76, 54.88, 15.52 seconds. This run preceded later table-comparison and
snapshot-sampling fixes, so it is integration evidence, not final-runtime validation.
Latest completed full suite before those final fixes: 184 passed in 70.64 seconds.
Earlier suite: 185 passed in 64.87 seconds (`outputs/pytest-system-frozen-nov11`).
Ruff and mypy passed (50 source files); this is CPU contract validation.

The earlier workflow in `outputs/qwen35-finalization-nov11/` stopped after the
runtime freeze detected edits. It did not fit calibration or evaluate holdout.
Those results must not be described as a running successful finalization job.
The current full CPU suite passed **202 tests in 115.95 seconds**; Ruff check,
format-check (108 files) and mypy (53 source files) passed. Calendar resolution
now reads the cited week start and Tet boundaries from each split's rules.

The single-9B fallback finished one inspected development question in 988.94s:
abstain, six model calls. SQL executed, but omitted rule references; its PlanIR
also declared unresolved semantics. This is not an accuracy benchmark. SQL
generation now requires the exact declared rule-reference array, and exact ISO
resolved windows are bound to half-open time semantics. Final evaluation uses
seed 12 so all three businesses inspected during debugging are development-only.
The new workflow defaults to 120 calibration and 120 holdout questions from
disjoint businesses, records `status.json`, and refuses an insufficient
calibration artifact. No calibrated final config or held-out PA is available yet.
See QWEN35_RUNBOOK.md.

Current frozen-runtime fixture (`outputs/qwen35-fixture/final-model-nov11.jsonl`):
2 correct, 0 wrong, 1 abstention, local PA 0.6667; LOCAL_FORMAT_PASS (3 records).
Five model calls, 285.78 question-seconds total, p50 91.984s, p95 106.299s;
startup is excluded. The initial evaluate command pointed to a nonexistent
fixture label filename, so scoring was rerun separately using `labels.jsonl`;
no inference or answers were changed. This remains synthetic integration evidence.

The 16K workflow in `outputs/final-seed12/` (PID 12192) was stopped after the
first prompt demonstrated insufficient reserved context. It has a failed status,
not a completed calibration. Evaluation configs now use 32K. A fresh workflow
was started in `outputs/final-seed12-32k/` with persistent status/stdout/stderr.
It uses the two-checkpoint seed-12 config and 120 calibration questions. The
actual 9B backend separately passed exact constant-array schema compatibility
on synthetic metadata (24.187s, 32 output tokens). **No calibration artifact,
holdout PA or completed long-run result is claimed yet.**

`models/qwen35-export/` uses verified hardlinks to selected blobs. The earlier
3B Docker image is historical. No two-model image build/offline smoke, full public
run, target GPU throughput or official eligibility/evaluator pass is claimed.
The attempted single-9B build exhausted host disk during model COPY and was
cancelled; no new image succeeded. Verified duplicate 3B export copies were
replaced by hardlinks to unchanged installed blobs, recovering about 1.8 GiB.
After cancellation about 4 GiB was free. No source data or unrelated Docker
cache/image/container was removed. Docker was unresponsive after exhaustion;
additional disk space is required before rebuilding. Model COPY now uses an
independent linked layer to permit reuse across runtime/document changes.
The following baseline section is historical context, not the selected two-model
configuration or a claim that the whole competition solution is complete.

## Current architecture and work in progress

The independent branch is **PandasCoder**, as named in `architecture.mmd`:
Qwen3.5-4B target, original question plus source evidence, typed JSON pandas
operations, project interpreter, no generated Python/eval/exec. It must not see
SQLCoder's SQL, PlanIR or results. There is no separate DataOps branch.
`DSL4`, `DataOpsCandidate`, `dataops_dsl.py`, `dsl_coder.md`, `independent_dsl`
and `max_dsl_source_rows` remain legacy implementation identifiers.

The ongoing Windows baseline uses an already installed Qwen2.5-Coder 3B Q4_K_M
checkpoint through Ollama, sharing weights across logical roles. It is a local
development configuration, not a replacement for the 4B/9B target architecture.
`execution_baseline` can return executed answers with confidence unset; this is
not calibrated correctness. Default configs retain the abstention policy.

Latest completed real-model fixture run, `outputs/ollama-fixture/run08.jsonl`:
2 correct, 0 wrong, 1 abstention, local PA 0.6667. The missing-measure question
abstained; no unanswerable classification is claimed. Local format validation passed.
Earlier runs exposed false consensus and planner/source mismatches, so execution
agreement must not be interpreted as semantic proof.

Real train smoke, `outputs/ollama/train-smoke-04.jsonl`: the first training question
was answered correctly, with independent SQL/pandas agreement, in 375.641 seconds
and 3 model calls using the 16K configuration. The prior 32K run took 606.73 seconds
for the same question. These are single-question measurements, not a held-out
benchmark or an estimate of full-split accuracy. The earlier 3-question smoke
abstained on all three because its 8K context configuration was too small.

The synthetic suite passed **153 tests** (`outputs/pytest-context-final-nov10`,
51.57 seconds). Ruff check/format-check passed (65 files); mypy passed (42 source
files). The final repeat after separating per-run trace directories also passed:
153 tests in 87.54 seconds (`outputs/pytest-resumed-final-nov10`).
These checks validate implementation contracts, not model answering accuracy.

Added compact typed generation schemas, lossless references for repeated Excel
column schemas, missing-measure early abstention, model-digest preflight, incremental
submission output, per-run evaluation logs, and support for the nested alias shapes
observed in released training labels. The scorer now reads all 3,000 train labels.
See `docs/SUBMIT_VI.md` for Windows evaluation and public submission commands.

Exported the installed checkpoint with SHA-256 checks to the Git-ignored
`models/ollama-export/`. `Dockerfile.offline` and `predict.sh` package and start the
local model server. After the user freed disk space, the image built successfully.
Tested image: `datamate-local:baseline`, ID
`sha256:c6e594a0bf45b9c9e6e94c20cf425462abd7e99b933ee7a57b1359234fe4c0e5`,
size 12,893,130,375 bytes. The three-question CPU smoke completed under
`--network none` with `/data` verified read-only and `/result` writable.
`outputs/docker-fixture-01/submission.jsonl`: 1 correct, 0 wrong, 2 abstentions,
local PA 0.3333, LOCAL_FORMAT_PASS (3 records). Per-question times were 140.208,
126.866 and 34.431 seconds (301.505 seconds total, excluding startup).
The count question abstained on SQL/pandas disagreement; the missing-measure
question abstained on invalid model output. Each trace contains answer and timing.
This differs from Windows run08, so cross-platform reproducibility is unresolved.
Linux Ollama reports the exported manifest digest; `ollama-docker.yaml` pins it
separately from the Windows revision without disabling model identity checks.
Latest regression: 153 passed in 57.46 seconds (`outputs/pytest-docker-final-nov10`);
Ruff check/format-check and mypy passed. Earlier WSL/disk failures are resolved
for this build; existing user images/caches and source files were not deleted.
No full public run, organizer-base-image, target-GPU or 6-hour pass is claimed.
Later documentation edits are not part of the image identified above.

## 2026-11-09 implementation and validation

- Reviewed architecture, runtime modules, tests, scripts and the historical master
  specification at Git revision `d1b9c68`. `CODEX_MASTER_SPEC.md` is absent from the
  current checkout; its deletion was preserved rather than silently restored.
- Baseline: **6 failed, 112 passed**. Fixed missing model-client token arguments and
  grounding-context arguments. Grounded source candidates now reach both branches
  without sharing SQL/PlanIR/results with the independent branch.
- Pipeline honors configured SLOW candidate count and records the serialized final
  answer alongside question ID and elapsed time in each trace.
- The PandasCoder interpreter reads up to `max_dsl_source_rows`, separately enforcing output row limits;
  a scalar aggregate can process more source rows than the output limit permits.
- Added local rule-text scoring and `datamate score`: alias-aware string matching,
  NFC normalization, ordered lists, unordered table rows, exact Decimal numbers,
  rounded percentages, null unanswerable references, missing-answer abstentions,
  and PA. See EVALUATION_CONTRACT.md for explicitly unresolved parity details.
- Added public split config using its own rules/calendar; excluded `/data` from
  Docker build context. No competition labels were used for these changes.
- Full synthetic suite: **137 passed**, using
  `python -m pytest -q --basetemp outputs/pytest-implementation-nov09 --tb=short`.
  The Windows sandbox denied pytest temporary-directory access, so the suite ran
  through an approved unsandboxed invocation. This is CPU validation only.
- Final checks: `ruff check src tests scripts` PASS;
  `ruff format --check src tests scripts` PASS (58 files);
  `mypy src/datamate` PASS (40 source files); `git diff --check` PASS.
- Train preflight: **3,000 questions / 30 businesses**. Public preflight:
  **1,000 questions / 14 businesses**. These are input checks, not inference scores.
- Hardware probe: Quadro T1000, 4,096 MiB VRAM. Docker CLI exists but its Linux
  Engine pipe is unavailable. No target GPU benchmark or Docker build is claimed.
  The configured local `models/` checkpoint directory is absent.

At that validation point `predict` deliberately abstained. Semantic proofs, a useful
real-model baseline, fitted business-held-out calibration and deployment remain unfinished.
The older validation and ingestion results below are historical, not rerun claims.

| Spec phase | Status | Evidence / remaining work |
|---|---|---|
| 0 bootstrap/audit | DONE_TESTED for CPU/dev | Install, CLI, config inheritance, typed boundaries, local audit, official fail-closed |
| 1 deterministic foundation | IN_PROGRESS; ingestion smoke DONE_TESTED | All 30 businesses load; CSV/XLSX/SQLite, raw/typed provenance, catalog, join proposals, grounding, source-span rules and time/calendar subset tested; full dirty-data/business semantics remain incomplete |
| 2 model/execution | LOCAL_TWO_MODEL_INTEGRATION / TARGET_GPU_UNVERIFIED | 4B/9B installed, pinned and structured generation passed; real fixture exercised; target hardware/time pending |
| 3 FAST/output/evaluation | IN_PROGRESS | Deterministic routing, FAST-to-SLOW escalation, typed output, crash/resume, local diagnostics tested; full semantic proof and official evaluator absent |
| 4 PandasCoder branch | SUPPORTED_OPERATIONS_CPU_TESTED | Independent relational/conditional operations, pre-aggregation and bounded differential snapshots tested; arbitrary pandas/Python unsupported by design |
| 5 calibration/ablation | RUNNING_CALIBRATION | Empirical bins, frozen signatures and business partitions implemented/tested; real calibration/holdout job running, no fitted success claim |
| 6 container/submission | LOCAL_CPU_OFFLINE_SMOKE_PASS / TARGET_GPU_UNVERIFIED | Image built; three synthetic questions completed offline with logs; cross-platform output differences, official base image and GPU/time verification pending |

## Completed validation

- Editable install in `.venv` on Python 3.12 / Windows.
- `python -m pytest -q --basetemp outputs/pytest-release --tb=short`: **98 passed**.
- `python -m ruff check src tests scripts`: PASS.
- `python -m ruff format --check src tests scripts`: PASS.
- `python -m mypy src/datamate`: PASS, 39 source files.
- `python -m pip check`: no broken requirements.
- `datamate preflight --config configs/dev.yaml`: 3,000 records, 30 business IDs.
- `datamate inspect --config configs/dev.yaml --all-businesses`: 30 loaded, 0 failed;
  340 tables/regions and 3,997,077 imported rows. See DATA_INGESTION_REPORT.md.
- Source hash recheck: all 158 package files match their ingestion fingerprints.
- `datamate test-fixtures`: actual read-only SQL and typed pandas operations smoke PASS.
- E2E predict/validate/resume on synthetic inputs: PASS; predictions intentionally abstain.
- Official-mode rejection with verification booleans toggled: tested.
- Docker daemon probe: NOT_AVAILABLE; no container PASS claimed.

Tests include 17 unsafe SQL patterns, source hash preservation, BOM/delimiter,
merged cells/multiple regions, ambiguous numbers/dates, Decimal width regression,
NULL vs zero, output typing/precision, duplicate IDs, resumable output, hard budgets,
false fanout consensus, independent branch isolation/disagreement and FAST fallback.
Numeric regression tests also cover more than 28 significant digits through parsing,
SQL/pandas execution, canonicalization and exact JSON serialization.

## Known implementation limits

This is a usable foundation, not all acceptance gates in the master spec. The
fixture suite has three illustrative questions plus parameterized tests, not the
full proposed 20–30 question synthetic corpus. Some hostile/irregular Excel layouts,
source group-fill/deduplication, SQLite views, exhaustive time semantics, compiled
business metrics, schema retrieval/index persistence, JOIN proof and complete
lineage remain incomplete. See DATA_CONTRACTS.md and THREAT_MODEL.md.

Model execution success or SQL/PandasCoder agreement is not proof of correctness.
The target policy requires semantic verification and calibration; the experimental
execution baseline is documented separately above. Synthetic smoke programs never
memorize train/public/private answers. No competition labels were used to tune this base.

## Next implementation milestones

1. Review real-data ingest reports; implement verified business transformations
   for grouped blanks, duplicate exports, totals and schema variants.
2. Verify the installed/pinned 4B/9B checkpoints on the target Linux/GPU machine,
   reconcile original parameter accounting and benchmark the final image.
3. Add proof-carrying metric/time/filter/join checks; build a measured baseline.
4. Integrate organizer evaluator/validator and deployment contract.
5. Complete the running calibration/holdout experiment; fit sufficient evidence
   bins and measure ablations/runtime before enabling the calibrated final policy.
