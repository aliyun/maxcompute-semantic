# Acquisition, evidence and review

The KB is independent of profiles; `--kb FILE` works on the `knowledge` /
`graph` group or on a subcommand. Ingest reads UTF-8 Markdown, recursively for
a directory. It indexes documents and explicitly declared metadata without
calling models, embedding services or MaxCompute.

## Markdown declarations

Place structured declarations under `mcs` in safe YAML frontmatter:

```yaml
---
mcs:
  nodes:
    - id: sales:active_customer
      kind: term
      name: active_customer
      properties:
        definition: A customer with a confirmed subscription.
        aka: [subscribed customer]
---
# Active customer
Use a confirmed subscription to determine active status.
```

Declared nodes start as candidates even if the file says `confirmed`. Ingest
attaches versioned evidence. Explicit `mcs.relations` carry `subject`, `predicate`
and `object`; both endpoints must exist, and optional `start_line/end_line`
select an inclusive source span. Markdown links become mentions, not inferred
equivalence, joins or dependencies. Inspect each candidate before confirming it.

Node kinds include document, term, entity, business_entity, table, column,
metric, query and claim. Stable IDs are identities; names are labels and may
need `--scope` for disambiguation. Edge states are candidate, confirmed, stale,
conflict, rejected and retracted. The same distinction applies to nodes.

## Reviewing a declaration

Read `knowledge show NODE_ID` and its `evidence_ids`. Prepare an `upsert_node`
patch containing the complete node, preserving ID, kind, scope and those
evidence references. `upsert_edge` contains the complete edge; `retract_edge`
contains the existing edge ID. Use the current revision from `knowledge init`
with `propose --expected-revision`, inspect the returned proposal ID, then apply
only after its evidence and semantics have been reviewed. Apply validates the
recorded prior value and the revision captured by the proposal. Another change
requires a new review rather than overriding that guard.

Source versions and evidence are retained. A changed document or imported
source revision can mark dependent assertions stale. Re-ingest the new source
and review the affected claims; do not restore confirmed status with old evidence.
Conflicting assertions remain visible, and retraction withdraws an edge while
retaining its history. `graph check` reports structural issues; isolated nodes
are informational, while invalid references, stale/conflicting edges, missing
evidence and dependency cycles are blocking issues.

## Existing semantic assets

`knowledge ingest-package --profile NAME` reads an already built local package
through public storage APIs. It does not resolve credentials or rebuild it.
Schema observations keep their provenance; legacy joins and suggestions remain
candidates even with confidence 1.0. Local query verification does not establish
server `trusted` status.

`knowledge ingest-semantic FILE` requires endpoint, namespace/account identity,
Region, SemanticSpec object ID, source and exact revision. Use
`package export-semantic --output FILE` to produce that snapshot. Native section
JSON is retained, and integrity metadata is verified when supplied. PUBLISHED
imports confirm the identity of published definitions, not their factual truth;
USER_DRAFT and SYSTEM_SUGGESTIONS remain candidates. Snapshot source evidence
never changes the original native query trust fields.

`knowledge export FILE` exports the full graph including KB identity,
revisions, source versions and review history. A full graph imported into an
empty KB retains its identity; cross-KB sharing uses explicit fragments. Export
does not send anything to a service. Review local document contents before
sharing that file with another party.
