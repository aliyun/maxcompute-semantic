# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""CLI tests for the new `mcs metric` verb group (top-level metrics).

Pins the behaviour introduced in Task 5 of the top-level-metrics
plan:

* ``mcs metric add`` accepts ``--expression`` and stores the metric
  profile-globally with UNIQUE(name).
* The expression is validated by :func:`maxcompute_semantic.metric_validator.
  validate_metric_expression` — unparseable input rejects with no
  write; column refs that don't resolve in the current profile produce
  warnings that ride along in the envelope but still commit.
* ``mcs metric list`` returns sorted-by-name.
* ``mcs metric show`` re-runs the validator and includes any warnings.
* ``mcs metric edit`` partially updates only the supplied fields.
* ``mcs metric remove`` requires ``--force`` in non-TTY contexts.
* ``mcs package propose --from-stdin`` with a top-level ``metrics:``
  list creates metric proposals via the same code path.

Plan-as-written referenced fixtures (``runner`` /
``built_single_source_profile`` / ``isolated_profile_env``) that
don't exist in this tree; this file follows the Task 3 precedent in
``test_annotate_role_rename.py`` — ``isolated_config`` fixture from
``tests/conftest.py``, inline ``CliRunner()``, ``_setup_profile``
helper mirroring ``test_annotate_cmd.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli


def _profile(name: str = "test") -> Profile:
    return Profile(
        name=name,
        compute_project="proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )


def _setup_profile(isolated_config: Path, profile_name: str = "test") -> None:
    p = _profile(profile_name)
    upsert(p)
    from maxcompute_semantic._internal.paths import profile_data_dir

    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    source_key = "proj__default"
    tid = db.upsert_table(source_key, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
            {"name": "amount", "type": "DOUBLE", "comment": "", "is_partition": 0},
        ],
    )
    db.close()


def _setup_profile_without_package(profile_name: str = "test") -> Profile:
    p = _profile(profile_name)
    upsert(p)
    return p


def test_metric_add_happy_path(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "add",
            "total_revenue",
            "--expression",
            "SUM(orders.amount)",
            "--description",
            "Gross order revenue",
            "--profile",
            "test",
        ],
    )
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    assert body["status"] == "success", body
    assert body["data"]["name"] == "total_revenue"


def test_metric_add_collision_exits_nonzero(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "metric",
            "add",
            "m1",
            "--expression",
            "SUM(orders.amount)",
            "--profile",
            "test",
        ],
    )
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "add",
            "m1",
            "--expression",
            "COUNT(*)",
            "--profile",
            "test",
        ],
    )
    assert res.exit_code != 0
    body = json.loads(res.output)
    assert body["status"] == "error"
    assert "already exists" in body["error"]["message"].lower()


def test_metric_add_warnings_still_commits(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "add",
            "m_warn",
            "--expression",
            "SUM(no_such_table.amount)",
            "--profile",
            "test",
        ],
    )
    # Commit succeeds (exit 0); warnings ride along in the envelope.
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    assert body["status"] == "success", body
    assert body["data"]["warnings"], body
    assert "not in the current profile" in " ".join(body["data"]["warnings"])


def test_metric_add_unparseable_rejects_no_write(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "add",
            "m_bad",
            "--expression",
            "SUM(((",
            "--profile",
            "test",
        ],
    )
    assert res.exit_code != 0
    # Pin the error envelope shape — wire code lands as
    # ``MetricValidation`` (its own ErrorCode, distinct from
    # ``AnnotateValidation``), exit_code = 2 mirrors annotate's so the
    # agent's "validation failed, retry with corrected payload" path
    # treats both surfaces identically.
    body = json.loads(res.output)
    assert body["status"] == "error", body
    assert body["error"]["code"] == "MetricValidation", body
    assert "could not parse" in body["error"]["message"].lower()
    list_res = runner.invoke(cli, ["-f", "json", "metric", "list", "--profile", "test"])
    body = json.loads(list_res.output)
    names = [r["name"] for r in body["data"]["metrics"]]
    assert "m_bad" not in names


