# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the MaxCompute SQLGlot dialect.

Covers: registration, function parse → exp type, round-trip generation,
DDL properties, cross-dialect transpilation, and regression against
existing sql_review / sql_pattern behaviour.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from maxcompute_semantic.dialect import parse_mc_one

# ── Registration ──────────────────────────────────────────────────────


class TestRegistration:
    def test_dialect_in_classes(self):
        from sqlglot.dialects.dialect import Dialect

        assert "maxcompute" in Dialect._classes

    def test_parse_with_read_param(self):
        stmts = sqlglot.parse("SELECT 1", read="maxcompute")
        assert len(stmts) == 1

    def test_transpile_write(self):
        result = sqlglot.transpile("SELECT 1", write="maxcompute")
        assert result == ["SELECT 1"]


# ── Function parsing ─────────────────────────────────────────────────

_FUNCTION_CASES = [
    ("SELECT DATEADD(dt, 1, 'dd')", exp.TsOrDsAdd),
    ("SELECT DATEDIFF(dt1, dt2, 'mm')", exp.DateDiff),
    ("SELECT DATETRUNC(dt, 'month')", exp.TimestampTrunc),
    ("SELECT TRUNC_TIME(dt, 'month')", exp.TimestampTrunc),
    ("SELECT DATEPART(dt, 'yyyy')", exp.Extract),
    ("SELECT GETDATE()", exp.CurrentTimestamp),
    ("SELECT NOW()", exp.CurrentTimestamp),
    ("SELECT TO_DATE('2024-01-01', 'yyyy-MM-dd')", exp.StrToTime),
    ("SELECT TO_DATE(col)", exp.TsOrDsToDate),
    ("SELECT DATE_FORMAT(dt, 'yyyy-MM-dd')", exp.TimeToStr),
    ("SELECT TO_CHAR(dt, 'yyyy-MM-dd')", (exp.TimeToStr, exp.ToChar)),
    ("SELECT FROM_UNIXTIME(ts)", exp.UnixToTime),
    ("SELECT TO_MILLIS(dt)", exp.UnixMillis),
    ("SELECT ADD_MONTHS(dt, 3)", exp.AddMonths),
    ("SELECT MONTHS_BETWEEN(dt1, dt2)", exp.MonthsBetween),
    ("SELECT DAY(dt)", exp.Day),
    ("SELECT MONTH(dt)", exp.Month),
    ("SELECT YEAR(dt)", exp.Year),
    ("SELECT HOUR(dt)", exp.Hour),
    ("SELECT MINUTE(dt)", exp.Minute),
    ("SELECT SECOND(dt)", exp.Second),
    ("SELECT QUARTER(dt)", exp.Quarter),
    ("SELECT DAYOFWEEK(dt)", exp.DayOfWeek),
    ("SELECT DAYOFYEAR(dt)", exp.DayOfYear),
    ("SELECT WEEKOFYEAR(dt)", exp.WeekOfYear),
    ("SELECT LAST_DAY(dt)", exp.LastDay),
    ("SELECT TOLOWER(name)", exp.Lower),
    ("SELECT TOUPPER(name)", exp.Upper),
    ("SELECT SPLIT_PART(s, ',', 1)", exp.SplitPart),
    ("SELECT SUBSTR(s, 1, 3)", exp.Substring),
    ("SELECT REGEXP_COUNT(s, 'a')", exp.RegexpCount),
    ("SELECT WM_CONCAT(',', col)", exp.GroupConcat),
    ("SELECT COUNT_IF(x > 0)", exp.CountIf),
    ("SELECT ARG_MAX(val, ts)", exp.ArgMax),
    ("SELECT ARG_MIN(val, ts)", exp.ArgMin),
    ("SELECT MAX_BY(val, ts)", exp.ArgMax),
    ("SELECT MIN_BY(val, ts)", exp.ArgMin),
    ("SELECT ANY_VALUE(col)", exp.AnyValue),
    ("SELECT APPROX_DISTINCT(col)", exp.ApproxDistinct),
    ("SELECT ALL_MATCH(arr, x -> x > 0)", exp.ArrayAll),
    ("SELECT ANY_MATCH(arr, x -> x > 0)", exp.ArrayAny),
    ("SELECT ARRAY_SORT(arr)", exp.ArraySort),
    ("SELECT ARRAY_DISTINCT(arr)", exp.ArrayDistinct),
    ("SELECT ARRAY_CONTAINS(arr, 1)", exp.ArrayContains),
    ("SELECT MAP_CONCAT(m1, m2)", exp.MapCat),
    ("SELECT MAP_FROM_ENTRIES(arr)", exp.MapFromEntries),
    ("SELECT FROM_JSON(s)", exp.ParseJSON),
    ("SELECT GET_USER_ID()", exp.CurrentUser),
    ("SELECT CURRENT_TIMEZONE()", exp.CurrentTimezone),
]


