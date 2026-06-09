"""Tests for the annotation-renderer amendments on <table>.md."""

from __future__ import annotations

import json
from pathlib import Path

from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.markdown import MarkdownRenderer
from maxcompute_semantic.build.storage import PackageDB

_SK = "test_proj__default"


def _make_profile() -> Profile:
    return Profile(
        name="test",
        compute_project="test_proj",
        endpoint="https://example.com",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="test_proj", schema="default", tables="*"),),
    )


def _setup(tmp_path: Path) -> tuple[PackageDB, Profile, Path]:
    db = PackageDB(tmp_path / "package.db")
    profile = _make_profile()
    out = tmp_path / "data"
    return db, profile, out


def test_render_table_frontmatter_only_no_body(tmp_path: Path, monkeypatch) -> None:
    """§5: rendered file is frontmatter-only, no pipe-table body after --- fence."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {
                "name": "status",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
                "is_enum": 1,
                "sample_values_json": json.dumps(["placed", "paid"]),
                "null_ratio": 0.05,
                "distinct_count": 2,
            },
        ],
    )
    db.set_column_semantics(_SK, "orders", "status", role="dimension", dim_type="categorical")
    db.set_table_ai_context(_SK, "orders", "Each row is one order event.")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    # After closing ---, there should be NO markdown body (just newline)
    parts = content.split("---", 2)
    assert len(parts) == 3  # opening ---, yaml block, closing ---
    body_after_fence = parts[2].strip()
    assert body_after_fence == ""
    db.close()


def test_render_table_includes_ai_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    db.set_table_ai_context(_SK, "orders", "Each row is one order event.")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    assert "ai_context:" in content
    assert "Each row is one order event." in content
    db.close()


def test_render_table_includes_dimensions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {
                "name": "status",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
                "is_enum": 1,
                "sample_values_json": json.dumps(["placed", "paid"]),
                "null_ratio": 0.05,
                "distinct_count": 2,
            },
        ],
    )
    db.set_column_semantics(_SK, "orders", "status", role="dimension", dim_type="categorical")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    assert "dimensions:" in content
    db.close()


def test_render_table_includes_metrics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "amount", "type": "DECIMAL", "comment": "", "is_partition": 0},
        ],
    )
    db.set_column_semantics(_SK, "orders", "amount", role="measure", agg="SUM")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    assert "metrics:" in content
    db.close()


def test_render_table_includes_identifiers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "order_id", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    db.set_column_semantics(_SK, "orders", "order_id", role="identifier", id_type="primary")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    assert "identifiers:" in content
    db.close()


def test_render_table_omits_empty_annotation_keys(tmp_path: Path, monkeypatch) -> None:
    """§5: unannotated state omits ai_context, dimensions, metrics, identifiers keys."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "col_a", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    assert "ai_context:" not in content
    assert "dimensions:" not in content
    assert "metrics:" not in content
    assert "identifiers:" not in content
    db.close()


def test_render_table_semantic_description_on_column(tmp_path: Path, monkeypatch) -> None:
    """§5: column entry in frontmatter gains semantic_description when annotated."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    db.set_column_semantics(
        _SK,
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
        semantic_description="order lifecycle stage",
    )
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    assert "semantic_description:" in content
    assert "order lifecycle stage" in content
    db.close()


def test_render_table_dimension_entry_has_description(tmp_path: Path, monkeypatch) -> None:
    """§5: dimension entry includes description from semantic_description."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    db.set_column_semantics(
        _SK,
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
        semantic_description="order lifecycle stage",
    )
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content = (out / _SK / "orders.md").read_text()
    # Parse YAML to verify dimension entry structure
    fm_str = content.split("---", 2)[1]
    yaml = __import__("ruamel.yaml", fromlist=["YAML"]).YAML(typ="safe")
    fm = yaml.load(fm_str)
    dims = fm["dimensions"]
    assert len(dims) == 1
    assert dims[0]["name"] == "status"
    assert dims[0]["dim_type"] == "categorical"
    assert dims[0]["description"] == "order lifecycle stage"
    db.close()


# ── Task 6: overview annotation_coverage, joins relationships, udfs frontmatter, state v4 ──


def test_render_overview_has_annotation_coverage(tmp_path):
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {
                "name": "status",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
                "semantic_role": "dimension",
                "dim_type": "categorical",
            },
        ],
    )
    db.set_table_ai_context(_SK, "orders", "order events")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_overview()
    content = (out / "_overview.md").read_text()
    assert "annotation_coverage:" in content
    assert "columns_with_role:" in content
    db.close()


def test_render_overview_has_annotated_tristate(tmp_path):
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {
                "name": "status",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
                "semantic_role": "dimension",
                "dim_type": "categorical",
            },
        ],
    )
    db.set_table_ai_context(_SK, "orders", "order events")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_overview()
    content = (out / "_overview.md").read_text()
    assert "annotated:" in content
    db.close()


