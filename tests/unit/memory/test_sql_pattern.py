from __future__ import annotations

from maxcompute_semantic.memory.sql_pattern import (
    analyze_sql_pattern,
    redact_for_display,
    redact_join_keys,
    redact_projection_columns,
)


def test_literal_values_share_same_shape_key() -> None:
    first = analyze_sql_pattern("SELECT name FROM cards WHERE id = 10")
    second = analyze_sql_pattern("SELECT name FROM cards WHERE id = 20")

    assert first.shape_key == second.shape_key
    assert first.normalizer_version == 1
    assert first.canonical_sql == "SELECT name FROM cards WHERE id = ?"
    assert first.frequency_key == second.frequency_key
    assert first.where_predicates == ("id = ?",)
    assert first.tables == ("cards",)


def test_string_and_numeric_literals_are_placeholders() -> None:
    pattern = analyze_sql_pattern("SELECT id FROM orders WHERE status = 'PAID' AND amount >= 100")

    assert pattern.canonical_sql == ("SELECT id FROM orders WHERE amount >= ? AND status = ?")
    assert pattern.where_predicates == ("amount >= ?", "status = ?")


def test_and_predicate_order_does_not_change_shape_key() -> None:
    left = analyze_sql_pattern("SELECT id FROM orders WHERE status = 'PAID' AND amount >= 100")
    right = analyze_sql_pattern("SELECT id FROM orders WHERE amount >= 200 AND status = 'REFUNDED'")

    assert left.shape_key == right.shape_key
    assert left.canonical_sql == right.canonical_sql


def test_join_key_difference_is_a_different_shape() -> None:
    uuid_join = analyze_sql_pattern(
        "SELECT c.id FROM cards c JOIN legalities l ON c.uuid = l.uuid WHERE l.format = 'commander'"
    )
    id_join = analyze_sql_pattern(
        "SELECT c.id FROM cards c JOIN legalities l ON c.id = l.id WHERE l.format = 'commander'"
    )

    assert uuid_join.shape_key != id_join.shape_key
    assert uuid_join.join_edges == ("cards.uuid = legalities.uuid",)
    assert id_join.join_edges == ("cards.id = legalities.id",)


def test_join_edges_resolve_aliases_to_real_table_names() -> None:
    """Edge keys must use real table names so downstream lookups (the
    candidate ranker's ``tables`` map keyed by table name) actually find
    the entry. If we leave alias-shaped strings like ``u.id = o.user_id``
    in the edge key, the workload edge silently never enriches with
    profile stats — there's no entry for bare ``u`` in ``tables``.
    """
    pattern = analyze_sql_pattern("SELECT * FROM users u JOIN orders o ON u.id = o.user_id")
    assert pattern.join_edges == ("users.id = orders.user_id",)


def test_parse_failure_falls_back_to_comment_whitespace_literal_normalization() -> None:
    first = analyze_sql_pattern("SELECT id FROM t WHERE id = 10 /* unterminated")
    second = analyze_sql_pattern("SELECT id FROM t WHERE id = 20 /* unterminated")

    assert first.shape_key == second.shape_key
    assert first.parse_error is not None
    assert second.parse_error is not None