class TestFunctionParsing:
    @pytest.mark.parametrize("sql, expected_type", _FUNCTION_CASES, ids=[c[0] for c in _FUNCTION_CASES])
    def test_function_produces_correct_exp_type(self, sql, expected_type):
        tree = parse_mc_one(sql)
        if isinstance(expected_type, tuple):
            matches = any(list(tree.find_all(t)) for t in expected_type)
        else:
            matches = bool(list(tree.find_all(expected_type)))
        assert matches, f"Expected {expected_type} in AST for: {sql}"

    @pytest.mark.parametrize("sql, expected_type", _FUNCTION_CASES, ids=[c[0] for c in _FUNCTION_CASES])
    def test_function_not_anonymous(self, sql, expected_type):
        """MaxCompute functions should NOT parse as Anonymous nodes."""
        tree = parse_mc_one(sql)
        anon = [n for n in tree.find_all(exp.Anonymous)]
        assert not anon, f"Unexpected Anonymous node for: {sql} -> {[n.this for n in anon]}"


# ── Round-trip generation ─────────────────────────────────────────────

_ROUNDTRIP_CASES = [
    # Current date/time
    ("SELECT GETDATE()", "SELECT GETDATE()"),
    ("SELECT CURRENT_TIMEZONE()", "SELECT CURRENT_TIMEZONE()"),
    ("SELECT GET_USER_ID()", "SELECT GET_USER_ID()"),
    # String
    ("SELECT TOLOWER(name)", "SELECT TOLOWER(name)"),
    ("SELECT TOUPPER(name)", "SELECT TOUPPER(name)"),
    ("SELECT SPLIT_PART(s, ',', 1)", "SELECT SPLIT_PART(s, ',', 1)"),
    ("SELECT REGEXP_COUNT(s, 'a')", "SELECT REGEXP_COUNT(s, 'a')"),
    # Aggregate
    ("SELECT APPROX_DISTINCT(col)", "SELECT APPROX_DISTINCT(col)"),
    ("SELECT COUNT_IF(x > 0)", "SELECT COUNT_IF(x > 0)"),
    ("SELECT ANY_VALUE(col)", "SELECT ANY_VALUE(col)"),
    ("SELECT STDDEV_SAMP(col)", "SELECT STDDEV_SAMP(col)"),
    ("SELECT CORR(a, b)", "SELECT CORR(a, b)"),
    ("SELECT MEDIAN(col)", "SELECT MEDIAN(col)"),
    ("SELECT VAR_POP(col)", "SELECT VAR_POP(col)"),
    # Array
    ("SELECT ARRAY_DISTINCT(arr)", "SELECT ARRAY_DISTINCT(arr)"),
    ("SELECT ARRAY_SORT(arr)", "SELECT ARRAY_SORT(arr)"),
    ("SELECT ARRAY_CONTAINS(arr, 1)", "SELECT ARRAY_CONTAINS(arr, 1)"),
    ("SELECT ALL_MATCH(arr, x -> x > 0)", "SELECT ALL_MATCH(arr, x -> x > 0)"),
    ("SELECT ANY_MATCH(arr, x -> x > 0)", "SELECT ANY_MATCH(arr, x -> x > 0)"),
    # Map
    ("SELECT MAP_CONCAT(m1, m2)", "SELECT MAP_CONCAT(m1, m2)"),
    ("SELECT MAP_FROM_ENTRIES(arr)", "SELECT MAP_FROM_ENTRIES(arr)"),
    # JSON
    ("SELECT FROM_JSON(s)", "SELECT FROM_JSON(s)"),
    # Date
    ("SELECT ADD_MONTHS(dt, 3)", "SELECT ADD_MONTHS(dt, 3)"),
    ("SELECT LAST_DAY(dt)", "SELECT LAST_DAY(dt)"),
    ("SELECT MONTHS_BETWEEN(dt1, dt2)", "SELECT MONTHS_BETWEEN(dt1, dt2)"),
    ("SELECT TO_MILLIS(dt)", "SELECT TO_MILLIS(dt)"),
]


