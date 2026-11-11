# ADR 001 — CPU foundation and official boundary

2026-11-04. Accepted for this code base.

Use a src-layout package, DuckDB snapshots, typed contracts, subprocess execution
and a CPU test suite before downloading models or building services. Develop with
installed Python 3.12. Keep GPU/vLLM outside CPU dependencies. Planner/PandasCoder share
one small-model client; SQL roles share one reasoner client. No Redis/cloud API.

Keep raw mirrors and parser ambiguities. Restrict SQL to a positive grammar and
use typed JSON pandas operations interpreted by project code. Reject unproven JOIN
answer semantics. Abstain until semantic verification/calibration are implemented;
label synthetic execution demos explicitly.

The local rulebook supplies scoring/output text but not the executable evaluator,
model-accounting policy or deployment contract. Official mode stays blocked until
those components are integrated with evidence. CPU source completeness and model
eligibility are different gates.

Terminology aligned on 2026-11-10 with `architecture.mmd`: PandasCoder is the
independent 4B role. Earlier DataOps/DSL names remain only in implementation
identifiers. This ADR records the original conservative decision boundary; the
in-progress uncalibrated execution baseline is not evidence that the target
semantic/calibration gates have passed.