class TestRedactProjectionColumns:
    def test_bare_column_projection_is_redacted(self) -> None:
        # The bug-of-record: agent copied
        # ``SELECT name, cardkingdomid, cardkingdomfoilid FROM cards
        # WHERE NOT cardkingdomfoilid IS NULL AND NOT cardkingdomid IS NULL``
        # from mined_low history into a downstream case whose gold
        # wanted just ``SELECT id``. Redaction forces the agent to
        # commit to its own projection while preserving the filter shape.
        sql = (
            "SELECT name, cardkingdomid, cardkingdomfoilid FROM cards "
            "WHERE NOT cardkingdomfoilid IS NULL AND NOT cardkingdomid IS NULL"
        )
        out = redact_projection_columns(sql)
        assert "<col>" in out
        assert "name" not in out  # projection redacted
        assert "cardkingdomid" in out  # WHERE predicate preserved
        assert "FROM cards" in out

    def test_aggregate_function_call_preserved(self) -> None:
        out = redact_projection_columns("SELECT COUNT(*) AS cnt FROM users WHERE x > ?")
        assert "COUNT(*)" in out
        assert "<col>" not in out

    def test_aggregate_over_column_preserved_intact(self) -> None:
        # SUM(amount) tells the agent ``amount`` is a measure on this
        # table — that's reusable across questions. Only the bare
        # ``region`` projection (a dimension) is redacted.
        out = redact_projection_columns("SELECT SUM(amount), region FROM orders GROUP BY region")
        assert "SUM(amount)" in out
        assert out.count("<col>") == 1  # only `region` redacted
        assert "GROUP BY region" in out

    def test_star_expression_redacted(self) -> None:
        # ``SELECT *`` is a copy-paste hazard: the agent must commit to
        # a specific column list, not regurgitate a star-select.
        out = redact_projection_columns("SELECT * FROM cards")
        assert "<col>" in out
        assert "*" not in out

    def test_distinct_modifier_preserved(self) -> None:
        out = redact_projection_columns("SELECT DISTINCT name FROM cards")
        assert "DISTINCT" in out
        assert "<col>" in out

    def test_complex_expression_preserved(self) -> None:
        # ROUND / CASE / arithmetic carry shape signal — keep intact.
        out = redact_projection_columns(
            "SELECT ROUND(SUM(amt) * 100 / COUNT(*), 2) AS pct FROM t WHERE x = ?"
        )
        assert "ROUND" in out
        assert "SUM(amt)" in out
        assert "COUNT(*)" in out
        assert "<col>" not in out

    def test_unparseable_sql_returned_unchanged(self) -> None:
        # Redaction must never raise — opaque SQL passes through.
        garbage = "this isn't SQL /* unterminated"
        assert redact_projection_columns(garbage) == garbage

    def test_where_join_group_clauses_untouched(self) -> None:
        sql = (
            "SELECT c.id, c.artist FROM cards AS c "
            "JOIN legalities AS l ON c.id = l.id "
            "WHERE l.format = ? GROUP BY c.id"
        )
        out = redact_projection_columns(sql)
        assert "JOIN legalities AS l ON c.id = l.id" in out
        assert "WHERE l.format = ?" in out
        assert "GROUP BY c.id" in out


class TestRedactJoinKeys:
    def test_simple_eq_join_redacts_both_columns(self) -> None:
        # The bug-of-record: downstream cases had the agent
        # copy a mined ``ON c.id = l.id`` pattern verbatim when the
        # authoritative join_candidates (built from data profiling)
        # said the real FK was ``c.uuid = l.uuid``. Redaction preserves
        # the cards-legalities relationship signal while forcing the
        # agent to consult join_candidates for the columns.
        sql = (
            "SELECT c.id, c.artist FROM cards AS c "
            "JOIN legalities AS l ON c.id = l.id "
            "WHERE l.format = ?"
        )
        out = redact_join_keys(sql)
        assert "JOIN legalities AS l" in out
        assert "ON <col> = <col>" in out
        assert "ON c.id = l.id" not in out
        # WHERE / SELECT are untouched.
        assert "WHERE l.format = ?" in out
        assert "SELECT c.id, c.artist" in out

    def test_compound_and_on_predicate_each_column_redacted(self) -> None:
        # Multi-key joins: every column ref under ON becomes <col>.
        sql = "SELECT a.x FROM a JOIN b ON a.k1 = b.k1 AND a.k2 = b.k2"
        out = redact_join_keys(sql)
        assert "a.k1" not in out and "b.k1" not in out
        assert "a.k2" not in out and "b.k2" not in out
        # Each column ref in the ON clause became <col>.
        assert out.count("<col>") >= 4
        # The AND structure of the predicate is preserved.
        assert " AND " in out

    def test_join_type_preserved(self) -> None:
        for join_kw in ("LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL OUTER JOIN"):
            sql = f"SELECT a.x FROM a {join_kw} b ON a.id = b.aid"
            out = redact_join_keys(sql)
            assert join_kw in out, f"{join_kw} should survive redaction in: {out}"

    def test_cross_join_passes_through_unchanged(self) -> None:
        # No ON clause => nothing to redact.
        sql = "SELECT a.x, b.y FROM a CROSS JOIN b"
        out = redact_join_keys(sql)
        assert out.replace(" ", "").lower() == sql.replace(" ", "").lower()

    def test_using_clause_redacted_to_single_placeholder(self) -> None:
        sql = "SELECT a.x FROM a JOIN b USING (id, customer_id)"
        out = redact_join_keys(sql)
        assert "id" not in out.split("USING")[1].split(")")[0].replace("<col>", "")
        assert "<col>" in out
        assert "USING" in out

    def test_select_and_where_untouched(self) -> None:
        # The complement to redact_projection_columns: SELECT items and
        # WHERE predicates carry their own signal (or get redacted by
        # the projection function) — this function only touches JOIN ON.
        sql = (
            "SELECT c.name, c.id FROM cards AS c "
            "JOIN legalities AS l ON c.id = l.id "
            "WHERE c.power IS NULL AND l.format = ?"
        )
        out = redact_join_keys(sql)
        assert "SELECT c.name, c.id" in out
        assert "WHERE c.power IS NULL AND l.format = ?" in out

    def test_unparseable_sql_returned_unchanged(self) -> None:
        garbage = "this isn't SQL /* unterminated"
        assert redact_join_keys(garbage) == garbage

    def test_no_joins_passes_through(self) -> None:
        sql = "SELECT id FROM cards WHERE id = ?"
        out = redact_join_keys(sql)
        # No joins => no rewrite.
        assert "<col>" not in out
        assert "SELECT id FROM cards" in out