class TestRoundTrip:
    @pytest.mark.parametrize("input_sql, expected_sql", _ROUNDTRIP_CASES)
    def test_round_trip(self, input_sql, expected_sql):
        tree = parse_mc_one(input_sql)
        output = tree.sql(dialect="maxcompute")
        assert output == expected_sql


class TestGeneratorTransforms:
    @pytest.mark.parametrize(
        ("input_sql", "expected_sql"),
        [
            ("SELECT DATEADD(dt, 1, 'dd')", "SELECT DATEADD(dt, 1, 'DD')"),
            ("SELECT DATEDIFF(dt1, dt2, 'mm')", "SELECT DATEDIFF(dt1, dt2, 'MM')"),
            ("SELECT DATETRUNC(dt, 'month')", "SELECT DATETRUNC(dt, 'MONTH')"),
            ("SELECT DATEPART(dt, 'yyyy')", "SELECT DATEPART(dt, 'yyyy')"),
            ("SELECT WM_CONCAT(',', col)", "SELECT WM_CONCAT(',', col)"),
            ("SELECT SUBSTR(s, 1)", "SELECT SUBSTR(s, 1)"),
            ("SELECT INSTR(s, sub)", "SELECT INSTR(s, sub)"),
            ("SELECT INSTR(s, sub, 3)", "SELECT INSTR(s, sub, 3)"),
        ],
    )
    def test_custom_generator_transforms(self, input_sql: str, expected_sql: str) -> None:
        assert parse_mc_one(input_sql).sql(dialect="maxcompute") == expected_sql

    def test_lifecycle_kept_outside_tblproperties_when_other_properties_exist(self) -> None:
        output = parse_mc_one(
            "CREATE TABLE t (id BIGINT) LIFECYCLE 7 TBLPROPERTIES ('k'='v')"
        ).sql(dialect="maxcompute")

        assert "TBLPROPERTIES ('k'='v')" in output
        assert "LIFECYCLE 7" in output

    def test_partitioned_by_roundtrip_uses_partitioned_by_clause(self) -> None:
        output = parse_mc_one("CREATE TABLE t (id BIGINT) PARTITIONED BY (ds STRING)").sql(
            dialect="maxcompute"
        )

        assert "PARTITIONED BY (ds STRING)" in output


# ── DDL properties ────────────────────────────────────────────────────


