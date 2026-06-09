# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for build/pipeline.py -- BuildPipeline orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build._logic_version import INFERENCE_LOGIC_VERSION
from maxcompute_semantic.build.errors import BuildPhaseError
from maxcompute_semantic.build.phases import PhaseResult
from maxcompute_semantic.build.pipeline import BuildOptions, BuildPipeline
from maxcompute_semantic.build.storage import PackageDB

_SK = "test_project__default"
_SOURCE = DataSource(project="test_project", schema="default", tables="*")


def _make_profile(schema: str = "default") -> Profile:
    return Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps.endpoint",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="test_project", schema=schema, tables="*"),),
    )


def _make_db(tmp_path: Path) -> PackageDB:
    return PackageDB(tmp_path / "test.db")


def _make_pipeline(
    tmp_path: Path,
    opts: BuildOptions | None = None,
) -> BuildPipeline:
    client = MagicMock()
    db = _make_db(tmp_path)
    profile = _make_profile()
    pipeline = BuildPipeline(client, db, profile, opts or BuildOptions())
    return pipeline


def _success_result(**kwargs) -> PhaseResult:
    """Convenience: build a success PhaseResult."""
    return PhaseResult(status="success", data=kwargs)


def _partial_failure_result(table: str, code: str, msg: str) -> PhaseResult:
    """Convenience: build a partial_failure PhaseResult."""
    return PhaseResult(
        status="partial_failure",
        errors=[{"table": table, "code": code, "message": msg}],
    )


# -- Test 1: full build success -----------------------------------------------


class TestFullBuildSuccess:
    def test_all_phases_succeed(self, tmp_path: Path) -> None:
        """All phases succeed -> tables_built > 0, phases_skipped empty."""
        pipeline = _make_pipeline(tmp_path)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all") as mock_render,
        ):
            mock_list.return_value = _success_result(table_names=["t1", "t2"])
            mock_describe.return_value = _success_result(table_name="t1", column_count=2)
            mock_sampling.return_value = _success_result(table_name="t1", sampled_rows=5)
            mock_profiling.return_value = _success_result(
                table_name="t1",
                profiled_columns=2,
            )

            summary = pipeline.run()

        assert summary.tables_built == 2
        assert summary.tables_skipped == 0
        assert summary.phases_skipped == []
        assert summary.errors == []
        mock_render.assert_called_once()


# -- Test 2: partial failure on describe ---------------------------------------


class TestPartialFailureDescribe:
    def test_one_table_partial_failure_skipped(self, tmp_path: Path) -> None:
        """One table describe partial_failure -> that table skipped."""
        pipeline = _make_pipeline(tmp_path)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["good_table", "bad_table"])

            def describe_side_effect(client, db, profile, source, table_name):
                if table_name == "bad_table":
                    return _partial_failure_result("bad_table", "PermissionDeniedTable", "denied")
                return _success_result(table_name=table_name, column_count=3)

            mock_describe.side_effect = describe_side_effect
            mock_sampling.return_value = _success_result(table_name="good_table", sampled_rows=5)
            mock_profiling.return_value = _success_result(
                table_name="good_table",
                profiled_columns=2,
            )

            summary = pipeline.run()

        assert summary.tables_built == 1
        assert summary.tables_skipped == 1
        describe_errors = [e for e in summary.errors if e.get("phase") == "describe"]
        assert len(describe_errors) == 1
        assert describe_errors[0]["table"] == "bad_table"
        mock_sampling.assert_called_once()


# -- Test 3: no_history flag ---------------------------------------------------


class TestNoHistoryFlag:
    def test_history_skipped_when_flag_set(self, tmp_path: Path) -> None:
        """opts.no_history=True -> history phase skipped."""
        opts = BuildOptions(no_history=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table"),
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history") as mock_history,
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            summary = pipeline.run()

        assert "history" in summary.phases_skipped
        mock_history.assert_not_called()


# -- Test 4: no_sampling flag --------------------------------------------------


class TestNoSamplingFlag:
    def test_sampling_skipped_when_flag_set(self, tmp_path: Path) -> None:
        """opts.no_sampling=True -> no sampling calls."""
        opts = BuildOptions(no_sampling=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["t1", "t2"])
            mock_describe.return_value = _success_result(table_name="t1", column_count=2)

            summary = pipeline.run()

        mock_sampling.assert_not_called()
        assert summary.tables_built == 2


# -- Test 5: hard_error on list_tables -----------------------------------------


class TestHardErrorListTables:
    def test_list_tables_hard_error_raises(self, tmp_path: Path) -> None:
        """list_tables returns hard_error -> raises BuildPhaseError."""
        pipeline = _make_pipeline(tmp_path)

        with patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list:
            mock_list.return_value = PhaseResult(status="hard_error")

            try:
                pipeline.run()
            except BuildPhaseError as exc:
                assert "list_tables" in exc.message
            else:
                raise AssertionError("Expected BuildPhaseError to be raised")


# -- Test 6: build summary counts ----------------------------------------------


class TestBuildSummaryCounts:
    def test_mixed_build_summary(self, tmp_path: Path) -> None:
        """Verify tables_built, tables_skipped, phases_skipped after a mixed build."""
        opts = BuildOptions(no_history=True, no_udf=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["ok1", "ok2", "fail1", "fail2"])

            def describe_side_effect(client, db, profile, source, table_name):
                if table_name.startswith("fail"):
                    return _partial_failure_result(table_name, "PermissionDeniedTable", "denied")
                return _success_result(table_name=table_name, column_count=3)

            mock_describe.side_effect = describe_side_effect
            mock_sampling.return_value = _success_result(table_name="ok1", sampled_rows=5)
            mock_profiling.return_value = _success_result(
                table_name="ok1",
                profiled_columns=2,
            )

            summary = pipeline.run()

        assert summary.tables_built == 2
        assert summary.tables_skipped == 2
        assert "history" in summary.phases_skipped
        assert "udf" in summary.phases_skipped
        describe_errors = [e for e in summary.errors if e.get("phase") == "describe"]
        assert len(describe_errors) == 2
        assert mock_sampling.call_count == 2


# -- Refresh logic tests -------------------------------------------------------


class TestRefreshNewTablesAdded:
    def test_refresh_new_tables_added(self, tmp_path: Path) -> None:
        """Add a new table to live list -> full build, tables_new=1."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "old_table", schema_hash="abc123")
        db.mark_build_complete(_SK, ["old_table"])

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["old_table", "new_table"])

            def describe_side_effect(client, db_arg, profile, source, table_name):
                if table_name == "old_table":
                    # Hash matches -> unchanged.
                    return _success_result(
                        table_name="old_table",
                        column_count=2,
                        schema_hash="abc123",
                    )
                # new_table: fresh describe.
                return _success_result(
                    table_name="new_table",
                    column_count=3,
                    schema_hash="def456",
                )

            mock_describe.side_effect = describe_side_effect
            mock_sampling.return_value = _success_result(table_name="new_table", sampled_rows=5)
            mock_profiling.return_value = _success_result(profiled_columns=1)

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_new == 1
        assert summary.tables_changed == 0
        assert summary.tables_unchanged == 1
        assert summary.tables_removed == 0
        assert summary.tables_built == 1
        assert mock_sampling.call_count == 1
        assert mock_profiling.call_count == 1

    def test_refresh_new_table_history_can_feed_profiling(self, tmp_path: Path) -> None:
        """Refresh must describe new tables before history mining.

        ``phase_mine_history`` attributes SQL by reading the current DB
        table set. If the refresh path mines history before the new
        table has been described into the DB, profiling misses workload
        columns for exactly the tables that need fresh profiling.
        """
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "old_table", schema_hash="abc123")
        db.mark_build_complete(_SK, ["old_table"])

        opts = BuildOptions(refresh=True, profile_level="light")
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db
        seen_workload_columns: list[set[str]] = []

        def describe_side_effect(client, db_arg, profile, source, table_name):
            if table_name == "old_table":
                return _success_result(
                    table_name="old_table",
                    column_count=2,
                    schema_hash="abc123",
                )
            db_arg.upsert_table(_SK, "new_table", schema_hash="def456")
            return _success_result(
                table_name="new_table",
                column_count=3,
                schema_hash="def456",
            )

        def history_side_effect(client, db_arg, profile, source):
            if db_arg.get_table(_SK, "new_table") is None:
                return _success_result(sample_sql_candidates={})
            return _success_result(
                sample_sql_candidates={
                    "new_table": [
                        "SELECT * FROM new_table WHERE amount = 1",
                        "SELECT * FROM new_table WHERE amount = 2",
                    ]
                },
                history_skipped=False,
            )

        def profiling_side_effect(
            client,
            db_arg,
            profile,
            source,
            table_name,
            *,
            workload_columns,
        ):
            seen_workload_columns.append(set(workload_columns))
            return _success_result(table_name=table_name, profiled_columns=1)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch(
                "maxcompute_semantic.build.pipeline.phase_describe_table",
                side_effect=describe_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch(
                "maxcompute_semantic.build.pipeline.phase_column_profiling",
                side_effect=profiling_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch(
                "maxcompute_semantic.build.pipeline.phase_mine_history",
                side_effect=history_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["old_table", "new_table"])
            mock_sampling.return_value = _success_result(table_name="new_table", sampled_rows=5)
            mock_renderer_cls.return_value = MagicMock()

            summary = pipeline.run()

        assert summary.tables_new == 1
        assert seen_workload_columns == [{"amount"}]


class TestRefreshChangedTablesRebuilt:
    def test_refresh_changed_tables_rebuilt(self, tmp_path: Path) -> None:
        """Hash mismatch -> describe + sampling, tables_changed=1."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "changed_table", schema_hash="old_hash")

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["changed_table"])
            mock_describe.return_value = _success_result(
                table_name="changed_table",
                column_count=4,
                schema_hash="new_hash",
            )
            mock_sampling.return_value = _success_result(table_name="changed_table", sampled_rows=5)
            mock_profiling.return_value = _success_result(profiled_columns=1)

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_changed == 1
        assert summary.tables_new == 0
        assert summary.tables_unchanged == 0
        assert summary.tables_built == 1
        assert mock_sampling.call_count == 1
        assert mock_profiling.call_count == 1


