# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``mcs show``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from maxcompute_semantic.auth.link_store import set_link
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.cli import cli


def _profile(name: str = "test-bench") -> Profile:
    return Profile(
        name=name,
        compute_project="test_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="test_proj", schema="default", tables="*"),),
    )


def _seed_overview(pdir: Path, content: str = "# overview\n\nfoo\n") -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "_overview.md").write_text(content, encoding="utf-8")


_DEFAULT_SOURCE_KEY = "test_proj__default"


def _seed_table(
    pdir: Path,
    table: str,
    content: str = "# table\n\nbar\n",
    *,
    source_key: str = _DEFAULT_SOURCE_KEY,
) -> None:
    (pdir / source_key).mkdir(parents=True, exist_ok=True)
    (pdir / source_key / f"{table}.md").write_text(content, encoding="utf-8")


def _invoke(args: list[str]) -> object:
    runner = CliRunner()
    return runner.invoke(cli, args)


def test_show_overview(isolated_config: Path) -> None:
    """``mcs show`` with a cwd-bound profile prints the overview."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_overview(pdir, "# test_proj overview\n\n3 tables\n")

    result = _invoke(["show"])
    assert result.exit_code == 0, result.output
    assert "test_proj overview" in result.output
    assert "3 tables" in result.output


def test_show_table(isolated_config: Path) -> None:
    """``mcs show --table T`` prints the per-table .md."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_table(pdir, "orders", "# orders columns\n\nid, ds\n")

    result = _invoke(["show", "--table", "orders"])
    assert result.exit_code == 0, result.output
    assert "orders columns" in result.output


def test_show_no_package_built(isolated_config: Path) -> None:
    """No package on disk → exit 5 with actionable message."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    # don't seed anything

    result = _invoke(["show"])
    # 5 = resource not found (package missing on disk); aligns with the
    # rest of the McsError taxonomy (TableNotFound, ProjectNotFound).
    assert result.exit_code == 5
    assert "no semantic package" in result.output.lower()
    # Query workflow's only fallback is live metadata.
    assert "mcs meta list-tables" in result.output
    # `mcs build` is deliberately NOT mentioned here — it's an
    # onboarding/maintenance op, not a query-flow next step.
    assert "mcs build" not in result.output


def test_show_table_not_found(isolated_config: Path) -> None:
    """``--table T`` for a non-existent table → exit 5 with hint."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_overview(pdir)  # overview exists, table doesn't

    result = _invoke(["show", "--table", "missing_table"])
    # 5 = resource not found (parallels TableNotFoundError.exit_code).
    assert result.exit_code == 5
    assert "missing_table" in result.output
    assert "list-tables" in result.output


def test_show_explicit_profile(isolated_config: Path) -> None:
    """``--profile X`` overrides cwd-link resolution."""
    p1 = _profile("alpha")
    p2 = _profile("beta")
    upsert(p1)
    upsert(p2)
    set_link(str(Path.cwd()), p1.name)

    pdir1 = isolated_config / "data" / p1.name
    pdir2 = isolated_config / "data" / p2.name
    _seed_overview(pdir1, "# alpha overview\n")
    _seed_overview(pdir2, "# beta overview\n")

    result = _invoke(["show", "--profile", "beta"])
    assert result.exit_code == 0, result.output
    assert "beta overview" in result.output
    assert "alpha overview" not in result.output


def test_show_honors_package_path(isolated_config: Path, tmp_path: Path) -> None:
    """``profile.package_path`` redirects ``mcs show`` away from default dir."""
    custom_dir = tmp_path / "custom-pkg"
    custom_dir.mkdir()
    p = Profile(
        name="custom",
        compute_project="test_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        package_path=custom_dir,
        sources=(DataSource(project="test_proj", schema="default", tables="*"),),
    )
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    _seed_overview(custom_dir, "# custom-path overview\n")
    # Default-location file would be at isolated_config/data/custom/_overview.md;
    # if package_path were ignored, mcs show would say "package not built".

    result = _invoke(["show"])
    assert result.exit_code == 0, result.output
    assert "custom-path overview" in result.output


