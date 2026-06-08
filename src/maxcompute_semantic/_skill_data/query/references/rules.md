# Rules — MaxCompute Syntax Rules & Function Traps

> **Loaded on demand** — SKILL.md loads this when user needs to write or fix SQL. Read these rules before composing any MaxCompute SQL.

## 8 Essential Syntax Rules

1. **ORDER BY must have LIMIT** — no LIMIT → error.
2. **No implicit CROSS JOIN** — `FROM a, b` without `ON` → error. Always write `JOIN ... ON`.
3. **String concat**: `CONCAT(a, b)` or `a || b` — `+` is numeric; string + string → NULL.
4. **Regex**: `RLIKE`, not `REGEXP`. Pattern is a Java regex. In a single-quoted SQL string literal, `\\` collapses to one backslash, so `\d+` (digits one-or-more) needs **two** backslashes in source: `RLIKE '\\d+'`. Four backslashes (`'\\\\d+'`) match a literal backslash followed by `d+` — almost never what you want. When in doubt, sidestep the escaping with a character class: `RLIKE '[0-9]+'`.
5. **String literals**: single quotes `'value'`. To embed a literal single quote, **double it**: `'O''Reilly'`, not `'O\'Reilly'` — backslash escaping is rejected by the SQL parser.
6. **Partition filter in WHERE**, not `JOIN ON` — latter skips partition pruning.
7. **Division**: `CAST(num AS DOUBLE)` — integer division silently truncates to 0.
8. **Conditional count**: `COUNT_IF(cond)` is the modern, terse form. Older `SUM(CASE WHEN cond THEN 1 ELSE 0 END)` is still accepted; **never** write `SUM(bool_expr)` (silent NULL on most modes).

## Function Trap Table

| Common mistake | MaxCompute correct form |
| --- | --- |
| `DATE_FORMAT(d, '%Y-%m-%d')` | `TO_CHAR(d, 'yyyy-MM-dd')` |
| `DATE_ADD(d, INTERVAL 7 DAY)` | `DATEADD(d, 7, 'dd')` |
| `DATEDIFF(end, start)` | `DATEDIFF(end, start, 'dd')` (3rd arg required) |
| `NOW()` / `CURRENT_TIMESTAMP` | `GETDATE()` |
| `FROM_UNIXTIME(ts, 'fmt')` | `FROM_UNIXTIME(ts)` (1 arg); format with `TO_CHAR(..., 'fmt')` |
| `GROUP_CONCAT(col, ',')` | `WM_CONCAT(',', col)` (**separator first**) |
| — | `IFNULL(x, 0)` / `NVL(x, 0)` / `COALESCE(x, 0)` are all accepted in MaxCompute; pick by team convention. |
| `JSON_EXTRACT(s, '$.k')` | `GET_JSON_OBJECT(s, '$.k')` |
| `SUBSTRING(s, 0, 5)` | `SUBSTR(s, 1, 5)` (**1-based**) |
| `YEAR(string_col)` / `TO_CHAR(string_col, 'yyyy')` on a STRING-typed date | `SUBSTR(col, 1, 4)` for year, `SUBSTR(col, 1, 7)` for `YYYY-MM` (see "STRING-typed dates" below) |

## STRING-typed dates

MaxCompute's date functions (`TO_CHAR`, `YEAR`, `MONTH`, `DAY`, `DATEDIFF`, `DATEADD`, `WEEKOFYEAR`, …) expect a `DATE` / `DATETIME` / `TIMESTAMP` argument. When given a `STRING` they **return NULL silently** — no error, no warning, just an empty result set downstream. This is the single most common cause of "my SQL ran but returned 0 rows" failures on this dialect.

Two safe patterns when the source column is STRING-typed:

1. **String-slice** — works when the format is fixed-width `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`:
   - Year:  `SUBSTR(col, 1, 4)` → `'2012'`
   - Month: `SUBSTR(col, 6, 2)` → `'07'`
   - Year-month: `SUBSTR(col, 1, 7)` → `'2012-07'`
2. **Cast first**, then use the date functions normally:
   - `YEAR(TO_DATE(col, 'yyyy-MM-dd'))`
   - `TO_CHAR(TO_DATE(col, 'yyyy-MM-dd'), 'yyyy-MM')`

Prefer pattern (1) for simple year/month/day extraction — it's cheaper and avoids the parse step.