def test_render_overview_frontmatter_only_no_body(tmp_path):
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(tid, [{"name": "c", "type": "STRING", "comment": "", "is_partition": 0}])
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_overview()
    content = (out / "_overview.md").read_text()
    parts = content.split("---", 2)
    body_after_fence = parts[2].strip()
    assert body_after_fence == ""
    db.close()


def test_render_joins_has_relationships_key(tmp_path):
    db, profile, out = _setup(tmp_path)
    db.upsert_join(_SK, "orders", "customer_id", _SK, "customers", "id", "link_to", 0.9, "1:n")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_joins()
    content = (out / "_joins.md").read_text()
    assert "relationships:" in content
    db.close()


def test_render_joins_frontmatter_only_no_pipe_table(tmp_path):
    db, profile, out = _setup(tmp_path)
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_joins()
    content = (out / "_joins.md").read_text()
    assert "## JOIN Inference" not in content
    db.close()


def test_render_udfs_frontmatter_only_no_pipe_table(tmp_path):
    db, profile, out = _setup(tmp_path)
    db.upsert_udf(
        "my_udf", "java", signature="my_udf(INT) -> INT", description="Custom aggregation"
    )
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_udfs()
    content = (out / "_udfs.md").read_text()
    assert "## UDFs" not in content
    assert "---" in content
    db.close()


def test_state_json_version_is_5(tmp_path):
    db, profile, out = _setup(tmp_path)
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_all()
    state = json.loads((out / "_state.json").read_text())
    assert state["version"] == 5


def test_state_json_includes_annotation_coverage(tmp_path):
    """v5: _state.json carries the same annotation_coverage rollup
    that _overview.md frontmatter does, so the eval verifier can read
    annotate-arm polarity from one structured file."""
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
            {"name": "amount", "type": "DOUBLE", "comment": "", "is_partition": 0},
        ],
    )
    db.set_column_semantics(_SK, "orders", "status", role="dimension", dim_type="categorical")
    db.set_table_ai_context(_SK, "orders", "order events")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_all()
    state = json.loads((out / "_state.json").read_text())
    cov = state["annotation_coverage"]
    assert cov["tables_total"] == 1
    assert cov["tables_with_ai_context"] == 1
    assert cov["tables_with_any_column_role"] == 1
    assert cov["columns_total"] == 2
    assert cov["columns_with_role"] == 1
    db.close()


def test_state_json_annotation_coverage_zero_on_fresh_build(tmp_path):
    """Mirror of the eval no-annotate ablation arm: a built-but-not-
    annotated profile must report columns_with_role == 0 so the
    verifier flags any leakage."""
    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [{"name": "status", "type": "STRING", "comment": "", "is_partition": 0}],
    )
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_all()
    state = json.loads((out / "_state.json").read_text())
    cov = state["annotation_coverage"]
    assert cov["columns_with_role"] == 0
    assert cov["tables_with_ai_context"] == 0
    db.close()


# ── Task 11: renderer determinism ──


def test_byte_identical_consecutive_renders(tmp_path: Path, monkeypatch) -> None:
    """§9.4: two consecutive renders produce byte-identical table.md output."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    db.set_column_semantics(
        _SK,
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    db.set_table_ai_context(_SK, "orders", "order events")

    renderer = MarkdownRenderer(db, profile, out)
    # render_table uses no timestamps — output is deterministic
    renderer.render_table(_SK, "orders")
    first = (out / _SK / "orders.md").read_bytes()

    renderer.render_table(_SK, "orders")
    second = (out / _SK / "orders.md").read_bytes()

    assert first == second
    db.close()


def test_annotation_keys_yaml_stable(tmp_path: Path, monkeypatch) -> None:
    """§9.4: YAML serialization order is deterministic across renders."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

    db, profile, out = _setup(tmp_path)
    tid = db.upsert_table(_SK, "orders", "hash1")
    db.upsert_columns(
        tid,
        [
            {
                "name": "status",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
                "semantic_role": "dimension",
                "dim_type": "categorical",
            },
        ],
    )
    db.set_table_ai_context(_SK, "orders", "order events")

    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_table(_SK, "orders")
    content1 = (out / _SK / "orders.md").read_text()

    # Re-render
    renderer.render_table(_SK, "orders")
    content2 = (out / _SK / "orders.md").read_text()

    assert content1 == content2
    db.close()
    db.close()


def test_render_overview_includes_description(tmp_path):
    from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile

    db, _profile, out = _setup(tmp_path)
    profile = Profile(
        name="test",
        compute_project="test_proj",
        endpoint="https://example.com",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="test_proj", schema="default", tables="*"),),
        description="monthly active users on orders",
    )
    db.upsert_table(_SK, "orders", "hash1")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_overview()
    content = (out / "_overview.md").read_text()
    assert "description:" in content
    assert "monthly active users on orders" in content
    db.close()


def test_render_overview_omits_empty_description(tmp_path):
    db, profile, out = _setup(tmp_path)  # _make_profile() has no description
    db.upsert_table(_SK, "orders", "hash1")
    renderer = MarkdownRenderer(db, profile, out)
    renderer.render_overview()
    content = (out / "_overview.md").read_text()
    assert "description:" not in content
    db.close()
