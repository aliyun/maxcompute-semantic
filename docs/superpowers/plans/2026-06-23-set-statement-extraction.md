# SET Statement Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `SET key=val; <query>` scripts run transparently through `mcs sql` — SET statements are extracted into pyodps hints before the write guard and cost gate, so `SET;SELECT` classifies as the remaining statement (read, no `--allow-write`) and no longer hits `CostBlocked` via `execute_sql_cost`.

**Architecture:** Add a pure helper `split_set_hints(sql) -> (stripped_sql, hints)` that uses the MaxCompute sqlglot tokenizer to split the SQL into verbatim statement segments at top-level semicolons, parses each segment, and converts assignment `SET key=val` segments into a hints dict while keeping all other segments (SELECT/DDL, and `SET LABEL` which parses as a `Command`) verbatim. Each of the 5 SQL verbs calls it first, then passes `stripped_sql` to the guard/routing/client and `set_hints` into the client's existing `hints=` kwarg. `classify_sql` is **unchanged** (non-extractable SETs like `SET LABEL` stay gated). The client layer already accepts and threads `hints` end-to-end, so no client changes are needed.

**Tech Stack:** Python 3.10+, sqlglot (MaxCompute dialect registered as `maxcompute_semantic.dialect`), pytest, click (CLI).

**Spec:** [docs/superpowers/specs/2026-06-23-set-statement-extraction-design.md](../specs/2026-06-23-set-statement-extraction-design.md)

---

## File Structure

- **Create** `src/maxcompute_semantic/mc_client/sql_preprocess.py` — pure `split_set_hints(sql) -> (stripped_sql, hints)`. No I/O, no mcs imports beyond the dialect.
- **Create** `tests/unit/mc_client/test_sql_preprocess.py` — unit tests for `split_set_hints`.
- **Modify** `src/maxcompute_semantic/commands/sql.py` — add `_split_or_emit` helper (extraction + standalone-SET rejection); wire `submit_cmd`, `execute_cmd`, `cost_cmd`, `explain_cmd`, `review_cmd` to extract first and pass `stripped_sql` + `set_hints`.
- **Modify** `tests/unit/commands/test_sql_cmd.py` — add verb-level integration tests; annotate the existing `test_set_is_write` (it stays — `classify_sql` is unchanged).

**Unchanged (intentional):** `src/maxcompute_semantic/mc_client/sql_guard.py` (`classify_sql` and `_WRITE_SESSION_EXPR_TYPES` still treat `Set` as write — this is the belt ensuring non-extractable SETs stay gated), `src/maxcompute_semantic/mc_client/client.py` (all four execution methods already accept `hints=`), `src/maxcompute_semantic/mc_client/hints.py`, `src/maxcompute_semantic/mc_client/cost_gate.py`.

---

## Task 1: `split_set_hints` pure helper + unit tests

**Files:**
- Create: `tests/unit/mc_client/test_sql_preprocess.py`
- Create: `src/maxcompute_semantic/mc_client/sql_preprocess.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/mc_client/test_sql_preprocess.py`:

```python
# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client.sql_preprocess.split_set_hints."""

from __future__ import annotations

from maxcompute_semantic.mc_client.sql_preprocess import split_set_hints


class TestSplitSetHints:
    def test_extracts_set_and_preserves_select_verbatim(self) -> None:
        sql = "SET odps.sql.mapper.split.size = 4096; SELECT a, b FROM t WHERE ds='20240101'"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT a, b FROM t WHERE ds='20240101'"
        assert hints == {"odps.sql.mapper.split.size": "4096"}

    def test_standalone_set_yields_empty_sql(self) -> None:
        stripped, hints = split_set_hints("SET odps.sql.mapper.split.size=4096")
        assert stripped == ""
        assert hints == {"odps.sql.mapper.split.size": "4096"}

    def test_multiple_sets_all_extracted(self) -> None:
        sql = "SET odps.sql.allow.fullscan = true; SET odps.sql.reducer.memory = 4096; SELECT x FROM t"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT x FROM t"
        assert hints == {"odps.sql.allow.fullscan": "TRUE", "odps.sql.reducer.memory": "4096"}

    def test_semicolon_inside_string_literal_is_not_a_separator(self) -> None:
        sql = "SET a=1; SELECT x FROM t WHERE s='a;b;c'"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT x FROM t WHERE s='a;b;c'"
        assert hints == {"a": "1"}

    def test_set_label_is_not_extracted_stays_verbatim(self) -> None:
        sql = "SET LABEL tbl TO user; SELECT 1"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SET LABEL tbl TO user; SELECT 1"
        assert hints == {}

    def test_no_set_returns_unchanged(self) -> None:
        stripped, hints = split_set_hints("SELECT 1 FROM dual")
        assert stripped == "SELECT 1 FROM dual"
        assert hints == {}

    def test_non_select_preserved_verbatim_no_function_rewrite(self) -> None:
        # TO_CHAR must NOT be rewritten to CAST — regeneration is lossy.
        sql = "set odps.sql.type.system.odps2 = true; SELECT TO_CHAR(d, 'yyyyMMdd') FROM t"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT TO_CHAR(d, 'yyyyMMdd') FROM t"
        assert hints == {"odps.sql.type.system.odps2": "TRUE"}

    def test_trailing_semicolon_handled(self) -> None:
        stripped, hints = split_set_hints("SET a=1; SELECT 1;")
        assert stripped == "SELECT 1"
        assert hints == {"a": "1"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/mc_client/test_sql_preprocess.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'maxcompute_semantic.mc_client.sql_preprocess'`

- [ ] **Step 3: Implement `split_set_hints`**

Create `src/maxcompute_semantic/mc_client/sql_preprocess.py`:

```python
# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Extract ``SET key=val`` statements into pyodps hints.

Called by the ``mcs sql`` verbs before the write guard and cost gate, so
``SET k=v; SELECT ...`` scripts run transparently (classified as the
remaining statement) instead of being rejected as a write or blocked by
the cost gate's ``execute_sql_cost`` (which, unlike ``run_sql``, does not
strip SET to hints).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from maxcompute_semantic.dialect import MaxCompute, parse_mc


def split_set_hints(sql: str) -> tuple[str, dict[str, str]]:
    """Extract ``SET key=val`` statements into hints.

    Returns ``(sql_without_sets, hints)``. Uses the MaxCompute sqlglot
    tokenizer to split the SQL into verbatim statement segments at
    top-level semicolons (string- and comment-aware, so ``;`` inside a
    literal or ``--`` comment is not a separator). Each segment is parsed
    with ``parse_mc``; segments that parse to an assignment ``Set`` whose
    every ``SetItem`` is an ``EQ`` (not ``UNSET``/tag, not a bare flag)
    become hints and are dropped; all other segments (SELECT/DDL, and
    ``SET LABEL`` which parses as a ``Command``) are kept verbatim and
    rejoined.

    Non-SET SQL is preserved verbatim (no AST regeneration): the MaxCompute
    sqlglot generator rewrites functions (verified lossy: ``TO_CHAR(d,
    fmt)`` -> ``CAST(d AS STRING)`` losing the format string, ``SUBSTRING``
    -> ``SUBSTR``), which would change the user's SQL semantics. key/val
    are read from the ``Set`` AST (``SetItem.this.this`` is the key,
    ``.expression`` the value); boolean values normalize to
    ``TRUE``/``FALSE`` (MaxCompute is case-insensitive on these) while
    other literal forms round-trip.
    """
    hints: dict[str, str] = []
    kept: list[str] = []
    toks = MaxCompute.Tokenizer().tokenize(sql)
    semi_ends = [t.end for t in toks if t.token_type is TokenType.SEMICOLON]
    bounds = [-1, *semi_ends, len(sql)]
    for i in range(len(bounds) - 1):
        segment = sql[bounds[i] + 1 : bounds[i + 1]].strip()
        if not segment:
            continue
        try:
            stmts = parse_mc(segment, error_level=sqlglot.ErrorLevel.IGNORE)
        except Exception:
            stmts = []
        stmt = stmts[0] if stmts else None
        if (
            isinstance(stmt, exp.Set)
            and not stmt.args.get("unset")
            and not stmt.args.get("tag")
        ):
            items = stmt.args.get("expressions") or []
            eqs = [it for it in items if isinstance(it.this, exp.EQ)]
            if eqs and len(eqs) == len(items):
                for it in eqs:
                    eq = it.this
                    hints.append((eq.this.sql(dialect="maxcompute"), eq.expression.sql(dialect="maxcompute")))
                continue
        kept.append(segment)
    return "; ".join(kept), dict(hints)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/mc_client/test_sql_preprocess.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/maxcompute_semantic/mc_client/sql_preprocess.py tests/unit/mc_client/test_sql_preprocess.py
git commit -m "feat(mc_client): add split_set_hints to extract SET key=val into hints

Uses the MaxCompute sqlglot tokenizer to split SQL into verbatim
statement segments, then converts assignment SET statements into a
hints dict while keeping all other segments (incl. SET LABEL) verbatim.
Non-SET SQL is never regenerated (the generator rewrites functions
lossily). Pure helper; not yet wired into the verbs."
```