class TestRefreshRemovedTablesDeleted:
    def test_refresh_removed_tables_deleted(self, tmp_path: Path) -> None:
        """Remove a table from live -> deleted from db + markdown removed."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "gone_table", schema_hash="xyz789")

        # Create a fake markdown file for the removed table — chain δ
        # layout puts per-table .md under ``<markdown_dir>/<source_key>/``.
        markdown_dir = tmp_path / "data" / "test"
        (markdown_dir / _SK).mkdir(parents=True, exist_ok=True)
        md_file = markdown_dir / _SK / "gone_table.md"
        md_file.write_text("# gone_table\n", encoding="utf-8")

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db
        pipeline._profile = _make_profile()

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table"),
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
            patch(
                "maxcompute_semantic.build.pipeline.profile_data_dir",
                return_value=tmp_path / "data" / "test",
            ),
        ):
            # Live list is empty -> all existing tables are "removed".
            mock_list.return_value = _success_result(table_names=[])

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_removed == 1
        assert summary.tables_new == 0
        assert summary.tables_changed == 0
        assert summary.tables_built == 0
        assert db.get_table(_SK, "gone_table") is None
        assert not md_file.exists()


class TestFullRemovesOrphanedTables:
    def test_full_removes_orphaned_tables(self, tmp_path: Path) -> None:
        """Full build (non-refresh) cleans up tables removed from the source."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "gone_table", schema_hash="xyz789")

        markdown_dir = tmp_path / "data" / "test"
        (markdown_dir / _SK).mkdir(parents=True, exist_ok=True)
        md_file = markdown_dir / _SK / "gone_table.md"
        md_file.write_text("# gone_table\n", encoding="utf-8")

        opts = BuildOptions(refresh=False)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db
        pipeline._profile = _make_profile()

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table"),
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history") as mock_hist,
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
            patch(
                "maxcompute_semantic.build.pipeline.profile_data_dir",
                return_value=tmp_path / "data" / "test",
            ),
        ):
            mock_list.return_value = _success_result(table_names=[])
            # ``_run_full`` reads ``hist_result.data.get("info_schema_source")`` /
            # ``history_skipped`` / ``sample_sql_candidates`` — a bare MagicMock
            # leaks truthy ``MagicMock`` instances into those values and
            # eventually into ``_state.json``. Return a real ``PhaseResult``
            # with serializable empties.
            mock_hist.return_value = _success_result(
                info_schema_source="tenant",
                history_skipped=False,
                sample_sql_candidates={},
                verified_queries={},
            )

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_removed == 1
        assert db.get_table(_SK, "gone_table") is None
        assert not md_file.exists()


