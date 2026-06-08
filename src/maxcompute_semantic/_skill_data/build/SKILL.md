---
name: build
description: Use when the user explicitly asks to build, refresh, onboard, or maintain a MaxCompute semantic package.
---

# mcs build workflow

Use this only for explicit build / refresh / onboarding / maintenance
tasks. Do not load this workflow while answering a data question.

## Workflow

1. Confirm the active profile with `mcs -f json doctor` only if setup is unclear.
2. Run `mcs build` with the user-requested profile/schema/table overrides.
3. After build, load `mcs skill get enrich` for the full review workflow.
4. Promote deterministic column-role suggestions into proposals:
   ```bash
   mcs package propose --from-suggestions
   ```
5. Check what still needs semantic enrichment:
   ```bash
   mcs -f json status --tables
   ```
   Look at `has_ai_context` and `columns_with_description` per table.
6. Compose a YAML with ai_context for every table + column descriptions
   for ambiguous columns (names shared across tables). If you find a
   build suggestion with the wrong role/subtype, include the corrected
   `role` / `dim_type` / `agg` / `id_type` in the same YAML. Then propose:
   ```bash
   mcs package propose --from-stdin <<'EOF'
   tables:
     - table: results
       ai_context: "Each row is one driver's race result in a single Grand Prix."
       columns:
         points: {role: measure, agg: SUM, description: "Points scored in this single race (0-50 scale)."}
         position: {role: dimension, dim_type: ordinal, description: "Finishing position in this race (1=winner)."}
     - table: driverstandings
       ai_context: "Each row is one driver's cumulative championship standing after a race."
       columns:
         points: {role: measure, agg: SUM, description: "Cumulative season championship points after this race."}
         position: {role: dimension, dim_type: ordinal, description: "Championship standing rank after this race (1=leader)."}
   EOF
   ```
7. Review all proposals (role + ai_context + description together):
   ```bash
   mcs package list-proposals
   mcs package show-proposal <id>
   ```
8. Apply or reject each proposal:
   ```bash
   mcs package apply <id>
   mcs package reject <id> --reason "..."
   ```
9. Re-check coverage:
   ```bash
   mcs -f json status --tables
   ```
   Every table should have `has_ai_context: true`. Tables with
   shared-name columns should have `columns_with_description > 0`.

## Why ai_context and column descriptions matter

- **ai_context** is the strongest table disambiguation signal (e.g.
  `results` = per-race outcome vs `driverstandings` = cumulative
  championship rank). Without it the query agent falls back to
  column-name heuristics which fail when tables share column names.
- **Column descriptions** disambiguate same-name columns across tables
  (e.g. `results.points` = "race points" vs `driverstandings.points` =
  "season points"). Table-level ai_context says *what a row is*;
  column-level description says *what this column means in this table*.

Write both from your domain knowledge — what entity each row represents,
what each column means in context. This is general semantic metadata.

`mcs build` is a persistent semantic-package maintenance operation. It
can take minutes and should never be used as a fallback for a query flow.

See `references/build.md` for flags and maintenance details.