---

## Task 2: Wire `execute_cmd` + `submit_cmd` via `_split_or_emit`

**Files:**
- Modify: `src/maxcompute_semantic/commands/sql.py` (import near line 72; add `_split_or_emit` near `_guard_sql_execution` at line 286; modify `execute_cmd` body at line 437; modify `submit_cmd` body at line 526-531)
- Modify: `tests/unit/commands/test_sql_cmd.py` (add tests in `TestSqlExecuteWriteGuard`)

- [ ] **Step 1: Add the import**

In `src/maxcompute_semantic/commands/sql.py`, add to the `mc_client` imports block (after the `sql_guard` imports around line 87):

```python
from maxcompute_semantic.mc_client.sql_preprocess import split_set_hints
```

- [ ] **Step 2: Add the `_split_or_emit` helper**

Add this function immediately after `_guard_sql_execution` (after line 323, before `_client_and_schema_for_sql`):

```python
def _split_or_emit(sql: str) -> tuple[str, dict[str, str]]:
    """Extract SET→hints; emit+exit if the SQL is only SETs (no query).

    Shared by every ``mcs sql`` verb so the write guard, project routing,
    cost gate, and pyodps submission all see the SET-free SQL and the
    extracted hints. A standalone ``SET k=v`` (no query) is rejected with
    ``WriteOpRejectedError`` because there is nothing to execute.
    """
    stripped_sql, set_hints = split_set_hints(sql)
    if not stripped_sql.strip():
        _emit_mcs_error(
            sql,
            None,
            WriteOpRejectedError(
                "SQL contained only SET statements with no query; nothing to execute",
                remediation=(
                    "add a SELECT/INSERT/... statement after the SET, or remove "
                    "the SET if you only meant to set a session property"
                ),
                sql=sql,
            ),
        )
    return stripped_sql, set_hints
```

- [ ] **Step 3: Wire `submit_cmd`**

In `submit_cmd` (around line 525-536), replace:

```python
    """Submit SQL asynchronously and return the MaxCompute instance ID."""
    _guard_sql_execution(sql, profile, allow_write=allow_write)

    client = None
    try:
        client, schema = _client_and_schema_for_sql(project, profile, schema, sql)
        instance_id = client.run_sql_async(
            sql,
            schema=schema,
            assume_yes=assume_yes,
            allow_write=allow_write,
        )
```

with:

```python
    """Submit SQL asynchronously and return the MaxCompute instance ID."""
    stripped_sql, set_hints = _split_or_emit(sql)
    _guard_sql_execution(stripped_sql, profile, allow_write=allow_write)

    client = None
    try:
        client, schema = _client_and_schema_for_sql(project, profile, schema, stripped_sql)
        instance_id = client.run_sql_async(
            stripped_sql,
            schema=schema,
            hints=set_hints or None,
            assume_yes=assume_yes,
            allow_write=allow_write,
        )
```