class TestRefreshUnchangedTablesSkipped:
    def test_refresh_unchanged_tables_skipped(self, tmp_path: Path) -> None:
        """No schema changes -> no rebuild, tables_unchanged=N."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "stable1", schema_hash="hash_a")
        db.upsert_table(_SK, "stable2", schema_hash="hash_b")
        db.mark_build_complete(_SK, ["stable1", "stable2"])

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["stable1", "stable2"])

            def describe_side_effect(client, db_arg, profile, source, table_name):
                hash_map = {"stable1": "hash_a", "stable2": "hash_b"}
                return _success_result(
                    table_name=table_name,
                    column_count=3,
                    schema_hash=hash_map[table_name],
                )

            mock_describe.side_effect = describe_side_effect

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_unchanged == 2
        assert summary.tables_new == 0
        assert summary.tables_changed == 0
        assert summary.tables_built == 0
        mock_sampling.assert_not_called()
        mock_profiling.assert_not_called()


class TestRefreshResumesIncompleteBuild:
    """--refresh resumes tables left described-but-not-sampled by an
    interrupted prior build (build_complete=0), while still skipping
    tables that finished (build_complete=1)."""

    def _run_refresh(self, db, tmp_path, table_names):
        opts = BuildOptions(refresh=True, no_history=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=table_names)
            mock_describe.side_effect = lambda c, d, p, s, name: _success_result(
                table_name=name, column_count=2, schema_hash=f"h_{name}"
            )
            mock_sampling.return_value = _success_result(sampled_rows=5)
            mock_profiling.return_value = _success_result(profiled_columns=1)
            mock_renderer_cls.return_value = MagicMock()
            summary = pipeline.run()
        return summary, mock_sampling, mock_profiling

    def test_incomplete_unchanged_table_is_resumed(self, tmp_path: Path) -> None:
        """A table with an unchanged schema_hash but build_complete=0
        (interrupted prior build) is re-sampled, not skipped."""
        db = _make_db(tmp_path)
        # Simulate interruption: described (hash set) but never sampled.
        db.upsert_table(_SK, "half_built", schema_hash="h_half_built")
        # build_complete defaults to 0 — no mark_build_complete call.

        summary, mock_sampling, mock_profiling = self._run_refresh(db, tmp_path, ["half_built"])

        mock_sampling.assert_called_once()
        mock_profiling.assert_called_once()
        assert summary.tables_built == 1
        assert summary.tables_unchanged == 0
        # After resume it must be marked complete.
        assert db.get_table(_SK, "half_built")["build_complete"] == 1

    def test_complete_unchanged_table_is_skipped(self, tmp_path: Path) -> None:
        """A fully-built table (build_complete=1) with unchanged schema is
        skipped as before — resume must not re-sample finished tables."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "done", schema_hash="h_done")
        db.mark_build_complete(_SK, ["done"])

        summary, mock_sampling, mock_profiling = self._run_refresh(db, tmp_path, ["done"])

        mock_sampling.assert_not_called()
        mock_profiling.assert_not_called()
        assert summary.tables_unchanged == 1
        assert summary.tables_built == 0

    def test_mixed_resumes_only_incomplete(self, tmp_path: Path) -> None:
        """With one complete and one incomplete unchanged table, only the
        incomplete one is resumed."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "done", schema_hash="h_done")
        db.mark_build_complete(_SK, ["done"])
        db.upsert_table(_SK, "half_built", schema_hash="h_half_built")

        summary, mock_sampling, mock_profiling = self._run_refresh(
            db, tmp_path, ["done", "half_built"]
        )

        assert mock_sampling.call_count == 1
        assert mock_profiling.call_count == 1
        assert summary.tables_unchanged == 1
        assert summary.tables_built == 1
        assert db.get_table(_SK, "half_built")["build_complete"] == 1


class TestDataAwareRefresh:
    """--refresh re-samples a schema-unchanged table whose DATA changed
    since the last sample, throttled by refresh_min_age_hours."""

    def _run(
        self,
        db,
        tmp_path,
        live_dm: dict,
        min_age: float = 24.0,
        sampling_result: PhaseResult | None = None,
    ):
        opts = BuildOptions(refresh=True, no_history=True, refresh_min_age_hours=min_age)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=list(live_dm))
            mock_describe.side_effect = lambda c, d, p, s, name: _success_result(
                table_name=name,
                column_count=2,
                schema_hash=f"h_{name}",
                data_modified_at=live_dm[name],
            )
            mock_sampling.return_value = sampling_result or _success_result(sampled_rows=5)
            mock_profiling.return_value = _success_result(profiled_columns=1)
            mock_renderer_cls.return_value = MagicMock()
            summary = pipeline.run()
            self._last_profiling_mock = mock_profiling
        return summary, mock_sampling

    def _backdate_sample(self, db, name, iso):
        """Force last_sampled_at to an old value (record_sampled stamps now)."""
        db._conn.execute(
            "UPDATE tables SET last_sampled_at=? WHERE source_key=? AND name=?",
            (iso, _SK, name),
        )
        db._conn.commit()

    def test_data_unchanged_is_skipped(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        db.record_sampled(_SK, "t", "2026-05-01T00:00:00+00:00")

        # Live modification time identical to stored baseline → no change.
        summary, mock_sampling = self._run(
            db, tmp_path, {"t": "2026-05-01T00:00:00+00:00"}, min_age=0.0
        )

        mock_sampling.assert_not_called()
        assert summary.tables_unchanged == 1

    def test_data_changed_triggers_resample_no_throttle(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        db.record_sampled(_SK, "t", "2026-05-01T00:00:00+00:00")

        # Newer live data + throttle disabled (min_age=0) → re-sample.
        summary, mock_sampling = self._run(
            db, tmp_path, {"t": "2026-05-02T00:00:00+00:00"}, min_age=0.0
        )

        mock_sampling.assert_called_once()
        assert summary.tables_built == 1
        assert summary.tables_unchanged == 0
        # Baseline advanced to the new modification time.
        assert db.get_table(_SK, "t")["data_modified_at"] == "2026-05-02T00:00:00+00:00"

    def test_data_changed_refresh_profiles_before_advancing_baseline(
        self,
        tmp_path: Path,
    ) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        db.record_sampled(_SK, "t", "2026-05-01T00:00:00+00:00")

        self._run(db, tmp_path, {"t": "2026-05-02T00:00:00+00:00"}, min_age=0.0)

        self._last_profiling_mock.assert_called_once()
        assert db.get_table(_SK, "t")["data_modified_at"] == "2026-05-02T00:00:00+00:00"

    def test_data_changed_but_recent_sample_is_throttled(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        # record_sampled stamps last_sampled_at = now (very recent).
        db.record_sampled(_SK, "t", "2026-05-01T00:00:00+00:00")

        # Data changed, but last sample is "now" < 24h → throttled, skipped.
        summary, mock_sampling = self._run(
            db, tmp_path, {"t": "2026-05-02T00:00:00+00:00"}, min_age=24.0
        )

        mock_sampling.assert_not_called()
        assert summary.tables_unchanged == 1

    def test_data_changed_and_old_sample_resamples(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        db.record_sampled(_SK, "t", "2026-05-01T00:00:00+00:00")
        # Backdate the sample so the 24h throttle window has elapsed.
        self._backdate_sample(db, "t", "2026-01-01T00:00:00+00:00")

        summary, mock_sampling = self._run(
            db, tmp_path, {"t": "2026-05-02T00:00:00+00:00"}, min_age=24.0
        )

        mock_sampling.assert_called_once()
        assert summary.tables_built == 1

    def test_missing_migration_baseline_resamples_once(self, tmp_path: Path) -> None:
        """A pre-v12 row has no data_modified_at baseline; the first
        refresh after upgrade must re-sample once to establish a truthful
        baseline instead of skipping forever."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        db.mark_build_complete(_SK, ["t"])
        db._conn.execute(
            "UPDATE tables SET data_modified_at=NULL, last_sampled_at=NULL "
            "WHERE source_key=? AND name=?",
            (_SK, "t"),
        )
        db._conn.commit()

        summary, mock_sampling = self._run(
            db, tmp_path, {"t": "2026-05-02T00:00:00+00:00"}, min_age=24.0
        )

        mock_sampling.assert_called_once()
        assert summary.tables_built == 1
        row = db.get_table(_SK, "t")
        assert row["data_modified_at"] == "2026-05-02T00:00:00+00:00"
        assert row["last_sampled_at"] is not None

    def test_sampling_failure_does_not_advance_data_baseline(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t", schema_hash="h_t")
        db.record_sampled(_SK, "t", "2026-05-01T00:00:00+00:00")
        self._backdate_sample(db, "t", "2026-01-01T00:00:00+00:00")

        summary, mock_sampling = self._run(
            db,
            tmp_path,
            {"t": "2026-05-02T00:00:00+00:00"},
            min_age=0.0,
            sampling_result=PhaseResult(
                status="partial_failure",
                errors=[{"code": "ParseException", "message": "sampling failed"}],
            ),
        )

        mock_sampling.assert_called_once()
        assert summary.tables_built == 1
        row = db.get_table(_SK, "t")
        assert row["build_complete"] == 0
        assert row["data_modified_at"] == "2026-05-01T00:00:00+00:00"


class TestFullBuildMarksTablesComplete:
    """_run_full must set build_complete=1 per table after sampling so a
    later --refresh can tell finished tables from interrupted ones."""

    def test_full_build_marks_each_table_complete(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        opts = BuildOptions(no_history=True, parallel=1)
        pipeline = BuildPipeline(MagicMock(), db, _make_profile(), opts)

        # describe is mocked, so simulate its side effect (writing the
        # table row) — otherwise there's no row for mark_build_complete
        # / get_table to touch.
        def describe(c, d, p, s, name):
            d.upsert_table(_SK, name, schema_hash=f"h_{name}")
            return _success_result(table_name=name, column_count=1)

        with patch.multiple(
            "maxcompute_semantic.build.pipeline",
            phase_list_tables=MagicMock(return_value=_success_result(table_names=["t1", "t2"])),
            phase_describe_table=MagicMock(side_effect=describe),
            phase_column_sampling=MagicMock(return_value=_success_result(sampled_rows=5)),
            phase_column_profiling=MagicMock(return_value=_success_result(profiled_columns=1)),
            phase_discover_udfs=MagicMock(),
            phase_mine_history=MagicMock(),
            phase_infer_joins_heuristic=MagicMock(),
            render_all=MagicMock(),
        ):
            pipeline.run()

        assert db.get_table(_SK, "t1")["build_complete"] == 1
        assert db.get_table(_SK, "t2")["build_complete"] == 1


class TestFullBuildResumesInterrupted:
    """Plain `mcs build` (no --refresh) skips already-built, unchanged
    tables so an interrupted build resumes; --fresh forces a full
    re-sample of everything."""

    def _run(self, db, tmp_path, fresh: bool):
        opts = BuildOptions(no_history=True, parallel=1, fresh=fresh)
        pipeline = BuildPipeline(MagicMock(), db, _make_profile(), opts)

        # describe is mocked — return a stable per-table hash so a
        # pre-seeded matching hash classifies as unchanged.
        def describe(c, d, p, s, name):
            return _success_result(table_name=name, schema_hash=f"h_{name}")

        mock_sampling = MagicMock(return_value=_success_result(sampled_rows=5))
        with patch.multiple(
            "maxcompute_semantic.build.pipeline",
            phase_list_tables=MagicMock(return_value=_success_result(table_names=["t1", "t2"])),
            phase_describe_table=MagicMock(side_effect=describe),
            phase_column_sampling=mock_sampling,
            phase_column_profiling=MagicMock(return_value=_success_result(profiled_columns=1)),
            phase_discover_udfs=MagicMock(),
            phase_mine_history=MagicMock(),
            phase_infer_joins_heuristic=MagicMock(),
            render_all=MagicMock(),
        ):
            summary = pipeline.run()
        return summary, mock_sampling

    def test_resume_skips_complete_unchanged_table(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        # t1: fully built last run (complete). t2: described but never
        # sampled (interrupted) — build_complete defaults to 0.
        db.upsert_table(_SK, "t1", schema_hash="h_t1")
        db.mark_build_complete(_SK, ["t1"])
        db.upsert_table(_SK, "t2", schema_hash="h_t2")

        summary, mock_sampling = self._run(db, tmp_path, fresh=False)

        # Only t2 (incomplete) is re-sampled; t1 is resumed-over.
        sampled = {c.args[4] for c in mock_sampling.call_args_list}
        assert sampled == {"t2"}
        assert summary.tables_resumed == 1
        # Both end up complete.
        assert db.get_table(_SK, "t1")["build_complete"] == 1
        assert db.get_table(_SK, "t2")["build_complete"] == 1

    def test_fresh_resamples_everything(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t1", schema_hash="h_t1")
        db.mark_build_complete(_SK, ["t1"])
        db.upsert_table(_SK, "t2", schema_hash="h_t2")

        summary, mock_sampling = self._run(db, tmp_path, fresh=True)

        sampled = {c.args[4] for c in mock_sampling.call_args_list}
        assert sampled == {"t1", "t2"}
        assert summary.tables_resumed == 0

    def test_missing_migration_baseline_resamples_complete_table(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "t1", schema_hash="h_t1")
        db.mark_build_complete(_SK, ["t1"])
        db._conn.execute(
            "UPDATE tables SET data_modified_at=NULL, last_sampled_at=NULL "
            "WHERE source_key=? AND name=?",
            (_SK, "t1"),
        )
        db._conn.commit()

        opts = BuildOptions(no_history=True, parallel=1)
        pipeline = BuildPipeline(MagicMock(), db, _make_profile(), opts)

        def describe(c, d, p, s, name):
            return _success_result(
                table_name=name,
                schema_hash=f"h_{name}",
                data_modified_at="2026-05-02T00:00:00+00:00",
            )

        mock_sampling = MagicMock(return_value=_success_result(sampled_rows=5))
        with patch.multiple(
            "maxcompute_semantic.build.pipeline",
            phase_list_tables=MagicMock(return_value=_success_result(table_names=["t1"])),
            phase_describe_table=MagicMock(side_effect=describe),
            phase_column_sampling=mock_sampling,
            phase_column_profiling=MagicMock(return_value=_success_result(profiled_columns=1)),
            phase_discover_udfs=MagicMock(),
            phase_mine_history=MagicMock(),
            phase_infer_joins_heuristic=MagicMock(),
            render_all=MagicMock(),
        ):
            pipeline.run()

        mock_sampling.assert_called_once()
        assert db.get_table(_SK, "t1")["data_modified_at"] == "2026-05-02T00:00:00+00:00"


class TestRefreshPreservesJoins:
    def test_refresh_preserves_joins(self, tmp_path: Path) -> None:
        """Changed table has joins re-validated during inference phase."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "tbl_a", schema_hash="old_a")
        db.upsert_table(_SK, "tbl_b", schema_hash="hash_b")
        db.mark_build_complete(_SK, ["tbl_a", "tbl_b"])

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic") as mock_joins,
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["tbl_a", "tbl_b"])

            def describe_side_effect(client, db_arg, profile, source, table_name):
                hash_map = {"tbl_a": "new_a", "tbl_b": "hash_b"}
                return _success_result(
                    table_name=table_name,
                    column_count=3,
                    schema_hash=hash_map[table_name],
                )

            mock_describe.side_effect = describe_side_effect
            mock_sampling.return_value = _success_result(table_name="tbl_a", sampled_rows=5)

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_changed == 1  # tbl_a changed
        assert summary.tables_unchanged == 1  # tbl_b unchanged
        mock_joins.assert_called_once_with(
            db, pipeline._profile, suppressed_source_pairs=frozenset()
        )


class TestRefreshUpdateStateJson:
    def test_refresh_update_state_json(self, tmp_path: Path) -> None:
        """Summary has all refresh counters; MarkdownRenderer called per changed/new."""
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "old_tbl", schema_hash="old_hash")
        # Pre-stamp current inference-logic version so refresh stays on
        # the selective per-table render path under test here (rather
        # than falling into the post-CLI-upgrade ``render_all`` branch).
        db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, opts=opts)
        pipeline._db = db

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
        ):
            mock_list.return_value = _success_result(table_names=["old_tbl", "new_tbl"])

            def describe_side_effect(client, db_arg, profile, source, table_name):
                if table_name == "old_tbl":
                    # Hash mismatch -> changed.
                    return _success_result(
                        table_name="old_tbl",
                        column_count=2,
                        schema_hash="new_hash",
                    )
                # new_tbl: fresh.
                return _success_result(
                    table_name="new_tbl",
                    column_count=3,
                    schema_hash="fresh_hash",
                )

            mock_describe.side_effect = describe_side_effect
            mock_sampling.return_value = _success_result(sampled_rows=5)

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            summary = pipeline.run()

        assert summary.tables_new == 1
        assert summary.tables_changed == 1
        assert summary.tables_removed == 0
        assert summary.tables_unchanged == 0
        assert summary.tables_built == 2
        assert mock_renderer.render_table.call_count == 2


class TestMultiSource:
    """Pipeline outer loop iterates every ``DataSource`` in the
    profile, attributing tables and verified queries to the source
    they came from. The profile-global phases (UDFs, joins,
    markdown render) run exactly once at the top regardless of how
    many sources the profile has.
    """

    def _multi_profile(self) -> Profile:
        return Profile(
            name="multi-test",
            compute_project="acme",
            endpoint="https://odps.endpoint",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            sources=(
                DataSource(project="acme", schema="warehouse", tables="*"),
                DataSource(project="acme", schema="staging", tables="*"),
            ),
        )

    def test_full_build_iterates_all_sources(self, tmp_path: Path) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = self._multi_profile()
        pipeline = BuildPipeline(client, db, profile, BuildOptions(no_history=True))

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs") as mock_udfs,
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic") as mock_joins,
            patch("maxcompute_semantic.build.pipeline.render_all") as mock_render,
        ):
            # Each source returns its own table list.
            mock_list.side_effect = [
                _success_result(table_names=["orders", "users"]),
                _success_result(table_names=["raw_events"]),
            ]
            mock_describe.return_value = _success_result(column_count=3)
            mock_sampling.return_value = _success_result(sampled_rows=5)

            pipeline.run()

        # phase_list_tables called once per source.
        assert mock_list.call_count == 2
        list_call_sources = [c.args[3] for c in mock_list.call_args_list]
        assert list_call_sources[0].schema == "warehouse"
        assert list_call_sources[1].schema == "staging"

        # phase_describe_table called once per (source, table).
        # warehouse: orders + users; staging: raw_events.
        assert mock_describe.call_count == 3

        # phase_discover_udfs / phase_infer_joins_heuristic / render_all
        # are profile-global — exactly once.
        assert mock_udfs.call_count == 1
        assert mock_joins.call_count == 1
        assert mock_render.call_count == 1

    def test_empty_sources_raises_with_remediation(self, tmp_path: Path) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = Profile(
            name="empty-source",
            compute_project="acme",
            endpoint="https://odps.endpoint",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            sources=(),
        )
        pipeline = BuildPipeline(client, db, profile, BuildOptions())

        import pytest

        with pytest.raises(BuildPhaseError, match="no data sources to build"):
            pipeline.run()

    def test_refresh_filters_existing_names_per_source(self, tmp_path: Path) -> None:
        """Same-named tables in different sources don't get classified
        as ``removed`` from each other — a ``users`` row that exists
        only in ``acme.warehouse`` doesn't disappear when the refresh
        of ``acme.staging`` doesn't see a ``users`` table.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        # Pre-seed two same-named rows under different sources.
        db.upsert_table("acme__warehouse", "users", "h_warehouse")
        db.upsert_table("acme__staging", "users", "h_staging")
        db.upsert_table("acme__warehouse", "orders", "h_orders")

        profile = self._multi_profile()
        pipeline = BuildPipeline(client, db, profile, BuildOptions(refresh=True, no_history=True))

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
        ):
            # warehouse refresh sees both pre-existing tables, staging
            # refresh sees only its own ``users``.
            mock_list.side_effect = [
                _success_result(table_names=["users", "orders"]),
                _success_result(table_names=["users"]),
            ]
            mock_describe.return_value = _success_result(column_count=2, schema_hash="unchanged")

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer
            pipeline.run()

        # Both pre-existing rows survive — the per-source ``existing_names``
        # filter prevented cross-source removal.
        assert db.get_table("acme__warehouse", "users") is not None
        assert db.get_table("acme__staging", "users") is not None
        assert db.get_table("acme__warehouse", "orders") is not None

    def test_refresh_rerenders_tables_touched_by_sample_sql(self, tmp_path: Path) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        db.upsert_table("acme__warehouse", "orders", "h_orders")
        db.mark_build_complete("acme__warehouse", ["orders"])
        db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)

        profile = self._multi_profile()
        pipeline = BuildPipeline(client, db, profile, BuildOptions(refresh=True))

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history") as mock_history,
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
        ):
            mock_list.side_effect = [
                _success_result(table_names=["orders"]),
                _success_result(table_names=[]),
            ]
            mock_describe.return_value = _success_result(
                table_name="orders",
                column_count=2,
                schema_hash="h_orders",
            )
            mock_history.side_effect = [
                _success_result(
                    verified_queries={"orders": ["SELECT * FROM orders"]},
                    info_schema_source="tenant",
                ),
                _success_result(
                    verified_queries={},
                    info_schema_source="tenant",
                ),
            ]
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            pipeline.run()

        mock_renderer.render_overview.assert_called_once()
        mock_renderer.render_joins.assert_called_once()
        mock_renderer.render_udfs.assert_called_once()
        mock_renderer.render_table.assert_any_call("acme__warehouse", "orders")

    def test_refresh_rerenders_sample_sql_touched_tables_per_source(
        self,
        tmp_path: Path,
    ) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        db.upsert_table("acme__warehouse", "orders", "h_orders")
        db.upsert_table("acme__staging", "raw_events", "h_raw_events")
        db.mark_build_complete("acme__warehouse", ["orders"])
        db.mark_build_complete("acme__staging", ["raw_events"])
        db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)

        profile = self._multi_profile()
        pipeline = BuildPipeline(client, db, profile, BuildOptions(refresh=True))

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_mine_history") as mock_history,
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
        ):
            mock_list.side_effect = [
                _success_result(table_names=["orders"]),
                _success_result(table_names=["raw_events"]),
            ]
            mock_describe.side_effect = [
                _success_result(table_name="orders", column_count=2, schema_hash="h_orders"),
                _success_result(
                    table_name="raw_events",
                    column_count=2,
                    schema_hash="h_raw_events",
                ),
            ]
            mock_history.side_effect = [
                _success_result(
                    verified_queries={"orders": ["SELECT * FROM orders"]},
                    info_schema_source="tenant",
                ),
                _success_result(
                    verified_queries={"raw_events": ["SELECT * FROM raw_events"]},
                    info_schema_source="tenant",
                ),
            ]
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            pipeline.run()

        render_calls = [call.args for call in mock_renderer.render_table.call_args_list]
        assert ("acme__warehouse", "orders") in render_calls
        assert ("acme__staging", "raw_events") in render_calls
        assert ("acme__staging", "orders") not in render_calls
        assert ("acme__warehouse", "raw_events") not in render_calls

    def test_refresh_rerenders_global_files_even_when_no_tables_changed(
        self, tmp_path: Path
    ) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        db.upsert_table("acme__warehouse", "orders", "h_orders")
        db.mark_build_complete("acme__warehouse", ["orders"])
        db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)

        profile = self._multi_profile()
        pipeline = BuildPipeline(client, db, profile, BuildOptions(refresh=True, no_history=True))

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
        ):
            mock_list.side_effect = [
                _success_result(table_names=["orders"]),
                _success_result(table_names=[]),
            ]
            mock_describe.return_value = _success_result(
                table_name="orders",
                column_count=2,
                schema_hash="h_orders",
            )
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            pipeline.run()

        mock_renderer.render_overview.assert_called_once()
        mock_renderer.render_joins.assert_called_once()
        mock_renderer.render_udfs.assert_called_once()
        mock_renderer.render_table.assert_not_called()


