# Query — SQL Execution

> **Loaded on demand** — SKILL.md loads this when the user wants to
> query data, check cost, or explain a plan.

## Default workflow (cwd-bound profile, semantic package built)

The standard real-user setup binds the current working directory to a
profile (via `mcs link`) whose semantic package has been built once
during onboarding (`mcs build`). All `mcs` commands then auto-resolve
to that profile — **don't pass `--project / --schema / --profile`
unless you explicitly need to override**.

1. **Understand** the question: extract metrics, fields, time range, candidate tables.
2. **Look up schema** from the semantic package:
   ```bash
   mcs -f json show                       # overview: table list + JOIN graph + UDFs
   mcs -f json show --table T             # one-table columns, enum values, annotations, mined sample SQL
   mcs -f json show --tables T1,T2,T3     # batch — all of the above for several tables in one call
   ```

   JSON shape:
   - `mcs -f json show` returns profile overview plus `data.markdown`,
     whose YAML frontmatter has `sources[].tables[].columns_index`.
   - `mcs -f json show --table T` returns one table directly:
     read `data.columns[]` for physical columns, `data.identifiers[]`,
     `data.dimensions[]`, `data.metrics[]`, `data.join_candidates[]`,
     and `data.annotation_suggestions[]`. Do not expect
     `data.tables[0]` for new code; a compatibility alias exists only
     so older agent scripts do not fail.
   - `mcs -f json show --tables T1,T2,T3` returns batch entries under
     `data.tables[]`; each entry has `status`, `table`, and, on
     success, its own `columns[]`.

   Prefer `--tables T1,T2,T3` whenever the question touches more than one
   table (joins, multi-fact comparisons, candidate-table sweep): it's a
   single round-trip and returns a `{"tables": [...]}` envelope with one
   entry per table. Missing / ambiguous tables are reported inline as
   `{"table": "X", "status": "error", "error": {"code": ..., "message": ...}}`
   — the command still exits 0, so check each entry's `status` field.
   Use `--table T` only when you genuinely want exactly one table.

   When `mcs show --table T` returns `sample_sql_patterns`, every entry
   is `confidence=user_verified` — these are SQLs an operator explicitly
   marked correct via `mcs memory verify`. Use them as templates only
   when the verified question matches yours; otherwise let the schema +
   joins + annotations guide the SQL you write. (Mined query history
   does not surface here — it's too easy to template-match against a
   pattern that happened to share a literal with the current question.)

   **Join candidates** (`join_candidates` in JSON output) are *evidence-
   ranked suggestions*, not confirmed relationships. Each candidate has a
   `confidence` score, `evidence` list (workload, name heuristic,
   uniqueness), and `status` (`suggested` / `confirmed` / `conflicting`).
   Use them as hints when composing JOINs — but always verify the column
   actually exists and the join makes sense for the current question. A
   candidate with `status=conflicting` means another candidate for the same
   table pair has higher confidence; prefer that one instead.

   **Annotation suggestions** (`annotation_suggestions` in JSON output) are
   machine-generated role hints (identifier, dimension, metric, attribute)
   — they are NOT confirmed annotations. They help the agent decide column
   roles during SQL composition, but should not be treated as ground truth.
   If a suggestion contradicts your own analysis of the column, trust your
   analysis. To confirm a suggestion, load `mcs skill get enrich` and use
   the proposal workflow.
3. **Probe ambiguous filter values**: if the question references a literal
   value that maps to a low-cardinality enum or code column, run a probe
   before composing the final SQL. See
   [`references/value-discovery.md`](value-discovery.md) for triggers, the
   probe template, and anti-patterns. The probe is one `mcs sql execute`
   round-trip and prevents the largest class of silent-empty-result
   failures.
