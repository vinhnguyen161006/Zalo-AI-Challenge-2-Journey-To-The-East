# Execution trust boundaries

Source files/cells/README/rulebook/model outputs are untrusted. Data is placed in
user payloads, not system prompts. Prompt isolation reduces exposure; it does
not prove model immunity to injection. Generated SQL is validated independently.

SQL has a positive sqlglot grammar and analytic-function allowlist, known table
scope, single-statement checks and explicit-ON joins. External readers, catalogs,
table functions, unknown functions, recursive CTEs, DDL/DML, attach/copy, extension
installation and raw mirrors are rejected. A child process validates again and
opens a DuckDB snapshot read-only, disables external access/extension autoload,
limits memory/threads, bounds results and has a parent-enforced timeout. Results
are rejected when too large, never truncated into an answer.

Child environment variables are filtered; Linux also sets CPU/file-size limits.
This is **not a full OS sandbox**: Windows DuckDB limits do not bound all Pandas
allocations, and workers share their user's filesystem/network permissions.
Only our own typed DataOps interpreter runs, with no free-form expressions,
eval, exec, shell or generated imports. Arbitrary model-written Python is disabled.

For deployment, use a nonroot container, --network none, read-only source mounts
and dedicated writable outputs. The Docker recipe is not organizer-certified.
Native-library vulnerabilities, hostile SQLite files and decompression bombs
need further hardening. Input file-size/count limits do not bound every workbook
allocation; use the trusted competition release.

Model clients allow only loopback HTTP, bypass proxy inheritance and reject
redirects. Actual model identity/weights/serving compatibility remain unverified.
Traces store IDs, hashes and reason codes rather than source rows/prompts/programs.
Catalogs/raw snapshots contain business data and remain gitignored in outputs/.