- [ ] **Step 4: Wire `execute_cmd`**

In `execute_cmd` (around line 437-450), replace:

```python
    _guard_sql_execution(sql, profile, allow_write=allow_write)

    client = None
    try:
        client, schema = _client_and_schema_for_sql(project, profile, schema, sql)
        envelope = client.execute_sql(
            sql,
            schema=schema,
            assume_yes=assume_yes,
            max_rows=max_rows,
            result_offset=result_offset,
            allow_write=allow_write,
            timeout=timeout,
        )
```

with:

```python
    stripped_sql, set_hints = _split_or_emit(sql)
    _guard_sql_execution(stripped_sql, profile, allow_write=allow_write)

    client = None
    try:
        client, schema = _client_and_schema_for_sql(project, profile, schema, stripped_sql)
        envelope = client.execute_sql(
            stripped_sql,
            schema=schema,
            hints=set_hints or None,
            assume_yes=assume_yes,
            max_rows=max_rows,
            result_offset=result_offset,
            allow_write=allow_write,
            timeout=timeout,
        )
```

- [ ] **Step 5: Write the failing integration tests**

In `tests/unit/commands/test_sql_cmd.py`, add these methods to the `TestSqlExecuteWriteGuard` class (after `test_show_tables_default_succeeds`):

```python
    def test_set_then_select_runs_without_allow_write(self, isolated_config: Path) -> None:
        # SET key=val is extracted to a hint; the remaining SELECT is a read,
        # so --allow-write is NOT required.
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default",
             "SET odps.sql.mapper.split.size = 4096; SELECT 1"]
        )
        assert result.exit_code == 0, result.output
        assert mock_client.execute_sql.called
        call = mock_client.execute_sql.call_args
        assert call.args[0] == "SELECT 1"
        assert call.kwargs["hints"] == {"odps.sql.mapper.split.size": "4096"}

    def test_set_label_still_requires_allow_write(self, isolated_config: Path) -> None:
        # SET LABEL is not key=val -> not extracted -> stays -> rejected.
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default",
             "SET LABEL tbl TO user; SELECT 1"]
        )
        assert result.exit_code == 2
        assert not mock_client.execute_sql.called

    def test_standalone_set_is_rejected(self, isolated_config: Path) -> None:
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default",
             "SET odps.sql.mapper.split.size = 4096"]
        )
        assert result.exit_code == 2
        assert not mock_client.execute_sql.called
        assert "no query" in result.output
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/commands/test_sql_cmd.py::TestSqlExecuteWriteGuard -v`
Expected: PASS (including the 3 new tests; existing `test_select_default_succeeds`, `test_show_tables_default_succeeds` still pass).

- [ ] **Step 7: Commit**

```bash
git add src/maxcompute_semantic/commands/sql.py tests/unit/commands/test_sql_cmd.py
git commit -m "feat(sql): extract SET->hints in execute/submit verbs

execute_cmd and submit_cmd now call _split_or_emit before the write
guard, so SET key=val;SELECT runs as a read (no --allow-write) and
the stripped SQL + SET hints reach execute_sql/run_sql_async (which
already thread hints). Standalone SET (no query) is rejected. SET
LABEL stays gated (not extracted)."
```

---

## Task 3: Wire `cost_cmd` + `explain_cmd`

**Files:**
- Modify: `src/maxcompute_semantic/commands/sql.py` (`cost_cmd` body around line 696-702; `explain_cmd` body around line 737-742)
- Modify: `tests/unit/commands/test_sql_cmd.py` (add tests; reuse the `_run`-style harness or the `profile_command` test pattern)

- [ ] **Step 1: Wire `cost_cmd`**

In `cost_cmd` (around line 696-702), replace:

