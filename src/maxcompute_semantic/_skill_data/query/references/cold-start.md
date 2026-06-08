# Cold-start — Querying without a semantic package

> **Loaded on demand** — SKILL.md / `references/query.md` route here
> when `mcs show` reports `"no semantic package for profile …"`.
> The normal path assumes a package; this is the fallback.

The semantic package (`mcs build` output: `_overview.md`, per-table
`<T>.md`, JOIN graph, etc.) is a **cache**. When it's absent, you can
still answer the question by reading schema live from MaxCompute.
**Don't run `mcs build` mid-query** — that's a multi-minute scan,
and a maintenance / onboarding action the user invokes explicitly.
`mcs status` returning `build_status: no build data` means the same
thing as `mcs show` returning `PackageNotBuilt` in a query flow: use
this workflow, not `mcs build`.
`mcs sql review` is still useful here: without a package it returns
`review_mode: syntax_only`, runs package-independent syntax / dialect
checks, and marks semantic hints / coverage skipped.

## Workflow

1. **Understand** the question — same as the default flow.
2. **List or search tables** to identify candidates:
   ```bash
   mcs -f json meta list-tables              # full table list
   mcs -f json meta search-tables  <KEYWORD> # match table names
   mcs -f json meta search-columns <KEYWORD> # match column names across tables
   ```
3. **Inspect candidate tables** for column types and partitions:
   ```bash
   mcs -f json meta describe-table <TABLE>
   mcs -f json meta list-partitions <TABLE> # if the table is partitioned
   mcs -f json meta freshness       <TABLE> # last-modified / row-count signal
   ```
   Each `mcs meta` call is an independent process. When inspecting N
   candidate tables, dispatch the N `describe-table` calls in parallel
   (single agent turn, multiple tool uses) — they don't share state.
4. **Compose, review, cost-gate, execute, record** — same as the default
   flow. Run `mcs sql review` for syntax / dialect checks even though
   semantic checks are skipped; then use `mcs sql cost`, `mcs sql
   execute`, and `mcs memory verify` / `fail`. See
   [`references/query.md`](query.md).

For multi-source profiles, every `mcs meta` verb scopes to one source
via the explicit `--project P --schema S` flag pair (`describe-table`,
`search-tables`, `search-columns`, `list-partitions`, `freshness`).
Bare table names that are unique across sources auto-resolve; ambiguous
bare names require `--project` / `--schema`.

## Positional arguments — not flags

The last argument on each `mcs meta` line is **positional**.
Pass the table name or keyword as a bare string; never `--table foo`
or `--keyword foo` (those flags don't exist; the CLI exits 2).

```text
mcs meta describe-table cards          # ✓
mcs meta describe-table --table cards  # ✗ (No such option: --table)
```

## The full set of `mcs meta` subcommands

```bash
mcs meta list-projects                                       # enumerate projects the credential can see
mcs meta list-schemas    --project <PROJECT>                 # enumerate schemas inside a 3-level project
mcs meta list-tables                                         # tables in the active profile's source(s)
mcs meta list-tables     --project <PROJECT> --schema <SCHEMA>
mcs meta describe-table  <TABLE>
mcs meta search-tables   <KEYWORD>
mcs meta search-columns  <KEYWORD>
mcs meta list-partitions <TABLE>
mcs meta freshness       <TABLE>
```

Multi-source profile? Pass `--project P --schema S` to scope any verb
to one source. Without scoping, bare table names auto-resolve when
unique across all sources, and error with a candidate-list hint
when ambiguous.

Eight verbs across the four catalog tiers (projects → schemas → tables → columns / partitions / freshness). Don't invent flags or sub-verbs not listed — if filtering needs go beyond what's shown, use SQL directly: `SELECT … FROM information_schema.…`.

## Why not just `mcs build` once at the start?

A query workflow is short-lived (one question, a few SQLs); building
a package is multi-minute, generates persistent on-disk state, and
locks you into the snapshot's freshness. Live `mcs meta` is
cheaper, always up-to-date, and reaches every table the auth allows.
Build only when the user is **setting up** a profile they'll reuse.

## Common errors during cold-start

| Symptom | Likely cause / next step |
| --- | --- |
| No active profile | Working dir isn't bound to a profile and `MCS_PROFILE` is unset. Run `mcs link bind <profile>` first, export `MCS_PROFILE`, or pass `--profile X`. |
| `Authorization Failed` | The bound profile lacks read permission on the schema. Tell the user; see [`references/onboarding.md`](onboarding.md). |
| `table not found` after a successful `list-tables` | Wrong source. In multi-source profiles `mcs meta list-tables` defaults to all sources; pass `--project P --schema S` to scope. Run `mcs profile show <name>` to see the configured sources. |
| Unsure which profile/source/schema is active | Run `mcs -f json doctor` for profile/link/auth/package checks, then use `mcs meta list-tables --project P --schema S` to inspect one source. |
