# Projection Discipline

Pick the minimum columns that answer the question. Extra "helpful"
columns turn a correct answer into a wrong one when callers compare result
sets exactly.

## SELECT only what was asked

- "Which / who / list / name the X" (asking for a *category*) means
  project the column that names X. Prefer a human-readable label
  (`*_name`, `*_title`, `*_label`) when one exists on the entity table;
  otherwise project the primary identifier.
- "What is the highest / largest / maximum / total / average X" (asking
  for a *value*) means project the aggregate of X itself, not the group
  it falls under. "What is the highest monthly consumption" wants
  `SUM(consumption)`, not the month string. The question word decides:
  *which / who* → categorical (project the label); *what value / how
  much* → scalar (project the aggregate).
- "How many / count / total / average / percentage / ratio / difference"
  means project one scalar. Do not add grouping keys or sub-aggregates
  unless the question asks for that breakdown.
- Columns referenced in `WHERE`, `JOIN`, or `ORDER BY` are filter/ranking
  signal, not output. The value used to order by `DESC LIMIT 1` is the
  ranking key; it belongs in `ORDER BY`, not necessarily in `SELECT`.
  *ORDER BY is conditional:* "**which** row has the highest X" → the
  ORDER BY column is just the ranking key, project the row identifier;
  "**what is** the highest X" → that same column IS the answer, project
  the aggregate.
- Add a column to `SELECT` only when the question explicitly names it.
  "show the name and the date" asks for two columns; "which orders are
  pending" asks for one order identifier.
- Do not project intermediate values. If a ratio or difference is the
  answer, return only that expression, not the raw inputs beside it.