```python
    client = None
    try:
        target_project = _route_project(pctx.project_override, pctx.profile.name, sql)
        client = make_client_for_project(target_project, profile_name=pctx.profile.name)
        tier = get_tier(client.profile, client.profile.compute_project, client=client)
        schema = resolve_schema_for_tier(tier, pctx.schema_override, profile=client.profile)
        result = client.cost_estimate(sql, schema=schema)
    except McsError as e:
        _emit_mcs_error(sql, client.profile if client is not None else pctx.profile, e)
```

with:

```python
    stripped_sql, set_hints = _split_or_emit(sql)
    client = None
    try:
        target_project = _route_project(pctx.project_override, pctx.profile.name, stripped_sql)
        client = make_client_for_project(target_project, profile_name=pctx.profile.name)
        tier = get_tier(client.profile, client.profile.compute_project, client=client)
        schema = resolve_schema_for_tier(tier, pctx.schema_override, profile=client.profile)
        result = client.cost_estimate(stripped_sql, schema=schema, hints=set_hints or None)
    except McsError as e:
        _emit_mcs_error(stripped_sql, client.profile if client is not None else pctx.profile, e)
```

- [ ] **Step 2: Wire `explain_cmd`**

In `explain_cmd` (around line 737-742), replace:

```python
    client = None
    try:
        target_project = _route_project(project, profile, sql)
        client = make_client_for_project(target_project, profile_name=profile)
        tier = get_tier(client.profile, client.profile.compute_project, client=client)
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        result = client.explain(sql, timeout=timeout, schema=schema)
    except McsError as e:
        _emit_mcs_error(sql, client.profile if client is not None else None, e)
```

with:

```python
    stripped_sql, set_hints = _split_or_emit(sql)
    client = None
    try:
        target_project = _route_project(project, profile, stripped_sql)
        client = make_client_for_project(target_project, profile_name=profile)
        tier = get_tier(client.profile, client.profile.compute_project, client=client)
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        result = client.explain(stripped_sql, timeout=timeout, schema=schema, hints=set_hints or None)
    except McsError as e:
        _emit_mcs_error(stripped_sql, client.profile if client is not None else None, e)
```

- [ ] **Step 3: Write the failing test**

Add to `tests/unit/commands/test_sql_cmd.py`, alongside the existing `cost` tests (e.g. next to `test_3level_cost_applies_hints` around line 881). Mirror that test's harness exactly — it patches `make_client_for_project` + `get_tier` on `maxcompute_semantic.commands.sql` and invokes `cost` via `_invoke`:

```python
    def test_cost_strips_set_and_passes_hints(self, isolated_config: Path) -> None:
        """SET key=val is extracted to a hint; cost_estimate gets the stripped
        SELECT + the SET as hints (so execute_sql_cost never sees the SET)."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "my_schema",
                    "SET odps.sql.mapper.split.size = 4096; SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0, result.output
        call = mock_client.cost_estimate.call_args
        assert call.args[0] == "SELECT * FROM t"
        assert call.kwargs.get("hints") == {"odps.sql.mapper.split.size": "4096"}
```

`patch`, `MagicMock`, `_mock_profile`, `_mock_client`, `_invoke`, and the `isolated_config` fixture are all already used at the top of `test_sql_cmd.py` (see `test_3level_cost_applies_hints`), so no new imports are needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/commands/test_sql_cmd.py -k "cost_strips_set_and_passes_hints or 3level_cost" -v`
Expected: PASS — `cost_estimate` is called with `SELECT * FROM t` + the SET hint. Existing cost tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/maxcompute_semantic/commands/sql.py tests/unit/commands/test_sql_cmd.py
git commit -m "feat(sql): extract SET->hints in cost/explain verbs

cost_cmd and explain_cmd now strip SET key=val into hints and pass the
stripped SQL to cost_estimate / explain (both already accept hints=).
Cost estimation on SET;SELECT no longer sends the SET to execute_sql_cost."
```

---

## Task 4: Wire `review_cmd`