class TestDDL:
    def test_lifecycle_property(self):
        tree = parse_mc_one("CREATE TABLE t (id BIGINT) LIFECYCLE 30")
        props = [
            p for p in tree.find_all(exp.Property)
            if isinstance(p.this, exp.Var) and p.this.name == "LIFECYCLE"
        ]
        assert len(props) == 1

    def test_lifecycle_roundtrip(self):
        sql = "CREATE TABLE t (id BIGINT) LIFECYCLE 30"
        tree = parse_mc_one(sql)
        output = tree.sql(dialect="maxcompute")
        assert "LIFECYCLE 30" in output
        assert "TBLPROPERTIES" not in output

    def test_range_clustered_by_roundtrip(self):
        sql = "CREATE TABLE t (id BIGINT) RANGE CLUSTERED BY (id) SORTED BY (id) INTO 10 BUCKETS"
        tree = parse_mc_one(sql)
        output = tree.sql(dialect="maxcompute")
        assert "RANGE CLUSTERED BY" in output

    def test_type_mapping_datetime(self):
        tree = parse_mc_one("CREATE TABLE t (dt DATETIME)")
        dtypes = list(tree.find_all(exp.DataType))
        assert any(d.this == exp.DType.DATETIME for d in dtypes)

    def test_type_mapping_timestamp_ntz(self):
        tree = parse_mc_one("CREATE TABLE t (ts TIMESTAMP_NTZ)")
        output = tree.sql(dialect="maxcompute")
        assert "TIMESTAMP_NTZ" in output

    def test_generated_column(self):
        tree = parse_mc_one("CREATE TABLE t (a INT, b AS a + 1)")
        assert tree is not None

    def test_varchar_to_string(self):
        tree = parse_mc_one("CREATE TABLE t (name VARCHAR(100))")
        output = tree.sql(dialect="maxcompute")
        assert "STRING" in output


# ── Query syntax (ANTLR-derived) ──────────────────────────────────────


class TestQuerySyntax:
    def test_qualify_clause(self):
        sql = "SELECT *, ROW_NUMBER() OVER(ORDER BY id) AS rn FROM t QUALIFY rn = 1"
        tree = parse_mc_one(sql)
        assert list(tree.find_all(exp.Qualify))

    def test_pivot(self):
        sql = "SELECT * FROM t PIVOT (SUM(amount) FOR month IN (1, 2, 3))"
        tree = parse_mc_one(sql)
        assert list(tree.find_all(exp.Pivot))

    def test_unpivot(self):
        sql = "SELECT * FROM t UNPIVOT (val FOR col IN (a, b, c))"
        tree = parse_mc_one(sql)
        pivots = list(tree.find_all(exp.Pivot))
        assert pivots and pivots[0].args.get("unpivot")

    def test_version_as_of(self):
        sql = "SELECT * FROM t VERSION AS OF '20240101'"
        tree = parse_mc_one(sql)
        assert list(tree.find_all(exp.Version))

    def test_timestamp_as_of(self):
        sql = "SELECT * FROM t TIMESTAMP AS OF '2024-01-01 00:00:00'"
        tree = parse_mc_one(sql)
        assert list(tree.find_all(exp.Version))

    def test_select_except(self):
        tree = parse_mc_one("SELECT * EXCEPT(id) FROM t")
        assert tree is not None

    def test_like_any(self):
        tree = parse_mc_one("SELECT * FROM t WHERE name LIKE ANY ('%test%', '%demo%')")
        assert tree is not None

    def test_lambda_expression(self):
        tree = parse_mc_one("SELECT TRANSFORM(arr, x -> x + 1)")
        assert list(tree.find_all(exp.Lambda))

    def test_left_semi_join(self):
        tree = parse_mc_one("SELECT * FROM t1 LEFT SEMI JOIN t2 ON t1.id = t2.id")
        output = tree.sql(dialect="maxcompute")
        assert "LEFT SEMI JOIN" in output

    def test_left_anti_join(self):
        tree = parse_mc_one("SELECT * FROM t1 LEFT ANTI JOIN t2 ON t1.id = t2.id")
        output = tree.sql(dialect="maxcompute")
        assert "LEFT ANTI JOIN" in output

    def test_lateral_view_explode(self):
        tree = parse_mc_one("SELECT t.id, v.col FROM t LATERAL VIEW EXPLODE(arr) v AS col")
        assert tree is not None

    def test_mapjoin_hint(self):
        tree = parse_mc_one("SELECT /*+ MAPJOIN(t2) */ * FROM t1 JOIN t2 ON t1.id = t2.id")
        assert list(tree.find_all(exp.Hint))

    def test_insert_overwrite_partition(self):
        tree = parse_mc_one("INSERT OVERWRITE TABLE t PARTITION (ds='20240101') SELECT * FROM s")
        assert isinstance(tree, exp.Insert)

    def test_merge_into(self):
        sql = """MERGE INTO target t USING source s ON t.id = s.id
            WHEN MATCHED THEN UPDATE SET t.val = s.val
            WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.val)"""
        tree = parse_mc_one(sql)
        assert isinstance(tree, exp.Merge)

    def test_concat_operator(self):
        tree = parse_mc_one("SELECT a || b FROM t")
        output = tree.sql(dialect="maxcompute")
        assert "||" in output

    def test_div_operator(self):
        tree = parse_mc_one("SELECT a DIV b FROM t")
        output = tree.sql(dialect="maxcompute")
        assert "DIV" in output


