# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Public CLI contracts for independent, offline knowledge and graph groups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result
from ruamel.yaml import YAML

from maxcompute_semantic.commands.graph import graph_group
from maxcompute_semantic.commands.knowledge import knowledge_group


@click.group()
@click.option("--format", "output_format", default="json")
@click.option("--quiet", is_flag=True)
@click.pass_context
def _cli(ctx: click.Context, output_format: str, quiet: bool) -> None:
    ctx.obj = {"format": output_format, "quiet": quiet}


_cli.add_command(knowledge_group)
_cli.add_command(graph_group)


def _invoke(
    kb: Path,
    *args: str,
    group: str = "knowledge",
    output_format: str = "json",
    quiet: bool = False,
) -> Result:
    flags = ["--format", output_format]
    if quiet:
        flags.append("--quiet")
    return CliRunner().invoke(_cli, [*flags, group, "--kb", str(kb), *args])


def _data(result: Result, output_format: str = "json") -> dict[str, Any]:
    assert result.exit_code == 0, (result.output, result.exception)
    envelope = (
        YAML(typ="safe").load(result.stdout)
        if output_format == "yaml"
        else json.loads(result.stdout)
    )
    assert envelope["status"] == "success", envelope
    assert not result.stderr
    return envelope["data"]


def _write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _node(identifier: str, name: str, *, kind: str = "term", scope: str = "team") -> dict:
    return {
        "id": identifier,
        "kind": kind,
        "name": name,
        "scope": scope,
        "properties": {},
        "status": "confirmed",
    }