**Files:**
- Modify: `src/maxcompute_semantic/commands/sql.py` (`review_cmd` body around line 781-842)
- Modify: `tests/unit/commands/test_sql_cmd.py` (add a review test)

- [ ] **Step 1: Wire `review_cmd`**

In `review_cmd` (around line 781-842), the SQL is used in `_route_project`, `_classify_sql`, `_emit_mcs_error`, and `build_review_envelope`. Extract first and use `stripped_sql` throughout. Replace the start of the body:

```python
    profile_obj: Profile | None = None
    try:
        profile_obj = resolve_profile_for_project(project, profile_name=profile)
        # Sibling-parity routing: ...
        target_project = _route_project(project, profile, sql)
    except McsError as e:
        _emit_mcs_error(sql, profile_obj, e)
    assert profile_obj is not None
```

with:

```python
    stripped_sql, _set_hints = _split_or_emit(sql)
    profile_obj: Profile | None = None
    try:
        profile_obj = resolve_profile_for_project(project, profile_name=profile)
        # Sibling-parity routing: ...
        target_project = _route_project(project, profile, stripped_sql)
    except McsError as e:
        _emit_mcs_error(stripped_sql, profile_obj, e)
    assert profile_obj is not None
```

Then update the classify + refuse + envelope calls further down. Replace:

```python
    verdict = _classify_sql(sql)
    if verdict != "read":
        _emit_mcs_error(
            sql,
            profile_obj,
            ReviewUnsupportedError(
                f"mcs sql review is read-only; got SQL classified as {verdict!r}",
```

with:

```python
    verdict = _classify_sql(stripped_sql)
    if verdict != "read":
        _emit_mcs_error(
            stripped_sql,
            profile_obj,
            ReviewUnsupportedError(
                f"mcs sql review is read-only; got SQL classified as {verdict!r}",
```

And replace the `build_review_envelope(sql=sql, ...)` call:

```python
    data = build_review_envelope(
        sql=sql,
        profile=profile_obj,
        project=target_project,
        schema_name=schema,
        tier=tier,
    )
```

with:

```python
    data = build_review_envelope(
        sql=stripped_sql,
        profile=profile_obj,
        project=target_project,
        schema_name=schema,
        tier=tier,
    )
```

(`review` is read-only linting and does not execute, so the extracted `set_hints` are unused — discard via `_set_hints`.)

- [ ] **Step 2: Write the failing tests**

Add to `tests/unit/commands/sql_review/test_review_cmd.py`, alongside `test_review_refuses_write` (line 76). Mirror that test's harness — it patches `resolve_profile_for_project` + `get_tier` on `maxcompute_semantic.commands.sql`, invokes `review` via the local `_invoke`, and asserts on the JSON envelope:

```python
    def test_set_then_select_is_reviewable(self, isolated_config: Path, make_review_package) -> None:
        """SET key=val is extracted; the remaining SELECT is a read -> review runs."""
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
        )
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "review",
                    "--project",
                    "rev_proj",
                    "--schema",
                    "default",
                    "SET odps.sql.mapper.split.size = 4096; SELECT id FROM orders",
                ]
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["data"]["sql"] == "SELECT id FROM orders"
```

`json`, `Path`, `MagicMock`, `patch`, `_invoke`, and the `isolated_config` / `make_review_package` fixtures are all already used at the top of `test_review_cmd.py` (see `test_returns_success_envelope_with_review_data` and `test_review_refuses_write`), so no new imports are needed. The `out["data"]["sql"] == "SELECT id FROM orders"` assertion confirms the SET was stripped before review.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/commands/sql_review/test_review_cmd.py -v`
Expected: PASS — `SET;SELECT` is now reviewable; `test_review_refuses_write` (INSERT) still passes because INSERT is not a SET and is not extracted.

- [ ] **Step 4: Commit**

```bash
git add src/maxcompute_semantic/commands/sql.py tests/unit/commands/sql_review/test_review_cmd.py
git commit -m "feat(sql): extract SET->hints in review verb

