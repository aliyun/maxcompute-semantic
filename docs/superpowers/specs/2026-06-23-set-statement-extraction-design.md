# SET Statement Extraction — Design

Date: 2026-06-23
Status: Approved (design), pending implementation plan
Versions affected: all (0.16.2 → 0.17.3 behave identically; this is not a version-specific bug)

## Background

`mcs sql submit` / `mcs sql execute` fail when the SQL contains a `SET`
statement, e.g. `SET odps.sql.mapper.split.size = 4096; SELECT ...`. Two
independent causes were verified in code, tests, and empirical runs:

### Cause 1 — write guard rejects SET

`classify_sql` ([sql_guard.py:29](../../../src/maxcompute_semantic/mc_client/sql_guard.py))
lists `sqlglot.exp.Set` under `_WRITE_SESSION_EXPR_TYPES`, so any SQL
containing a SET is classified `"write"` and refused with
`WriteOpRejectedError` (code `WRITE_OP_REJECTED`, exit 2) unless
`--allow-write` is passed. This is by-design since v0.16.1 and test-asserted
([test_sql_cmd.py:2634](../../../tests/unit/commands/test_sql_cmd.py)).

### Cause 2 — cost gate blocks SET even with `--allow-write`

`--allow-write` only clears the write guard. `enforce_cost_gate`
([cost_gate.py:115](../../../src/maxcompute_semantic/mc_client/cost_gate.py))
still runs and calls `odps.execute_sql_cost(sql)` with the **raw**
`SET; SELECT` SQL. pyodps's `execute_sql_cost`
([core.py:1466](../../../.venv/lib/python3.10/site-packages/odps/core.py))
does **not** strip SET to hints — unlike `run_sql`, which does via
`options.sql.parse_set_as_hints` (default `True`). The cost estimator
receives the SET statement, typically cannot cost it, errors out, and
because `SET; SELECT` is multi-statement `_is_low_risk_uncosted_read`
([cost_gate.py:80](../../../src/maxcompute_semantic/mc_client/cost_gate.py))
returns `False` → `CostBlockedError`. Both branches were verified by
simulating `execute_sql_cost` success and failure locally:

```text
classify_sql("SET k=v; SELECT ...")            -> write
_is_low_risk_uncosted_read("SET k=v; SELECT") -> False
Case A (execute_sql_cost OK):   CLEARED  -> proceeds to run_sql
Case B (execute_sql_cost ERR):  BLOCKED  -> CostBlockedError
```

### Key finding: SET has no write semantics

pyodps treats `SET key=val` as **hints/settings**, not writes: `run_sql`'s
`parse_set_as_hints` converts `SET key=val` into the `hints` dict using the
regex `set\s+([a-z0-9.]+)\s*=\s*([^;]+)`. MaxCompute classifies SET as a
session property (transient, instance-scoped), not DML/DDL. mcs's
classification of SET as `"write"` was a conservative fail-closed proxy
(code comment: "SET mutates session state, e.g. odps.sql.allow.fullscan"),
not a reflection of true write semantics.

### Version scope

The behavior is **identical in 0.16.2 and 0.17.3**. Verified via full
diffs between `v0.16.2` and `HEAD`: `client.py` only changed the
synchronous-timeout handoff (`64497ba`); `sql_guard.py` / `cost_gate.py`
only swapped `sqlglot.parse` → `parse_mc` with zero effect on SET
classification (both parsers emit a `Set` node for `SET key=val`, confirmed
empirically); `hints.py` unchanged; the MaxCompute dialect (added `0.17.0`)
does not change SET+SELECT parsing. There is no 0.16.2-specific bug; the
fix applies to all versions.

## Goal

Make `SET key=val; <query>` scripts work transparently: SET statements are
extracted into pyodps hints, and the remaining query is classified and
executed normally — no `--allow-write` needed for `SET; SELECT`.

## Non-goals

- Not adding a `--set` / `--hint` CLI flag. Extraction handles pre-baked
  `SET; SELECT` scripts (what agents/users actually write); a flag is
  unnecessary friction and does not help pre-baked scripts.
- Not changing how non-hint SETs (`SET LABEL`, `SETPROJECT`, …) are
  handled — they stay in the SQL and keep current classification.
- Not removing or weakening the cost gate. It still runs and remains the
  cost backstop.

## Design

### Decision: extract SET→hints, classify the remaining statement

`SET key=val` statements are removed from the SQL text and collected into a
hints dict. The remaining SQL is classified under the existing
read/write/unparseable rules:

