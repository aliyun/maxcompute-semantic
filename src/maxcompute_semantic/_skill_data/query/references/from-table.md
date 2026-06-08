# Pick the FROM table from the *subject* of the question

Result-set comparison is sensitive to the FROM clause, not just the
filter logic. The same numeric answer can come from two different tables
but produce different row counts when the tables sit on the "1" vs "N"
side of a join.

- **"How many / what percentage of X"** — X names an *entity*. Build the
  query around X's table: `FROM x_table [JOIN other ON ...]`, with
  `COUNT(x_table.id)` (or its primary identifier from `identifiers[]`)
  as the denominator. **Do not use a join-partner table as the FROM**
  even if it has the filter column you need — pull the partner in via
  JOIN instead. A common trap: a parent table has 1 row per X, a child
  table has N rows per X; counting from the child inflates the
  denominator by the average fan-out.
- **"List X with their Y"** — FROM the X table, JOIN to Y. The JOIN type
  matters: "**list X with their Y**" (every X has a Y) is `INNER JOIN`;
  "**list X along with Y, if any**" / "**X with their optional Y**" /
  "**X and the Y count, including zero**" is `LEFT JOIN` so X rows
  without a Y still appear (with NULL Y).
- **The filter column lives in a different table than the subject** —
  JOIN to the filter table; do not change the FROM. "Number of schools
  in county Z" stays `FROM schools` and joins to whatever table holds
  the county column, even when the county column also appears on another
  table in the schema. The subject of the question (schools) decides the
  FROM, not where the filter columns live.

## Join cardinality — the `joins_to` marker

Use the `joins_to` list in `mcs show` to find legal join paths. Each
entry has the form `partner_table via own_col [cardinality]` — `own_col`
is the column on **the current table** that joins to `partner_table`,
and `[cardinality]` is from the current table's perspective:

- `[1:n]` — this row maps to **many** partner rows (partner is a fan-out
  child). Preferred shape: keep this table as the FROM and pull the
  partner in via JOIN purely for filtering. `COUNT(*)` (equivalently
  `COUNT(this_table.id)`) after a 1:n JOIN where the partner is only in
  WHERE counts the surviving filtered-tuple rows, which is what "how many
  X meet condition C" reads as in natural-language SQL. Reach for
  `COUNT(DISTINCT this_table.pk)` **only** when (a) the question
  explicitly says "distinct / unique / different X", **or** (b) a partner
  column appears in SELECT (which forces the tuple to fan out) and you
  genuinely want the distinct-entity count. Defensive `COUNT(DISTINCT
  pk)` on every 1:n JOIN is a common over-correction — it changes the
  answer when one parent row matches several partner rows that all
  satisfy the filter (`how many banned cards` with a card banned in three
  formats: `COUNT(*)` = 3, `COUNT(DISTINCT id)` = 1; the surrounding
  NL-SQL convention reads the question as the former unless "distinct" is
  explicit).
- `[n:1]` — this row maps to **one** partner row (partner is a parent).
  Safe to count from this side without `DISTINCT`; the partner's filter
  columns can sit in WHERE without changing the this-table row count.
- `[1:1]` — identity mapping; JOIN direction is exchangeable.
- `[n:m]` — many-to-many through a bridge; both sides fan out, so every
  count needs explicit `DISTINCT` on the entity you're measuring.

Concretely: `joins_to: [orders via customer_id [1:n]]` on the
`customers` entry means `customers.id = orders.customer_id` AND each
customer has many orders. `joins_to: [customers via id [n:1]]` on the
`orders` entry is the same edge from the other side. Look at the
partner's `[pk]` / `[fk]` markers in its `columns_index` for the
matching key on the other side; the cross-source form is
`source_key.partner_table via own_col [cardinality]`.