class TestDeepValidation:
    """When profile_level="deep", the pipeline runs cost-gated value-overlap
    validation on top join candidates after ranking."""

    def test_deep_triggers_overlap_validation(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("MCS_TIER_OVERRIDE", "3")
        db = _make_db(tmp_path)
        sk = _SK
        tid = db.upsert_table(sk, "orders", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                {"name": "customer_id", "type": "BIGINT", "comment": "", "is_partition": 0},
            ],
        )
        tid2 = db.upsert_table(sk, "customers", "h2")
        db.upsert_columns(
            tid2,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_join(
            sk,
            "orders",
            "customer_id",
            sk,
            "customers",
            "id",
            "xxx_id",
            0.8,
        )
        profile = _make_profile()
        client = MagicMock()
        client.get_project_tier.return_value = "3"
        client.cost_estimate_fq.return_value = {
            "estimated_input_bytes": 100,
            "estimated_cost_cny": 0.01,
            "verdict": "ok",
        }
        client.execute_fq_sql.return_value = MagicMock(
            data={"rows": [{"left_non_null": "100", "matched_rows": "98"}]},
        )

        pipeline = BuildPipeline(
            client,
            db,
            profile,
            BuildOptions(
                profile_level="deep",
                no_history=True,
                no_sampling=True,
                no_udf=True,
            ),
        )

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all") as mock_render,
        ):
            mock_list.return_value = _success_result(table_names=["orders", "customers"])
            mock_describe.return_value = _success_result(table_name="orders", column_count=2)
            summary = pipeline.run()

        assert summary.tables_built == 2
        client.cost_estimate_fq.assert_called()
        client.execute_fq_sql.assert_called()
        # Cost-estimate and execute MUST receive the same ``projects=``
        # arg so the namespace.schema hint they auto-inject is identical.
        # Earlier versions only passed hints into execute, leaving
        # cost_estimate to reject 3-part FQNs and silently bump every
        # candidate to skipped_err — collapsing deep validation to a
        # no-op for 3-level cross-source pairs.
        cost_projects = client.cost_estimate_fq.call_args.kwargs.get("projects")
        exec_projects = client.execute_fq_sql.call_args.kwargs.get("projects")
        assert cost_projects == exec_projects
        assert cost_projects  # non-empty: at least one source project
        # Join candidate should be promoted to confirmed (coverage 98/100 = 0.98 >= 0.95).
        jc_list = db.list_join_candidates(
            left_source_key=sk,
            left_table="orders",
        )
        assert len(jc_list) >= 1
        assert any(j["status"] == "confirmed" for j in jc_list), (
            f"expected confirmed candidate, got: {jc_list}"
        )
        mock_render.assert_called_once()

    def test_deep_respects_cost_budget(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("MCS_TIER_OVERRIDE", "3")
        db = _make_db(tmp_path)
        sk = _SK
        tid = db.upsert_table(sk, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        tid2 = db.upsert_table(sk, "customers", "h2")
        db.upsert_columns(
            tid2,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_join(
            sk,
            "orders",
            "id",
            sk,
            "customers",
            "id",
            "same_name",
            0.5,
        )
        profile = _make_profile()
        client = MagicMock()
        client.get_project_tier.return_value = "3"
        # Cost exceeds the tiny budget.
        client.cost_estimate_fq.return_value = {
            "estimated_input_bytes": 100000000,
            "estimated_cost_cny": 5.0,  # > profile_budget_cny=0.10
            "verdict": "blocked",
        }
        client.execute_fq_sql.return_value = MagicMock(
            data={"rows": [{"left_non_null": "100", "matched_rows": "50"}]},
        )

        pipeline = BuildPipeline(
            client,
            db,
            profile,
            BuildOptions(
                profile_level="deep",
                profile_budget_cny=0.10,
                no_history=True,
                no_sampling=True,
                no_udf=True,
            ),
        )

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["orders", "customers"])
            mock_describe.return_value = _success_result(table_name="orders", column_count=2)
            pipeline.run()

        # execute_fq_sql should NOT be called because cost is blocked.
        client.execute_fq_sql.assert_not_called()


# -- Regression: sampling/profiling partial_failure must surface --------------


class TestPartialFailureSurfacing:
    """Sampling/profiling phases returning ``partial_failure`` used to be
    silently swallowed by the orchestrator (return value discarded). The
    smoke-CI build then reported ``errors: []`` even when every table's
    sampling SQL failed with "full qualified name ... is not supported".

    The orchestrator must capture sampling/profiling PhaseResult and
    append partial_failure warnings/errors into BuildSummary so the
    failure is visible to ``mcs status`` and CI artifact inspection.
    """

    def test_sampling_partial_failure_lands_in_summary(self, tmp_path: Path) -> None:
        pipeline = _make_pipeline(tmp_path)
        pipeline._db.upsert_table(_SK, "t1", schema_hash="h_t1")

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["t1"])
            mock_describe.return_value = _success_result(table_name="t1", column_count=2)
            mock_sampling.return_value = PhaseResult(
                status="partial_failure",
                warnings=["Sampling failed for t1: full qualified name not supported"],
                errors=[{"code": "ParseException", "message": "fq name not supported"}],
            )
            mock_profiling.return_value = _success_result(
                table_name="t1",
                profiled_columns=0,
            )

            summary = pipeline.run()

        # tables_built still counts (describe succeeded), but the
        # sampling failure must be visible in the summary.
        assert summary.tables_built == 1
        sampling_errors = [e for e in summary.errors if e.get("phase") == "sampling"]
        assert len(sampling_errors) == 1
        assert sampling_errors[0]["table"] == "t1"
        assert any("sampling/t1" in w for w in summary.warnings)
        row = pipeline._db.get_table(_SK, "t1")
        assert row["build_complete"] == 0
        assert row["last_sampled_at"] is None

    def test_profiling_partial_failure_lands_in_summary(self, tmp_path: Path) -> None:
        pipeline = _make_pipeline(tmp_path)
        pipeline._db.upsert_table(_SK, "t1", schema_hash="h_t1")

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["t1"])
            mock_describe.return_value = _success_result(table_name="t1", column_count=2)
            mock_sampling.return_value = _success_result(table_name="t1", sampled_rows=3)
            mock_profiling.return_value = PhaseResult(
                status="partial_failure",
                errors=[{"code": "ParseException", "message": "fq name not supported"}],
                data={"table_name": "t1", "profiled_columns": 0},
            )

            summary = pipeline.run()

        assert summary.tables_built == 1
        # The profiling error must be in summary.errors.
        profiling_errors = [e for e in summary.errors if e.get("phase") == "profiling"]
        assert len(profiling_errors) == 1
        assert profiling_errors[0]["table"] == "t1"
        row = pipeline._db.get_table(_SK, "t1")
        assert row["build_complete"] == 0
        assert row["last_sampled_at"] is None


