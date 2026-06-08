# ADR-0001: Rename column-level `metric` to `measure`, reserve `metric` for top-level

**Date:** 2026-05-26
**Status:** Accepted
**Deciders:** jiexian.hc

## Context

mcs originally stored a single concept under `semantic_role = "metric"`:
"this physical column is suitable for aggregation, with `agg` hint X". This
collapsed two distinct ideas — a *column property* and a *business measure* —
into one slot.

When designing OSI export, we needed to emit `semantic_model.metrics[]` (a
top-level array of business measures with `name` + `expression`). The
column-level `metric` annotation does not carry a business name or a
composable expression, so it cannot natively populate OSI's metrics[]
without either (a) generating mechanical names like `orders__amount__sum`
or (b) introducing a second, independently-named concept.

We chose (b) — introduce top-level metrics as a first-class entity — and
realized the two layers must have distinct vocabulary or every conversation
("is X a metric?") becomes ambiguous.

## Decision

- **column-level** annotation: rename `semantic_role = "metric"` →
  `semantic_role = "measure"`. The `agg` hint stays as-is.
- **top-level** entity: new `metrics` table, `UNIQUE(name)`, holding business
  measures with `expression`, `description`, `ai_context`.

The rename is a **hard cut** in schema v10 (next bump after the current
v9): a migrator rewrites existing `semantic_role = 'metric'` rows to
`'measure'`, and the `_ROLE_ALIASES` table loses its `metric`/`measure`
swap (today `measure` aliases *to* `metric`; post-rename the alias is
removed and `metric` becomes a reserved word at the column-annotation
layer — `--role metric` on `mcs annotate column` is rejected with a
clear error pointing at `mcs metric add`).

## Alternatives considered

- **Keep column-level `metric`, name top-level something else** (e.g.
  `derived_metric`, `composite_metric`): self-coined terms force mapping
  documentation when exchanging with OSI / dbt / Cube; users have to learn
  mcs-specific words for industry-standard concepts.
- **Disambiguate by CLI verb only** (`annotate column --role metric` vs
  `metric add`): storage stays single-word, but storage-layer ambiguity
  leaks back as soon as anyone reads the schema or queries `columns` directly.
- **Soft alias window** (accept both `metric` and `measure` for N versions,
  emit deprecation warnings): in a 0.x project with a small user base, the
  permanent maintenance cost of dual-word surfaces (CLI, docs, SKILL.md,
  storage filters) exceeds the one-time cost of a hard cut.

## Consequences

**Positive:**
- Vocabulary aligns with dbt / Cube / LookML — zero translation cost when
  exchanging metric models with external tooling.
- OSI export has a clean conceptual mapping: mcs measure ↔ OSI
  `Field.custom_extensions.measure`; mcs metric ↔ OSI `Metric`.
- Conversations and SKILL.md prose can say "measure" and "metric" without
  qualifiers.

**Negative:**
- Breaking change: bumps mcs from 0.11.x to 0.12.0; all existing profiles
  need the v9→v10 migrator; SKILL.md instructions to agents change;
  annotate batch YAML uses `measures:` where it used `metrics:` for
  column-level entries (now `metrics:` at the top level means top-level
  metrics).
- The `agg` hint sits on `measure` rows — slightly odd that a property
  called "measure" carries an aggregation type, but this matches dbt's
  `measure` schema and reads fine in practice.

**Reference:** Q3 in the grill session of 2026-05-26.