# ── Cross-dialect transpilation ───────────────────────────────────────


class TestCrossDialect:
    def test_spark_date_add_to_maxcompute(self):
        result = sqlglot.transpile(
            "SELECT DATE_ADD(dt, 1)", read="spark", write="maxcompute"
        )
        assert "DATEADD" in result[0]

    def test_maxcompute_getdate_to_spark(self):
        result = sqlglot.transpile(
            "SELECT GETDATE()", read="maxcompute", write="spark"
        )
        assert "CURRENT_TIMESTAMP" in result[0]

    def test_maxcompute_tolower_to_standard(self):
        result = sqlglot.transpile(
            "SELECT TOLOWER(name)", read="maxcompute", write="spark"
        )
        assert "LOWER" in result[0]


# ── Placeholder handling (sql_pattern.py regression) ──────────────────


class TestPlaceholders:
    def test_placeholder_parse_and_roundtrip(self):
        tree = parse_mc_one("SELECT ? FROM t WHERE id = ?")
        sql = tree.sql(dialect="maxcompute")
        assert "?" in sql

    def test_placeholder_in_canonical_sql(self):
        tree = parse_mc_one("SELECT :id FROM t WHERE name = :name")
        assert tree is not None


# ── Regression: sql_review dialect rules ──────────────────────────────


class TestSqlReviewRulesRegression:
    """Verify SQLite-function detection still works with MaxCompute dialect."""

    @staticmethod
    def _ctx(sql: str):
        from maxcompute_semantic.build.workload import extract_sql_evidence
        from maxcompute_semantic.commands.sql_review.types import ReviewContext

        return ReviewContext(
            sql=sql,
            evidence=extract_sql_evidence(sql),
            profile=None,
            project="p",
            schema_name=None,
            tier="2",
            db=None,
            classification="read",
        )

    def test_iif_still_detected(self):
        from maxcompute_semantic.commands.sql_review.rules.dialect import (
            check_sqlite_iif,
        )

        issues = check_sqlite_iif(self._ctx("SELECT IIF(x > 0, 'yes', 'no') FROM t"))
        assert len(issues) == 1

    def test_strftime_still_detected(self):
        from maxcompute_semantic.commands.sql_review.rules.dialect import (
            check_sqlite_strftime,
        )

        issues = check_sqlite_strftime(self._ctx("SELECT STRFTIME('%Y', dt) FROM t"))
        assert len(issues) == 1

    def test_julianday_still_detected(self):
        from maxcompute_semantic.commands.sql_review.rules.dialect import (
            check_sqlite_julianday,
        )

        issues = check_sqlite_julianday(self._ctx("SELECT JULIANDAY(dt) FROM t"))
        assert len(issues) == 1

    def test_substr_neg_still_detected(self):
        from maxcompute_semantic.commands.sql_review.rules.dialect import (
            check_sqlite_substr_neg,
        )

        issues = check_sqlite_substr_neg(self._ctx("SELECT SUBSTR(s, -3) FROM t"))
        assert len(issues) == 1
