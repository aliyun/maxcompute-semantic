# Enrich - Proposal-First Semantic Package Maintenance

## What proposals mean

Generated suggestions are evidence-backed candidates. They are separate
from confirmed annotations until `mcs package apply <id>` writes them
through the package annotation layer. A proposal can be useful evidence
without being correct enough to apply.

Use proposals for all annotation writes — both build-generated suggestions
and agent-authored enrichment.

## Review policy

Apply a proposal when the target table and column exist, the proposed role
matches the column's business meaning, the evidence supports the patch, and
there is no conflict with existing confirmed annotations.

Ask the user before applying when the proposal changes business meaning,
when two proposals compete for the same semantic role, when the evidence is
only a name heuristic, or when the column's unit/grain is unclear.

Reject a proposal when it contradicts confirmed annotations, points at the
wrong table or column, proposes an unsafe aggregation for pre-aggregated
data, or lacks enough evidence to preserve for later review.

Prefer small decisions. Inspect a proposal, apply or reject it, then move to
the next one. Do not bulk-apply proposal ids without review.

## Command examples

Inspect coverage before enriching:

```bash
mcs status --tables
mcs -f json status --tables
```

Create proposal rows from build-time annotation suggestions:

```bash
mcs package propose --from-suggestions
mcs -f json package propose --from-suggestions --min-confidence 0.8
```

Create proposal rows from reviewed agent YAML. This accepts the same
table/column shape agents use for batch annotation, but creates proposals
instead of writing confirmed annotations directly:

```bash
mcs package propose --from-stdin <<'EOF'
tables:
  - table: orders
    ai_context: "Each row is one customer order event."
    columns:
      status: {role: dimension, dim_type: categorical, description: "Order lifecycle state."}
      total_amount: {role: measure, agg: SUM, description: "Raw order amount."}
EOF
```

If a reviewed agent YAML entry targets a proposal that was rejected earlier
in the same enrichment pass, `--from-stdin` reopens it as `suggested` so it
can be inspected and applied through the queue.

List proposals. The default status filter is `suggested`.

```bash
mcs package list-proposals
mcs package list-proposals --status suggested --limit 20
mcs -f json package list-proposals --status suggested --target-type column
```

Show one proposal before deciding:

```bash
mcs package show-proposal <id>
mcs -f json package show-proposal <id>
```

Apply a reviewed proposal:

```bash
mcs package apply <id>
mcs -f json package apply <id> --reviewed-by agent
```

Reject an unsupported proposal. A short, durable reason is recommended
when it explains the decision, but omitting `--reason` is valid.

```bash
mcs package reject <id>
mcs package reject <id> --reason "pre-aggregated column should stay attribute"
mcs -f json package reject <id> --reason "ambiguous business meaning" --reviewed-by agent
```

## Decision checklist

Before applying, confirm:

- The proposal target matches the intended source, table, and column.
- The role/subtype follows semantic taxonomy rules.
- Measures are raw aggregable values, not already-aggregated values.
- Identifier proposals do not conflict with stronger structural evidence.
- The proposal does not overwrite user-confirmed semantics without approval.

When any check fails, reject or ask the user.