4. **Compose SQL** following MaxCompute syntax rules — see [`references/rules.md`](rules.md). Apply the projection-discipline rules below before finalizing the SELECT list.
5. **Review** the SQL with `mcs -f json sql review '<SQL>'` against the
   profile's package + memory (no MaxCompute round-trip). The envelope
   carries `{issues, hints, model_coverage}`:
   - **Fix every `error`-severity issue.** These are deterministic
     conformance failures (schema-not-found, sqlite-isms, tier
     mismatch, lexical-vs-chronological date compares).
   - **Weigh each hint against your understanding.** Hints carry an
     `if_misleading` text showing the exact `mcs package propose` /
     `mcs memory remove` command that would correct the underlying
     data if the hint is wrong. A wrong hint is data, not noise — fix
     it once and the next review is sharper.
   - `model_coverage` reports how much of the SQL the package
     understands; low coverage means schema discovery missed a table
     or column annotations are thin.

   Review is cheap (no MaxCompute) and stateless. Run it on every
   non-trivial query before the cost gate. If no semantic package exists,
   review returns a success envelope with `review_mode: syntax_only` and
   `semantic_checks_skipped: true`: fix any syntax / dialect / tier issues
   it reports, ignore missing semantic hints, and continue with cold-start
   metadata. Do not run `mcs build` to make review "more complete" during
   a query flow.
6. **Cost-gate** by default for real table scans: `mcs -f json sql cost '<SQL>'` → verdict `ok` (choose sync vs async from query shape), `confirm` (tell user cost, await OK, then prefer async), `blocked` (refuse, suggest LIMIT / partition filter). The command exits 0 even on `blocked` — read the JSON `verdict` field, never the exit code.
7. **Execute**: use synchronous `mcs -f json sql execute '<SQL>'` only for probes and small-result queries (`SELECT 1`, schema/value probes, explicit small `LIMIT` previews, or tightly partition-filtered lookups/aggregations expected to finish in the current turn). Use async `submit` / `wait` / `result` for final analytical scans, joins, aggregations, unbounded queries, prior timeouts, and confirmed-cost queries. `execute` and `submit` are read-only by default at both CLI and client layer; pass `--allow-write` only for user-confirmed DML/DDL or MaxCompute-specific write syntax. `--allow-write` does **not** skip the cost gate. Pass `-y` / `--yes` only after the user has accepted a `confirm` verdict; it has **no** effect on `blocked`.
8. **Record** (optional): user-confirmed success → `mcs memory verify`; failure → `mcs memory fail`. Include `--tables` for verified queries. See [`references/memory.md`](memory.md).

> **If `mcs show` reports `"no semantic package for profile …"`**,
> this profile hasn't been built yet. Switch to the cold-start
> workflow — read schema live via `mcs meta` — see
> [`references/cold-start.md`](cold-start.md). **Never run
> `mcs build` to answer a question**: it's a multi-minute MaxCompute
> scan, a maintenance / onboarding action the user invokes
> explicitly, not the agent in a query flow. Treat `mcs status`
> returning `build_status: no build data` as the same cold-start signal.
> `mcs sql review` remains useful in this state because it still runs
> package-independent syntax / dialect checks.

## SELECT projection discipline

Pick the **minimum** columns that answer the question. Extra
"helpful" columns turn a correct answer into a wrong one when the
caller compares result sets.

- **"Which / who / what / list / give me X"** → project the column
  that names X. Prefer a human-readable label (`*_name`, `*_title`,
  `*_label`) when one exists on the entity table; otherwise the
  primary identifier (`identifiers[].name` with `type=primary` in the
  `mcs show --table T` output, ranked by confidence). If two
  identifiers exist (e.g. `id` and `uuid`), prefer the shorter
  business-key form (`id`) unless the question specifically asks for
  the surrogate.
- **"How many / count / total / average / percentage / ratio /
  difference"** → project **one** scalar (one aggregate or one
  computed expression). Don't add a grouping key the question didn't
  ask for, and don't break the answer into per-category sub-
  aggregates unless the question asks for the breakdown.
- **Columns referenced in WHERE / JOIN / ORDER BY are filter signal,
  not output.** The agent often pattern-matches "the question
  mentions column X" → "include X in the SELECT". Don't. WHERE / JOIN
  / ORDER BY references are how you find the rows; SELECT is what the
  caller receives. The column you `ORDER BY ... DESC LIMIT 1` is the
  ranking key, not part of the answer — only the row identifier is.
- **Only add a column to the SELECT when the question explicitly
  names it.** "show the name and the date" → 2 columns;
  "which orders are pending" → 1 column (the order identifier).
  When the question says "show me … with their X", X joins the
  identifier.
