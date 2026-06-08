# Metrics — top-level named SQL expressions

A **metric** is a profile-global named SQL fragment that captures a
business measure (e.g. `total_revenue`, `paid_revenue`,
`active_users_30d`). Metrics live in the `metrics` table of the
PackageDB and are surfaced through the `mcs metric` verb group.

Metrics ≠ measures:

- **measure** — column-level annotation (`--role measure --agg SUM`)
  saying "this column can be aggregated". No name, no expression.
- **metric** — profile-level entity (`mcs metric add NAME --expression "..."`)
  with a name, an SQL expression, and optional description /
  ai_context. The expression is *copied into* generated SQL; mcs has
  no Metric Query Language that compiles metric names into SQL.

## Verbs

### `mcs metric add NAME --expression "..."`

Add a new metric. Profile-global `UNIQUE(name)`.

```bash
mcs metric add total_revenue \
  --expression "SUM(orders.amount)" \
  --description "Gross order revenue, all sources"
```

Optional flags:

- `--description "..."` — one-line business description
- `--ai-context "..."` — longer NL paragraph; downstream LLMs read this
  the same way they read `tables.ai_context`

On collision, exit 4 (`MetricExists`). Use `mcs metric edit` to update.

The expression is statically lint-checked via sqlglot at add-time;
warnings ride along in the JSON envelope but do not block the commit.

### `mcs metric list [-f json]`

List all metrics in the profile, sorted by name. Default text output
truncates long expressions; JSON returns full rows.

### `mcs metric show NAME [-f json]`

Show one metric's full row including the latest validator warnings
(e.g. "expression references `acme__warehouse.orders` which is not in
the current profile" — surfaces schema drift over time).

### `mcs metric edit NAME [--expression ...] [--description ...] [--ai-context ...]`

Partial-update. Only non-`None` flags are written; `updated_at` bumps.

### `mcs metric remove NAME [--force]`

Delete. On TTY, prompts; off TTY, requires `--force`.

## When to add a metric

Add a metric when **both** are true:

1. The user confirmed the calculation is correct.
2. The user said (or strongly implied) they'd ask for it again.

If either is missing, do not sediment — propose instead, and only
write after the user agrees. See SKILL.md "Sedimenting metrics".

## Expression form

The expression is a MaxCompute SQL fragment that will be inlined into
the SELECT (or WHERE / HAVING) clause of generated queries. It can be:

- A simple aggregate: `SUM(orders.amount)`
- An aggregate with a filter: `SUM(orders.amount) FILTER (WHERE orders.payment_status = 'paid')`
- Multi-column arithmetic: `SUM(orders.amount) - SUM(refunds.amount)`
- Cross-source (in multi-source profiles): `SUM(warehouse.orders.amount) + SUM(crm.refunds.amount)`

References use the same forms as `mcs sql execute`:

- Single-source profile: bare `<table>.<col>` or just `<col>` if
  unambiguous
- Multi-source profile: qualified `<source_key>.<table>.<col>` to
  resolve cross-source name collisions

## Batch import

`mcs package propose --from-stdin` accepts a top-level `metrics:` list
alongside the existing `tables:` / `table:` shape:

```yaml
tables:
  - table: orders
    columns:
      amount: {role: measure, agg: SUM}
metrics:
  - name: total_revenue
    expression: SUM(orders.amount)
    description: Gross order revenue
  - name: paid_revenue
    expression: "SUM(orders.amount) FILTER (WHERE orders.payment_status = 'paid')"
```

Each metric entry creates a `metric` proposal. Apply with
`mcs package apply <id>` after review. Expression validation runs
at apply time — unparseable expressions and UNIQUE name collisions
reject the proposal with a recorded validation failure.
