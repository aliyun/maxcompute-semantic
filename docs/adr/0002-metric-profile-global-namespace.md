# ADR-0002: Top-level metrics live in a single profile-global namespace

**Date:** 2026-05-26
**Status:** Accepted
**Deciders:** jiexian.hc

## Context

mcs profiles can contain multiple data sources (e.g.
`sources: [acme__warehouse, acme__crm]`). When introducing top-level
metrics (see ADR-0001), the scope of the metric namespace was open:

- Should `total_revenue` be defined once per profile, or once per source?
- If once per source, what happens to cross-source metrics like
  `SUM(warehouse.orders.amount) + SUM(crm.refunds.amount)`?
- If once per profile, how do two teams with collision-prone names
  (`total_revenue` from both warehouse and CRM teams) coordinate?

OSI's `semantic_model.metrics[]` is a flat, model-level array with no
source concept — whatever mcs chooses, the OSI mapping has to flatten to
this form at export.

## Decision

Top-level metrics live in a **single profile-global namespace**:

- `metrics` table has no `source_key` column.
- `UNIQUE(name)` constraint across the entire profile.
- Metric expressions may reference any source via three-part
  `<source_key>.<table>.<col>` form (degrading to `<table>.<col>` in
  single-source profiles, matching `mcs sql execute` conventions).

## Alternatives considered

- **Source-scoped namespace** (`UNIQUE(source_key, name)`): each source
  gets its own `total_revenue`. Rejected — kills cross-source metrics,
  which is one of the main reasons for having a top-level metric layer
  in a multi-source profile (combining ods/dwd/ads or crm/erp/finance).
- **Optional source binding** (`source_key NULL = global, else source-local`):
  pushes a meta-decision ("is this metric global or local?") onto users
  and agents. In practice, once a measure is promoted to a named business
  metric, it is *always* global — the local case is a transient
  pre-coordination state, not a real long-term mode.
- **Dataset-scoped namespace**: degenerates to the same level as
  column-level measures; doesn't match OSI's model-level metrics either.

## Consequences

**Positive:**
- Direct alignment with OSI: mcs profile = OSI SemanticModel; mcs metrics
  table = OSI `semantic_model.metrics[]`. No flattening / disambiguation
  at export time.
- Cross-source business measures are first-class — they have a natural
  home in the metric layer.
- Schema is minimal: no `source_key` column on `metrics`, no compound
  uniqueness constraint, no source filter on `mcs metric list`.

**Negative:**
- A profile with two teams contributing metrics needs name coordination
  (no per-team prefix enforced by the schema). Mitigated by
  `mcs metric add` rejecting duplicate names with a clear error, and by
  the fact that *needing* to coordinate is exactly the value a top-level
  metric layer provides.
- A metric whose expression references a source that is later removed
  from the profile becomes dangling. `mcs metric show` will warn via
  sqlglot-based reference checking; no automatic deletion.

**Reference:** Q5 in the grill session of 2026-05-26.
