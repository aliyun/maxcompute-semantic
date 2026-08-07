# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sqlite3

import pytest

from maxcompute_semantic.build.proposals import (
    SemanticProposalError,
    apply_semantic_proposal,
    create_annotation_promotion_proposals,
    proposal_payload,
)
from maxcompute_semantic.build.storage import _SCHEMA_VERSION, PackageDB


def test_semantic_proposal_upsert_list_get_and_status(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    try:
        patch = {
            "kind": "column_semantics",
            "source_key": "proj__default",
            "table": "orders",
            "column": "status",
            "role": "dimension",
            "dim_type": "categorical",
            "agg": None,
            "id_type": None,
            "references_target": None,
            "semantic_description": "Business status used for filtering orders.",
        }
        evidence = [{"source": "annotation_suggestions", "confidence": 0.82}]

        pid = db.upsert_semantic_proposal(
            proposal_key="annotation:proj__default:orders:status:dimension",
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch=patch,
            confidence=0.82,
            evidence=evidence,
            provenance="build.annotation_suggestions",
            created_by="mcs-build",
        )

        same_pid = db.upsert_semantic_proposal(
            proposal_key="annotation:proj__default:orders:status:dimension",
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch=patch,
            confidence=0.91,
            evidence=evidence + [{"source": "manual_probe", "confidence": 0.91}],
            provenance="build.annotation_suggestions",
            created_by="mcs-build",
        )

        assert same_pid == pid
        rows = db.list_semantic_proposals(status="suggested")
        assert len(rows) == 1
        assert rows[0]["id"] == pid
        assert rows[0]["confidence"] == 0.91
        assert json.loads(rows[0]["patch_json"])["column"] == "status"

        row = db.get_semantic_proposal(pid)
        assert row is not None
        assert row["target_ref"] == "proj__default.orders.status"

        updated = db.update_semantic_proposal_status(
            pid,
            status="rejected",
            reviewed_by="tester",
            validation={"ok": True, "reason": "not needed"},
        )
        assert updated is True
        rejected = db.get_semantic_proposal(pid)
        assert rejected is not None
        assert rejected["status"] == "rejected"
        assert rejected["reviewed_by"] == "tester"
        assert json.loads(rejected["validation_json"])["reason"] == "not needed"
    finally:
        db.close()


@pytest.mark.parametrize("final_status", ["rejected", "applied"])
def test_semantic_proposal_upsert_preserves_reviewed_status(tmp_path, final_status):
    db = PackageDB(tmp_path / "package.db")
    try:
        proposal_key = "annotation:proj__default:orders:status:dimension"
        pid = db.upsert_semantic_proposal(
            proposal_key=proposal_key,
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch={"column": "status", "role": "dimension"},
            confidence=0.82,
            evidence=[{"source": "annotation_suggestions", "confidence": 0.82}],
            provenance="build.annotation_suggestions",
            created_by="mcs-build",
        )
        assert db.update_semantic_proposal_status(
            pid,
            status=final_status,
            reviewed_by="tester",
            validation={"ok": False, "reason": "reviewed once"},
        )
        reviewed = db.get_semantic_proposal(pid)
        assert reviewed is not None

        same_pid = db.upsert_semantic_proposal(
            proposal_key=proposal_key,
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch={"column": "status", "role": "dimension", "new": True},
            confidence=0.99,
            evidence=[{"source": "rerun", "confidence": 0.99}],
            provenance="build.annotation_suggestions",
            created_by="mcs-build",
        )

        assert same_pid == pid
        after = db.get_semantic_proposal(pid)
        assert after is not None
        assert after["status"] == final_status
        assert after["reviewed_by"] == "tester"
        assert after["reviewed_at"] == reviewed["reviewed_at"]
        assert after["applied_at"] == reviewed["applied_at"]
        assert after["validation_json"] == reviewed["validation_json"]
        assert json.loads(after["patch_json"]) == {"column": "status", "role": "dimension"}
        assert after["confidence"] == 0.82
    finally:
        db.close()


def test_semantic_proposal_migration_from_v12(tmp_path):
    db_path = tmp_path / "package.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE package_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("PRAGMA user_version = 12")
    conn.commit()
    conn.close()

    db = PackageDB(db_path)
    try:
        assert _SCHEMA_VERSION >= 13
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _SCHEMA_VERSION
        cols = {
            row[1] for row in db._conn.execute("PRAGMA table_info('semantic_proposals')").fetchall()
        }
        assert "proposal_key" in cols
        assert "patch_json" in cols
        assert "validation_json" in cols
    finally:
        db.close()


def test_semantic_proposal_migration_from_v11_branch_baseline(tmp_path):
    db_path = tmp_path / "package.db"
    db = PackageDB(db_path)
    db.close()

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS semantic_proposals")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()

    db = PackageDB(db_path)
    try:
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _SCHEMA_VERSION
        cols = {
            row[1] for row in db._conn.execute("PRAGMA table_info('semantic_proposals')").fetchall()
        }
        assert "proposal_key" in cols
        assert "patch_json" in cols
        assert "validation_json" in cols
    finally:
        db.close()


def test_create_annotation_promotion_proposals_from_suggestions(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    try:
        db.upsert_table("proj__default", "orders", schema_hash="h1")
        table = db.get_table("proj__default", "orders")
        assert table is not None
        db.upsert_columns(
            table["id"],
            [{"name": "status", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        db.upsert_annotation_suggestion(
            source_key="proj__default",
            table_name="orders",
            column_name="status",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.81,
            evidence=[{"source": "history_sql", "where_count": 4}],
        )

        result = create_annotation_promotion_proposals(db, min_confidence=0.8)

        assert result == {"created": 1, "updated": 0, "skipped": 0}
        rows = db.list_semantic_proposals(status="suggested")
        assert len(rows) == 1
        payload = proposal_payload(rows[0])
        assert payload["patch"]["role"] == "dimension"
        assert payload["patch"]["dim_type"] == "categorical"
        assert payload["evidence"][0]["source"] == "history_sql"
    finally:
        db.close()


@pytest.mark.parametrize("final_status", ["rejected", "applied"])
def test_create_annotation_promotion_proposals_skips_reviewed_proposal(tmp_path, final_status):
    db = PackageDB(tmp_path / "package.db")
    try:
        db.upsert_annotation_suggestion(
            source_key="proj__default",
            table_name="orders",
            column_name="status",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.81,
            evidence=[{"source": "history_sql", "where_count": 4}],
        )
        assert create_annotation_promotion_proposals(db, min_confidence=0.8) == {
            "created": 1,
            "updated": 0,
            "skipped": 0,
        }
        rows = db.list_semantic_proposals(status="suggested")
        assert len(rows) == 1
        pid = rows[0]["id"]
        db.update_semantic_proposal_status(
            pid,
            status=final_status,
            reviewed_by="tester",
            validation={"ok": True},
        )

        result = create_annotation_promotion_proposals(db, min_confidence=0.8)

        assert result == {"created": 0, "updated": 0, "skipped": 1}
        assert db.list_semantic_proposals(status="suggested") == []
        reviewed_rows = db.list_semantic_proposals(status=final_status)
        assert len(reviewed_rows) == 1
        assert reviewed_rows[0]["id"] == pid
    finally:
        db.close()


def test_create_annotation_promotion_proposals_counts_existing_suggested_as_updated(
    tmp_path,
):
    db = PackageDB(tmp_path / "package.db")
    try:
        db.upsert_annotation_suggestion(
            source_key="proj__default",
            table_name="orders",
            column_name="status",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.81,
            evidence=[{"source": "history_sql", "where_count": 4}],
        )
        assert create_annotation_promotion_proposals(db, min_confidence=0.8) == {
            "created": 1,
            "updated": 0,
            "skipped": 0,
        }
        before = db.list_semantic_proposals(status="suggested")
        assert len(before) == 1
        db.upsert_annotation_suggestion(
            source_key="proj__default",
            table_name="orders",
            column_name="status",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.91,
            evidence=[{"source": "history_sql", "where_count": 7}],
        )

        result = create_annotation_promotion_proposals(db, min_confidence=0.8)

        assert result == {"created": 0, "updated": 1, "skipped": 0}
        after = db.list_semantic_proposals(status="suggested")
        assert len(after) == 1
        assert after[0]["id"] == before[0]["id"]
        assert after[0]["confidence"] == 0.91
    finally:
        db.close()


def test_apply_semantic_proposal_writes_column_semantics(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    try:
        db.upsert_table("proj__default", "orders", schema_hash="h1")
        table = db.get_table("proj__default", "orders")
        assert table is not None
        db.upsert_columns(
            table["id"],
            [{"name": "status", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        pid = db.upsert_semantic_proposal(
            proposal_key="manual:proj__default.orders.status",
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch={
                "kind": "column_semantics",
                "source_key": "proj__default",
                "table": "orders",
                "column": "status",
                "role": "dimension",
                "dim_type": "categorical",
                "agg": None,
                "id_type": None,
                "references_target": None,
                "semantic_description": "Order lifecycle state.",
            },
            confidence=0.8,
            evidence=[],
            provenance="test",
            created_by="test",
        )

        result = apply_semantic_proposal(db, pid, reviewed_by="tester")

        assert result["applied"] is True
        sem = db.get_column_semantics("proj__default", "orders", "status")
        assert sem is not None
        assert sem["semantic_role"] == "dimension"
        assert sem["dim_type"] == "categorical"
        row = db.get_semantic_proposal(pid)
        assert row is not None
        assert row["status"] == "applied"
    finally:
        db.close()


def test_apply_semantic_proposal_records_validation_for_malformed_patch(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    try:
        pid = db.upsert_semantic_proposal(
            proposal_key="manual:proj__default.orders.status",
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch={
                "kind": "column_semantics",
                "source_key": "proj__default",
                "table": "orders",
                "role": "dimension",
                "dim_type": "categorical",
            },
            confidence=0.8,
            evidence=[],
            provenance="test",
            created_by="test",
        )

        with pytest.raises(SemanticProposalError):
            apply_semantic_proposal(db, pid, reviewed_by="tester")

        row = db.get_semantic_proposal(pid)
        assert row is not None
        assert row["status"] == "suggested"
        assert row["reviewed_by"] == "tester"
        validation = json.loads(row["validation_json"])
        assert validation["ok"] is False
        assert validation["code"] == "SemanticProposalError"
    finally:
        db.close()