- **Don't promote intermediate values to the projection.** If a
  CASE / IIF / ratio / difference is the answer, return only that
  expression — not the raw inputs alongside it. The intermediates
  belong in the expression body, not the SELECT list.
- **Don't apply display formatting unless the question explicitly
  asks for it.** `ROUND(x, 2)`, `CAST(... AS INT)` on a true real value,
  `CONCAT(x, '%')`, and similar transformations discard precision or
  change the type. Programmatic callers compare values, not display
  strings — wrapping a correct ratio in `ROUND(..., 2)` turns
  `33.33333...` into `33.33` and a value comparison then fails.
  Return the raw numeric expression; let the caller format for
  display. Apply formatting only when the question says "round to N
  decimals", "as a whole number", "with a percent sign", or similar.

### Pre-execution self-check

Read your SELECT list left-to-right before running `mcs sql execute`.
For each projected column, ask: *did the user explicitly name this in
the question?* Justifications that are NOT valid:

- "It's context the human reader would want"
- "It's the value I ordered by"
- "It's a step in the computation the final scalar uses"
- "It's the column the WHERE clause filters on"

If a column's only justification is one of the above, remove it. The
result set the caller compares against is a tuple of exactly the
columns the question asked for — no more.

For each value-transforming function in the SELECT (`ROUND`, `CAST`,
`CONCAT`, `FORMAT`, `STR_TO_DATE`, etc.), ask: *did the user
explicitly ask for this transformation?* If the only reason is "to
make it look nicer", remove it.

## Execution Plan (EXPLAIN)

```bash
mcs sql explain '<SQL>'
mcs sql explain --timeout 30 '<SQL>'   # cap how long mcs waits for the plan (default 120s)
```

Returns the plan text — useful for understanding JOIN order, scan
ranges, and stage boundaries before running.

## Cost Gate

`mcs sql cost '<SQL>'` returns a verdict against the active profile's
`cost_thresholds`:

- `ok` (estimate `< confirm_cny`) — proceed
- `confirm` (`confirm_cny ≤ estimate < blocked_cny`) — tell user cost, await confirmation
- `blocked` (estimate `≥ blocked_cny`) — refuse, suggest LIMIT / partition filter

Defaults are `confirm_cny=10.0` and `blocked_cny=100.0`; both are
per-profile knobs set via `mcs profile create --confirm-cny X --blocked-cny Y`
or edited later via `mcs profile update`. Never quote 10 / 100 CNY as
absolute boundaries to the user — read the verdict.

Run the gate before executing any SQL that scans real tables unless the
query is clearly tiny (`SELECT 1`, metadata-only, or a small preview with
an explicit `LIMIT`) or the user explicitly asks to skip the estimate.
**`mcs sql cost` always exits 0**, even on `blocked` — the verdict is in
the JSON payload (`-f json`), not the exit code. An agent that gates on
exit code alone will silently execute blocked queries.

For `verdict=confirm`, tell the user the estimate and ask before running.
After confirmation, prefer async `submit` / `wait` / `result` and pass `-y`
so the confirmed query does not stop at the non-TTY cost prompt. `--yes` is
ignored on `blocked` (still refused).

## Execution Mode

Do not use the vague "short query" label as the decision rule. Branch on cost
verdict and query shape:

- Synchronous `mcs sql execute`: `SELECT 1`, schema/value probes, explicit
  small `LIMIT` previews, or tightly partition-filtered lookups/aggregations
  expected to finish in the current turn.
- Async lifecycle: final analytical scans, joins, or aggregations over real
  tables; SQL without a tight partition/filter/`LIMIT`; any query after a
  prior timeout; any `verdict=confirm` query after user confirmation; or any
  query the user says can run in the background.

```bash
mcs -f json sql submit -y '<SQL>'          # returns data.instance_id immediately
mcs -f json sql status <instance_id>       # inspect data.lifecycle_state
mcs -f json sql wait <instance_id>         # wait until data.terminal is true
mcs -f json sql result <instance_id>       # fetch schema / rows / row_count
mcs -f json sql result --offset 10000 <instance_id>  # fetch next page
mcs -f json sql cancel <instance_id>       # stop a running instance
```

