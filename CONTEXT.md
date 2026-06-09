# mcs Glossary

Canonical terms for the `maxcompute-semantic` package. Add a term here the first
time it's resolved in design discussion. Implementation details and rationale
belong in ADRs (`docs/adr/`) or specs (`docs/superpowers/specs/`), not here.

## measure

A **column-level** annotation marking a physical column as *suitable for
aggregation*, paired with an `agg` hint (`SUM` / `COUNT` / `AVG` / `MAX` /
`MIN` / `COUNT_DISTINCT`). Storage: `columns.semantic_role = "measure"` plus
`columns.agg`. A measure has no business name and no standalone identity — it
describes a *property of a column*, not an independent object.

Example: `orders.amount` marked as `measure(SUM)` means "this column can be
summed", not "this column *is* total revenue".

A measure can be *promoted* to a [metric](#metric) when the aggregation gains
business meaning worth naming and reusing.

## metric

A **profile-level**, top-level business *measure with a name*. Storage:
`metrics` table, `UNIQUE(name)` across the entire profile (no source binding).
A metric has its own identity, expression, and (optionally) description and
ai_context.

Example: `metric(name="total_revenue", expression="SUM(orders.amount)")`. The
same physical column (`orders.amount`) may underlie zero, one, or many metrics
(`total_revenue`, `revenue_2025`, `paid_revenue` with different filters).

A metric's expression is a MaxCompute SQL fragment that is *copied* into a
generated query, not *referenced* — mcs has no query engine that compiles
metric names into SQL.

## measure vs metric

| | measure | metric |
|---|---|---|
| Layer | column property | top-level entity |
| Identity | none (described by `<table>.<col>` + agg) | `name` (business term) |
| Composition | atomic only (one column, one agg) | arbitrary SQL expression (cross-column, cross-table OK) |
| Created by | `mcs annotate column --role measure --agg SUM` or `mcs annotate batch` | `mcs metric add NAME --expression ...` |
| OSI export | `fields[].custom_extensions[].data.measure` | `semantic_model.metrics[]` |

## agent bootstrap playbook

An agent-facing installation workflow that gives the agent room to diagnose and
adapt to the user's local environment before installing mcs. It is not merely a
fixed shell script: its autonomy is bounded by transparency before risky
actions and artifact integrity checks.

## process-auth helper

A local command that mcs runs to obtain temporary MaxCompute credentials for a
profile. A helper discovered in an external config is adopted only after the
command is visible to the user or explicitly trusted by the importing workflow;
the canonical ncs credential command is the standard known helper.

## managed write path

A named mcs workflow whose purpose includes a bounded MaxCompute mutation after
user intent has been captured. It differs from arbitrary SQL execution: the
write is part of a product-owned workflow, not an open-ended query channel.
