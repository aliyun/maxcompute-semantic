---
name: query
description: Use when answering MaxCompute data questions, writing SQL, inspecting schema for a query, using cold-start live metadata, reviewing SQL, cost-gating, executing SQL, or recording verified/failed query memory.
---

# mcs query workflow

## Hard rule

Never run `mcs build` or `mcs package propose` while answering a query.
`mcs build` is maintenance / onboarding work and must be run only when
the user explicitly asks to build, refresh, onboard, or maintain a profile.

## Profile resolution

All `mcs` commands auto-resolve the active profile via `--profile` →
`MCS_PROFILE` → cwd-link → env-var fallback. Do not pass identity flags
unless you need to override. Do not `cd` before running `mcs`; cwd-link
binding is keyed on the starting directory.

Use `mcs -f json ...` whenever the next step depends on parsing output.

## Read-only by default

`mcs sql execute` and `mcs sql submit` are read-only by default. The same
guard is enforced by the MaxCompute client APIs unless a managed write path
sets `allow_write=True`. They refuse INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/
ALTER/TRUNCATE/GRANT/REVOKE, session mutations such as `SET`, and any
statement sqlglot can't classify as a known read shape *before* the cost gate,
returning a `WriteOpRejected` envelope (exit code 2). Pass `--allow-write`
only when the user explicitly asked for a write; it confirms write intent but
does not skip the cost gate. For UDF lifecycle use `mcs udf *`; for profile
rebuilds use `mcs build`.

## Workflow

1. Understand the question: metrics, fields, time range, filters, candidate tables.
2. Try package-backed schema:
   ```bash
   mcs -f json show
   mcs -f json show --table T
   mcs -f json show --tables T1,T2,T3
   ```
3. If no semantic package exists, use live metadata:
   ```bash
   mcs -f json meta list-tables
   mcs -f json meta search-tables KEYWORD
   mcs -f json meta search-columns KEYWORD
   mcs -f json meta describe-table TABLE
   ```
   See `references/cold-start.md`.
4. Probe ambiguous low-cardinality literal values before final SQL. See
   `references/value-discovery.md`.
5. Compose SQL using `references/rules.md`, `references/sql.md`,
   `references/projection.md`, and `references/from-table.md` (FROM-table
   choice + join cardinality / `COUNT(DISTINCT)` discipline). Before
   deriving an aggregation from scratch, check for a named metric — the
   user may have vetted the math once already:
   ```bash
   mcs -f json metric list
   mcs -f json metric show <name>
   ```
   If one matches, copy its `expression` verbatim instead of
   reimplementing it (same name should mean the same number). Then run
   the **SQL correctness checklist** below before finalizing the SELECT.
6. Review SQL:
   ```bash
   mcs -f json sql review '<SQL>'
   ```
   Fix every error-severity issue. If the envelope has
   `review_mode: syntax_only` and `semantic_checks_skipped: true`, the
   profile has no package; fix syntax / dialect / tier issues, ignore
   missing semantic hints, and continue with cold-start metadata. Do not
   run `mcs build` to make review more complete.
7. Cost-gate real table scans:
   ```bash
   mcs -f json sql cost '<SQL>'
   ```
   Read the JSON `verdict`; the command exits 0 even on `blocked`.
8. Choose the execution mode from the cost verdict and query shape:
   - `verdict=blocked`: do not run; explain the cost and add a tighter
     partition filter, predicate, or preview `LIMIT`.
   - `verdict=confirm`: ask the user. After confirmation, prefer async
     `submit` / `wait` / `result` and pass `-y` so the confirmed query does
     not stop at the non-TTY cost prompt.
   - `verdict=ok` (or cost skipped because the SQL is clearly tiny): use
     synchronous `execute` only for probes and small-result queries:
     `SELECT 1`, schema/value probes, explicit small `LIMIT` previews, or
     tightly partition-filtered lookups/aggregations expected to finish in
     the current turn.
   - Use async for final analytical scans, joins, or aggregations over real
     tables; SQL without a tight partition/filter/`LIMIT`; any query after a
     prior timeout; or any query the user says can run in the background.
   Synchronous path:
   ```bash
   mcs -f json sql execute '<SQL>'
   ```
   Async path:
   ```bash
   mcs -f json sql submit -y '<SQL>'
   mcs -f json sql wait <instance_id>
   mcs -f json sql result <instance_id>
   ```
   If `submit` returns `data.status == "Submitted"` with
   `data.status_probe_error`, the SQL was submitted but the immediate status
   probe failed. Keep `data.instance_id` and use `sql status` / `sql wait`
   later.
   Read `data.lifecycle_state`, `data.terminal`, `data.successful`, and
   `data.task_statuses[].status_name`; do not decide from raw
   `data.status == "Terminated"` because MaxCompute's instance status can be
   terminated even when the task failed or was cancelled. Call `result` only
   after `data.lifecycle_state == "success"`.
   `execute` and `result` cap returned rows at 10000 by default without
   rewriting SQL. If `data.has_more` is true, fetch the next page with
   `--offset <data.next_offset>`; use `--max-rows N` to change page size.
9. If the user confirms correctness, record:
   ```bash
   mcs memory verify --question Q --sql '<SQL>' --tables T1,T2
   ```

## SQL correctness checklist (inline)

The highest-leverage rules from the references — they apply to almost
every query, so they live in the workflow body, not behind `--full`.
Load `mcs skill get query --full` only when you need the worked examples.

**Projection — SELECT only what the question names.**

- "which / who / list X" → project the one column naming X (its label or
  id). "what is the highest / total / average X" → project the scalar
  aggregate of X, not the group it falls under.
- Columns used only in WHERE / JOIN / ORDER BY are filter / ranking
  signal, not output. Don't project intermediate values either.
- Don't wrap with `ROUND` / `CAST` / `CONCAT` unless the question asked
  for that format — it breaks exact result-set comparison.
- One statement per answer: a compound "what is X and list Y" is one
  JOIN-ed SELECT, not two `;`-separated queries.

**FROM — the subject of the question decides the FROM table.**

- "how many / what percentage of X" → `FROM x_table` with
  `COUNT(x_table.pk)`; pull filter tables in via JOIN. Don't count from a
  fan-out child table (it inflates the denominator).
- A filter column living in another table → JOIN to it; don't switch the
  FROM to the filter's table.

**Join cardinality — read the `joins_to [..]` markers in `mcs show`.**

- After a `[1:n]` JOIN where the partner is only in WHERE, use
  `COUNT(*)`. Reach for `COUNT(DISTINCT pk)` ONLY when the question says
  "distinct / unique / different X" or a partner column is in SELECT.
  Defensive `DISTINCT` on every 1:n JOIN changes the answer.

## References

- Cold-start query path: `references/cold-start.md`
- Projection discipline: `references/projection.md`
- FROM-table choice + join cardinality: `references/from-table.md`
- Literal value probing: `references/value-discovery.md`
- MaxCompute rules (syntax, column markers, tier-aware table form): `references/rules.md`
- Advanced SQL: `references/sql.md`