@pytest.mark.parametrize("verb", ["init", "build", "refresh", "status"])
def test_legacy_profile_data_verbs_removed(isolated_config: Path, verb: str) -> None:
    """``mcs profile init/build/refresh/status`` are gone (Phase B cutover)."""
    result = _invoke(["profile", verb, "--help"])
    assert result.exit_code != 0
    assert "no such command" in result.output.lower() or "Error" in result.output


def test_show_table_json_includes_sample_sqls(isolated_config: Path) -> None:
    """``mcs show --table`` JSON output surfaces user-verified sample_sqls.

    Only ``user_verified`` SQL is surfaced — both the literal
    ``sample_sqls`` list and the structured ``sample_sql_patterns``
    list filter to verified entries. Mined patterns (any confidence)
    are dropped entirely from the show surface; see the comment in
    ``build/markdown.py`` for the empirical history.
    """
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.markdown import MarkdownRenderer
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    tid = db.upsert_table(sk, "orders", "h1")
    db.upsert_columns(
        tid,
        [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
    )
    db.upsert_memory(
        "sample_sql",
        json.dumps(
            {
                "table": "orders",
                "source_key": sk,
                "sql": "SELECT COUNT(*) FROM orders",
                "confidence": "user_verified",
            }
        ),
        f"sample_sql for {sk}:orders: SELECT COUNT(*) FROM orders",
    )
    MarkdownRenderer(db, profile, pdir).render_table(sk, "orders")
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--table", "orders", "--profile", profile.name],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "success"
    assert payload["data"]["sample_sqls"] == ["SELECT COUNT(*) FROM orders"]


def test_show_table_sample_sql_filter_applies_before_limit(isolated_config: Path) -> None:
    """list_sample_sqls limit must apply post-filter so target table isn't dropped."""
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.markdown import MarkdownRenderer
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    for table_name in ["cards", "legalities"]:
        tid = db.upsert_table(sk, table_name, "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
    cards_payload = {
        "table": "cards",
        "source_key": sk,
        "sql": "SELECT id FROM cards WHERE id = 10",
        "representative_sql": "SELECT id FROM cards WHERE id = 10",
        "canonical_sql": "SELECT id FROM cards WHERE id = ?",
        "shape_key": "cards_shape_001",
        "frequency": 1,
        "verified_count": 1,
        # The fixture uses ``user_verified`` because mined patterns are
        # now dropped from per-table markdown / show output entirely;
        # this test asserts the per-(source, table) filter survives a
        # crowded ``legalities`` neighborhood, not the redaction layer.
        "confidence": "user_verified",
        "provenance": "user_verified",
        "where_predicates": ["id = ?"],
        "join_edges": [],
    }
    db.upsert_memory(
        "sample_sql",
        json.dumps(cards_payload),
        f"sample_sql for {sk}:cards: SELECT id FROM cards WHERE id = 10",
    )
    for idx in range(6):
        sql = f"SELECT id FROM legalities WHERE status = 'S{idx}'"
        payload = {
            "table": "legalities",
            "source_key": sk,
            "sql": sql,
            "representative_sql": sql,
            "canonical_sql": f"SELECT id FROM legalities WHERE marker_{idx} = ?",
            "shape_key": f"legalities_shape_{idx}",
            "frequency": 1,
            "verified_count": 0,
            "confidence": "mined_low",
            "provenance": "mined_history",
            "where_predicates": [f"marker_{idx} = ?"],
            "join_edges": [],
        }
        db.upsert_memory(
            "sample_sql",
            json.dumps(payload),
            f"sample_sql for {sk}:legalities: {sql}",
        )
    MarkdownRenderer(db, profile, pdir).render_table(sk, "cards")
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--table", "cards", "--profile", profile.name],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    # The cards-table verified pattern must survive the per-(source,
    # table) filter even though it sits behind 6 ``legalities``
    # entries with earlier insert ids — the limit applies post-
    # filter. ``legalities`` entries are also ``mined_low`` so they
    # would be dropped by the verified-only gate regardless; the
    # assertion still anchors on the surviving cards pattern.
    patterns = payload["data"]["sample_sql_patterns"]
    assert len(patterns) == 1
    assert patterns[0]["shape_key"] == "cards_shape_001"
    assert patterns[0]["confidence"] == "user_verified"
    assert patterns[0]["sql"] == "SELECT id FROM cards WHERE id = 10"


def test_sample_sql_extractors_skip_bad_and_unverified_entries() -> None:
    from maxcompute_semantic.commands.show import (
        _extract_sample_sql_patterns,
        _extract_sample_sqls,
    )

    class FakeDB:
        def list_sample_sqls(self, **_kwargs):
            return [
                {"payload_json": "{not json"},
                {"payload_json": json.dumps(["not", "a", "dict"])},
                {"payload_json": json.dumps({"sql": ""})},
                {
                    "payload_json": json.dumps(
                        {
                            "sql": "SELECT * FROM mined",
                            "confidence": "mined_low",
                        }
                    )
                },
                {
                    "payload_json": json.dumps(
                        {
                            "sql": "SELECT * FROM verified",
                            "canonical_sql": "SELECT * FROM verified",
                            "shape_key": "verified_shape",
                            "frequency": 3,
                            "verified_count": 2,
                            "confidence": "user_verified",
                            "provenance": "user_verified",
                        }
                    )
                },
            ]

    db = FakeDB()

    assert _extract_sample_sqls(db, _DEFAULT_SOURCE_KEY, "orders") == [
        "SELECT * FROM verified"
    ]
    assert _extract_sample_sql_patterns(db, _DEFAULT_SOURCE_KEY, "orders") == [
        {
            "canonical_sql": "SELECT * FROM verified",
            "shape_key": "verified_shape",
            "normalizer_version": 0,
            "frequency": 3,
            "verified_count": 2,
            "confidence": "user_verified",
            "provenance": "user_verified",
            "where_predicates": [],
            "join_edges": [],
            "sql": "SELECT * FROM verified",
        }
    ]


def test_show_table_json_without_db_returns_markdown(isolated_config: Path) -> None:
    from maxcompute_semantic._internal.paths import profile_data_dir

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    _seed_table(pdir, "orders", "# orders markdown\n")

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--table", "orders", "--profile", profile.name],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["data"] == {
        "profile": profile.name,
        "table": "orders",
        "markdown": "# orders markdown\n",
    }


def test_show_overview_json_without_db_returns_markdown(isolated_config: Path) -> None:
    from maxcompute_semantic._internal.paths import profile_data_dir

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    _seed_overview(pdir, "# overview only\n")

    result = CliRunner().invoke(cli, ["-f", "json", "show", "--profile", profile.name])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["data"] == {
        "profile": profile.name,
        "table": None,
        "markdown": "# overview only\n",
    }


def test_read_sources_state_handles_missing_bad_and_non_mapping(tmp_path: Path) -> None:
    from maxcompute_semantic.commands.show import _read_sources_state

    assert _read_sources_state(tmp_path) == {}

    state = tmp_path / "_state.json"
    state.write_text("{not json", encoding="utf-8")
    assert _read_sources_state(tmp_path) == {}

    state.write_text(json.dumps({"sources": ["not", "mapping"]}), encoding="utf-8")
    assert _read_sources_state(tmp_path) == {}

    state.write_text(json.dumps({"sources": {_DEFAULT_SOURCE_KEY: {"tier": "2"}}}), encoding="utf-8")
    assert _read_sources_state(tmp_path) == {_DEFAULT_SOURCE_KEY: {"tier": "2"}}


def test_show_tables_plain_concatenates_with_separator(isolated_config: Path) -> None:
    """``--tables T1,T2`` concatenates per-table markdown with ``---`` separator."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_table(pdir, "orders", "# orders columns\n\nid, ds\n")
    _seed_table(pdir, "customers", "# customers columns\n\ncust_id, name\n")

    result = _invoke(["show", "--tables", "orders,customers"])
    assert result.exit_code == 0, result.output
    assert "orders columns" in result.output
    assert "customers columns" in result.output
    # Per-table header uses `## <sk>.<table>` form.
    assert f"## {_DEFAULT_SOURCE_KEY}.orders" in result.output
    assert f"## {_DEFAULT_SOURCE_KEY}.customers" in result.output
    # Tables are separated by `---` rule.
    assert "\n---\n" in result.output


def test_show_tables_inline_error_for_missing(isolated_config: Path) -> None:
    """A missing entry in --tables produces an inline error, not fail-fast."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_table(pdir, "orders", "# orders body\n")
    # 'missing_table' is intentionally not seeded.

    result = _invoke(["show", "--tables", "orders,missing_table"])
    assert result.exit_code == 0, result.output
    assert "orders body" in result.output
    assert "missing_table" in result.output
    assert "ERROR" in result.output


def test_show_tables_all_missing_exits_5(isolated_config: Path) -> None:
    """Every requested table missing → exit 5; callers need to tell
    "got something" from "got nothing usable" by exit code, not by
    parsing the per-entry status field."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_overview(pdir)
    # Nothing seeded under the source dir.

    result = _invoke(["show", "--tables", "missing_a,missing_b"])
    assert result.exit_code == 5, result.output
    assert "missing_a" in result.output
    assert "missing_b" in result.output


def test_show_table_and_tables_mutually_exclusive(isolated_config: Path) -> None:
    """Passing both --table and --tables is a usage error."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    pdir = isolated_config / "data" / p.name
    _seed_table(pdir, "orders")

    result = _invoke(["show", "--table", "orders", "--tables", "orders,customers"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_show_tables_json_returns_batch_payload(isolated_config: Path) -> None:
    """``-f json show --tables`` returns ``{tables: [...]}`` with per-entry status."""
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.markdown import MarkdownRenderer
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    for table_name in ("orders", "customers"):
        tid = db.upsert_table(sk, table_name, "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        MarkdownRenderer(db, profile, pdir).render_table(sk, table_name)
    db.close()

    result = CliRunner().invoke(
        cli,
        [
            "-f",
            "json",
            "show",
            "--tables",
            "orders,customers,missing_table",
            "--profile",
            profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "success"
    data = payload["data"]
    assert data["profile"] == profile.name
    by_name = {entry["table"]: entry for entry in data["tables"]}
    assert by_name["orders"]["status"] == "ok"
    assert by_name["orders"]["source_key"] == sk
    assert by_name["orders"]["columns"][0]["name"] == "id"
    assert by_name["customers"]["status"] == "ok"
    assert by_name["missing_table"]["status"] == "error"
    assert by_name["missing_table"]["error"]["code"] == "TableNotFound"


def test_show_tables_json_all_errors_returns_error_envelope(isolated_config: Path) -> None:
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    profile = Profile(
        name="multi",
        compute_project="test_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(
            DataSource(project="proj_a", schema="default", tables="*"),
            DataSource(project="proj_b", schema="default", tables="*"),
        ),
    )
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--tables", "missing", "--profile", profile.name],
    )

    assert result.exit_code == 5, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "TableNotFound"
    assert "none of the requested tables resolved" in payload["error"]["message"]


def test_show_multi_source_single_table_resolves_via_package_db(
    isolated_config: Path,
) -> None:
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    profile = Profile(
        name="multi-source",
        compute_project="test_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(
            DataSource(project="proj_a", schema="default", tables="*"),
            DataSource(project="proj_b", schema="default", tables="*"),
        ),
    )
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    sk = "proj_b__default"
    db = PackageDB(pdir / "package.db")
    db.upsert_table(sk, "orders", "hash")
    db.close()
    _seed_table(pdir, "orders", "# orders from proj_b\n", source_key=sk)

    result = CliRunner().invoke(cli, ["show", "--table", "orders", "--profile", profile.name])

    assert result.exit_code == 0, result.output
    assert "orders from proj_b" in result.output


def test_show_multi_source_single_table_ambiguous_name_errors(
    isolated_config: Path,
) -> None:
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    profile = Profile(
        name="ambiguous",
        compute_project="test_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(
            DataSource(project="proj_a", schema="default", tables="*"),
            DataSource(project="proj_b", schema="default", tables="*"),
        ),
    )
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    db.upsert_table("proj_a__default", "orders", "hash_a")
    db.upsert_table("proj_b__default", "orders", "hash_b")
    db.close()

    result = CliRunner().invoke(cli, ["show", "--table", "orders", "--profile", profile.name])

    assert result.exit_code == 2, result.output
    assert "exists in 2 sources" in result.output


def test_show_tables_empty_list_rejects(isolated_config: Path) -> None:
    """``--tables ,,`` (all-whitespace) is a usage error."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)

    result = _invoke(["show", "--tables", " , ,"])
    assert result.exit_code != 0
    assert "at least one" in result.output.lower()


def test_show_table_json_includes_structured_annotations(
    isolated_config: Path,
) -> None:
    """The JSON envelope surfaces ai_context / dimensions / metrics /
    identifiers / per-column semantic_description and emits the
    semantic-layer signal BEFORE the bulk columns array.

    Why this test exists: Claude Code persists outputs above ~5 KB and
    only shows the agent a small preview before linking to the saved
    file (which agents forget to read). If the column array sits
    first and the annotation signal sits behind it, wide tables
    (e.g. a ``cards`` table at 74 cols ≈ 79 KB) push every gate out of the agent's
    preview window — the agent then guesses joins and projections
    without ever seeing the data-profiling evidence we just built.
    """
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.markdown import MarkdownRenderer
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    tid = db.upsert_table(sk, "orders", "h1")
    db.set_table_ai_context(sk, "orders", "Order header table; one row per checkout.")
    db.upsert_columns(
        tid,
        [
            {"name": "id", "type": "BIGINT", "comment": "PK", "is_partition": 0},
            {
                "name": "amount",
                "type": "DECIMAL(10,2)",
                "comment": "",
                "is_partition": 0,
            },
            {
                "name": "status",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
                "is_enum": 1,
                "sample_values_json": json.dumps(["new", "paid", "cancelled"]),
            },
            {"name": "ds", "type": "STRING", "comment": "", "is_partition": 1},
        ],
    )
    db.set_column_semantics(
        sk,
        "orders",
        "id",
        role="identifier",
        id_type="primary",
        semantic_description="Unique order id.",
    )
    db.set_column_semantics(
        sk,
        "orders",
        "amount",
        role="measure",
        agg="SUM",
    )
    db.set_column_semantics(
        sk,
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    MarkdownRenderer(db, profile, pdir).render_table(sk, "orders")
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--table", "orders", "--profile", profile.name],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip().splitlines()[-1])["data"]

    assert data["ai_context"] == "Order header table; one row per checkout."
    assert data["dimensions"] == [{"name": "status", "dim_type": "categorical"}]
    assert data["metrics"] == [
        {"name": "amount", "expr": "amount", "agg": "SUM"},
    ]
    assert data["identifiers"] == [
        {
            "name": "id",
            "type": "primary",
            "description": "Unique order id.",
        }
    ]
    assert data["partition_columns"] == ["ds"]

    # Enum sample_values arrive as a parsed list, not a JSON-encoded
    # string — re-emitting the raw string would double-escape every
    # quote and bloat the payload.
    cols_by_name = {c["name"]: c for c in data["columns"]}
    assert cols_by_name["status"]["sample_values"] == ["new", "paid", "cancelled"]
    # ``markdown`` is dropped from the DB-present path — structured
    # fields above carry the same signal in a fraction of the bytes.
    assert "markdown" not in data
    # Empty annotation buckets are omitted (we ordered nothing into them).
    assert "annotation_suggestions" not in data
    assert "join_candidates" not in data

    # Key ordering: annotation evidence comes BEFORE the bulk columns
    # array so wide tables still surface their gates in Claude Code's
    # preview window.
    keys = list(data.keys())
    bulk_idx = keys.index("columns")
    for gate in ("ai_context", "dimensions", "metrics", "identifiers"):
        assert keys.index(gate) < bulk_idx, (
            f"{gate} must appear before the bulk columns array in {keys!r}"
        )


def test_show_table_json_single_table_includes_legacy_tables_alias(
    isolated_config: Path,
) -> None:
    """Single-table JSON keeps a batch-shaped alias for agent scripts.

    CI smoke transcripts showed agents mixing ``mcs show --table T`` with
    the ``mcs show --tables ...`` JSON shape and then indexing
    ``data.tables[0].columns_index``. Keep that path non-fatal while the
    canonical single-table shape remains ``data.columns``.
    """
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.markdown import MarkdownRenderer
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    tid = db.upsert_table(sk, "cards", "h1")
    db.upsert_columns(
        tid,
        [
            {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
            {
                "name": "cardkingdomfoilid",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
            },
            {
                "name": "cardkingdomid",
                "type": "STRING",
                "comment": "",
                "is_partition": 0,
            },
        ],
    )
    MarkdownRenderer(db, profile, pdir).render_table(sk, "cards")
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--table", "cards", "--profile", profile.name],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip().splitlines()[-1])["data"]
    assert [c["name"] for c in data["columns"]] == [
        "id",
        "cardkingdomfoilid",
        "cardkingdomid",
    ]
    assert len(data["tables"]) == 1
    table = data["tables"][0]
    assert table["status"] == "ok"
    assert table["table"] == "cards"
    assert table["name"] == "cards"
    assert "tables" not in table
    assert table["columns"][1]["name"] == "cardkingdomfoilid"
    assert [c for c in table["columns_index"] if "kingdom" in c.lower()] == [
        "cardkingdomfoilid",
        "cardkingdomid",
    ]


def test_show_table_json_includes_join_candidates_and_suggestions(
    isolated_config: Path,
) -> None:
    """``mcs show --table`` JSON output surfaces join_candidates and
    annotation_suggestions from PackageDB."""
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.markdown import MarkdownRenderer
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    tid = db.upsert_table(sk, "orders", "h1")
    db.upsert_columns(
        tid,
        [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
    )
    db.upsert_join_candidate(
        left_source_key=sk,
        left_table="orders",
        left_col="id",
        right_source_key=sk,
        right_table="customers",
        right_col="order_id",
        confidence=0.85,
        evidence=[{"kind": "name_heuristic"}],
    )
    db.upsert_annotation_suggestion(
        source_key=sk,
        table_name="orders",
        column_name="id",
        suggested_role="identifier",
        suggested_subtype="primary",
        confidence=0.90,
        evidence=["uniqueness_ratio=0.999"],
    )
    MarkdownRenderer(db, profile, pdir).render_table(sk, "orders")
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "show", "--table", "orders", "--profile", profile.name],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "success"
    jc = payload["data"]["join_candidates"]
    assert len(jc) == 1
    # ``left_table`` / ``left_source_key`` are implied by the per-(source,
    # table) call context and stripped by ``trim_join_candidate`` to keep
    # the agent-facing payload terse.
    assert "left_table" not in jc[0]
    assert "left_source_key" not in jc[0]
    assert jc[0]["right_table"] == "customers"
    assert jc[0]["left_col"] == "id"
    assert jc[0]["right_col"] == "order_id"
    suggestions = payload["data"]["annotation_suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["suggested_role"] == "identifier"
    # ``source_key`` / ``table_name`` / ``status`` / ``id`` / ``updated_at``
    # are also implied by call context and dropped by ``trim_annotation_suggestion``.
    assert "source_key" not in suggestions[0]
    assert "table_name" not in suggestions[0]
    assert "status" not in suggestions[0]
    assert "id" not in suggestions[0]
    assert "updated_at" not in suggestions[0]


def test_show_overview_json_joins_count_excludes_phantom_endpoints(
    isolated_config: Path,
) -> None:
    """``mcs show`` JSON ``joins_count`` mirrors the filtered ``_joins.md`` list.

    The build pipeline records loose join candidates whose right-side table
    is not (yet) part of the package — ``render_joins`` filters those out
    so the agent-facing ``_joins.md`` only enumerates real edges. The JSON
    envelope's ``joins_count`` must agree, otherwise the agent sees an
    over-reported number and chases relationships that aren't surfaced.
    """
    import json

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    profile = _profile()
    upsert(profile)
    set_link(str(Path.cwd()), profile.name)
    pdir = profile_data_dir(profile)
    _seed_overview(pdir, "# overview\n")
    db = PackageDB(pdir / "package.db")
    sk = _DEFAULT_SOURCE_KEY
    db.upsert_table(sk, "orders", "h_orders")
    db.upsert_table(sk, "customers", "h_customers")
    db.upsert_join(
        left_source_key=sk,
        left_table="orders",
        left_col="customer_id",
        right_source_key=sk,
        right_table="customers",
        right_col="id",
        kind="inferred",
        confidence=0.9,
    )
    db.upsert_join(
        left_source_key=sk,
        left_table="orders",
        left_col="ghost_id",
        right_source_key=sk,
        right_table="ghost_table",
        right_col="id",
        kind="inferred",
        confidence=0.6,
    )
    db.close()

    result = CliRunner().invoke(cli, ["-f", "json", "show"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "success"
    assert payload["data"]["joins_count"] == 1
