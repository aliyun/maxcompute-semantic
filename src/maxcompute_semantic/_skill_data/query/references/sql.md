# SQL Reference — Advanced MaxCompute Syntax

> **Loaded on demand** — SKILL.md loads this when advanced SQL patterns (CTE, window functions, complex joins, set ops, DDL) are needed beyond the 8 essential rules and function trap table in [`rules.md`](rules.md).

## Set operations

```sql
-- Concatenate without dedup (preferred; cheaper).
SELECT id FROM a UNION ALL SELECT id FROM b;

-- Dedup union. Default UNION = UNION DISTINCT; the implicit form is
-- a frequent source of needless shuffle cost. Always write the
-- distinct/all word explicitly so reviewers see the intent.
SELECT id FROM a UNION DISTINCT SELECT id FROM b;

-- INTERSECT and EXCEPT are both DISTINCT by default; ALL variants
-- (INTERSECT ALL / EXCEPT ALL) keep duplicates.
SELECT id FROM a INTERSECT     SELECT id FROM b;
SELECT id FROM a EXCEPT        SELECT id FROM b;
```

## Conditional aggregation

`COUNT_IF(cond)` is the modern, terse form. Prefer it over the
older `SUM(CASE WHEN cond THEN 1 ELSE 0 END)` idiom:

```sql
SELECT COUNT_IF(status = 'paid') AS paid_orders,
       COUNT_IF(amount > 1000)   AS large_orders
FROM orders;
```

For non-count aggregates, `SUM(CASE …) / AVG(CASE …)` is still the
right shape — there is no `SUM_IF` in MaxCompute:

```sql
SELECT SUM(CASE WHEN region = 'EU' THEN amount END) AS eu_revenue
FROM orders;
```

## EXISTS / NOT EXISTS

Correlated subquery for membership testing. Prefer EXISTS over
`IN (SELECT …)` when the inner set can be large — EXISTS short-
circuits at the first match per outer row:

```sql
SELECT c.*
FROM customers c
WHERE EXISTS (
  SELECT 1 FROM orders o
  WHERE o.customer_id = c.id AND o.status = 'paid'
);
```

`NOT EXISTS` is the safe anti-join — unlike `NOT IN (subquery)` it
handles NULLs in the inner set correctly (NOT IN against any NULL
yields UNKNOWN and silently drops outer rows).

## LATERAL VIEW + explode / posexplode

Row expansion from array/map columns:

```sql
SELECT col1, item
FROM my_table
LATERAL VIEW explode(array_col) t AS item;

-- posexplode also exposes the array index
SELECT col1, idx, item
FROM my_table
LATERAL VIEW posexplode(array_col) t AS idx, item;
```

Use `LATERAL VIEW OUTER` to preserve rows where the array is NULL
or empty — without OUTER, those rows are silently dropped:

```sql
SELECT u.id, t.tag
FROM users u
LATERAL VIEW OUTER explode(u.tags) t AS tag;
```

## MAPJOIN hint

For small tables (< 10 MB), broadcast the small side to avoid
shuffle:

```sql
SELECT /*+ MAPJOIN(small_dim) */ a.*, b.name
FROM large_fact a JOIN small_dim b ON a.dim_id = b.id;
```

Required for non-equi joins (`a.ts BETWEEN b.start AND b.end`) —
MaxCompute refuses non-equi joins without a MAPJOIN hint.

## Window functions

```sql
-- Row number per group.
SELECT *, ROW_NUMBER() OVER (PARTITION BY dim ORDER BY ts DESC) AS rn
FROM my_table;

-- Cumulative distribution.
SELECT *, CUME_DIST() OVER (PARTITION BY dim ORDER BY val) AS cd
FROM my_table;

-- Frame: 7-day rolling sum (rows-based).
SELECT ds, SUM(amount) OVER (
  ORDER BY ds ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
) AS rolling7
FROM daily;

-- LAG / LEAD for prior-row references.
SELECT id, ts, LAG(amount, 1) OVER (PARTITION BY id ORDER BY ts) AS prev
FROM events;
```

## CTE (WITH clause)

```sql
WITH recent AS (
  SELECT * FROM orders WHERE ds >= DATEADD(GETDATE(), -7, 'dd')
)
SELECT customer_id, COUNT(*) AS cnt
FROM recent
GROUP BY customer_id;
```

Multiple CTEs chain via comma:

```sql
WITH a AS (SELECT … ),
     b AS (SELECT … FROM a WHERE …)
SELECT … FROM b;
```

CTEs are inlined at planning time; reusing the same CTE multiple
times re-evaluates it. For genuinely shared intermediate state,
materialize via a temporary table or write back to MaxCompute.

## Grouping extensions

```sql
-- Multiple grouping sets in one scan.
SELECT region, channel, SUM(amount)
FROM sales
GROUP BY GROUPING SETS ((region), (channel), (region, channel), ());

-- ROLLUP is the prefix-hierarchy shorthand.
SELECT region, channel, SUM(amount)
FROM sales
GROUP BY ROLLUP (region, channel);
```

## DDL

For CREATE TABLE/VIEW, refer to MaxCompute official documentation
or use `mcs meta describe-table` to inspect existing table
definitions as templates.