def test_metric_list_sorted(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    for n in ("z", "a", "m"):
        runner.invoke(
            cli,
            ["metric", "add", n, "--expression", "1", "--profile", "test"],
        )
    res = runner.invoke(cli, ["-f", "json", "metric", "list", "--profile", "test"])
    body = json.loads(res.output)
    assert [m["name"] for m in body["data"]["metrics"]] == ["a", "m", "z"]


def test_metric_list_missing_package_does_not_create_db(isolated_config: Path) -> None:
    """Read-only metric inventory should not stamp an empty package.db.

    An empty DB would make `mcs sql review` think the profile was built and
    switch from syntax-only mode to full semantic mode with no tables.
    """
    p = _setup_profile_without_package()
    from maxcompute_semantic._internal.paths import profile_data_dir

    db_path = profile_data_dir(p) / "package.db"
    assert not db_path.exists()

    res = CliRunner().invoke(cli, ["-f", "json", "metric", "list", "--profile", "test"])

    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    assert body["status"] == "success"
    assert body["data"]["metrics"] == []
    assert not db_path.exists()


def test_metric_show_missing_package_does_not_create_db(isolated_config: Path) -> None:
    p = _setup_profile_without_package()
    from maxcompute_semantic._internal.paths import profile_data_dir

    db_path = profile_data_dir(p) / "package.db"

    res = CliRunner().invoke(cli, ["-f", "json", "metric", "show", "missing", "--profile", "test"])

    assert res.exit_code != 0
    body = json.loads(res.output)
    assert body["status"] == "error"
    assert body["error"]["code"] == "MetricNotFound"
    assert not db_path.exists()


def test_metric_show_includes_warnings(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "metric",
            "add",
            "x",
            "--expression",
            "SUM(no_such_table.col)",
            "--profile",
            "test",
        ],
    )
    res = runner.invoke(cli, ["-f", "json", "metric", "show", "x", "--profile", "test"])
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    assert body["data"]["warnings"]


def test_metric_edit_partial(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "metric",
            "add",
            "x",
            "--expression",
            "SUM(orders.amount)",
            "--description",
            "old",
            "--profile",
            "test",
        ],
    )
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "edit",
            "x",
            "--expression",
            "COUNT(*)",
            "--profile",
            "test",
        ],
    )
    assert res.exit_code == 0, res.output
    show = runner.invoke(cli, ["-f", "json", "metric", "show", "x", "--profile", "test"])
    body = json.loads(show.output)
    assert body["data"]["expression"] == "COUNT(*)"
    assert body["data"]["description"] == "old"  # unchanged


def test_metric_remove_force(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    runner.invoke(
        cli,
        ["metric", "add", "x", "--expression", "1", "--profile", "test"],
    )
    res = runner.invoke(cli, ["metric", "remove", "x", "--force", "--profile", "test"])
    assert res.exit_code == 0, res.output
    list_res = runner.invoke(cli, ["-f", "json", "metric", "list", "--profile", "test"])
    body = json.loads(list_res.output)
    assert body["data"]["metrics"] == []


def test_metric_remove_no_tty_no_force_exits_nonzero(isolated_config: Path) -> None:
    _setup_profile(isolated_config)
    runner = CliRunner()
    runner.invoke(
        cli,
        ["metric", "add", "x", "--expression", "1", "--profile", "test"],
    )
    res = runner.invoke(
        cli,
        ["-f", "json", "metric", "remove", "x", "--profile", "test"],
        input="",
    )
    assert res.exit_code != 0
    body = json.loads(res.output)
    msg = (body["error"]["message"] + " " + (body["error"].get("remediation") or "")).lower()
    assert "--force" in msg or "tty" in msg


def test_metric_edit_nonexistent_exits_nonzero(isolated_config: Path) -> None:
    """Editing a metric that doesn't exist must surface
    ``MetricNotFound`` (exit 5) with the offending name in the
    error message, *not* a bare AssertionError / generic Unknown."""
    _setup_profile(isolated_config)
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "edit",
            "no_such_metric",
            "--expression",
            "COUNT(*)",
            "--profile",
            "test",
        ],
    )
    assert res.exit_code != 0
    body = json.loads(res.output)
    assert body["status"] == "error", body
    assert body["error"]["code"] == "MetricNotFound", body
    assert "no_such_metric" in body["error"]["message"]


def test_metric_add_on_fork_profile_rejected(isolated_config: Path) -> None:
    """``mcs metric add`` against a ``kind="fork"`` profile must hit
    ``reject_if_fork`` before any DB work — exit 2, envelope
    ``status="error"`` with code ``ProfileReadOnly``.

    Mirrors the fork-rejection contract pinned in
    ``test_fork_read_only.py`` for the annotate / memory / udf
    surfaces. We inline-construct the fork via ``upsert`` (no need
    for the ``versioned_profile`` fixture's full ``mcs profile create``
    bootstrap) because the guard only inspects ``profile.kind``."""
    from maxcompute_semantic._internal.paths import profile_data_dir

    _setup_profile(isolated_config, profile_name="parent")
    parent = _profile("parent")
    parent_pdir = profile_data_dir(parent)
    fork_pdir = parent_pdir.parent / "fork"
    fork_pdir.mkdir(parents=True, exist_ok=True)
    PackageDB(fork_pdir / "package.db").close()

    fork = Profile(
        name="fork",
        compute_project=parent.compute_project,
        endpoint=parent.endpoint,
        auth=parent.auth,
        sources=parent.sources,
        package_path=fork_pdir,
        kind="fork",
        parent_profile=parent.name,
        git_sha="a" * 40,
    )
    upsert(fork)

    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "metric",
            "add",
            "x",
            "--expression",
            "1",
            "--profile",
            "fork",
        ],
    )
    assert res.exit_code == 2, res.output
    body = json.loads(res.output)
    assert body["status"] == "error", body
    assert body["error"]["code"] == "ProfileReadOnly", body
