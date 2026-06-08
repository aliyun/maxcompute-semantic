# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Auto-generate package_doc entries from PackageDB tables + UDFs.

Called by the build pipeline after a successful build.
Deletes all existing package_doc entries (they are rebuilt every time)
then inserts new entries with auto-generated retrieval_text.
"""

from __future__ import annotations

import json
from typing import Any


def _build_table_summary(
    table_name: str,
    table_row: dict[str, Any],
    columns: list[dict[str, Any]],
) -> dict[str, str]:
    """Build a package_doc payload for a table.

    Note: ``tables.errors_json`` stores phase failure JSON (e.g.
    ``{"phase":"describe","code":"PermissionDenied"}``), not a
    table-level description — the schema doesn't currently carry a
    table comment field. Earlier this used ``errors_json`` as a
    fallback comment, which polluted BM25 retrieval text with error
    payloads for permission-restricted tables (review issue B3).
    Until a real ``description`` column lands, the summary lists only
    columns; that's still the most retrieval-relevant signal.
    """
    col_parts: list[str] = []
    for c in columns:
        col_comment = c.get("comment", "") or ""
        if c.get("is_partition", 0):
            col_comment = f"{col_comment}, partition" if col_comment else "partition"
        col_parts.append(f"{c['name']} ({c['type']}, {col_comment})")
    col_list = ", ".join(col_parts)
    summary = f"{table_name}; columns: {col_list}"
    return {
        "table_or_udf_name": table_name,
        "source_key": table_row.get("source_key", ""),
        "summary": summary,
    }


def _build_udf_summary(udf_row: dict[str, Any]) -> dict[str, str]:
    """Build a package_doc payload for a UDF."""
    name = udf_row["name"]
    kind = udf_row["kind"]
    signature = udf_row.get("signature") or "no signature"
    description = udf_row.get("description") or "no description"
    summary = f"{name} ({kind}): {signature} — {description}"
    return {
        "table_or_udf_name": name,
        "summary": summary,
    }


def generate_package_docs(db: Any) -> int:
    """Generate package_doc entries from PackageDB tables + UDFs.

    1. Delete all existing kind='package_doc' entries (FTS5 rows auto-deleted by trigger).
    2. For each table: build summary from columns.
    3. For each UDF: build summary from name + kind + signature + description.
    4. Insert each as a package_doc memory entry with auto-generated retrieval_text.
    Returns the count of entries created.
    """
    # Clear existing package_doc entries
    db.clear_memories(kind="package_doc")

    count = 0

    # Generate table entries — one per (source_key, table) pair so a
    # multi-source profile that holds same-named tables under two
    # sources gets two entries with source-aware retrieval_text.
    for table_row in db.list_tables():
        tid = table_row["id"]
        columns = db.get_columns(tid)
        payload = _build_table_summary(table_row["name"], table_row, columns)
        payload_json = json.dumps(payload, ensure_ascii=False)
        sk = payload.get("source_key", "")
        # Prefix retrieval_text with ``source_key:table`` so BM25 can
        # match either ``users`` (table only) or ``acme__staging:users``
        # (source-qualified) — same disambiguation surface ``mcs memory
        # verify`` writes.
        prefix = f"{sk}:{payload['table_or_udf_name']}" if sk else payload["table_or_udf_name"]
        retrieval_text = f"{prefix}: {payload['summary']}"
        db.upsert_memory("package_doc", payload_json, retrieval_text)
        count += 1

    # Generate UDF entries
    for udf_row in db.list_udfs():
        payload = _build_udf_summary(udf_row)
        payload_json = json.dumps(payload, ensure_ascii=False)
        retrieval_text = f"{payload['table_or_udf_name']}: {payload['summary']}"
        db.upsert_memory("package_doc", payload_json, retrieval_text)
        count += 1

    return count
