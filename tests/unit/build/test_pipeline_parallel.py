# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for BuildPipeline parallel sampling/profiling fan-out
(BuildOptions.parallel)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.phases import PhaseResult
from maxcompute_semantic.build.pipeline import BuildOptions, BuildPipeline
from maxcompute_semantic.build.storage import PackageDB


def _make_profile() -> Profile:
    return Profile(
        name="t",
        compute_project="p",
        endpoint="https://e",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="p", schema="default", tables="*"),),
    )


def _ok(**kwargs) -> PhaseResult:
    return PhaseResult(status="success", data=kwargs)


def _build_with(
    tmp_path: Path, table_names: list[str], parallel: int | None = None
) -> tuple[BuildPipeline, MagicMock, MagicMock, MagicMock]:
    """Build a pipeline with the given parallelism and mocked
    sampling/profiling phases, ready to run. Returns
    (pipeline, mock_sampling, mock_profiling, mock_describe)."""
    client = MagicMock()
    db = PackageDB(tmp_path / "p.db")
    pipeline = BuildPipeline(
        client,
        db,
        _make_profile(),
        BuildOptions(no_history=True, parallel=parallel),
    )

    patches = patch.multiple(
        "maxcompute_semantic.build.pipeline",
        phase_list_tables=MagicMock(return_value=_ok(table_names=table_names)),
        phase_describe_table=MagicMock(return_value=_ok(column_count=1)),
        phase_column_sampling=MagicMock(return_value=_ok(sampled_rows=5)),
        phase_column_profiling=MagicMock(return_value=_ok(profiled_columns=1)),
        phase_discover_udfs=MagicMock(),
        phase_mine_history=MagicMock(),
        phase_infer_joins_heuristic=MagicMock(),
        render_all=MagicMock(),
    )
    patches.start()
    import maxcompute_semantic.build.pipeline as _pp

    return (
        pipeline,
        _pp.phase_column_sampling,
        _pp.phase_column_profiling,
        _pp.phase_describe_table,
    )


class TestParallelSamplingProfiling:
    def test_parallel_one_matches_serial_summary(self, tmp_path: Path) -> None:
        """parallel=1 produces the same summary as the old serial code path."""
        pipeline, mock_sampling, mock_profiling, _ = _build_with(
            tmp_path, ["t1", "t2", "t3"], parallel=1
        )
        try:
            summary = pipeline.run()
        finally:
            patch.stopall()
        assert summary.tables_built == 3
        assert mock_sampling.call_count == 3
        assert mock_profiling.call_count == 3
        assert summary.errors == []

    def test_parallel_four_calls_each_phase_once_per_table(self, tmp_path: Path) -> None:
        """parallel=4 with 10 tables — each table's sampling and profiling
        function is called exactly once; no duplicates from fan-out bugs."""
        table_names = [f"t{i}" for i in range(10)]
        pipeline, mock_sampling, mock_profiling, _ = _build_with(tmp_path, table_names, parallel=4)
        try:
            summary = pipeline.run()
        finally:
            patch.stopall()

        assert summary.tables_built == 10
        assert mock_sampling.call_count == 10
        assert mock_profiling.call_count == 10
        sampled = {c.args[4] for c in mock_sampling.call_args_list}
        profiled = {c.args[4] for c in mock_profiling.call_args_list}
        assert sampled == set(table_names)
        assert profiled == set(table_names)

    def test_one_worker_exception_is_absorbed(self, tmp_path: Path) -> None:
        """If phase_column_sampling raises for one table, the build finishes
        the rest and records the failure in summary.errors."""
        from maxcompute_semantic.mc_client.errors import McsError

        table_names = ["t1", "t2", "t3"]
        pipeline, mock_sampling, _mock_profiling, _ = _build_with(tmp_path, table_names, parallel=2)

        def sampling_side_effect(
            client, db, profile, source, table_name, *_args, **_kw
        ) -> PhaseResult:
            if table_name == "t2":
                raise McsError(code="UnknownError", message="boom")
            return _ok(sampled_rows=5)

        mock_sampling.side_effect = sampling_side_effect

        try:
            summary = pipeline.run()
        finally:
            patch.stopall()

        assert summary.tables_built == 3
        assert mock_sampling.call_count == 3
        err_tables = {e["table"] for e in summary.errors}
        assert "t2" in err_tables

    def test_auto_parallel_scales_to_table_count(self, tmp_path: Path) -> None:
        """parallel=None (auto) scales workers to min(table_count, 32)."""
        table_names = [f"t{i}" for i in range(10)]
        pipeline, mock_sampling, _mock_profiling, _ = _build_with(
            tmp_path, table_names, parallel=None
        )
        try:
            summary = pipeline.run()
        finally:
            patch.stopall()

        assert summary.tables_built == 10
        assert summary.parallel_workers == 10
        assert mock_sampling.call_count == 10

    def test_auto_parallel_capped_at_32(self, tmp_path: Path) -> None:
        """parallel=None with >32 tables caps workers at 32."""
        from maxcompute_semantic.build.pipeline import _AUTO_PARALLEL_CAP

        table_names = [f"t{i}" for i in range(50)]
        pipeline, _mock_sampling, _, _ = _build_with(tmp_path, table_names, parallel=None)
        try:
            summary = pipeline.run()
        finally:
            patch.stopall()

        assert summary.parallel_workers == _AUTO_PARALLEL_CAP

    def test_elapsed_seconds_is_set(self, tmp_path: Path) -> None:
        """Build summary includes positive elapsed_seconds."""
        pipeline, _, _, _ = _build_with(tmp_path, ["t1"], parallel=1)
        try:
            summary = pipeline.run()
        finally:
            patch.stopall()
        assert summary.elapsed_seconds >= 0

    def test_progress_includes_eta(self, tmp_path: Path) -> None:
        """Progress callback receives sampling messages with ETA info."""
        import time as _time

        messages: list[str] = []

        def slow_sampling(client, db, profile, source, table_name, *_args, **_kw):
            _time.sleep(0.05)
            return _ok(sampled_rows=5)

        table_names = ["t1", "t2", "t3"]
        client = MagicMock()
        db = PackageDB(tmp_path / "p.db")
        pipeline = BuildPipeline(
            client,
            db,
            _make_profile(),
            BuildOptions(no_history=True, parallel=1),
            progress=messages.append,
        )
        patches = patch.multiple(
            "maxcompute_semantic.build.pipeline",
            phase_list_tables=MagicMock(return_value=_ok(table_names=table_names)),
            phase_describe_table=MagicMock(return_value=_ok(column_count=1)),
            phase_column_sampling=MagicMock(side_effect=slow_sampling),
            phase_column_profiling=MagicMock(return_value=_ok(profiled_columns=1)),
            phase_discover_udfs=MagicMock(),
            phase_mine_history=MagicMock(),
            phase_infer_joins_heuristic=MagicMock(),
            render_all=MagicMock(),
        )
        patches.start()
        try:
            pipeline.run()
        finally:
            patch.stopall()

        sampling_msgs = [m for m in messages if "sampling + profiling" in m]
        assert len(sampling_msgs) >= 2
        mid_msgs = [m for m in sampling_msgs if "remaining" in m]
        assert len(mid_msgs) >= 1


class TestFormatDuration:
    def test_seconds(self) -> None:
        assert BuildPipeline._format_duration(45) == "45s"

    def test_minutes(self) -> None:
        assert BuildPipeline._format_duration(135) == "2m15s"

    def test_hours(self) -> None:
        assert BuildPipeline._format_duration(3661) == "1h01m01s"
