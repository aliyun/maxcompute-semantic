"""Tests for build/profiling.py — column profile selection and SQL generation."""

from __future__ import annotations

from maxcompute_semantic.build.profiling import (
    DEFAULT_PROFILE_LIMIT,
    apply_profile_result,
    build_column_profile_sql,
    select_profile_columns,
)


class TestSelectProfileColumns:
    def test_prioritizes_workload_then_id_then_metric(self) -> None:
        columns = [
            {"name": "id", "type": "BIGINT", "is_partition": 0},
            {"name": "customer_id", "type": "BIGINT", "is_partition": 0},
            {"name": "status", "type": "STRING", "is_partition": 0},
            {"name": "description", "type": "STRING", "is_partition": 0},
            {"name": "ds", "type": "STRING", "is_partition": 1},
        ]
        workload_cols = {"status", "description"}

        selected = select_profile_columns(columns, workload_columns=workload_cols, limit=3)

        assert selected == ["status", "description", "id"]

    def test_excludes_partition_columns(self) -> None:
        columns = [
            {"name": "id", "type": "BIGINT", "is_partition": 0},
            {"name": "ds", "type": "STRING", "is_partition": 1},
        ]
        selected = select_profile_columns(columns, limit=10)
        assert "ds" not in selected

    def test_no_workload_still_selects_ids_and_metrics(self) -> None:
        columns = [
            {"name": "order_id", "type": "BIGINT", "is_partition": 0},
            {"name": "total_amount", "type": "DOUBLE", "is_partition": 0},
            {"name": "notes", "type": "STRING", "is_partition": 0},
        ]
        selected = select_profile_columns(columns, workload_columns=set(), limit=3)
        assert "order_id" in selected[:2]
        assert "total_amount" in selected[:3]


class TestBuildColumnProfileSql:
    def test_uses_approx_ndv_and_row_count(self) -> None:
        sql = build_column_profile_sql(
            fq_name="project.schema.orders",
            columns=["id", "status"],
            where_clause="WHERE ds = '20260521'",
        )

        assert "COUNT(1) AS row_count" in sql
        assert "APPROX_DISTINCT(`id`) AS id__approx_ndv" in sql
        assert "SUM(CASE WHEN `id` IS NULL THEN 1 ELSE 0 END) AS id__nulls" in sql
        assert "FROM project.schema.orders" in sql
        assert "WHERE ds = '20260521'" in sql

    def test_no_where_clause(self) -> None:
        sql = build_column_profile_sql(
            fq_name="project.schema.orders",
            columns=["id"],
            where_clause="",
        )
        assert "FROM project.schema.orders" in sql
        assert "WHERE" not in sql

    def test_uses_backticks_not_double_quotes(self) -> None:
        # Regression: MaxCompute treats "col" as a string literal, so
        # APPROX_DISTINCT("id") fails with "Illegal constant val type:
        # String: \"id\"". Identifiers must be backtick-quoted.
        sql = build_column_profile_sql(
            fq_name="project.schema.orders",
            columns=["id", "status"],
            where_clause="",
        )

        assert '"id"' not in sql
        assert '"status"' not in sql
        assert "`id`" in sql
        assert "`status`" in sql

    def test_escapes_embedded_backticks(self) -> None:
        # An exotic column name containing a backtick must be escaped by
        # doubling it (MaxCompute backtick-escape convention), not by
        # leaving the literal backtick that would close the identifier.
        sql = build_column_profile_sql(
            fq_name="project.schema.weird",
            columns=["a`b"],
            where_clause="",
        )

        assert "APPROX_DISTINCT(`a``b`)" in sql

    def test_emits_numeric_count_only_for_string_columns(self) -> None:
        # The dirty-numeric guard fires CAST(col AS DOUBLE) only on
        # STRING/VARCHAR/CHAR columns — numeric-typed columns can't be
        # "dirty" in the same way, and the extra aggregate would just
        # bloat the SQL text.
        sql = build_column_profile_sql(
            fq_name="project.schema.lab",
            columns=["amount", "crp", "patient_id"],
            where_clause="",
            column_types={
                "amount": "DOUBLE",
                "crp": "STRING",
                "patient_id": "BIGINT",
            },
        )

        assert "COUNT(CAST(`crp` AS DOUBLE)) AS crp__numeric_count" in sql
        assert "amount__numeric_count" not in sql
        assert "patient_id__numeric_count" not in sql

    def test_string_numeric_count_uses_backticks_and_escape(self) -> None:
        sql = build_column_profile_sql(
            fq_name="project.schema.weird",
            columns=["a`b"],
            where_clause="",
            column_types={"a`b": "STRING"},
        )
        assert "COUNT(CAST(`a``b` AS DOUBLE)) AS a``b__numeric_count" in sql

    def test_default_limit_is_generous(self) -> None:
        # The cap exists only because ODPS imposes a SQL text-size limit;
        # the aggregate scans the table once regardless of column count,
        # so the marginal cost per column is essentially free. Keep the
        # default wide enough to cover typical warehouse tables and
        # most wide analytic tables without truncation.
        assert DEFAULT_PROFILE_LIMIT >= 100


