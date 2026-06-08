# Build — Semantic Package Management

> **Loaded on demand** — SKILL.md loads this only when "build / scan
> tables / refresh" onboarding or maintenance intent is detected. Do
> not load this as the next step for an ordinary query that lacks a
> semantic package; that path uses live `mcs meta` discovery instead
> (see [`cold-start.md`](cold-start.md)).

## Overview

`mcs build` populates the deterministic half of a profile's package
(schema + samples + JOIN inferences + UDFs + history mining) and writes
generated semantic suggestions. The agent-maintained semantic half is a
review queue: generated suggestions are promoted to proposals, then
reviewed, applied, or rejected. `mcs show` / `mcs status` read the
result — the agent never opens package files directly. A profile's
`sources` list defines which MaxCompute project/schema/table ranges land
in the package; `mcs build` walks every source end-to-end.

## Default workflow (cwd-bound profile)

When the cwd is bound to a profile, `mcs build` uses it automatically.
The flow has a deterministic build step followed by semantic review;
don't stop after Step 1 when the user asked to maintain the package.

### Step 1 — deterministic data dump

```bash
mcs build               # scan + sample + JOIN inference + UDF discovery + history mining (resumes an interrupted prior build: already-built unchanged tables are skipped)
mcs build --fresh       # force full rebuild from scratch, ignoring resume state (re-sample every table)
mcs build --refresh     # incremental: re-build tables whose schema OR data changed since last build (also resumes any tables left incomplete by an interrupted build)
mcs build --refresh-min-age-hours 6  # data-changed re-sample throttle: only re-sample if last sample older than N hours (default 24; 0 = re-sample on any data change)
mcs build --schema S    # 3-level project: override the schema portion of the build target
mcs build --profile-level light   # column profiling: APPROX_DISTINCT + null ratios + uniqueness (default)
mcs build --profile-level deep    # adds value-overlap validation for top join candidates (cost-gated)
mcs build --profile-level none    # skip profiling entirely (fastest; no annotation suggestions)
mcs build --no-history    # skip TASKS_HISTORY mining (no past-query evidence in profile)
mcs build --tables T1,T2  # restrict to specific tables
mcs build --no-sampling   # skip column-value sampling (cuts cost; markdown loses sample/enum values)
mcs build --no-joins      # skip JOIN inference (no _joins.md / join_candidates table)
mcs build --no-udf        # skip UDF discovery (no _udfs.md / udfs table)
mcs build --include-views # include VIRTUAL_VIEW / OBJECT_TABLE in sampling/profiling (default: skip; their underlying SQL re-execution is expensive)
mcs build --parallel N    # concurrent workers for per-table sampling and profiling (default 'auto' = min(table_count, 32); pass an integer to override, 1 to force serial for debug/repro)
mcs build --with-vectors  # also build vector embeddings for memory recall (requires maxcompute-semantic[vec])
mcs build --join-candidate-limit 5   # cap join candidates per table (default 5)
mcs build --profile-budget-cny 3.0   # max estimated cost for --profile-level deep validation (default 3.0)
```

## Build status

```bash
mcs status                  # high-level: profile, freshness, table count
mcs status --tables         # per-table detail (name, columns, annotated tristate, data_modified_at / last_sampled_at freshness)
mcs status --by-source      # multi-source profiles: group tables by source_key
```

Only run these commands when the user is setting up or refreshing a
reusable profile. If the user is asking one data question and `mcs show`
reports no package, switch to the cold-start flow and read schema via
`mcs meta` rather than starting a build.

### What `--profile-level light` produces (default)

`light` profiling runs one aggregate SQL per table
(`APPROX_DISTINCT`, null counts, row count) and combines the results
with mined SQL workload evidence to produce two kinds of **suggestions**
(NOT confirmed annotations):

1. **Join candidates** — ranked by workload frequency + name heuristic +
   uniqueness ratio, capped at `--join-candidate-limit` per table
   (default 5). Stored in `join_candidates` table with `status=suggested`.
2. **Annotation suggestions** — per-column role hints (identifier /
   dimension / metric / attribute) with confidence scores. Stored in
   `annotation_suggestions` table; **never** written to the confirmed
   `columns.semantic_role` field. Use `mcs skill get enrich` and
   `mcs package propose --from-suggestions` to review them through the
   proposal queue.

Both surfaces appear in `mcs -f json show --table T` output (see
[`query.md`](query.md)). The agent should treat them as hints, not
ground truth.

### What `--profile-level deep` adds

`deep` mode extends `light` by running value-overlap validation queries
for top join candidates (LEFT JOIN + COUNT to compute `coverage_ratio`).
These queries are cost-gated by `--profile-budget-cny` (default 3.0 CNY).
If the budget is exhausted, remaining candidates stay at `status=suggested`
without overlap data. Candidates with `coverage_ratio >= 0.95` are
promoted to `status=confirmed`.

### View / object-table skip (default)

`mcs build` skips VIRTUAL_VIEW and OBJECT_TABLE objects in the
sampling and profiling phases by default. Views re-execute their
underlying SQL on every sample — multi-minute per object on complex
stacks — and OBJECT_TABLE has no row structure to profile.
MATERIALIZED_VIEW and EXTERNAL_TABLE are physical and continue to be
profiled normally. Pass `--include-views` to opt back in when view
coverage is needed despite the cost.

Schema and columns for every kind are still recorded by the describe
phase — only sampling and profile-suggestion work is elided. The
per-table `table_type` field (`MANAGED_TABLE`, `VIRTUAL_VIEW`,
`MATERIALIZED_VIEW`, `EXTERNAL_TABLE`, `OBJECT_TABLE`) is exposed in
the `mcs -f json status --tables` envelope so the agent can see which
entries were skipped.

### Step 2 — semantic review and enrichment

Step 1 produced raw physical facts and generated suggestion rows; Step 2
turns reviewed suggestions into confirmed package semantics. This is
**review work**: the agent evaluates build evidence, table context, and
the user's intent before applying anything. `mcs build` alone never
writes confirmed column roles from generated suggestions.

After `mcs build` succeeds:

1. **Load the review workflow** — `mcs skill get enrich`.
2. **Promote generated suggestions into proposals**:
   ```bash
   mcs package propose --from-suggestions
   ```
3. **Review the queue**:
   ```bash
   mcs package list-proposals
   mcs package show-proposal <id>
   ```
4. **Apply or reject one proposal at a time**:
   ```bash
   mcs package apply <id>
   mcs package reject <id>
   ```

Applied proposals write through the package annotation layer and re-render
the affected package projections. Rejected proposals stay as review
history instead of silently becoming annotations. Treat a table with no
applied semantics as incomplete, but do not bulk-apply proposal ids without
review.

All post-build annotation work flows through `mcs package propose` →
review → `mcs package apply`. There is no direct-write shortcut.

For profile resolution, see
[`onboarding.md`](onboarding.md#profile-management).