review_cmd now reviews the SET-stripped SQL, so SET key=val;SELECT is
reviewable instead of refused as a write. set_hints are unused (review
is read-only linting). Non-extractable SETs (SET LABEL) still refused."
```

---

## Task 5: Annotate `test_set_is_write` + full suite

**Files:**
- Modify: `tests/unit/commands/test_sql_cmd.py` (annotate `test_set_is_write` around line 2631-2634)

- [ ] **Step 1: Annotate the classifier belt test**

The existing `test_set_is_write` asserts `_classify_sql("SET odps.sql.allow.fullscan=true") == "write"`. This stays **unchanged** and correct: `classify_sql` is NOT modified by this change (non-extractable SETs like `SET LABEL` rely on it staying `write`). Add a clarifying comment so a future contributor does not "clean it up":

Replace:

```python
    def test_set_is_write(self) -> None:
        """SET mutates session state — requires --allow-write."""
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SET odps.sql.allow.fullscan=true") == "write"
```

with:

```python
    def test_set_is_write(self) -> None:
        """SET mutates session state — requires --allow-write.

        This stays 'write' on purpose: classify_sql is NOT modified by
        the SET-extraction feature (see
        docs/superpowers/specs/2026-06-23-set-statement-extraction-design.md).
        Extraction happens in the verbs before classification, so
        extractable SETs never reach classify_sql; non-extractable SETs
        (SET LABEL, SETPROJECT) still do and must stay gated as write.
        Do not remove this assertion.
        """
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SET odps.sql.allow.fullscan=true") == "write"
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all existing tests green plus the new ones. If any test that previously asserted `SET;SELECT` was rejected as write now expects success, update it to the new behavior (the verb now extracts; `_classify_sql` itself is unchanged, so pure-classifier tests should not need changes).

- [ ] **Step 3: Run the linters/type-checks the project uses**

Run: `.venv/bin/python -m ruff check src/maxcompute_semantic/mc_client/sql_preprocess.py src/maxcompute_semantic/commands/sql.py tests/unit/mc_client/test_sql_preprocess.py tests/unit/commands/test_sql_cmd.py`
And (if configured): `.venv/bin/python -m mypy src/maxcompute_semantic/mc_client/sql_preprocess.py`
Expected: no errors. Fix any unused-import / naming findings inline.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/commands/test_sql_cmd.py
git commit -m "test(sql): annotate test_set_is_write as the classify-unchanged belt

classify_sql still classifies SET as 'write' by design (extraction
happens in the verbs, not the classifier); non-extractable SETs rely on
this. Comment added so the assertion is not removed."
```

---

## Self-Review (completed during planning)

**Spec coverage:** Every spec section maps to a task — extraction helper + mechanism (Task 1); `submit`/`execute` wiring + standalone-SET rejection + SET-LABEL gating (Task 2); `cost`/`explain` wiring (Task 3); `review` wiring (Task 4); `classify_sql` unchanged belt + full suite (Task 5). Hints merging/precedence is satisfied by reusing the client's existing `build_hints(user_hints=)` path (no new code). Cost-gate-no-longer-blocks is satisfied by the verb passing stripped SQL to `cost_estimate` (which calls `execute_sql_cost(stripped, hints)`).

**Placeholder scan:** Task 3 Step 3 and Task 4 Step 2 note that the exact test harness should mirror existing `cost`/`review` test patterns in the file (the implementer greps for them) — the assertion contracts are fully specified (stripped SQL + SET hints passed). No "TBD"/"implement later".

**Type consistency:** `split_set_hints` returns `tuple[str, dict[str, str]]` in Task 1 and is consumed as such in `_split_or_emit` (Task 2) and every verb. `_split_or_emit` returns `tuple[str, dict[str, str]]`. `set_hints or None` matches the client methods' `hints: dict[str, str] | None = None` signatures (verified for `execute_sql`, `run_sql_async`, `cost_estimate`, `explain`).

**Known follow-up (out of scope, per spec):** a `--set`/`--hint` CLI flag, and a denylist for security-only SETs — not implemented here.
