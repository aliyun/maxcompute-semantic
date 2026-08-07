# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""OSI export tests for top-level metrics emission and nested measure shape."""

from __future__ import annotations

import json
from pathlib import Path

from ruamel.yaml import YAML

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.osi.export import dump_yaml, to_osi_dict


def _build_profile_with_metrics(tmp_path: Path) -> PackageDB:
    db = PackageDB(tmp_path / "pkg.db")
    orders_id = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    db.upsert_columns(
        orders_id,
        [
            {"name": "id", "type": "BIGINT"},
            {"name": "amount", "type": "BIGINT"},
        ],
    )
    db.set_column_semantics(
        "warehouse",
        "orders",
        "amount",
        role="measure",
        agg="SUM",
    )
    db.add_metric(
        name="total_revenue",
        expression="SUM(orders.amount)",
        description="Gross",
        ai_context="Sum across all rows.",
    )
    return db


def test_osi_export_emits_metrics_array(tmp_path: Path) -> None:
    db = _build_profile_with_metrics(tmp_path)
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    metrics = out["semantic_model"][0]["metrics"]
    assert len(metrics) == 1
    m = metrics[0]
    assert m["name"] == "total_revenue"
    assert m["expression"]["dialects"][0]["dialect"] == "ANSI_SQL"
    assert m["expression"]["dialects"][0]["expression"] == "SUM(orders.amount)"
    assert m["description"] == "Gross"


def test_osi_export_metric_ai_context_uses_native_field(tmp_path: Path) -> None:
    db = _build_profile_with_metrics(tmp_path)
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    m = out["semantic_model"][0]["metrics"][0]
    assert m["ai_context"] == "Sum across all rows."
    assert "custom_extensions" not in m


def test_osi_export_no_metrics_key_when_empty(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    tid = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    db.upsert_columns(
        tid,
        [{"name": "id", "type": "BIGINT"}],
    )
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    assert "metrics" not in out["semantic_model"][0]


def test_osi_export_field_measure_carries_nested_agg(tmp_path: Path) -> None:
    db = _build_profile_with_metrics(tmp_path)
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    fields = out["semantic_model"][0]["datasets"][0]["fields"]
    amount_field = next(f for f in fields if f["name"] == "amount")
    ext = amount_field["custom_extensions"][0]
    data = json.loads(ext["data"])
    assert data["semantic_role"] == "measure"
    assert data["measure"] == {"agg": "SUM"}
    # Top-level ``agg`` kept for one-release back-compat per ADR-0001.
    assert data["agg"] == "SUM"


def test_osi_export_bare_metric_omits_optional_fields(tmp_path: Path) -> None:
    """A metric with no ``description`` / ``ai_context`` must NOT carry
    null-valued keys: ``description`` is omitted entirely and the
    ``custom_extensions`` sidecar (which only exists to ferry
    ``ai_context``) is suppressed.
    """
    db = PackageDB(tmp_path / "pkg.db")
    tid = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    db.upsert_columns(tid, [{"name": "id", "type": "BIGINT"}])
    db.add_metric(name="bare_count", expression="COUNT(*)")
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    metrics = out["semantic_model"][0]["metrics"]
    assert len(metrics) == 1
    m = metrics[0]
    assert m["name"] == "bare_count"
    assert m["expression"]["dialects"][0]["expression"] == "COUNT(*)"
    assert "description" not in m
    assert "custom_extensions" not in m


def test_osi_export_with_metrics_matches_golden(tmp_path: Path) -> None:
    """Round-trip the metrics-emit golden so the fixture stays in lockstep
    with what ``to_osi_dict`` + ``dump_yaml`` actually produce. Mirrors
    the pattern in ``test_export_yaml.test_export_matches_golden`` —
    without it, the fixture silently rots if ruamel.yaml changes its
    line-wrapping rules.
    """
    db = _build_profile_with_metrics(tmp_path)
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    # Round-trip through dump_yaml to exercise the same serialization
    # the production CLI uses (sort_keys / indent / default_flow_style).
    dest = tmp_path / "demo-with-metrics.osi.yaml"
    dump_yaml(out, dest)

    golden_path = (
        Path(__file__).resolve().parent.parent.parent
        / "fixtures"
        / "osi"
        / "expected_export_with_metrics.yaml"
    )
    y = YAML(typ="safe")
    y.default_flow_style = False
    written = y.load(dest.read_text(encoding="utf-8"))
    golden = y.load(golden_path.read_text(encoding="utf-8"))
    assert written == golden, (
        "Adapter output diverged from expected_export_with_metrics.yaml. "
        "If the change is intentional, regenerate the golden by re-running "
        "_build_profile_with_metrics and calling "
        "dump_yaml(to_osi_dict(db, semantic_model_name='demo'), golden_path)."
    )
