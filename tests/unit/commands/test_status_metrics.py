# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""CLI tests for ``mcs status`` surfacing the top-level metric count.

Pins Task 8 of the top-level-metrics plan: ``mcs status`` reports
how many profile-global metrics live in the package, both in the
JSON envelope (under ``data.metrics_count``) and in the human-readable
text output. The line lives right after the existing ``tables: N``
field so an operator scanning the summary sees the new entity class
without scrolling past the freshness / age block.

Note on the text assertion shape: the plan-as-written prescribed an
explicit ``click.echo("Metrics: N")`` line, but ``commands/status.py``
renders its summary via ``Renderer.success`` which iterates a dict and
emits ``<key>: <value>`` pairs (lowercase keys). Adding the metric
count as another dict entry keeps the envelope+text shapes coherent
and lets ``r.success`` do the right thing in each format. The
deviation from the plan's literal capital-M text is documented in
the Task 8 commit message.
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


def test_status_envelope_includes_metric_count(isolated_config: Path) -> None:
    """JSON envelope must surface ``data.metrics_count`` so the agent can
    detect whether the profile carries any top-level metrics without
    fanning out an extra ``mcs metric list`` call. Adds two metrics
    via the public ``mcs metric add`` path so the test exercises the
    same CRUD wire the agent uses.
    """
    _setup_profile(isolated_config)
    runner = CliRunner()
    for name, expr in (
        ("m1", "SUM(orders.amount)"),
        ("m2", "COUNT(orders.id)"),
    ):
        add = runner.invoke(
            cli,
            [
                "metric",
                "add",
                name,
                "--expression",
                expr,
                "--profile",
                "test",
            ],
        )
        assert add.exit_code == 0, add.output

    res = runner.invoke(cli, ["-f", "json", "status", "--profile", "test"])
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    assert body["status"] == "success", body
    assert body["data"]["metrics_count"] == 2, body["data"]


def test_status_text_includes_metrics_line(isolated_config: Path) -> None:
    """Plain-text mode must surface a metric-count line so the operator
    scanning the summary sees the new entity class. The line shape
    mirrors the existing ``tables: N`` / ``udfs: N`` keys produced by
    the Renderer's dict-iteration path; see the module docstring for
    the rationale on this divergence from the plan's literal
    ``Metrics: 1`` text.
    """
    _setup_profile(isolated_config)
    runner = CliRunner()
    add = runner.invoke(
        cli,
        [
            "metric",
            "add",
            "m1",
            "--expression",
            "1",
            "--profile",
            "test",
        ],
    )
    assert add.exit_code == 0, add.output

    res = runner.invoke(cli, ["status", "--profile", "test"])
    assert res.exit_code == 0, res.output
    assert "metrics_count: 1" in res.output, res.output
    # The new line must land directly after the ``tables:`` field — same
    # cluster as ``udfs`` / ``joins`` — so the operator's eye sticks to
    # the inventory block.
    tables_idx = res.output.find("tables:")
    metrics_idx = res.output.find("metrics_count:")
    udfs_idx = res.output.find("udfs:")
    assert tables_idx != -1 and metrics_idx != -1 and udfs_idx != -1, res.output
    assert tables_idx < metrics_idx < udfs_idx, res.output