class TestApplyProfileResult:
    def test_computes_uniqueness_and_enum(self) -> None:
        columns = [
            {"name": "id", "type": "BIGINT", "is_partition": 0},
            {"name": "status", "type": "STRING", "is_partition": 0},
        ]
        row = {
            "row_count": 100,
            "id__approx_ndv": 100,
            "id__nulls": 0,
            "status__approx_ndv": 3,
            "status__nulls": 5,
        }

        result = apply_profile_result(
            columns,
            selected_columns=["id", "status"],
            row=row,
            scope="latest_partition",
            method="approx_ndv",
        )

        by_name = {c["name"]: c for c in result}
        assert by_name["id"]["row_count"] == 100
        assert by_name["id"]["approx_ndv"] == 100
        assert by_name["id"]["uniqueness_ratio"] == 1.0
        assert by_name["id"]["is_enum"] == 0
        assert by_name["status"]["null_ratio"] == 0.05
        assert by_name["status"]["is_enum"] == 1
        assert by_name["status"]["profile_confidence"] == 0.9

    def test_clamps_uniqueness_ratio_when_approx_distinct_overshoots(self) -> None:
        """APPROX_DISTINCT (HyperLogLog) can return NDV slightly above the
        true row count for columns with near-zero duplicates. Witnessed in
        a ``cards`` table where ``uuid`` and ``id`` both came back
        as ``uniqueness_ratio = 1.049`` and ``1.045``. The ratio is
        documented as a probability in the public schema; values above 1.0
        are mathematically invalid and violate the project's
        "no erroneous information in the semantic layer" contract.
        """
        columns = [{"name": "uuid", "type": "STRING", "is_partition": 0}]
        # 1001 NDV for 956 actual rows — a realistic HLL overshoot.
        row = {"row_count": 956, "uuid__approx_ndv": 1001, "uuid__nulls": 0}

        result = apply_profile_result(
            columns,
            selected_columns=["uuid"],
            row=row,
            scope="full_table",
            method="approx_ndv",
        )
        # Raw NDV preserved so the agent can still see the estimator's output.
        assert result[0]["approx_ndv"] == 1001
        # Ratio clamped to the legal upper bound.
        assert result[0]["uniqueness_ratio"] == 1.0

    def test_partial_scope_has_lower_confidence(self) -> None:
        columns = [{"name": "id", "type": "BIGINT", "is_partition": 0}]
        row = {"row_count": 50, "id__approx_ndv": 50, "id__nulls": 0}

        result = apply_profile_result(
            columns,
            selected_columns=["id"],
            row=row,
            scope="sample",
            method="approx_ndv",
        )

        assert result[0]["profile_confidence"] == 0.5

    def test_cast_rate_for_dirty_string_numeric(self) -> None:
        # Real-world ``laboratory.crp``-style column: ~26% of non-null values are
        # numeric strings ("0.5", "12.3"), the rest are codes ("negative",
        # "trace"). The dirty-numeric guard downstream needs the ratio.
        columns = [{"name": "crp", "type": "STRING", "is_partition": 0}]
        row = {
            "row_count": 100,
            "crp__approx_ndv": 40,
            "crp__nulls": 0,
            "crp__numeric_count": 26,
        }

        result = apply_profile_result(
            columns,
            selected_columns=["crp"],
            row=row,
            scope="full_table",
            method="approx_ndv",
        )
        assert result[0]["cast_rate"] == 0.26

    def test_cast_rate_clamped_when_numeric_count_overshoots(self) -> None:
        # COUNT(CAST(...)) cannot exceed non-null count in MaxCompute,
        # but defend at the source the same way we clamp uniqueness_ratio
        # so downstream consumers see a valid [0, 1] probability.
        columns = [{"name": "crp", "type": "STRING", "is_partition": 0}]
        row = {
            "row_count": 100,
            "crp__approx_ndv": 40,
            "crp__nulls": 10,
            "crp__numeric_count": 95,  # > 90 non-null
        }

        result = apply_profile_result(
            columns,
            selected_columns=["crp"],
            row=row,
            scope="full_table",
            method="approx_ndv",
        )
        assert result[0]["cast_rate"] == 1.0

    def test_cast_rate_none_when_column_all_null(self) -> None:
        # All-NULL STRING column: non_null denominator is 0; cast_rate is
        # meaningless. None signals "no information" to the suggester so
        # the dirty-numeric guard doesn't fire.
        columns = [{"name": "notes", "type": "STRING", "is_partition": 0}]
        row = {
            "row_count": 100,
            "notes__approx_ndv": 0,
            "notes__nulls": 100,
            "notes__numeric_count": 0,
        }

        result = apply_profile_result(
            columns,
            selected_columns=["notes"],
            row=row,
            scope="full_table",
            method="approx_ndv",
        )
        assert result[0]["cast_rate"] is None

    def test_cast_rate_absent_for_non_string_columns(self) -> None:
        # Numeric columns don't get a numeric_count aggregate, so the
        # cast_rate key is omitted entirely (None vs key-not-present is
        # the difference the storage layer uses to skip the column).
        columns = [{"name": "amount", "type": "DOUBLE", "is_partition": 0}]
        row = {"row_count": 100, "amount__approx_ndv": 80, "amount__nulls": 0}

        result = apply_profile_result(
            columns,
            selected_columns=["amount"],
            row=row,
            scope="full_table",
            method="approx_ndv",
        )
        assert "cast_rate" not in result[0]

    def test_cast_rate_clean_numeric_string_stays_at_one(self) -> None:
        # A STRING column that happens to be fully numeric (clean money
        # values stored as text) clears the 0.99 threshold the suggester
        # uses, so it stays metric-eligible.
        columns = [{"name": "price", "type": "STRING", "is_partition": 0}]
        row = {
            "row_count": 100,
            "price__approx_ndv": 50,
            "price__nulls": 0,
            "price__numeric_count": 100,
        }
        result = apply_profile_result(
            columns,
            selected_columns=["price"],
            row=row,
            scope="full_table",
            method="approx_ndv",
        )
        assert result[0]["cast_rate"] == 1.0
