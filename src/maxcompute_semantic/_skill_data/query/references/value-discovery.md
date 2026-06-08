# Value Discovery (Intermediate SQL)

When the user's question references a literal value that may not match the
canonical form in the data, **probe before composing the final SQL**. This is
the single largest source of NL2SQL failures on real warehouses: the user
says "California" but the column stores `CA`; the user says "active users"
but `status` holds `'ACTIVE'`, `'A'`, `1`, or `'enabled'` depending on the
table.

## When to trigger

Run a probe SQL before the final answer when ANY of these hold:

- The question contains a quoted-or-implied string literal that maps to a
  low-cardinality enum-like column (`status`, `type`, `category`, `region`,
  `tier`).
- The question names an entity by a human-readable label that the schema
  likely encodes as a code or ID (`"USA"` → `country_code='US'`,
  `"iPhone 15"` → `product_id=...`).
- The column's `mcs meta describe-table` output shows a comment like
  "enum: A/B/C" but you're not sure which value the user means.
- A prior `mcs sql execute` returned an empty result set and the WHERE
  clause uses a string literal you guessed.

Skip the probe when:

- The literal is numeric and clearly a measurement (amount, count, year).
- The column is a free-text field (`description`, `comment`, `note`) where
  fuzzy matching is intended.
- A `verified_query` from `mcs memory recall` already contains the exact
  WHERE clause you need.

## How to probe

For partitioned tables, always anchor at the latest partition so the probe
is fast and representative. Find the partition column name via
`mcs show --table T` (or `mcs meta describe-table T` if no semantic
package) — real tables use `pt`, `ds`, `dt`, `bizdate`, etc.

In a cwd-bound profile, bare table names work; pass FQN
`<project>.<schema>.<table>` only when names are ambiguous across
sources (see [`cold-start.md`](cold-start.md)).

```sql
SELECT DISTINCT <col> AS v, COUNT(*) AS n
FROM <table>
WHERE <pt_col> = MAX_PT('<table>')
GROUP BY <col>
ORDER BY n DESC
LIMIT 50
```

For non-partitioned tables, drop the `WHERE <pt_col> = MAX_PT(...)` clause
but keep `LIMIT 50` — never `SELECT DISTINCT col FROM T` without a limit on
a production table.

Run the probe with `mcs sql execute` and inspect the result. Map the user's
phrasing to the closest distinct value before composing the final SQL.

> **Cost-gate high-basis or unpartitioned probes.** `SELECT DISTINCT col …
> GROUP BY col` on a wide partition or a fully unpartitioned 100M-row table
> can scan tens of GB. When the column basis is unknown, or the partition
> filter degenerated to a wide range, `mcs sql cost` the probe first. If
> the verdict is `confirm` or `blocked`, narrow the probe (tighter partition
> filter, smaller `LIMIT`, or sample via `TABLESAMPLE`) before re-running.

## Anti-patterns

- **Don't** invent values. If the probe returns `['A', 'B', 'C']` and the
  user said "active", ask the user which one they mean — do not guess.
- **Don't** chain probes. One probe per ambiguous column. If a question
  has three ambiguous columns, dispatch the three `mcs sql execute`
  calls in the same agent turn (multiple parallel tool uses) — each is
  a separate `mcs` process, no shared state.
- **Don't** skip the probe when a `failed_query` in memory already shows
  the same column failed before — the probe is cheap, the wrong-literal
  failure is the expensive bug.

## After a successful run

If the probe + final SQL produced a user-confirmed-correct result, record
it:

```bash
mcs memory verify --question 'natural-language question' --sql 'SELECT ...' --tables <table>
```

The verified query becomes a high-precision retrieval target for future
questions about the same enum.