- `SET k=v; SELECT ...` → `read` (no `--allow-write` needed)
- `SET k=v; INSERT ...` → `write` (`--allow-write` required, for the INSERT)
- `SET LABEL x TO y; SELECT ...` → `SET LABEL` not extracted (not `key=val`)
  → stays → classified as before

### What does NOT change

`classify_sql` and `_WRITE_SESSION_EXPR_TYPES` (which still contains
`sqlglot.exp.Set`) are **not modified**. The extraction layer runs before
classification, so `classify_sql` only ever sees SET-free SQL for
extractable scripts. Non-extractable SETs (`SET LABEL`, `SETPROJECT`) still
reach `classify_sql` and are still classified `write`/`unparseable` —
which is the desired behavior (those are real session/security ops that
stay gated by `--allow-write`). Do not "clean up" the Set classification as
part of this change; doing so would let `SET LABEL` slip through ungated.

### Approach (chosen): extract once at each verb, thread stripped SQL + hints

New pure helper in a new module
`src/maxcompute_semantic/mc_client/sql_preprocess.py`:

```python
def split_set_hints(sql: str) -> tuple[str, dict[str, str]]:
    """Extract `SET key=val` statements into hints.

    Returns ``(sql_without_sets, hints)``. Uses the MaxCompute sqlglot
    tokenizer to split the SQL into verbatim statement segments at
    top-level semicolons (string- and comment-aware, so ``;`` inside
    literals or ``--`` comments is not treated as a separator). Each
    segment is parsed with ``parse_mc``; segments that parse to an
    assignment ``Set`` (a ``SetItem`` with an ``EQ``, not ``UNSET``/tag)
    become hints; all other segments (SELECT/DDL, and ``SET LABEL`` which
    parses as a ``Command``) are kept verbatim and rejoined.

    Non-SET SQL is preserved verbatim (no AST regeneration): the MaxCompute
    sqlglot generator rewrites functions (verified: ``TO_CHAR(d, fmt)`` →
    ``CAST(d AS STRING)`` losing the format string, ``SUBSTRING`` →
    ``SUBSTR``, ``GROUP_CONCAT`` → ``WM_CONCAT``), which would change the
    user's SQL semantics. key/val are read from the ``Set`` AST
    (``SetItem.this.this`` is the key, ``.expression`` the value); boolean
    values normalize to ``TRUE``/``FALSE`` (MaxCompute is case-insensitive
    on these) while other literal forms round-trip.
    """
```

**Extraction mechanism** (sqlglot-based, verbatim-preserving):

1. Tokenize the SQL with the MaxCompute dialect tokenizer. Tokens carry
   char offsets (`start`/`end`) and the tokenizer is string- and
   comment-aware, so a `;` inside a string literal or `--` comment is not
   emitted as a `SEMICOLON` token.
2. Slice the original SQL at each top-level `SEMICOLON` token's position
   into verbatim statement segments.
3. Parse each segment with `parse_mc`. If it parses to an assignment `Set`
   (`isinstance(stmt, exp.Set)` and not `unset`/`tag`), read its `SetItem`
   key/val from the AST into `hints` and drop the segment. Otherwise keep
   the verbatim segment (`SET LABEL` parses as a `Command`, so it stays).
4. Rejoin the kept segments with a `;` separator.

The non-SET SQL is never regenerated — only original substrings are
recombined — so the function rewrites the MaxCompute generator would apply
(verified lossy: `TO_CHAR` → `CAST`, `SUBSTRING` → `SUBSTR`,
`GROUP_CONCAT` → `WM_CONCAT`) never touch the user's query.

Each verb calls `split_set_hints` as its **first** step, before
`_guard_sql_execution` / `_route_project` / client construction:

```python
# commands/sql.py — submit_cmd (and execute_cmd / cost_cmd / explain_cmd / review_cmd)
def submit_cmd(..., sql):
    stripped_sql, set_hints = split_set_hints(sql)
    _guard_sql_execution(stripped_sql, profile, allow_write=allow_write)
    client, schema = _client_and_schema_for_sql(project, profile, schema, stripped_sql)
    instance_id = client.run_sql_async(
        stripped_sql, schema=schema, hints=set_hints or None,
        assume_yes=assume_yes, allow_write=allow_write,
    )
```

`run_sql_async` / `execute_sql` / `cost_estimate` receive `hints` (now
carrying extracted SETs) and the stripped SQL:

- **write guard** sees stripped SQL (no SET) → classifies the actual
  statement (`SET; SELECT` → `read`).
- **cost gate** calls `execute_sql_cost(stripped_sql, hints=merged)` →
  clean SELECT + SET-as-hints → estimates normally, no `CostBlocked`.
- **`run_sql`** receives `stripped_sql` + `hints=merged` → pyodps finds no
  SET (already stripped) and uses mcs's hints. mcs extracts once and passes
  the same stripped SQL + hints to both `execute_sql_cost` and `run_sql`, so
  the cost estimate and execution process identical SQL.

### Hints merging / precedence

Extracted `set_hints` enter `build_hints` via the `user_hints=` position
([hints.py:23](../../../src/maxcompute_semantic/mc_client/hints.py)), which
uses `setdefault`. Precedence is therefore:
caller-supplied `hints` > extracted SETs > tier-derived
(`odps.namespace.schema`). Explicit user hints win, matching pyodps's
`code_hints.update(hints)` semantics.

### Guardrail analysis: no denylist needed

Only `SET key=val` (hint form) is extracted → becomes a read-enabling hint.
Security-relevant SETs that do not match the hint regex (`SET LABEL …`,
`SETPROJECT`) stay in the SQL and keep current write/unparseable
classification (still gated by `--allow-write`). The guardrail is therefore
naturally preserved for non-hint SETs without a maintained denylist.

For cost-relevant hint SETs that **are** extracted (e.g.
`odps.sql.allow.fullscan=true`): the cost gate still estimates the resulting
query (with the SET applied as a hint) and blocks if it exceeds
`blocked_cny`. So the cost gate remains the backstop; dropping the
SET→write gate does not remove cost safety.

The narrow residual gap — security-only, non-cost SETs (e.g. system-table
access) that the cost gate cannot price — is out of scope for this change
and can be addressed with a denylist later if it becomes a real concern.

## Edge cases

| Case | Behavior |
| --- | --- |
| `SET k=v; SELECT ...` | Extracted → `read`, runs without `--allow-write` |
| `SET k=v; INSERT ...` | Extracted → `write`, `--allow-write` required (for the INSERT) |
| `SET a=1; SET b=2; SELECT ...` | All SETs extracted → `read` |
| Standalone `SET k=v` (no query) | `stripped_sql` empty → reject with `WriteOpRejectedError`-class: "SET with no query — nothing to execute", remediation "add a SELECT/INSERT after the SET" |
| `SET LABEL x TO y; SELECT ...` | `SET LABEL` not extracted (not `key=val`) → stays → classified as before |
| No SET in SQL | `split_set_hints` returns `(sql, {})` unchanged — zero behavior change |

## Verb scope

All five `mcs sql` verbs that take a SQL argument:
`submit`, `execute`, `cost`, `explain`, `review`. `review` is read-only
linting and calls `_classify_sql` directly; after extraction it classifies
the stripped SELECT and can lint it (currently it refuses `SET; SELECT` as
"write", which is also fixed by this change).

## Test impact

- **Update** [test_sql_cmd.py:2631-2634](../../../tests/unit/commands/test_sql_cmd.py):
  `SET k=v; SELECT 1` now classifies `read`; pure standalone `SET k=v`
  rejected. Replace the `SET == "write"` assertion.
- **Add** `tests/unit/mc_client/test_sql_preprocess.py`:
  - `split_set_hints` extracts `SET k=v`, preserves the SELECT verbatim,
    handles multi-SET, leaves `SET LABEL` in the SQL, returns `(sql, {})`
    when no SET.
  - standalone `SET` → empty `stripped_sql` → rejection.
- **Add** cost-gate test: `SET k=v; SELECT ...` with a mocked
  `execute_sql_cost` — assert the stripped SQL is passed and `set_hints`
  land in `hints`, verdict `ok`, no `CostBlocked`.
- **Add** integration test in `TestSqlExecuteWriteGuard`
  ([test_sql_cmd.py](../../../tests/unit/commands/test_sql_cmd.py)):
  `mcs sql execute "SET k=v; SELECT 1"` succeeds without `--allow-write`;
  `SET LABEL ...; SELECT 1` still requires `--allow-write`.

## Out of scope / future

- `--set` / `--hint` CLI flag — could layer on top later if users want to
  set hints without embedding SET in SQL.
- Denylist for security-only SETs (system-table access) if the cost-gate
  backstop proves insufficient in practice.