class TestRedactForDisplay:
    def test_both_projection_and_join_keys_redacted_in_single_pass(self) -> None:
        # Regression: chaining ``redact_join_keys(redact_projection_columns(sql))``
        # silently failed because the first call emitted ``<col>`` placeholders,
        # sqlglot then refused to re-parse the result (``<col>`` tokenizes as
        # an LT-col-GT triple → ParseError), and the second call's
        # ``except SqlglotError: return canonical_sql`` swallowed the error
        # and returned the input unchanged — leaving wrong JOIN keys
        # surfacing to the agent. Single-pass ``redact_for_display`` does
        # both transforms on one AST and serializes once.
        sql = (
            "SELECT c.id, c.artist FROM cards AS c "
            "JOIN legalities AS l ON c.id = l.id "
            "WHERE l.format = ?"
        )
        out = redact_for_display(sql)
        # Projection is redacted.
        assert "c.artist" not in out
        # JOIN keys are redacted — the bug-of-record.
        assert "ON c.id = l.id" not in out
        assert "ON <col> = <col>" in out
        # Relationship signal preserved.
        assert "JOIN legalities AS l" in out
        # WHERE predicate untouched.
        assert "WHERE l.format = ?" in out

    def test_aggregate_projection_preserved_join_keys_redacted(self) -> None:
        sql = (
            "SELECT COUNT(*), SUM(o.amount) FROM orders AS o "
            "JOIN customers AS c ON o.customer_id = c.id"
        )
        out = redact_for_display(sql)
        # Aggregates carry shape signal — preserved intact.
        assert "COUNT(*)" in out
        assert "SUM(o.amount)" in out
        # JOIN keys still redacted.
        assert "o.customer_id" not in out
        assert "c.id" not in out
        assert "<col>" in out

    def test_no_join_only_projection_redacted(self) -> None:
        sql = "SELECT name, id FROM cards WHERE id = ?"
        out = redact_for_display(sql)
        assert "<col>" in out
        assert "WHERE id = ?" in out

    def test_unparseable_sql_returned_unchanged(self) -> None:
        garbage = "this isn't SQL /* unterminated"
        assert redact_for_display(garbage) == garbage

    def test_chaining_separate_functions_reproduces_the_bug(self) -> None:
        # Documents the failure mode: don't chain the two by string.
        # The second call's parser bails on ``<col>`` and returns its
        # input unchanged, so the JOIN keys survive un-redacted.
        sql = "SELECT c.id, c.artist FROM cards AS c JOIN legalities AS l ON c.id = l.id"
        chained = redact_join_keys(redact_projection_columns(sql))
        # Projection got redacted by the first call.
        assert "<col>" in chained
        # But the JOIN keys survived because the second call's
        # parser bailed on the ``<col>`` placeholder.
        assert "ON c.id = l.id" in chained