def _fragment(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {
        "kind": "mcs.knowledge_graph",
        "schema_version": 1,
        "nodes": nodes,
        "edges": edges or [],
        "evidence": [],
    }


@pytest.fixture
def kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No command may cross profile, credential or network boundaries."""
    import socket

    from maxcompute_semantic.auth import context
    from maxcompute_semantic.commands import _import_creds

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("local knowledge commands must not resolve profiles or contact a backend")

    monkeypatch.setattr(context, "_resolve_profile", forbidden)
    monkeypatch.setattr(context, "make_client_for_project", forbidden)
    monkeypatch.setattr(_import_creds, "discover_creds", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    return tmp_path / "workspace with spaces" / "knowledge.db"


def test_init_is_profile_independent_and_idempotent(kb: Path) -> None:
    first = _data(_invoke(kb, "init"))
    second = _data(_invoke(kb, "init"))
    assert first["kb_id"] == second["kb_id"]
    assert first["revision"] == second["revision"]
    assert kb.is_file()


def test_init_uses_cwd_default_without_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(_cli, ["knowledge", "init"])
    _data(result)
    assert (tmp_path / ".mcs" / "knowledge.db").is_file()


def test_command_local_kb_overrides_group_path(kb: Path, tmp_path: Path) -> None:
    override = tmp_path / "override.db"
    created = _data(_invoke(kb, "init", "--kb", str(override)))
    assert override.is_file()
    assert not kb.exists()
    check = _data(_invoke(kb, "check", "--kb", str(override), group="graph"))
    assert check["kb_id"] == created["kb_id"]
    assert not kb.exists()


@pytest.mark.parametrize(
    ("group", "args"),
    [
        ("knowledge", ["list"]),
        ("knowledge", ["search", "revenue"]),
        ("knowledge", ["show", "revenue"]),
        ("knowledge", ["export"]),
        ("graph", ["check"]),
        ("graph", ["neighbors", "revenue"]),
        ("graph", ["path", "revenue", "orders"]),
        ("graph", ["impact", "revenue"]),
    ],
)
def test_read_missing_knowledge_does_not_create_files(kb: Path, group: str, args: list[str]) -> None:
    result = _invoke(kb, *args, group=group)
    assert result.exit_code == 2
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "KnowledgeError"
    assert not kb.parent.exists()


@pytest.mark.parametrize("output_format", ["json", "yaml"])
def test_structured_success_and_errors_keep_requested_format(
    kb: Path, output_format: str
) -> None:
    _data(_invoke(kb, "init", output_format=output_format, quiet=True), output_format)
    result = _invoke(kb, "show", "missing", output_format=output_format)
    assert result.exit_code == 2
    envelope = (
        YAML(typ="safe").load(result.stdout)
        if output_format == "yaml"
        else json.loads(result.stdout)
    )
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "KnowledgeError"
    assert not result.stderr


def test_plain_error_goes_to_stderr(kb: Path) -> None:
    result = _invoke(kb, "list", output_format="plain")
    assert result.exit_code == 2
    assert not result.stdout
    assert "Error:" in result.stderr


def test_import_search_scope_and_export_roundtrip(kb: Path, tmp_path: Path) -> None:
    source = _write_json(
        tmp_path / "中文 graph.json",
        _fragment([
            _node("team-customer", "有效客户", scope="team"),
            _node("other-customer", "有效客户", scope="other"),
            _node("revenue", "收入", kind="metric"),
        ]),
    )
    _data(_invoke(kb, "import", str(source)))
    nodes = _data(_invoke(kb, "list", "--scope", "team", "--kind", "term"))["nodes"]
    assert [node["id"] for node in nodes] == ["team-customer"]
    assert _data(_invoke(kb, "show", "有效客户", "--scope", "team"))["id"] == "team-customer"
    ambiguity = _invoke(kb, "show", "有效客户")
    assert ambiguity.exit_code == 2
    assert json.loads(ambiguity.stdout)["status"] == "error"
    hits = _data(_invoke(kb, "search", "有效客户", "--scope", "team"))["results"]
    assert hits
    assert "team-customer" in json.dumps(hits, ensure_ascii=False)
    assert "other-customer" not in json.dumps(hits, ensure_ascii=False)

    original = _data(_invoke(kb, "export"))
    export_path = tmp_path / "shared knowledge.json"
    written = _data(_invoke(kb, "export", str(export_path)))
    assert written["path"] == str(export_path)
    assert json.loads(export_path.read_text(encoding="utf-8")) == original
    copy = tmp_path / "copy.db"
    _data(_invoke(copy, "import", str(export_path)))
    assert _data(_invoke(copy, "export")) == original


def test_ingest_creates_local_store_and_can_search_document(kb: Path, tmp_path: Path) -> None:
    document = tmp_path / "客户定义.md"
    document.write_text("# 有效客户\n\n完成实名认证的客户。\n", encoding="utf-8")
    ingested = _data(_invoke(kb, "ingest", str(document), "--scope", "growth"))
    hits = _data(_invoke(kb, "search", "实名认证", "--scope", "growth"))["results"]
    assert hits
    assert hits[0]["id"] == ingested["id"]
    assert kb.is_file()


def test_ingest_directory_and_quiet_document_identifier(kb: Path, tmp_path: Path) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    (directory / "one.md").write_text("# First document\n", encoding="utf-8")
    (directory / "two.md").write_text("# Second document\n", encoding="utf-8")
    ingested = _data(_invoke(kb, "ingest", str(directory)))
    assert len(ingested["documents"]) == 2
    quiet = _invoke(kb, "ingest", str(directory / "one.md"), output_format="plain", quiet=True)
    assert quiet.exit_code == 0
    assert quiet.stdout.strip() == ingested["documents"][0]["id"]


@pytest.mark.parametrize("contents", [b"{", b"[]", b"\xff"])
def test_invalid_import_file_is_classified_and_does_not_create_store(
    kb: Path, tmp_path: Path, contents: bytes
) -> None:
    source = tmp_path / "invalid.json"
    source.write_bytes(contents)
    result = _invoke(kb, "import", str(source))
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert not kb.parent.exists()


def test_missing_import_file_is_classified(kb: Path, tmp_path: Path) -> None:
    result = _invoke(kb, "import", str(tmp_path / "absent.json"))
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert not kb.exists()


@pytest.mark.parametrize("command", ["init", "ingest", "import"])
def test_store_parent_permission_error_uses_structured_envelope(
    kb: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    document = tmp_path / "document.md"
    document.write_text("# Document\n", encoding="utf-8")
    source = _write_json(tmp_path / "source.json", _fragment([]))

    def denied(_self: Path, *_args: object, **_kwargs: object) -> None:
        raise PermissionError("knowledge directory is read-only")

    monkeypatch.setattr(Path, "mkdir", denied)
    args = [command]
    if command == "ingest":
        args.append(str(document))
    elif command == "import":
        args.append(str(source))
    result = _invoke(kb, *args)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert not result.stderr
    assert not kb.exists()


def test_proposal_review_and_plain_quiet_identifier(kb: Path, tmp_path: Path) -> None:
    revision = _data(_invoke(kb, "init"))["revision"]
    patch = _write_json(
        tmp_path / "patch.json",
        {"kind": "upsert_node", "node": _node("revenue", "收入", kind="metric")},
    )
    proposal = _data(_invoke(
        kb, "propose", str(patch), "--expected-revision", str(revision), "--created-by", "alice"
    ))
    proposal_id = proposal["proposal"]["id"]
    assert _invoke(kb, "show", "revenue").exit_code == 2
    applied = _data(_invoke(kb, "apply", proposal_id, "--reviewed-by", "bob"))
    assert applied["proposal"]["status"] == "applied"
    node = _data(_invoke(kb, "show", "revenue"))
    assert node["name"] == "收入"
    quiet_show = _invoke(kb, "show", "revenue", output_format="plain", quiet=True)
    assert quiet_show.exit_code == 0
    assert quiet_show.stdout == "revenue\n"

    second = _data(_invoke(kb, "propose", str(patch)))
    rejected = _data(_invoke(
        kb, "reject", second["proposal"]["id"], "--reason", "outdated definition", "--reviewed-by", "bob"
    ))
    assert rejected["proposal"]["status"] == "rejected"
    assert _invoke(kb, "apply", second["proposal"]["id"]).exit_code == 2


def test_propose_requires_existing_store_and_valid_before_value(kb: Path, tmp_path: Path) -> None:
    patch = _write_json(
        tmp_path / "patch.json", {"kind": "upsert_node", "node": _node("term", "Term")}
    )
    result = _invoke(kb, "propose", str(patch))
    assert result.exit_code == 2
    assert not kb.exists()
    _data(_invoke(kb, "init"))
    before = _data(_invoke(kb, "export"))
    result = _invoke(kb, "propose", str(patch), "--before-value", "{broken")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert _data(_invoke(kb, "export")) == before


def test_explicit_null_before_value_is_not_treated_as_omitted(kb: Path, tmp_path: Path) -> None:
    node = _node("revenue", "Revenue", kind="metric")
    source = _write_json(tmp_path / "source.json", _fragment([node]))
    _data(_invoke(kb, "import", str(source)))
    patch = _write_json(tmp_path / "patch.json", {"kind": "upsert_node", "node": node})
    before = _data(_invoke(kb, "export"))
    explicit_null = _invoke(kb, "propose", str(patch), "--before-value", "null")
    assert explicit_null.exit_code == 2
    assert json.loads(explicit_null.stdout)["error"]["code"] == "KnowledgeError"
    assert _data(_invoke(kb, "export")) == before
    _data(_invoke(kb, "propose", str(patch)))


def test_revision_conflict_preserves_node_and_records_review(kb: Path, tmp_path: Path) -> None:
    node = _node("revenue", "Revenue", kind="metric")
    source = _write_json(tmp_path / "source.json", _fragment([node]))
    imported = _data(_invoke(kb, "import", str(source)))
    updated = {**node, "name": "Updated revenue"}
    patch = _write_json(tmp_path / "patch.json", {"kind": "upsert_node", "node": updated})
    proposal = _data(_invoke(
        kb, "propose", str(patch), "--before-value", json.dumps(node),
        "--expected-revision", str(imported["revision"])
    ))["proposal"]
    document = tmp_path / "unrelated.md"
    document.write_text("# New document\n", encoding="utf-8")
    _data(_invoke(kb, "ingest", str(document)))
    result = _invoke(kb, "apply", proposal["id"], "--reviewed-by", "bob")
    assert result.exit_code == 2
    assert "revision" in json.loads(result.stdout)["error"]["message"]
    assert _data(_invoke(kb, "show", "revenue"))["name"] == "Revenue"
    review = _data(_invoke(kb, "show", proposal["id"]))
    assert review["status"] == "conflict"
    assert review["reviewed_by"] == "bob"


def test_dangling_relation_import_leaves_existing_graph_unchanged(kb: Path, tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source.json", _fragment([_node("existing", "Existing")]))
    _data(_invoke(kb, "import", str(source)))
    before = _data(_invoke(kb, "export"))
    invalid = _write_json(tmp_path / "invalid.json", _fragment(
        [_node("new", "New")],
        [{"id": "dangling", "subject": "new", "predicate": "uses", "object": "missing"}],
    ))
    result = _invoke(kb, "import", str(invalid))
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert _data(_invoke(kb, "export")) == before


def test_full_export_can_explicitly_replace_an_existing_graph(kb: Path, tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source.json", _fragment([_node("shared", "Shared")]))
    _data(_invoke(kb, "import", str(source)))
    export_path = tmp_path / "export.json"
    _data(_invoke(kb, "export", str(export_path)))
    other = tmp_path / "other.db"
    _data(_invoke(other, "init"))
    _data(_invoke(other, "import", str(export_path), "--replace"))
    assert _data(_invoke(other, "export")) == _data(_invoke(kb, "export"))


def test_graph_preserves_assertions_evidence_status_and_retraction(kb: Path, tmp_path: Path) -> None:
    graph = _fragment([
        _node("revenue", "Revenue", kind="metric"),
        _node("amount", "Amount", kind="column"),
        _node("orders", "Orders", kind="table"),
        _node("guide", "Guide", kind="document"),
    ])
    graph["evidence"] = [{
        "id": "user-evidence",
        "source_kind": "user",
        "source_id": "alice",
        "source_version": "assertion-v1",
        "content_hash": "a" * 64,
    }]
    graph["edges"] = [
        {
            "id": "revenue-amount", "subject": "revenue", "predicate": "derived_from",
            "object": "amount", "status": "confirmed", "evidence_ids": ["user-evidence"],
        },
        {
            "id": "amount-orders", "subject": "amount", "predicate": "part_of",
            "object": "orders", "status": "confirmed", "evidence_ids": ["user-evidence"],
        },
        {
            "id": "guide-revenue", "subject": "guide", "predicate": "mentions",
            "object": "revenue", "status": "candidate", "evidence_ids": ["user-evidence"],
        },
    ]
    source = _write_json(tmp_path / "graph.json", graph)
    _data(_invoke(kb, "import", str(source)))
    neighbors = _data(_invoke(kb, "neighbors", "revenue", group="graph"))
    assert [edge["id"] for edge in neighbors["edges"]] == ["revenue-amount"]
    assert neighbors["edges"][0]["evidence_ids"] == ["user-evidence"]
    candidates = _data(_invoke(
        kb, "neighbors", "revenue", "--direction", "in", "--status", "candidate", group="graph"
    ))
    assert [edge["id"] for edge in candidates["edges"]] == ["guide-revenue"]
    path = _data(_invoke(kb, "path", "revenue", "orders", group="graph"))
    assert path["found"] is True
    assert [edge["predicate"] for edge in path["edges"]] == ["derived_from", "part_of"]
    assert all(edge["status"] == "confirmed" for edge in path["edges"])
    assert "not factual proof" in path["interpretation"]
    too_shallow = _data(_invoke(
        kb, "path", "revenue", "orders", "--max-depth", "1", group="graph"
    ))
    assert too_shallow["found"] is False
    impact = _data(_invoke(kb, "impact", "amount", group="graph"))
    assert [node["id"] for node in impact["nodes"]] == ["revenue"]
    check = _data(_invoke(kb, "check", group="graph"))
    assert check["kb_id"] == path["kb_id"]
    assert check["ok"] is True
    retracted = _data(_invoke(kb, "retract", "revenue-amount", "--reason", "definition replaced"))
    assert retracted["edge"]["status"] == "retracted"
    assert retracted["edge"]["properties"]["retraction_reason"] == "definition replaced"
    assert not _data(_invoke(kb, "neighbors", "revenue", group="graph"))["edges"]
    assert _data(_invoke(kb, "show", "revenue-amount"))["status"] == "retracted"


def test_graph_check_reports_issues_and_nonzero_exit_for_failed_integrity(
    kb: Path, tmp_path: Path
) -> None:
    source = _write_json(tmp_path / "graph.json", _fragment(
        [_node("one", "One"), _node("two", "Two")],
        [{"id": "no-evidence", "subject": "one", "predicate": "mentions", "object": "two"}],
    ))
    _data(_invoke(kb, "import", str(source)))
    result = _invoke(kb, "check", group="graph")
    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["data"]["ok"] is False
    assert {"kind": "missing_evidence", "id": "no-evidence"} in envelope["data"]["issues"]
    assert not result.stderr


def test_export_write_failure_is_classified(kb: Path, tmp_path: Path) -> None:
    _data(_invoke(kb, "init"))
    blocked_parent = tmp_path / "regular-file"
    blocked_parent.write_text("original contents")
    result = _invoke(kb, "export", str(blocked_parent / "export.json"))
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert blocked_parent.read_text() == "original contents"


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_export_cannot_overwrite_its_active_database_or_alias(
    kb: Path, tmp_path: Path, alias: str
) -> None:
    _data(_invoke(kb, "init"))
    before = _data(_invoke(kb, "export"))
    before_bytes = kb.read_bytes()
    destination = kb
    if alias != "same":
        destination = tmp_path / f"{alias}.json"
        if alias == "symlink":
            destination.symlink_to(kb)
        else:
            destination.hardlink_to(kb)
    result = _invoke(kb, "export", str(destination))
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "KnowledgeError"
    assert kb.read_bytes() == before_bytes
    assert _data(_invoke(kb, "export")) == before


@pytest.mark.parametrize(
    "args",
    [
        ["path", "one", "two", "--max-depth", "11"],
        ["impact", "one", "--max-depth", "0"],
        ["neighbors", "one", "--limit", "1001"],
        ["path", "one", "two", "--limit", "0"],
    ],
)
def test_graph_traversal_bounds_reject_before_creating_store(kb: Path, args: list[str]) -> None:
    result = _invoke(kb, *args, group="graph")
    assert result.exit_code == 2
    assert "Invalid value" in result.stderr
    assert not kb.exists()


@pytest.mark.parametrize("group", ["knowledge", "graph"])
def test_groups_do_not_offer_profile_or_live_backend_options(group: str) -> None:
    result = CliRunner().invoke(_cli, [group, "--help"])
    assert result.exit_code == 0
    assert "--kb" in result.stdout
    for option in ("--profile", "--project", "--schema"):
        assert option not in result.stdout


@pytest.mark.parametrize(
    ("group", "command"),
    [
        ("knowledge", command)
        for command in (
            "init", "ingest", "search", "list", "show", "propose", "apply", "reject",
            "retract", "export", "import",
        )
    ] + [("graph", command) for command in ("neighbors", "path", "impact", "check")],
)
def test_every_command_accepts_kb_after_command_name(group: str, command: str) -> None:
    result = CliRunner().invoke(_cli, [group, command, "--help"])
    assert result.exit_code == 0
    assert "--kb" in result.stdout
