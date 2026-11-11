# Submission checklist

- [x] Development package, CLI, dependencies and CPU regression tests.
- [x] Source preservation, local snapshots, typed outputs and resumable writer.
- [x] Positive SQL policy, child deadline, raw generated Python disabled.
- [x] Windows Ollama baseline, typed PandasCoder schema, local PA scorer and resume commands.
- [x] Single-model offline export with blob hashes; source-only Docker recipe and Vietnamese submission guide.
- [x] Local image built and CPU synthetic inference completed with network disabled,
  read-only input and per-question logs (1 correct, 0 wrong, 2 abstentions).
- [x] PandasCoder independently emits supported typed operations from original
  evidence; relational/conditional operations and isolation have synthetic tests.
- [x] Actual 4B/9B checkpoints installed/pinned, local structured generation passed;
  all selected GGUF blobs hashed and stored tensor elements counted.
- [ ] Every hard official rule independently audited with source evidence.
- [ ] Full dirty-data/business/time semantics verified on released inputs.
- [ ] Eligible pinned checkpoints loaded and compatibility tested on GPU.
- [ ] Total parameters legally audited, including auxiliary components.
- [ ] Full semantic verification, calibrated decisions and held-out baseline.
- [ ] Official validator/evaluator executed successfully.
- [ ] Docker built/run offline under the organizer's actual resource limits.
- [ ] Runtime/VRAM/accuracy/coverage benchmarked; ablations recorded.

Current state: IMPLEMENTED_BUT_NOT_VERIFIED. The earlier calibration job stopped
when the runtime changed. No completed calibration/holdout is claimed; the
calibrated final policy and two-model image are not yet verified.
See QWEN35_RUNBOOK.md and IMPLEMENTATION_STATUS.md for current evidence.
