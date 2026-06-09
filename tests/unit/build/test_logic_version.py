"""Tests for the inference-logic version stamp + offline re-derive path.

The ``inference_logic_version`` stored in ``package_settings`` decides
whether ``mcs build --refresh`` will re-run Phase 7c (semantic
suggestions) and re-render the full markdown bundle from already-
cached column / sample-SQL evidence — the recovery flow for a CLI
upgrade that changed the inference layer.

These tests cover four behaviors:

* fresh full build stamps the current version
* refresh with matching stamp short-circuits (no re-derive, selective
  render only)
* refresh with stale stamp triggers Phase 7c + render_all + restamp
* if the re-derive raises mid-way, the stamp stays at the old value
  so the next refresh retries
* a profile built before this feature shipped (no row in
  ``package_settings``) reads back as 0 and triggers re-derive
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build._logic_version import INFERENCE_LOGIC_VERSION
from maxcompute_semantic.build.phases import PhaseResult
from maxcompute_semantic.build.pipeline import BuildOptions, BuildPipeline
from maxcompute_semantic.build.storage import PackageDB

_SK = "test_project__default"


def _make_profile() -> Profile:
    return Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps.endpoint",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
    )


def _make_pipeline(
    tmp_path: Path,
    db: PackageDB,
    opts: BuildOptions | None = None,
) -> BuildPipeline:
    client = MagicMock()
    profile = _make_profile()
    return BuildPipeline(client, db, profile, opts or BuildOptions())


def _success(**kwargs) -> PhaseResult:
    return PhaseResult(status="success", data=kwargs)


def _patch_phases() -> dict:
    """Patch every MC-touching phase + the markdown renderer so a
    refresh call can run end-to-end without a live MaxCompute client.

    Returns the patch-context-manager dict so the test can configure
    per-call return values before exercising the pipeline.
    """
    return {
        "list": patch("maxcompute_semantic.build.pipeline.phase_list_tables"),
        "describe": patch("maxcompute_semantic.build.pipeline.phase_describe_table"),
        "sampling": patch("maxcompute_semantic.build.pipeline.phase_column_sampling"),
        "profiling": patch("maxcompute_semantic.build.pipeline.phase_column_profiling"),
        "history": patch("maxcompute_semantic.build.pipeline.phase_mine_history"),
        "udfs": patch("maxcompute_semantic.build.pipeline.phase_discover_udfs"),
        "joins": patch("maxcompute_semantic.build.pipeline.phase_infer_joins_heuristic"),
        "renderer": patch("maxcompute_semantic.build.pipeline.MarkdownRenderer"),
        "render_all": patch("maxcompute_semantic.build.pipeline.render_all"),
    }


class TestFullBuildStampsCurrentVersion:
    """A successful full build writes the current logic-version into
    ``package_settings`` so a follow-up refresh against the same CLI
    sees ``stored == current`` and skips the re-derive branch."""

    def test_full_build_stamps_current_version(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        # Pre-condition: a fresh DB has no stamp (reads back as 0).
        assert db.get_inference_logic_version() == 0

        pipeline = _make_pipeline(tmp_path, db)
        patches = _patch_phases()

        with (
            patches["list"] as mock_list,
            patches["describe"] as mock_describe,
            patches["sampling"] as mock_sampling,
            patches["profiling"] as mock_profiling,
            patches["history"],
            patches["udfs"],
            patches["joins"],
            patches["render_all"],
        ):
            mock_list.return_value = _success(table_names=["t1"])
            mock_describe.return_value = _success(table_name="t1", column_count=2)
            mock_sampling.return_value = _success(table_name="t1", sampled_rows=5)
            mock_profiling.return_value = _success(table_name="t1", profiled_columns=2)
            pipeline.run()

        assert db.get_inference_logic_version() == INFERENCE_LOGIC_VERSION


class TestRefreshWithMatchingVersionSkipsRederive:
    """When the stored stamp equals the CLI's current
    :data:`INFERENCE_LOGIC_VERSION`, ``_run_refresh`` must take the
    selective-render path: ``render_all`` is *not* called, and
    Phase 7c (``_run_phase_7c``) is *not* invoked beyond what the
    standard refresh path would do (which is: never)."""

    def test_matching_version_no_rederive(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)
        # Seed a table so the refresh diff has something to look at.
        db.upsert_table(_SK, "unchanged_table", schema_hash="stable_hash")

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, db, opts=opts)
        patches = _patch_phases()

        with (
            patches["list"] as mock_list,
            patches["describe"] as mock_describe,
            patches["sampling"],
            patches["history"],
            patches["udfs"],
            patches["joins"],
            patches["renderer"] as mock_renderer_cls,
            patches["render_all"] as mock_render_all,
            patch.object(BuildPipeline, "_run_phase_7c", autospec=True) as mock_phase_7c,
        ):
            mock_list.return_value = _success(table_names=["unchanged_table"])
            mock_describe.return_value = _success(
                table_name="unchanged_table",
                column_count=4,
                schema_hash="stable_hash",
            )
            mock_renderer_cls.return_value = MagicMock()

            pipeline.run()

        mock_phase_7c.assert_not_called()
        mock_render_all.assert_not_called()
        # Stamp unchanged (still at the matching version).
        assert db.get_inference_logic_version() == INFERENCE_LOGIC_VERSION


class TestRefreshWithStaleVersionTriggersRederive:
    """When the stored stamp is behind the CLI, refresh must:

    * call ``_run_phase_7c`` once across all sources;
    * call ``render_all`` (not the selective per-table render);
    * stamp the current version on success.
    """

    def test_stale_version_runs_rederive(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        # Stamp one less than current — guaranteed stale.
        db.set_inference_logic_version(INFERENCE_LOGIC_VERSION - 1)
        db.upsert_table(_SK, "unchanged_table", schema_hash="stable_hash")

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, db, opts=opts)
        patches = _patch_phases()

        with (
            patches["list"] as mock_list,
            patches["describe"] as mock_describe,
            patches["sampling"],
            patches["history"],
            patches["udfs"],
            patches["joins"],
            patches["renderer"] as mock_renderer_cls,
            patches["render_all"] as mock_render_all,
            patch.object(BuildPipeline, "_run_phase_7c", autospec=True) as mock_phase_7c,
            patch.object(
                BuildPipeline,
                "_reconstruct_workload_from_db",
                autospec=True,
                return_value=MagicMock(to_jsonable=lambda: {}),
            ),
        ):
            mock_list.return_value = _success(table_names=["unchanged_table"])
            mock_describe.return_value = _success(
                table_name="unchanged_table",
                column_count=4,
                schema_hash="stable_hash",
            )
            mock_renderer_cls.return_value = MagicMock()

            pipeline.run()

        mock_phase_7c.assert_called_once()
        mock_render_all.assert_called_once()
        assert db.get_inference_logic_version() == INFERENCE_LOGIC_VERSION


class TestRederiveFailurePreservesStamp:
    """If the re-derive raises mid-way, the stamp must stay at the
    old value so the next refresh tries again. This is the atomicity
    guarantee — there is no "half-derived" intermediate stamp state."""

    def test_phase_7c_failure_keeps_old_stamp(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        stale_version = INFERENCE_LOGIC_VERSION - 1
        db.set_inference_logic_version(stale_version)
        db.upsert_table(_SK, "unchanged_table", schema_hash="stable_hash")

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, db, opts=opts)
        patches = _patch_phases()

        with (
            patches["list"] as mock_list,
            patches["describe"] as mock_describe,
            patches["sampling"],
            patches["history"],
            patches["udfs"],
            patches["joins"],
            patches["renderer"] as mock_renderer_cls,
            patches["render_all"],
            patch.object(
                BuildPipeline,
                "_run_phase_7c",
                autospec=True,
                side_effect=RuntimeError("boom"),
            ),
            patch.object(
                BuildPipeline,
                "_reconstruct_workload_from_db",
                autospec=True,
                return_value=MagicMock(to_jsonable=lambda: {}),
            ),
        ):
            mock_list.return_value = _success(table_names=["unchanged_table"])
            mock_describe.return_value = _success(
                table_name="unchanged_table",
                column_count=4,
                schema_hash="stable_hash",
            )
            mock_renderer_cls.return_value = MagicMock()

            with pytest.raises(RuntimeError, match="boom"):
                pipeline.run()

        # Stamp untouched — refresh did not reach the set call.
        assert db.get_inference_logic_version() == stale_version


class TestMissingStampTreatedAsZero:
    """A profile built before this feature shipped has no
    ``inference_logic_version`` row. The getter returns 0, which
    sorts below any future version, so a refresh against the
    upgraded CLI triggers the re-derive path."""

    def test_no_stamp_triggers_rederive(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        # Explicitly delete any stamp (defensive — fresh DB shouldn't
        # have one anyway, but the test contract is "no row").
        db.set_setting("inference_logic_version", None)
        assert db.get_inference_logic_version() == 0
        db.upsert_table(_SK, "t", schema_hash="h")

        opts = BuildOptions(refresh=True)
        pipeline = _make_pipeline(tmp_path, db, opts=opts)
        patches = _patch_phases()

        with (
            patches["list"] as mock_list,
            patches["describe"] as mock_describe,
            patches["sampling"],
            patches["history"],
            patches["udfs"],
            patches["joins"],
            patches["renderer"] as mock_renderer_cls,
            patches["render_all"] as mock_render_all,
            patch.object(BuildPipeline, "_run_phase_7c", autospec=True) as mock_phase_7c,
            patch.object(
                BuildPipeline,
                "_reconstruct_workload_from_db",
                autospec=True,
                return_value=MagicMock(to_jsonable=lambda: {}),
            ),
        ):
            mock_list.return_value = _success(table_names=["t"])
            mock_describe.return_value = _success(
                table_name="t",
                column_count=4,
                schema_hash="h",
            )
            mock_renderer_cls.return_value = MagicMock()

            pipeline.run()

        mock_phase_7c.assert_called_once()
        mock_render_all.assert_called_once()
        assert db.get_inference_logic_version() == INFERENCE_LOGIC_VERSION


class TestStorageHelpers:
    """Round-trip the typed accessors against a real PackageDB."""

    def test_get_returns_zero_when_unset(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        assert db.get_inference_logic_version() == 0

    def test_set_then_get(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        db.set_inference_logic_version(7)
        assert db.get_inference_logic_version() == 7

    def test_get_returns_zero_when_value_unparseable(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "test.db")
        # Bypass the typed setter to stamp a non-integer value
        # (defensive: a hand-edited DB shouldn't crash refresh).
        db.set_setting("inference_logic_version", "not-a-number")
        assert db.get_inference_logic_version() == 0
