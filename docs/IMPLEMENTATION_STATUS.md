# Implementation status

Updated 2026-11-04 (Asia/Bangkok). Deliverable: working development code base.
Overall: **IMPLEMENTED_BUT_NOT_VERIFIED** for competition deployment.

| Spec phase | Status | Evidence / remaining work |
|---|---|---|
| 0 bootstrap/audit | DONE_TESTED for CPU/dev | Install, CLI, config inheritance, typed boundaries, local audit, official fail-closed |
| 1 deterministic foundation | IN_PROGRESS; ingestion smoke DONE_TESTED | All 30 businesses load; CSV/XLSX/SQLite, raw/typed provenance, catalog, join proposals, grounding, source-span rules and time/calendar subset tested; full dirty-data/business semantics remain incomplete |
| 2 model/execution | IN_PROGRESS / BLOCKED_BY_GPU | Fake/local clients, structured planning, bounded repair, AST policy and child executor tested; no real model load or compatibility benchmark |
| 3 FAST/output/evaluation | IN_PROGRESS | Deterministic routing, FAST-to-SLOW escalation, typed output, crash/resume, local diagnostics tested; full semantic proof and official evaluator absent |
| 4 independent branch | IN_PROGRESS | Typed DataOps subset, clean-room prompt payload, disagreement/false-consensus regression tests; no full join DSL or targeted differential fixture generation |
| 5 calibration/ablation | NOT_STARTED | Disjoint business split and utility helper only; no fitted artifact or accuracy/latency claims |
| 6 container/submission | BLOCKED_BY_OFFICIAL_KIT / BLOCKED_BY_GPU | CPU Docker recipe exists; Docker Engine unavailable locally, image not built; official entrypoint/validator/limits pending |

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
- `datamate test-fixtures`: actual read-only SQL and typed DataOps smoke PASS.
- E2E predict/validate/resume on synthetic inputs: PASS; predictions intentionally abstain.
- Official-mode rejection with verification booleans toggled: tested.
- Docker daemon probe: NOT_AVAILABLE; no container PASS claimed.

Tests include 17 unsafe SQL patterns, source hash preservation, BOM/delimiter,
merged cells/multiple regions, ambiguous numbers/dates, Decimal width regression,
NULL vs zero, output typing/precision, duplicate IDs, resumable output, hard budgets,
false fanout consensus, independent branch isolation/disagreement and FAST fallback.
Numeric regression tests also cover more than 28 significant digits through parsing,
SQL/DataOps execution, canonicalization and exact JSON serialization.

## Known implementation limits

This is a usable foundation, not all acceptance gates in the master spec. The
fixture suite has three illustrative questions plus parameterized tests, not the
full proposed 20–30 question synthetic corpus. Some hostile/irregular Excel layouts,
source group-fill/deduplication, SQLite views, exhaustive time semantics, compiled
business metrics, schema retrieval/index persistence, JOIN proof and complete
lineage remain incomplete. See DATA_CONTRACTS.md and THREAT_MODEL.md.

Model execution success or SQL/DataOps agreement never authorizes an answer in the
current uncalibrated policy. `predict` intentionally abstains. Synthetic smoke
programs execute outside that production decision boundary and never memorize
train/public/private answers. No competition labels were used to tune this base.

## Next implementation milestones

1. Review real-data ingest reports; implement verified business transformations
   for grouped blanks, duplicate exports, totals and schema variants.
2. Load pinned eligible 4B/9B weights on Linux/GPU, audit every deployed component,
   test chat template/structured generation and record actual model identities.
3. Add proof-carrying metric/time/filter/join checks; build a measured baseline.
4. Integrate organizer evaluator/validator and deployment contract.
5. Fit held-out confidence calibration and measure ablations/runtime before
   enabling non-abstaining model predictions and official mode.