- **GROUP BY does not pull columns into SELECT.** MaxCompute (like most
  engines) requires non-aggregated SELECT columns to appear in GROUP BY,
  but the reverse is *not* true. Don't drag a filter or join column into
  SELECT "to keep the GROUP BY tidy" — if the question doesn't ask for
  it, it doesn't belong. `SELECT customerid ... WHERE segment='LAM'
  GROUP BY customerid ORDER BY SUM(consumption) LIMIT 1` is correct;
  adding `, segment` to both SELECT and GROUP BY pollutes the result
  tuple with a column the WHERE already pins to one value.
- **One statement per answer.** Don't return two `SELECT` statements
  separated by `;` when a single JOIN-ed query would return the same
  data — the harness reads the first result set only. When the question
  reads as "What is X about Y? List Z about Y." or "Give me X and the
  corresponding Z", the answer is *one* SELECT that JOINs and projects
  both columns, not two parallel queries.
- Do not apply display formatting unless the question asks for it.
  `ROUND`, `CAST`, `CONCAT('%')`, `FORMAT`, and similar transformations
  can change values and break programmatic comparison. Wrapping a correct
  ratio in `ROUND(..., 2)` turns `33.33333...` into `33.33` and the
  value comparison then fails. Apply only when the question explicitly
  says "round to N decimals", "as a whole number", "with a percent
  sign", or similar.

## Evidence is not the question

Evidence is a glossary, not an instruction to add every mentioned column.
Before writing SQL, list the entities and attributes in the question text.
Apply only evidence lines that map those named entities/attributes. An
evidence line that doesn't tie back to a noun in the question is noise.

When evidence says "eyes refers to `eye_colour_id`" or "publisher refers
to `publisher_id`", project that exact column unless the question asks for
a human-readable name. Do not dereference IDs into lookup-table names for
readability — dereferencing changes the projected value's *type*
(id → name) and breaks strict result-set comparison.

## Worked examples — common projection traps

- **"Which event has the highest spend-to-budget ratio?"**
  - Correct: `SELECT event_name FROM ... ORDER BY spent/amount DESC LIMIT 1` (1 column)
  - Wrong: `SELECT event_name, spent/amount AS ratio FROM ... ORDER BY ratio DESC LIMIT 1` (the ratio belongs in ORDER BY only — adding it to SELECT makes the result tuple mismatch)
- **"Which month of 2012 had the largest consumption?"** (categorical)
  - Correct: `SELECT SUBSTR(date,5,2) FROM ... GROUP BY SUBSTR(date,5,2) ORDER BY SUM(consumption) DESC LIMIT 1` (1 column — the month)
  - Wrong: `SELECT SUBSTR(date,5,2), SUM(consumption) FROM ...` (the consumption is the ranking key, not part of the answer)
- **"What is the highest monthly consumption in 2012?"** (scalar — the value)
  - Correct: `SELECT SUM(consumption) FROM ... GROUP BY SUBSTR(date,5,2) ORDER BY SUM(consumption) DESC LIMIT 1` (1 column — the value)
  - Wrong: `SELECT SUBSTR(date,5,2) FROM ... ORDER BY SUM(consumption) DESC LIMIT 1` (returns the month, but the question asked for the *amount*)
- **"What is the difference between male and female counts?"**
  - Correct: `SELECT SUM(CASE WHEN sex='M' THEN 1 END) - SUM(CASE WHEN sex='F' THEN 1 END) FROM ...` (1 scalar)
  - Wrong: `SELECT SUM(...) AS male, SUM(...) AS female, SUM(...) - SUM(...) AS diff FROM ...` (the inputs are intermediates, not part of the answer)
- **"What percentage of clients are male?"**
  - Correct: `SELECT SUM(CASE WHEN gender='M' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) FROM client` (raw ratio)
  - Wrong: `SELECT ROUND(SUM(CASE WHEN gender='M' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) FROM client` (the question said "percentage", not "round to 2 decimals" — `ROUND` discards precision and a value comparison against `33.33333...` then fails)
- **"In 2012, who had the least consumption in LAM?"** (filter column dragged into SELECT/GROUP BY)
  - Correct: `SELECT customerid FROM customers c JOIN yearmonth y ON c.customerid=y.customerid WHERE c.segment='LAM' AND SUBSTR(y.date,1,4)='2012' GROUP BY customerid ORDER BY SUM(y.consumption) ASC LIMIT 1` (1 column — `segment='LAM'` is in WHERE, so it doesn't need to be in SELECT *or* GROUP BY)
  - Wrong: `SELECT customerid, segment FROM ... WHERE segment='LAM' GROUP BY customerid, segment ORDER BY SUM(consumption) ASC LIMIT 1` (segment is a filter column — projecting it adds a second tuple field pinned to 'LAM')
- **"List entities with blue eyes and blond hair."** (evidence carries extra noise)
  - Correct: `SELECT e.entity_name FROM entity e JOIN colour ec ON e.eye_colour_id=ec.id JOIN colour hc ON e.hair_colour_id=hc.id WHERE ec.colour='Blue' AND hc.colour='Blond'` (the question names two attributes — that's the entire WHERE)
  - Wrong: `... JOIN entity_skill es ... WHERE ec.colour='Blue' AND hc.colour='Blond' AND sd.skill_name='Agility'` (the evidence happened to include a skill mapping, but the question never mentions skills — discard that evidence line)
- **"What is order O's status? List the items in this order."** (compound — two clauses, *one* JOIN-ed statement)
  - Correct: `SELECT o.status, i.product_name FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.id='O'` (returns N rows; `status` repeats across rows — both clauses answered by one result set)
  - Wrong: `SELECT o.status FROM orders WHERE id='O'; SELECT product_name FROM order_items WHERE order_id='O'` (two statements separated by `;` — the harness reads only the first result set, so the second clause is silently dropped)

## Pre-execution self-check

Before `mcs sql execute`, read the `SELECT` list left-to-right. For each
projected column, ask: did the user explicitly request this output?

Invalid justifications:

- It gives useful context.
- It is the value I ordered by.
- It is a step in the final computation.
- It is the column used in the filter.

If a projected column only has one of those justifications, remove it.

Then read the WHERE / JOIN list: for each filter, point at the noun in
the question text that motivates it — if you can't, drop the filter (it
likely came from an evidence line that doesn't apply). Finally scan for
value-changing wrappers (`ROUND`, `CAST`, `CONCAT`, `FORMAT`) — if the
question didn't ask for the format change, strip them.
