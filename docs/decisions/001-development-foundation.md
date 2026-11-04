# ADR 001 — CPU foundation and official boundary

2026-11-04. Accepted for this code base.

Use a src-layout package, DuckDB snapshots, typed contracts, subprocess execution
and a CPU test suite before downloading models or building services. Develop with
installed Python 3.12. Keep GPU/vLLM outside CPU dependencies. Planner/DataOps share
one small-model client; SQL roles share one reasoner client. No Redis/cloud API.

Keep raw mirrors and parser ambiguities. Restrict SQL to a positive grammar and
use a typed DataOps subset rather than generated Python. Reject unproven JOIN
answer semantics. Abstain until semantic verification/calibration are implemented;
label synthetic execution demos explicitly.

The local rulebook supplies scoring/output text but not the executable evaluator,
model-accounting policy or deployment contract. Official mode stays blocked until
those components are integrated with evidence. CPU source completeness and model
eligibility are different gates.
