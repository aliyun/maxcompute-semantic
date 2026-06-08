# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Translate a PackageDB into an OSI-conformant dict / YAML file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maxcompute_semantic.osi.vocabulary import (
    CUSTOM_EXTENSION_FIELDS,
    CUSTOM_EXTENSION_VENDOR,
    OSI_SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from maxcompute_semantic.build.storage import PackageDB

_DEFAULT_DIALECT = "ANSI_SQL"


def to_osi_dict(db: PackageDB, *, semantic_model_name: str) -> dict[str, Any]:
    """Translate ``db`` into an OSI-conformant dict."""
    tables = db.list_tables()
    if not tables:
        raise ValueError("Cannot export an OSI semantic model from an empty PackageDB")

    table_ids = [t["id"] for t in tables]
    cols_by_table = db.get_columns_bulk(table_ids)

    datasets: list[dict[str, Any]] = []
    for t in tables:
        cols = cols_by_table.get(t["id"], [])
        datasets.append(_build_dataset(t, cols))

    model: dict[str, Any] = {
        "name": semantic_model_name,
        "datasets": datasets,
    }
    rels = _build_relationships(db.list_joins())
    if rels:
        model["relationships"] = rels
    metrics = _build_metrics(db.list_metrics())
    if metrics:
        model["metrics"] = metrics
    return {
        "version": OSI_SCHEMA_VERSION,
        "semantic_model": [model],
    }


def _qualified_dataset_name(source_key: str, table: str) -> str:
    """OSI dataset names are unique-per-model. mcs sources may share a
    table name across sources (warehouse.orders + crm.orders), so the
    OSI ``name`` field always prefixes the source — even in single-source
    profiles, to keep the 1-source ↔ N-source diff structural."""
    return f"{source_key}__{table}"


def _physical_source_name(source_key: str, table: str) -> str:
    """Return OSI Dataset.source as a physical table reference.

    mcs stores source attribution as ``<project>__<schema>``. OSI's
    ``source`` slot describes the backing table/view, so expand the
    internal key back to dotted form and append the table name. Older
    tests and hand-built PackageDB fixtures may use a simple one-segment
    source key; those still get a table-specific ``<source>.<table>``.
    """
    if "__" in source_key:
        project, schema = source_key.split("__", 1)
        return f"{project}.{schema}.{table}"
    return f"{source_key}.{table}"


def _build_relationships(joins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rels: list[dict[str, Any]] = []
    for j in joins:
        from_name = _qualified_dataset_name(j["left_source_key"], j["left_table"])
        to_name = _qualified_dataset_name(j["right_source_key"], j["right_table"])
        rels.append(
            {
                "name": (f"{from_name}__{j['left_col']}__to__{to_name}__{j['right_col']}"),
                "from": from_name,
                "to": to_name,
                "from_columns": [j["left_col"]],
                "to_columns": [j["right_col"]],
            }
        )
    return rels


def _build_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        m: dict[str, Any] = {
            "name": r["name"],
            "expression": {
                "dialects": [{"dialect": _DEFAULT_DIALECT, "expression": r["expression"]}]
            },
        }
        if r.get("description"):
            m["description"] = r["description"]
        if r.get("ai_context"):
            m["ai_context"] = r["ai_context"]
        out.append(m)
    return out


def _build_dataset(table_row: dict[str, Any], columns: list[dict[str, Any]]) -> dict[str, Any]:
    ds: dict[str, Any] = {
        "name": _qualified_dataset_name(table_row["source_key"], table_row["name"]),
        "source": _physical_source_name(table_row["source_key"], table_row["name"]),
    }
    ai_ctx = table_row.get("ai_context")
    if ai_ctx:
        ds["ai_context"] = ai_ctx

    pks, uniques = _collect_keys(columns)
    if pks:
        ds["primary_key"] = pks
    if uniques:
        ds["unique_keys"] = uniques

    fields = [_build_field(c) for c in columns]
    if fields:
        ds["fields"] = fields
    return ds


def _collect_keys(columns: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    primaries: list[str] = []
    uniques: list[list[str]] = []
    for c in columns:
        if c.get("semantic_role") != "identifier":
            continue
        id_type = c.get("id_type")
        if id_type == "primary":
            primaries.append(c["name"])
        elif id_type == "unique":
            uniques.append([c["name"]])
        # 'foreign' identifiers stay in custom_extensions; no key promotion.
    if len(primaries) > 1:
        # First wins as primary_key; rest demote to unique_keys.
        head, *rest = primaries
        primaries = [head]
        for col in rest:
            uniques.append([col])
    return primaries, uniques


def _build_field(col: dict[str, Any]) -> dict[str, Any]:
    f: dict[str, Any] = {
        "name": col["name"],
        "expression": {"dialects": [{"dialect": _DEFAULT_DIALECT, "expression": col["name"]}]},
    }
    desc = col.get("semantic_description")
    if desc:
        f["description"] = desc

    if col.get("semantic_role") == "dimension":
        dim_type = col.get("dim_type")
        if dim_type == "time":
            f["dimension"] = {"is_time": True}
        else:
            f["dimension"] = {}

    ext_data = _custom_extension_payload(col)
    if ext_data:
        # OSI CustomExtension.data is a JSON string per schema; serialize
        # deterministically so YAML round-trips and diffs stay stable.
        f["custom_extensions"] = [
            {
                "vendor_name": CUSTOM_EXTENSION_VENDOR,
                "data": json.dumps(ext_data, sort_keys=True),
            }
        ]
    return f


def _custom_extension_payload(col: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    role = col.get("semantic_role")
    if role:
        payload["semantic_role"] = role
        if role == "measure" and col.get("agg"):
            # Nest measure metadata so it travels as a structured
            # sub-object instead of role-conditional top-level keys.
            payload["measure"] = {"agg": col["agg"]}
            # Keep the flat ``agg`` for one release of back-compat with
            # the v0.11 reader (none exists yet; slated for v0.13 drop).
            payload["agg"] = col["agg"]
    id_type = col.get("id_type")
    if id_type:
        payload["id_type"] = id_type
    ref = col.get("references_target")
    if ref:
        payload["references_target"] = ref
    # Preserve the physical MaxCompute column metadata so a future
    # importer can reconstruct mcs-flavour state losslessly. Guarded
    # against missing/None so the emitted payload never carries a
    # ``"physical_type": null`` key — same omit-when-empty discipline
    # as the conditional ``description`` / ``custom_extensions``
    # handling above.
    physical_type = col.get("type")
    if physical_type:
        payload["physical_type"] = physical_type
    if col.get("is_partition"):
        payload["is_partition"] = True
    # Only emit an extension entry if there's at least one mcs-only field
    # beyond the always-present physical_type. Plain columns get nothing.
    distinguishing = (set(payload.keys()) & CUSTOM_EXTENSION_FIELDS) - {"physical_type"}
    if not distinguishing:
        return {}
    return payload


def dump_yaml(data: dict[str, Any], path: Path) -> None:
    from ruamel.yaml import YAML

    y = YAML(typ="safe")
    y.default_flow_style = False
    y.indent(mapping=2, sequence=4, offset=2)
    with path.open("w", encoding="utf-8") as f:
        y.dump(data, f)
