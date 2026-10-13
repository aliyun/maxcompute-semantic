# Selected knowledge into SemanticSpec

`knowledge project --selected ID` is a local preview, with repeatable IDs and
optional `--output REPORT`. It includes explicit dependencies and reports every
unsupported mapping or missing/current-version evidence gap. Only confirmed
items with current, pinned evidence are eligible; dependency and source changes
can block an otherwise confirmed item.

Mappings are explicit:

| Local content | Required binding / result |
| --- | --- |
| term | `properties.definition`; `aka` / `aliases` become glossary `alsoKnownAs` |
| table or table-level business entity | `properties.server_binding` with logical `name` and physical `baseTable`, or an explicit native logical table |
| metric | native `semanticType` plus the server's measure or calculated-metric fields |
| approved join | evidence-backed `joins_with`, native endpoint names and column pairs, matching the explicitly bound physical endpoint nodes |
| query | explicit native query definition; preserving remote trust requires unchanged source evidence |

Explicit native content lives in `properties.server_native`, identified by
`server_section`: `logicalTables`, `namedFilters`, `approvedRelationships`,
`metricDefinitions`, `verifiedQueries`, `glossary` or `dataReferences`.
SemanticModel members may also use the `semanticModel.` prefix.

A local `SUM(amount)` metric is opaque content here: the bridge does not guess
the server's row expression, aggregation, table binding or grain. General
business relations and private extensions stay unmapped. A locally verified
SQL example is not promoted to a trusted server query. Preserve these gaps in
the report instead of silently dropping them or widening trust.

Review `selected_ids`, `included_ids`, `source_kb_id`, `source_revision`,
`mapping_manifest`, `unmapped`, `validation_issues` and `sections`.
`publishable` means structural/evidence checks and finite MEASURE scalar AST
checks passed. Inspect `sql_validation.validated_measure_ids` and `unchecked`.
The existing MaxCompute dialect parser requires one scalar expression, with no
aggregates, windows, subqueries, query clauses, aliases or multiple statements.
Strings, comments and quoted identifiers are interpreted by that parser; the
original expression text is preserved. Unclassified functions produce an
explicit issue because their names cannot establish scalar versus UDAF behavior.
Have an owner or server workflow establish that behavior; do not bypass the
issue by renaming a function or issuing an automatic SQL execution.

Physical columns/types, business semantics and runtime function behavior still
require their own validation. CALCULATED_METRIC formulas retain Catalog's
grammar and dependency contract; the bridge does not substitute a SQL grammar
for them. Named-filter expressions and verified-query SQL are also listed as
unchecked. Local validation never promotes a query's `trusted` flag. Server
acceptance and explicit write/publication authorization remain separate.

When that review is complete and a target workspace is already bound/pulled:

```bash
mcs package stage-projection --file projection.json --workspace .mcs-remote
mcs -f json package diff --workspace .mcs-remote
```

Staging merges typed section entries locally and preserves unrelated native
entries. It does not upload the KB, source Markdown, query logs or credentials.
Continue with `mcs skill get remote` only for an authorized draft update or
publication; its fresh exact diff and revision guards still apply.