# -- Multi-source ordering and aggregation regression tests -------------------


def _multisource_profile() -> Profile:
    return Profile(
        name="multi",
        compute_project="proj_a",
        endpoint="https://odps.endpoint",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(
            DataSource(project="proj_a", schema="default", tables="*"),
            DataSource(project="proj_b", schema="default", tables="*"),
        ),
    )


class TestProfilingSeesSameSourceWorkload:
    """H2: profiling must run AFTER mine_history within the same source
    so the workload-derived column hints come from THIS source, not
    from a prior source's mined queries."""

    def test_profile_receives_current_source_workload_columns(
        self,
        tmp_path: Path,
    ) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _multisource_profile()
        opts = BuildOptions(profile_level="light", no_history=False, no_sampling=False)
        pipeline = BuildPipeline(client, db, profile, opts)

        # Each source has a single table; mine_history returns SQLs whose
        # WHERE columns are unique to that source. Two identical-shape
        # SQLs per source so the ``min_shape_frequency=2`` filter on
        # workload aggregation lets the column through — without it,
        # both singleton mined SQLs would be dropped before they could
        # contribute to ``workload_columns``.
        per_source_sqls = {
            "proj_a__default": [
                "SELECT * FROM t_a WHERE col_a = 1",
                "SELECT * FROM t_a WHERE col_a = 2",
            ],
            "proj_b__default": [
                "SELECT * FROM t_b WHERE col_b = 3",
                "SELECT * FROM t_b WHERE col_b = 4",
            ],
        }
        seen: list[tuple[str, set[str]]] = []

        def mine_side_effect(client_, db_, profile_, source):
            sk = source.source_key()
            return _success_result(
                sample_sql_candidates={"t": per_source_sqls[sk]},
                history_skipped=False,
            )

        def profile_side_effect(
            client_,
            db_,
            profile_,
            source,
            table_name,
            *,
            workload_columns,
        ):
            seen.append((source.source_key(), set(workload_columns)))
            return _success_result(table_name=table_name, profiled_columns=0)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch(
                "maxcompute_semantic.build.pipeline.phase_column_profiling",
                side_effect=profile_side_effect,
            ),
            patch(
                "maxcompute_semantic.build.pipeline.phase_mine_history",
                side_effect=mine_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.side_effect = lambda *a, **k: _success_result(
                table_names=["t_a" if a[3].project == "proj_a" else "t_b"],
            )
            mock_describe.return_value = _success_result(table_name="t", column_count=1)
            mock_sampling.return_value = _success_result(table_name="t", sampled_rows=0)

            pipeline.run()

        # Profile was invoked once per source; each call's workload set
        # carries THIS source's column, not the union or the other source's.
        by_source = dict(seen)
        assert "col_a" in by_source["proj_a__default"]
        assert "col_b" not in by_source["proj_a__default"], (
            "source A's profile saw source B's columns — workload leaked across sources"
        )
        assert "col_b" in by_source["proj_b__default"]
        assert "col_a" not in by_source["proj_b__default"], (
            "source B's profile saw source A's columns — workload leaked across sources"
        )

    def test_refresh_profile_receives_current_source_workload_columns(
        self,
        tmp_path: Path,
    ) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _multisource_profile()
        opts = BuildOptions(refresh=True, profile_level="light")
        pipeline = BuildPipeline(client, db, profile, opts)

        per_source_sqls = {
            "proj_a__default": [
                "SELECT * FROM t_a WHERE col_a = 1",
                "SELECT * FROM t_a WHERE col_a = 2",
            ],
            "proj_b__default": [
                "SELECT * FROM t_b WHERE col_b = 3",
                "SELECT * FROM t_b WHERE col_b = 4",
            ],
        }
        seen: list[tuple[str, set[str]]] = []

        def mine_side_effect(client_, db_, profile_, source):
            sk = source.source_key()
            table = "t_a" if sk == "proj_a__default" else "t_b"
            return _success_result(
                sample_sql_candidates={table: per_source_sqls[sk]},
                history_skipped=False,
            )

        def profile_side_effect(
            client_,
            db_,
            profile_,
            source,
            table_name,
            *,
            workload_columns,
        ):
            seen.append((source.source_key(), set(workload_columns)))
            return _success_result(table_name=table_name, profiled_columns=0)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch(
                "maxcompute_semantic.build.pipeline.phase_column_profiling",
                side_effect=profile_side_effect,
            ),
            patch(
                "maxcompute_semantic.build.pipeline.phase_mine_history",
                side_effect=mine_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.MarkdownRenderer") as mock_renderer_cls,
        ):
            mock_list.side_effect = lambda *a, **k: _success_result(
                table_names=["t_a" if a[3].project == "proj_a" else "t_b"],
            )
            mock_describe.side_effect = lambda *a, **k: _success_result(
                table_name=a[4],
                column_count=1,
                schema_hash=f"h_{a[4]}",
            )
            mock_sampling.return_value = _success_result(sampled_rows=0)
            mock_renderer_cls.return_value = MagicMock()

            pipeline.run()

        by_source = dict(seen)
        assert "col_a" in by_source["proj_a__default"]
        assert "col_b" not in by_source["proj_a__default"]
        assert "col_b" in by_source["proj_b__default"]
        assert "col_a" not in by_source["proj_b__default"]


class TestRankerReceivesMergedWorkload:
    """H3: rank_join_candidates and suggest_column_semantics must see
    workload data merged across all sources, not just sources[0]."""

    def test_rank_workload_sums_counts_across_sources(self, tmp_path: Path) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _multisource_profile()
        opts = BuildOptions(profile_level="light")
        pipeline = BuildPipeline(client, db, profile, opts)

        # Each source contributes a different join_count key to the
        # workload. Two literal-only-different SQLs per shape so they
        # survive the ``min_shape_frequency=2`` filter on workload
        # aggregation (analyze_sql_pattern replaces literals with
        # placeholders so ``WHERE x=1`` and ``WHERE x=2`` share a
        # shape_key). Table names in each SQL must match
        # ``per_source_tables`` below so the ``allowed_tables``
        # cross-source filter in ``aggregate_workload_evidence`` keeps
        # them in the merged workload (post a55 history-mining
        # attribution fix — cross-source refs are dropped on
        # purpose; legitimate same-source edges still merge).
        per_source_sqls = {
            "proj_a__default": [
                "SELECT * FROM t_a a JOIN t_b b ON a.id = b.id WHERE a.x = 1",
                "SELECT * FROM t_a a JOIN t_b b ON a.id = b.id WHERE a.x = 2",
            ],
            "proj_b__default": [
                "SELECT * FROM t_c c JOIN t_d d ON c.id = d.id WHERE c.x = 1",
                "SELECT * FROM t_c c JOIN t_d d ON c.id = d.id WHERE c.x = 2",
            ],
        }
        per_source_tables = {
            "proj_a__default": ["t_a", "t_b"],
            "proj_b__default": ["t_c", "t_d"],
        }

        def mine_side_effect(client_, db_, profile_, source):
            sk = source.source_key()
            return _success_result(
                sample_sql_candidates={"t_a": per_source_sqls[sk]}
                if sk == "proj_a__default"
                else {"t_c": per_source_sqls[sk]},
                history_skipped=False,
            )

        def list_side_effect(client_, db_, profile_, source, *a, **k):
            sk = source.source_key()
            return _success_result(table_names=per_source_tables[sk])

        captured: dict[str, dict] = {}

        def fake_rank(*, tables, workload_summary, name_edges, limit_per_table):
            captured["workload"] = workload_summary
            return []

        with (
            patch(
                "maxcompute_semantic.build.pipeline.phase_list_tables",
                side_effect=list_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_prof,
            patch(
                "maxcompute_semantic.build.pipeline.phase_mine_history",
                side_effect=mine_side_effect,
            ),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch(
                "maxcompute_semantic.build.join_candidates.rank_join_candidates",
                side_effect=fake_rank,
            ),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_describe.side_effect = lambda *a, **k: _success_result(
                table_name=a[3] if len(a) > 3 else k.get("table", "t"), column_count=1
            )
            mock_sampling.side_effect = lambda *a, **k: _success_result(
                table_name=a[3] if len(a) > 3 else k.get("table", "t"), sampled_rows=0
            )
            mock_prof.side_effect = lambda *a, **k: _success_result(
                table_name=a[3] if len(a) > 3 else k.get("table", "t"), profiled_columns=0
            )

            pipeline.run()

        ws = captured["workload"]
        keys = set(ws["join_counts"].keys())
        # Post-a55 attribution: sqlglot resolves the FROM/JOIN aliases
        # (``a``/``b`` / ``c``/``d``) back to real table names
        # (``t_a``/``t_b`` / ``t_c``/``t_d``), and the allowed_tables
        # cross-source filter keeps these in the merged workload
        # because each edge's tables are a subset of the source's own
        # table set. A ``summaries[0]``-style merge would drop one
        # side — assert BOTH sources' edges are present.
        a_edge = next((k for k in keys if "t_a.id" in k and "t_b.id" in k), None)
        b_edge = next((k for k in keys if "t_c.id" in k and "t_d.id" in k), None)
        assert a_edge is not None, f"source A's edge missing from merged join_counts: {keys}"
        assert b_edge is not None, f"source B's edge missing from merged join_counts: {keys}"




class TestDetectAndWarnCrossEnvDuplicates:
    """Tests the BuildPipeline._detect_and_warn_cross_env_duplicates
    helper that wraps the cross_env detector for the build pipeline:
    it reads the current DB state, emits per-pair warnings into the
    summary, and returns the suppression set to pass into
    ``phase_infer_joins_heuristic``."""

    def test_single_source_returns_empty_set_and_no_warning(self, tmp_path: Path) -> None:
        pipeline = _make_pipeline(tmp_path)
        for name in ("users", "orders", "products"):
            pipeline._db.upsert_table("acme__prod", name, f"h_{name}")

        suppressed = pipeline._detect_and_warn_cross_env_duplicates()

        assert suppressed == frozenset()
        assert pipeline._summary.warnings == []

    def test_duplicate_sources_flagged_warns_and_returns_pair(self, tmp_path: Path) -> None:
        pipeline = _make_pipeline(tmp_path)
        for sk in ("acme__prod", "acme__staging"):
            for name in ("users", "orders", "products", "events"):
                pipeline._db.upsert_table(sk, name, f"h_{sk}_{name}")

        suppressed = pipeline._detect_and_warn_cross_env_duplicates()

        assert suppressed == frozenset({frozenset({"acme__prod", "acme__staging"})})
        assert len(pipeline._summary.warnings) == 1
        warning = pipeline._summary.warnings[0]
        assert "cross_env/acme__prod+acme__staging" in warning
        assert "share 4 of 4 tables" in warning
        assert "100%" in warning

    def test_three_sources_two_overlap_only_overlapping_pair_suppressed(
        self, tmp_path: Path
    ) -> None:
        pipeline = _make_pipeline(tmp_path)
        for sk in ("acme__prod", "acme__staging"):
            for name in ("users", "orders", "products", "events"):
                pipeline._db.upsert_table(sk, name, f"h_{sk}_{name}")
        for name in ("invoices", "refunds", "payments"):
            pipeline._db.upsert_table("billing__main", name, f"h_billing_{name}")

        suppressed = pipeline._detect_and_warn_cross_env_duplicates()

        assert suppressed == frozenset({frozenset({"acme__prod", "acme__staging"})})
        assert len(pipeline._summary.warnings) == 1


# -- View / object-table profiling skip gate ----------------------------------


class TestSkipViewProfiling:
    """Pipeline skips sampling/profiling for VIRTUAL_VIEW and OBJECT_TABLE
    objects by default; --include-views opts back in."""

    def _setup_two_object_profile(
        self,
        tmp_path: Path,
        monkeypatch,
        types: dict[str, str],
        *,
        omit_type_key: bool = False,
    ):
        """Return (client, db, profile, source) where the mocked client
        returns objects with the given types. ``types`` maps table_name
        → pyodps type string. When ``omit_type_key`` is set, the describe
        envelope drops the ``type`` key entirely (simulating a legacy
        pre-v9 describe payload)."""
        from maxcompute_semantic.mc_client.envelope import Envelope

        # Redirect MCS_DATA_DIR so render_all + tier_cache writes stay
        # under tmp_path instead of ~/.local/share.
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path / "mcs-data"))

        client = MagicMock()
        client.list_tables.return_value = list(types.keys())

        def _describe(name, *, schema=None, project=None):
            payload = {
                "table": {
                    "name": name,
                    "schema": [{"name": "id", "type": "BIGINT", "comment": ""}],
                    "partition_columns": [],
                },
            }
            if not omit_type_key:
                payload["table"]["type"] = types[name]
            return payload

        client.describe_table.side_effect = _describe
        # execute_sql is what sampling + profiling call. Return a
        # one-row envelope so the phases finish quickly.
        client.execute_sql.return_value = Envelope(
            status="success",
            data={"rows": [{"id": 1, "row_count": 1}]},
        )
        client.list_partitions.return_value = {"latest_partition": None}
        # Force tier=2 so qualified_for_connection returns bare table name
        # (deterministic across machines; otherwise tier=get_tier probes
        # via MagicMock.list_schemas which returns an empty iterable → "2"
        # but is fragile to refactors).
        monkeypatch.setenv("MCS_TIER_OVERRIDE", "2")

        db = PackageDB(tmp_path / "package.db")
        profile = _make_profile()
        source = profile.sources[0]
        return client, db, profile, source

    def test_default_skips_virtual_view_sampling_and_profiling(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"phys_tbl": "MANAGED_TABLE", "the_view": "VIRTUAL_VIEW"},
        )
        opts = BuildOptions(no_history=True, no_joins=True, no_udf=True)
        BuildPipeline(client, db, profile, opts).run()

        # execute_sql was called for sampling+profiling on phys_tbl
        # (2 calls) but never for the_view.
        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert any("phys_tbl" in s for s in sql_calls)
        assert not any("the_view" in s for s in sql_calls), (
            f"VIRTUAL_VIEW must not be sampled/profiled; got: {sql_calls}"
        )

    def test_include_views_runs_sampling_and_profiling_on_views(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"the_view": "VIRTUAL_VIEW"},
        )
        opts = BuildOptions(
            no_history=True,
            no_joins=True,
            no_udf=True,
            include_views=True,
        )
        BuildPipeline(client, db, profile, opts).run()

        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert any("the_view" in s for s in sql_calls), (
            f"--include-views must enable sampling/profiling on views; got: {sql_calls}"
        )

    def test_object_table_is_skipped(self, tmp_path: Path, monkeypatch) -> None:
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"oss_files": "OBJECT_TABLE"},
        )
        opts = BuildOptions(no_history=True, no_joins=True, no_udf=True)
        BuildPipeline(client, db, profile, opts).run()

        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert not any("oss_files" in s for s in sql_calls)

    def test_materialized_view_is_profiled(self, tmp_path: Path, monkeypatch) -> None:
        """Per Alibaba docs, materialized views ARE physical tables with
        stored data. They must be sampled/profiled like managed tables."""
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"mv_orders": "MATERIALIZED_VIEW"},
        )
        opts = BuildOptions(no_history=True, no_joins=True, no_udf=True)
        BuildPipeline(client, db, profile, opts).run()

        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert any("mv_orders" in s for s in sql_calls)

    def test_null_table_type_is_treated_as_table(self, tmp_path: Path, monkeypatch) -> None:
        """Legacy profiles built before v9 have NULL table_type. Pipeline
        must treat NULL conservatively as 'table' so upgrade does not
        silently change what gets profiled."""
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"legacy_tbl": "MANAGED_TABLE"},
            omit_type_key=True,
        )
        opts = BuildOptions(no_history=True, no_joins=True, no_udf=True)
        BuildPipeline(client, db, profile, opts).run()

        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert any("legacy_tbl" in s for s in sql_calls), (
            "NULL table_type must be treated as table (sampled+profiled)"
        )

    def test_refresh_default_skips_new_virtual_view_sampling_and_profiling(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"the_view": "VIRTUAL_VIEW"},
        )
        opts = BuildOptions(refresh=True, no_history=True, no_joins=True, no_udf=True)
        BuildPipeline(client, db, profile, opts).run()

        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert not any("the_view" in s for s in sql_calls), (
            f"refresh must not sample/profile VIRTUAL_VIEW by default; got: {sql_calls}"
        )
        assert db.get_table(_SK, "the_view")["build_complete"] == 1

    def test_refresh_include_views_profiles_new_virtual_view(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        client, db, profile, _ = self._setup_two_object_profile(
            tmp_path,
            monkeypatch,
            {"the_view": "VIRTUAL_VIEW"},
        )
        opts = BuildOptions(
            refresh=True,
            no_history=True,
            no_joins=True,
            no_udf=True,
            include_views=True,
        )
        BuildPipeline(client, db, profile, opts).run()

        sql_calls = [c.args[0] for c in client.execute_sql.call_args_list]
        assert any("the_view" in s for s in sql_calls), (
            f"refresh --include-views must sample/profile views; got: {sql_calls}"
        )


def test_build_options_parallel_default_is_auto() -> None:
    """BuildOptions.parallel default is None (auto) — the value plumbed by
    mcs build when --parallel is omitted."""
    assert BuildOptions().parallel is None


class TestParallelPriming:
    def test_prime_client_called_before_phases(self, tmp_path: Path) -> None:
        """_prime_client_for_parallel must be invoked before any
        sampling/profiling so workers never race on lazy ODPS/tier init."""
        pipeline = _make_pipeline(tmp_path)
        pipeline._prime_client_for_parallel = MagicMock(wraps=pipeline._prime_client_for_parallel)

        with (
            patch("maxcompute_semantic.build.pipeline.phase_list_tables") as mock_list,
            patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
            patch("maxcompute_semantic.build.pipeline.phase_describe_table") as mock_describe,
            patch("maxcompute_semantic.build.pipeline.phase_column_sampling") as mock_sampling,
            patch("maxcompute_semantic.build.pipeline.phase_column_profiling") as mock_profiling,
            patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
            patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
            patch("maxcompute_semantic.build.pipeline.render_all"),
        ):
            mock_list.return_value = _success_result(table_names=["t1"])
            mock_describe.return_value = _success_result(table_name="t1", column_count=1)
            mock_sampling.return_value = _success_result(table_name="t1", sampled_rows=5)
            mock_profiling.return_value = _success_result(
                table_name="t1",
                profiled_columns=1,
            )

            pipeline.run()

        pipeline._prime_client_for_parallel.assert_called_once()
