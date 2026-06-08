---
name: enrich
description: Review and apply semantic-package proposals generated from build evidence. Use when maintaining package semantics through an agent rather than writing direct annotations.
---

# mcs enrich workflow

Use this workflow when generated build evidence should become reviewed
semantic-package metadata. The proposal queue is a review buffer between
machine suggestions and confirmed annotations.

## Hard rules

All annotation writes go through proposals. Do not bypass the proposal queue.
Apply only proposals you have reviewed against table context, evidence, and
the user's stated semantics. Reject proposals that are unsupported, ambiguous,
or conflict with confirmed annotations.

## Workflow

1. Inspect package coverage:
   ```bash
   mcs -f json status --tables
   ```
   Check `has_ai_context` and `columns_with_description` per table.
2. Promote build-time column-role suggestions into proposals:
   ```bash
   mcs package propose --from-suggestions
   ```
3. Propose ai_context, column descriptions, and reviewed column semantics
   via YAML:
   ```bash
   mcs package propose --from-stdin <<'EOF'
   tables:
     - table: orders
       ai_context: "Each row is one customer order event."
       columns:
         status: {role: dimension, dim_type: categorical, description: "Order lifecycle state (pending/paid/shipped/cancelled)."}
         total_amount: {role: measure, agg: SUM, description: "Raw order amount to aggregate for revenue."}
   EOF
   ```
4. List all proposals (role + ai_context + description together):
   ```bash
   mcs package list-proposals
   ```
5. Inspect each candidate:
   ```bash
   mcs package show-proposal <id>
   ```
6. Apply or reject after review (omitting `--reason` is valid):
   ```bash
   mcs package apply <id>
   mcs package reject <id> --reason "<short reason>"
   ```
7. Re-check coverage:
   ```bash
   mcs -f json status --tables
   ```

Use `mcs -f json ...` when you need to parse command output or compare
proposal payloads programmatically.

See `references/enrich.md` for review policy and examples.