`submit` keeps the same read-only default and cost gate as `execute`; pass
`--allow-write` only for intentional DML/DDL and `-y` / `--yes` only after the
cost verdict is acceptable. If `submit` returns `data.status == "Submitted"`
with `data.status_probe_error`, the SQL was submitted but the immediate status
probe failed; keep `data.instance_id` and continue with `status` or `wait`
later. `result` is the step that opens the reader and returns rows, so do not
call it until `status` or `wait` shows `data.lifecycle_state == "success"`.
Do not key on the raw `data.status` string alone: MaxCompute's instance status
may be `Terminated` for both successful and failed SQL. Use `data.terminal`,
`data.successful`, and `data.task_statuses[].status_name` to branch on
success / failure / cancelled / suspended / running states.

`execute` and `result` do not add `LIMIT` to submitted SQL. They cap the
returned reader window at 10000 rows by default, matching odpscmd-style
client-side result display. The JSON includes `returned_rows`,
`result_max_rows`, `result_offset`, `has_more`, `truncated`, `next_offset`,
and sometimes `total_row_count`. If `has_more` is true, fetch more rows with
`--offset <next_offset>`; pass `--max-rows N` to change the page size or
`--max-rows 0` to disable the cap.

## Feedback / memory

Record a verified or failed query into the BM25-indexed memory store
(see [`references/memory.md`](memory.md) for retrieval):

```bash
mcs memory verify --question Q --sql '<SQL>' --tables T1,T2
mcs memory fail   --question Q --sql '<SQL>' --error-msg '<msg>' --remediation '<fix>'
```

For multi-source profiles, pass FQN table references in `--tables`
(`project.schema.table`) to disambiguate ambiguous bare table names
when recording a verified query.

## Error Recovery

Retry up to 3 times per error class — if the same class fires twice
after a fix attempt, stop and ask the user. Codes below match the
classifier in `mcs memory fail` (see [`memory.md`](memory.md)).

| ODPS code | Class | Fix |
| --- | --- | --- |
| `ODPS-0130161` | `SYNTAX_ERROR` | Check ORDER BY+LIMIT, string quotes, function names — see [`rules.md`](rules.md) |
| `ODPS-0420111` | `TABLE_NOT_FOUND` | Re-check via `mcs show --table <T>` (or `mcs meta describe-table <T>` if no package) |
| (no stable code) | `COLUMN_NOT_FOUND` | Re-check the table's `mcs show --table <T>` output |
| (no stable code) | `TYPE_MISMATCH` | Add explicit `CAST()` |
| `ODPS-0420061` | `PARTITION_NOT_FOUND` | Check available partitions via `mcs meta list-partitions <T>` |
| `ODPS-0421065` | `FULL_SCAN_BLOCKED` | Add partition filter |
| `ODPS-0420095` / `ODPS-0130013` | `PERMISSION_DENIED` | Tell user — see [`onboarding.md`](onboarding.md) |
| (no stable code) | `CROSS_JOIN_ERROR` | Add `/*+ mapjoin(<small_table>) */` hint for non-equi joins |

## Troubleshooting (only on error)

- `mcs` not found → `pip install maxcompute-semantic`
- Auth error → set env vars or run `mcs profile create` (see [`references/onboarding.md`](onboarding.md))
- Profile absent / cwd-link missing → `mcs profile create` then `mcs link bind <profile>` — see [`references/onboarding.md`](onboarding.md)
- Unclear active profile / auth / package state → `mcs -f json doctor` (`--offline` for local-only checks)
- `mcs show` says `"no semantic package"` → switch to cold-start ([`references/cold-start.md`](cold-start.md)); do **not** run `mcs build` in a query flow

## Explicit overrides (rarely needed)

If the agent's working directory is wrong, name the profile or
project explicitly:

```bash
mcs sql execute --profile NAME '<SQL>'
mcs sql execute --project P [--schema S] '<SQL>'
mcs sql execute --yes '<SQL>'                # bypass confirm prompt (non-TTY agent path)
mcs sql execute --max-rows 500 --offset 1000 '<SQL>'  # read a result page
mcs sql submit --profile NAME --yes '<SQL>'  # async submit for long-running SQL
mcs sql result --profile NAME <instance_id>  # read async rows after completion
mcs sql result --max-rows 500 --offset 1000 <instance_id>
mcs show       --profile NAME --table T
```