## Table reference form in SQL — tier-aware

Run `mcs status` once at session start and read the `tier:` line. It
governs which form of table reference your SQL must use:

- **`tier: 3-level`** — prefer the 3-segment FQN `project.schema.table`
  for every table reference. The form is self-contained (no dependence
  on the session's default schema), parses unambiguously across all
  sources, and `mcs` injects `odps.namespace.schema=true` on every
  request so the engine accepts it. Example: `SELECT * FROM
  acme_dw.sales.orders LIMIT 10`. (Single-source profiles also accept
  bare names — `mcs` auto-fills the default schema — so a bare-name
  query is not wrong there; the FQN is just always safe.)
- **`tier: 2-level`** — use bare table names (`FROM orders`). The
  2-level parser misreads `project.table` as `schema.table` and rejects
  the 3-segment form entirely for tables the connection owns, so the
  bare form is the only one that resolves.

## Column-index markers (overview / compatibility output)

The profile overview (`mcs -f json show` → `data.markdown`) renders
each table's compact `columns_index` entries as
`name[:type] [marker] [format_hint]  # description`. Single-table JSON
(`mcs -f json show --table T`) should be read from `data.columns[]`;
its `data.tables[0].columns_index` field is only a compatibility alias
for older scripts. The bracketed tags are agent-actionable hints from
the semantic layer:

| Marker | Meaning | Action |
| --- | --- | --- |
| `[pk]` | Primary identifier | Single-row lookup key; deduped projections can use it. |
| `[fk]` | Foreign key | Join target — pair with the referenced table's `[pk]`. |
| `[unique]` | Uniqueness ≥ 0.98 but not annotated as `[pk]` | Safe as a join key or for `DISTINCT`. |
| `[null]` | `null_ratio ≥ 0.99` | Don't filter on it; don't project it as the answer. |
| `[const]` | Exactly one distinct value | Same — useless as filter, projection, or join key. |
| `[str-date]` | STRING-typed date column (values are pure dates `'YYYY-MM-DD'`) | **Never** wrap with `YEAR()`, `MONTH()`, `TO_CHAR(col, fmt)` — they return NULL silently on STRING. Use `SUBSTR(col, 1, 4)` for year, `SUBSTR(col, 1, 7)` for `YYYY-MM`, or pre-cast with `TO_DATE(col, 'yyyy-MM-dd')` then apply the date functions. See "STRING-typed dates" above. |
| `[str-datetime]` | STRING-typed temporal column with a time component (e.g. `'2014-09-01 12:34:56'`) | Same trap as `[str-date]` for `YEAR`/`MONTH`/`TO_CHAR`. **Plus**: `col > 'YYYY-MM-DD'` lexically mis-orders boundary rows — `'2014-09-01 12:34:56' > '2014-09-01'` is TRUE because the longer string sorts after the shorter prefix. For date-level boundary comparison use `SUBSTR(col, 1, 10) > 'YYYY-MM-DD'` or wrap with `TO_DATE(SUBSTR(col, 1, 10), 'yyyy-MM-dd')`. |
| `[str-time]` | STRING-typed pure-time / duration column (no leading date) — e.g. lap times `'1:34.188'`, wall-clock `'12:34:56'`, response durations `'2:30.500'` | Date / time functions all return NULL on STRING — there is no `HOUR(STRING_COL)` recovery. Lexical `ORDER BY` / `MIN` / `MAX` only sort correctly when every value has uniform width (`'1:34.188'` lex-sorts before `'12:34.188'`). **First scan `columns_index` for a sibling `*_ms` / `milliseconds` / `*_seconds` BIGINT column** — that is the numeric equivalent and is correct for sort / aggregate / compare without further work. Only fall back to `REGEXP_EXTRACT(col, '^(\\d+):(\\d+)\\.?(\\d*)$', N)` on the string itself when no numeric sibling exists. |
| `[date]` | Non-STRING, non-native-temporal date-shaped column (BIGINT unix timestamp annotated as `dim_type='time'`) | Wrap with `FROM_UNIXTIME(col)` before applying any date function. |

Native temporal columns (`DATE`, `DATETIME`, `TIMESTAMP`) carry their `:date` / `:datetime` / `:timestamp` type tag and no extra marker — the date functions work on them directly without wrapping.

Advanced patterns (CTE, window functions, LATERAL VIEW, etc.): [`references/sql.md`](sql.md).
