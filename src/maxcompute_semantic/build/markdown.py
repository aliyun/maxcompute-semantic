# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Markdown projection renderer — reads PackageDB data and writes .md files.

Per-table markdown lives under per-source subdirectories
(``<output_dir>/<source_key>/<table>.md``) so a multi-source profile
that legitimately holds two same-named tables under different
``(project, schema)`` pairs doesn't have one .md silently overwrite
the other. Profile-global files — ``_overview.md`` (frontmatter-only
with annotation_coverage and per-source table entries), ``_joins.md``
(frontmatter-only with relationships key, source_key qualification on
cross-source pairs), ``_udfs.md`` (frontmatter-only), ``_state.json``
(v4, partitioned under a ``sources`` key) — stay at the profile data
dir's top level.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from maxcompute_semantic._internal.paths import profile_data_dir, tier_cache_path
from maxcompute_semantic.auth.schema import DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB


def _yaml_dumps(data: Any) -> str:
    """Serialize data to a YAML string using ruamel.yaml."""
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    from io import StringIO

    stream = StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def _read_tier_for_project(profile: Profile, project: str) -> str:
    """Return the cached tier sentinel ("2" / "3") for ``project`` —
    matches what ``mc_client.tier.get_tier`` writes during the build
    pipeline's per-source iteration. Defaults to "3" when the
    sentinel is missing (tier hasn't been probed yet) so the renderer
    still emits valid output rather than crashing.
    """
    sentinel = tier_cache_path(profile, project)
    if sentinel.exists():
        content = sentinel.read_text(encoding="utf-8").strip()
        if content in {"2", "3"}:
            return content
    # Legacy ``.tier-level`` fallback — kept for users with packages
    # built before the per-project ``tier_cache/`` layout landed.
    legacy = profile_data_dir(profile) / ".tier-level"
    if legacy.exists():
        content = legacy.read_text(encoding="utf-8").strip()
        if content in {"2", "3"}:
            return content
    return "3"


def build_role_groups(
    cols: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Group a column list by ``semantic_role`` into the
    ``(dimensions, metrics, identifiers)`` entries the agent reads
    before scanning the bulk ``columns`` array.

    Lifted here so the markdown frontmatter and the ``mcs show --table``
    JSON envelope share a single source of truth — drift between the
    two surfaces previously meant agents that read JSON saw a different
    semantic-layer projection than agents that read the on-disk .md.
    """
    dimensions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    identifiers: list[dict[str, Any]] = []
    for col in cols:
        role = col.get("semantic_role")
        if role == "dimension":
            dim_entry: dict[str, Any] = {
                "name": col["name"],
                "dim_type": col.get("dim_type"),
            }
            if col.get("semantic_description"):
                dim_entry["description"] = col["semantic_description"]
            dimensions.append(dim_entry)
        elif role == "measure":
            met_entry: dict[str, Any] = {
                "name": col["name"],
                "expr": col["name"],
                "agg": col.get("agg"),
            }
            if col.get("semantic_description"):
                met_entry["description"] = col["semantic_description"]
            metrics.append(met_entry)
        elif role == "identifier":
            id_entry: dict[str, Any] = {
                "name": col["name"],
                "type": col.get("id_type"),
            }
            if col.get("id_type") == "foreign" and col.get("references_target"):
                id_entry["references"] = col["references_target"]
            if col.get("semantic_description"):
                id_entry["description"] = col["semantic_description"]
            identifiers.append(id_entry)
    return dimensions, metrics, identifiers


def compact_column_entry(
    col: dict[str, Any],
    *,
    sample_cap: int = 5,
    value_truncate: int = 80,
) -> dict[str, Any]:
    """Project a column row to the agent-facing fields, parsing the
    raw ``sample_values_json`` string into a python list and capping
    the breadth (count) + depth (per-value length).

    Why this shape:

    * ``sample_values_json`` lands on disk as an already-JSON-encoded
      string; re-emitting it as a string inside another JSON envelope
      double-escapes every quote and bloats the payload. We parse it
      back to a list so the consumer reads it as data, not as text.
    * For ``is_enum`` columns the stored list is the full distinct
      set (capped at 30 by the build phase) — surface it under the
      ``sample_values`` key so the agent treats it as authoritative.
      For non-enum columns the same list is just a couple of stored
      shapes (timestamp format, currency-code casing); surface it
      under ``format_examples`` so the agent doesn't read three
      arbitrary samples as the full domain.
    * Null-valued / empty-string keys are omitted so the YAML / JSON
      doesn't render ``comment: ""`` / ``null_ratio: null`` placeholders
      that push the load-bearing fields out of the preview window.
    """
    out: dict[str, Any] = {
        "name": col["name"],
        "type": col["type"],
    }
    comment = col.get("comment") or ""
    if comment:
        out["comment"] = comment
    if col.get("is_partition"):
        out["is_partition"] = True
    if col.get("null_ratio") is not None:
        out["null_ratio"] = _round_null_ratio(col["null_ratio"])
    if col.get("distinct_count") is not None:
        out["distinct_count"] = col["distinct_count"]
    raw = col.get("sample_values_json")
    if raw:
        parsed: Any = None
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            capped = parsed[:sample_cap]
            normalized: list[Any] = []
            for v in capped:
                if isinstance(v, str) and len(v) > value_truncate:
                    normalized.append(v[: value_truncate - 1] + "…")
                else:
                    normalized.append(v)
            key = "sample_values" if col.get("is_enum") else "format_examples"
            out[key] = normalized
    if col.get("semantic_description"):
        out["semantic_description"] = col["semantic_description"]
    fmt_hint = _date_format_hint(col)
    if fmt_hint:
        out["format_hint"] = fmt_hint
    return out


# Per-table annotation suggestions below this confidence are dropped
# from the rendered .md — they're overwhelmingly the
# ``pattern: fallback, source: name_heuristic`` rows the classifier
# emits when it has no signal, which carry no actionable information
# but inflate the always-loaded payload by 4-8 YAML lines per column.
# A wide table (e.g. a 74-column ``cards`` table) buries the high-signal entries
# (PK/FK / dimension-ordinal suggestions in the 0.65-0.85 confidence
# band) under ~300 lines of these no-op fallback rows otherwise.
_SUGGESTION_MIN_CONFIDENCE = 0.5

# Confidence values are summed/averaged from per-signal floats in
# ``semantic_suggestions`` and ``join_candidates``; the arithmetic
# leaves FP residue (0.65 → 0.6499999999999999, 0.6 → 0.6000000000000001).
# Rendering the residue verbatim in the agent-facing YAML costs tokens
# and reads as garbage data — a small model may also misjudge a
# threshold comparison. Round at the agent boundary; the in-DB raw
# value is preserved for ranking and threshold checks.
_CONFIDENCE_PRECISION = 2
# ``null_ratio`` carries fine-grained signal the agent uses to decide
# whether a NULL-filter is needed; 2-decimal rounding (1% resolution)
# would collapse "0.5% null" to 0 and read as "no nulls at all".
# 4 decimals (0.01% resolution) kills FP residue without losing the
# distinction between "essentially never null" and "completely null"
# rows the agent reasons about.
_NULL_RATIO_PRECISION = 4


def _round_confidence(value: Any) -> Any:
    """Round a confidence float to ``_CONFIDENCE_PRECISION`` decimals,
    leaving non-numeric and None values untouched."""
    if isinstance(value, float):
        return round(value, _CONFIDENCE_PRECISION)
    return value


def _round_null_ratio(value: Any) -> Any:
    """Round a null_ratio float to ``_NULL_RATIO_PRECISION`` decimals,
    leaving non-numeric and None values untouched."""
    if isinstance(value, float):
        return round(value, _NULL_RATIO_PRECISION)
    return value


def _trim_evidence(evidence: Any) -> Any:
    """Round any ``confidence`` / ``*uniqueness_ratio`` / ``coverage_ratio``
    float inside an evidence list so the rendered YAML doesn't leak
    FP arithmetic residue to the agent. Matches both bare
    ``uniqueness_ratio`` (annotation_suggestions evidence) and the
    ``left_uniqueness_ratio`` / ``right_uniqueness_ratio`` pair the
    join-candidate miner emits in ``evidence[].join_shape`` entries —
    earlier fixes missed the prefixed pair and the per-table .md kept
    surfacing values like ``0.5875444289908824``."""
    if not isinstance(evidence, list):
        return evidence
    trimmed: list[Any] = []
    for entry in evidence:
        if not isinstance(entry, dict):
            trimmed.append(entry)
            continue
        cleaned = dict(entry)
        for key in (
            "confidence",
            "uniqueness_ratio",
            "left_uniqueness_ratio",
            "right_uniqueness_ratio",
            "coverage_ratio",
            "cast_rate",
        ):
            if key in cleaned:
                cleaned[key] = _round_confidence(cleaned[key])
        trimmed.append(cleaned)
    return trimmed


def trim_annotation_suggestion(
    s: dict,
    *,
    owner_source_key: str,
    strip_filter_evidence: bool = False,
) -> dict | None:
    """Project an ``annotation_suggestions`` row to the agent-relevant
    keys. Drops internal-only fields (``id``, ``updated_at``,
    ``status``, ``source_key``, ``table_name``, ``evidence_json``) that
    the per-(source, table) markdown filename already implies — every
    entry in a table's ``annotation_suggestions`` list shares the same
    source / table / status, so repeating them per row is noise that
    pushes signal out of the agent's context window. ``evidence`` is
    kept (parsed list form) and ``evidence_json`` dropped to avoid
    rendering both representations of the same data.

    When ``strip_filter_evidence`` is True (caller has already
    confirmed this column as a dimension/metric/identifier via the
    ``columns.semantic_role`` annotation pass), the ``where_count``
    key inside each ``history_sql`` evidence entry is removed. The
    row is dropped entirely (returns None) if that strip leaves the
    evidence list empty. Rationale: ``where_count`` on an already-
    confirmed column is a filter-bias signal — the agent reads it
    as "this column is filtered on N times in history" and tends to
    add gratuitous WHERE clauses for queries that don't need them.
    The rtype over-filter regression in case 0274 of the
    california_schools subset traces to this exact bias on a column
    that was already annotated as a dimension. Other history_sql
    evidence (``aggregate``, ``group_by_count``) confirms role
    assignment without biasing the filter shape, so it's preserved.
    """
    out: dict[str, Any] = {
        "column_name": s["column_name"],
        "suggested_role": s["suggested_role"],
        "confidence": _round_confidence(s.get("confidence")),
    }
    if s.get("suggested_subtype") is not None:
        out["suggested_subtype"] = s["suggested_subtype"]
    evidence = s.get("evidence")
    if evidence:
        trimmed_evidence = _trim_evidence(evidence)
        if strip_filter_evidence and isinstance(trimmed_evidence, list):
            filtered: list[Any] = []
            for entry in trimmed_evidence:
                if not isinstance(entry, dict):
                    filtered.append(entry)
                    continue
                if entry.get("source") == "history_sql" and "where_count" in entry:
                    cleaned = {k: v for k, v in entry.items() if k != "where_count"}
                    # Drop entries that contain ONLY ``source`` + ``where_count``
                    # — keeping them after the strip would render an
                    # evidence row with no informational payload.
                    if len(cleaned) <= 1:
                        continue
                    filtered.append(cleaned)
                else:
                    filtered.append(entry)
            trimmed_evidence = filtered
            if not trimmed_evidence:
                # All evidence was over-filter bias on an already-
                # annotated column — the role assignment in the
                # confirmed block already carries the load-bearing
                # signal, so the suggestion row would render as noise.
                return None
        if trimmed_evidence:
            out["evidence"] = trimmed_evidence
    return out


def trim_join_candidate(jc: dict, *, owner_source_key: str) -> dict:
    """Project a ``join_candidates`` row to the agent-relevant keys.
    The left side is always the current table (the .md file), so
    ``left_source_key`` / ``left_table`` are dropped. ``right_source_key``
    is dropped when it matches the owner's source_key (same-source join)
    and kept only on cross-source pairs where it's load-bearing.
    ``id``, ``status``, ``updated_at``, ``evidence_json`` are internal
    fields; ``right_uniqueness_ratio`` is the same value already inside
    ``evidence[].right_uniqueness_ratio``. Null-valued tail keys
    (``coverage_ratio`` / ``conflict_group``) are omitted so the YAML
    doesn't render ``key: null`` placeholders.
    """
    out: dict[str, Any] = {
        "left_col": jc["left_col"],
        "right_table": jc["right_table"],
        "right_col": jc["right_col"],
        "confidence": _round_confidence(jc.get("confidence")),
    }
    if jc.get("right_source_key") and jc["right_source_key"] != owner_source_key:
        out["right_source_key"] = jc["right_source_key"]
    if jc.get("cardinality"):
        out["cardinality"] = jc["cardinality"]
    if jc.get("coverage_ratio") is not None:
        out["coverage_ratio"] = _round_confidence(jc["coverage_ratio"])
    if jc.get("conflict_group"):
        out["conflict_group"] = jc["conflict_group"]
    evidence = jc.get("evidence")
    if evidence:
        out["evidence"] = _trim_evidence(evidence)
    return out


def _percent(value: float | None) -> str:
    """Format a null_ratio as a percentage string, e.g. 0.01 -> "1%"."""
    if value is None:
        return "-"
    pct = round(value * 100)
    if pct == 0 and value > 0:
        return "<1%"
    return f"{pct}%"


def _enum_display(col: dict) -> str:
    """Format the Enum column display: 'Yes (a/b/c)' or 'No'."""
    if not col.get("is_enum"):
        return "No"
    sample_json = col.get("sample_values_json")
    if sample_json:
        try:
            vals = json.loads(sample_json)
            # Show up to 3 values, slash-separated.
            shown = "/".join(str(v) for v in vals[:3])
            return f"Yes ({shown})"
        except (json.JSONDecodeError, TypeError):
            pass
    return "Yes"


def _source_for_key(profile: Profile, source_key: str) -> DataSource | None:
    """Look up the ``DataSource`` whose ``source_key()`` matches the
    given key. Returns None when the key references a source that's
    been removed from the profile but still has rows on disk (a
    transient state during a partial-cleanup refresh).
    """
    for src in profile.sources:
        if src.source_key() == source_key:
            return src
    return None


_FLIP_CARDINALITY = {"1:n": "n:1", "n:1": "1:n", "1:1": "1:1", "n:m": "n:m"}


def _format_partner(
    partner_sk: str,
    partner_table: str,
    owner_sk: str,
    own_col: str,
    cardinality: str | None = None,
) -> str:
    """Render a join-partner reference for the overview's ``joins_to`` list.

    Bare table names when the partner sits under the same source as the
    owner (the common single-source case); ``source_key.table`` form when
    the partner is in a different source so cross-source joins remain
    unambiguous on bare-name lookup. The own-side join column is appended
    as `` via {own_col}`` so the agent sees the join key directly from
    the always-loaded overview instead of paying a per-partner
    ``mcs show --table T`` round-trip to discover which column on the
    current table is the FK / PK that links to the partner.

    When ``cardinality`` is provided it is appended as `` [1:n]`` /
    ``[n:1]`` / ``[1:1]`` / ``[n:m]`` — from THIS table's perspective
    (left side of the relation), so the agent knows whether the partner
    is a parent (n:1) or fan-out child (1:n). Callers MUST flip the
    raw stored cardinality when the owner is the right-hand side of the
    join record (see ``_FLIP_CARDINALITY``).
    """
    base = partner_table if partner_sk == owner_sk else f"{partner_sk}.{partner_table}"
    suffix = f" [{cardinality}]" if cardinality else ""
    return f"{base} via {own_col}{suffix}"


# Lowercased-ODPS-type-keyword → short tag used in ``columns_index``
# entries. Anything that maps to "" (STRING / VARCHAR / CHAR / unknown)
# is rendered as the bare column name — the tag is opt-in, only shown
# when it carries a hint the agent can act on (date wrap, numeric cast,
# array/struct unnesting, etc.). The keyword test runs against the
# leading word of the type string so parameterized types like
# ``DECIMAL(10,2)`` / ``ARRAY<STRING>`` / ``STRUCT<...>`` still resolve.
_TYPE_TAGS = {
    "bigint": "int",
    "int": "int",
    "smallint": "int",
    "tinyint": "int",
    "float": "decimal",
    "double": "decimal",
    "decimal": "decimal",
    "boolean": "bool",
    "date": "date",
    "datetime": "datetime",
    "timestamp": "datetime",
    "timestamp_ntz": "datetime",
    "binary": "binary",
    "array": "array",
    "map": "map",
    "struct": "struct",
    "json": "json",
}


def _type_tag(odps_type: str | None) -> str:
    """Return the short ``columns_index`` tag for an ODPS column type.

    Empty string for STRING / VARCHAR / CHAR / unknown — those carry no
    actionable hint, and emitting ``name:string`` for every text column
    would just bloat the always-loaded overview without helping the
    agent decide anything.
    """
    if not odps_type:
        return ""
    head = odps_type.strip().split("(", 1)[0].split("<", 1)[0].strip().lower()
    return _TYPE_TAGS.get(head, "")


_IDENTIFIER_MARKER = {
    "primary": "pk",
    "foreign": "fk",
    "unique": "unique",
}

# Suggestions below this confidence are too uncertain to broadcast as a
# top-level structural cue in the always-loaded overview. The gate
# matches the threshold the enrich workflow uses ("≥0.80 can
# usually be confirmed directly; lower-confidence suggestions need
# human/agent review") with one notch of headroom — overview markers
# are advisory, not authoritative, so 0.7 still keeps the false-positive
# rate low without being so strict that real PK/FK signals get dropped.
_PK_MARKER_CONFIDENCE_FLOOR = 0.7


# Join-graph evidence above this confidence floor is strong enough to
# surface a column as ``[pk]``/``[fk]`` in the always-loaded overview
# even when the annotation-suggestions classifier didn't reach the 0.7
# bar. The join engine already filters most coincidental matches
# (PK↔PK same_name suppression at phases.py:1150, attr↔attr same_name
# suppression at :1181, loose_id phantom filtering at :1213), so a
# surviving join row at ≥ 0.5 is a real structural signal.
_JOIN_MARKER_CONFIDENCE_FLOOR = 0.5


def _join_derived_markers(
    joins: list[dict[str, Any]],
    source_key: str,
    table_name: str,
) -> dict[str, str]:
    """Derive ``[pk]`` / ``[fk]`` markers from the inferred join graph.

    Returns a ``{column_name: marker}`` map for columns of
    ``(source_key, table_name)`` that participate in confirmed-enough
    join edges.

    Per-kind mapping (filtered to ``confidence >=
    _JOIN_MARKER_CONFIDENCE_FLOOR``):

    - ``link_to`` / ``xxx_id``: left side is the FK (the ``_id`` or
      ``link_to_X`` column), right side is the PK target. Right side
      is marked ``pk`` only when it's literally named ``id`` — other
      right-col names could be natural keys but lack a strong enough
      shape signal to broadcast at overview level.
    - ``same_name``: both sides are join keys. The kind survives only
      when at least one side is FK-shaped or PK-like (phases.py
      pattern 3 filter), so marking ``fk`` on both endpoints is
      defensible — it's at minimum a join key.
    - ``loose_id``: skipped. The 0.3 base confidence already falls
      below the floor and the right side is a phantom table, so
      surfacing the left column as ``[fk]`` would tell the agent
      "this points somewhere" when it doesn't.

    This is the third-priority marker source in
    ``_identifier_markers_by_column``: confirmed annotations > 0.7+
    suggestions > join-graph evidence. The motivating failure mode
    was a table with two name-plausible join-key candidates
    (e.g. ``cdscode`` vs ``school_code``) where neither carried a
    confirmed annotation nor a ≥0.7 suggestion, so the overview
    surfaced both as bare names
    and the agent picked the wrong one. Surfacing ``cdscode [fk]``
    via the join graph nudges the agent toward the canonical join
    key without requiring per-DB annotation passes.
    """
    markers: dict[str, str] = {}
    for j in joins:
        if (j.get("confidence") or 0.0) < _JOIN_MARKER_CONFIDENCE_FLOOR:
            continue
        kind = j.get("kind") or ""
        if kind == "loose_id":
            continue
        if j.get("left_source_key") == source_key and j.get("left_table") == table_name:
            col = j.get("left_col")
            if col and col not in markers and kind in ("link_to", "xxx_id", "same_name"):
                markers[col] = "fk"
        if j.get("right_source_key") == source_key and j.get("right_table") == table_name:
            col = j.get("right_col")
            if col and col not in markers:
                if kind in ("link_to", "xxx_id") and col == "id":
                    markers[col] = "pk"
                elif kind == "same_name":
                    markers[col] = "fk"
    return markers


def _identifier_markers_by_column(
    suggestions: list[dict[str, Any]],
    columns: list[dict[str, Any]] | None = None,
    joins: list[dict[str, Any]] | None = None,
    source_key: str | None = None,
    table_name: str | None = None,
) -> dict[str, str]:
    """Pick the best identifier marker per column.

    Returns a ``{column_name: marker}`` map where marker is one of
    ``pk`` / ``fk`` / ``unique``.

    Priority chain (higher signals win unconditionally; lower signals
    fill gaps only):

    1. **Confirmed annotations** — any column with
       ``semantic_role='identifier'`` and an ``id_type`` recognized by
       ``_IDENTIFIER_MARKER`` (the operator/agent's written
       confirmation via ``mcs package apply``).
    2. **High-confidence suggestions** —
       ``annotation_suggestions.suggested_role='identifier'`` rows
       above ``_PK_MARKER_CONFIDENCE_FLOOR`` (0.7).
    3. **Join-graph evidence** — columns participating in confirmed
       join edges (see ``_join_derived_markers``). Requires ``joins``
       + ``source_key`` + ``table_name`` to be passed; falls through
       to no-op when any are missing (back-compat for callers that
       don't have the join list handy).

    ``list_annotation_suggestions`` already orders rows by
    ``(table, confidence DESC, column)`` so the first identifier
    suggestion encountered for a given column is the highest-confidence
    one — later rows are ignored. Suggestions below
    ``_PK_MARKER_CONFIDENCE_FLOOR`` or with an unrecognized subtype are
    skipped (the join-graph layer then gets a chance at them).

    **Join-graph overrides ``[unique]`` from suggestions.** ``[unique]``
    only tells the agent the column is distinctive; ``[pk]`` / ``[fk]``
    tells the agent *where to join*. In 1:1 relationships (e.g. a
    ``disp.client_id`` where every disp row maps to exactly one client)
    the suggestion layer fires ``[unique]`` because sample uniqueness
    is high, masking the more actionable FK signal from the join graph.
    FK is strictly more useful for SQL generation, so when the
    suggestion is ``[unique]`` and the join graph emits ``[pk]`` /
    ``[fk]`` for the same column, the join-graph marker wins.
    Confirmed annotations and suggestion-level ``[pk]`` / ``[fk]``
    still beat the join graph (they're operator-confirmed or higher-
    confidence than the join-edge floor).
    """
    markers: dict[str, str] = {}
    # Confirmed identifiers from the annotation pass take precedence.
    for col in columns or []:
        if col.get("semantic_role") != "identifier":
            continue
        marker = _IDENTIFIER_MARKER.get(col.get("id_type") or "")
        if marker and col.get("name"):
            markers[col["name"]] = marker
    # Fall back to suggestions for any column not yet annotated.
    for suggestion in suggestions:
        if suggestion.get("suggested_role") != "identifier":
            continue
        col_name = suggestion.get("column_name")
        if not col_name or col_name in markers:
            continue
        if (suggestion.get("confidence") or 0.0) < _PK_MARKER_CONFIDENCE_FLOOR:
            continue
        marker = _IDENTIFIER_MARKER.get(suggestion.get("suggested_subtype") or "")
        if marker:
            markers[col_name] = marker
    # Join-graph evidence: fills gaps, AND upgrades [unique] suggestions
    # to [pk]/[fk] when the join graph has stronger evidence.
    if joins and source_key and table_name:
        for col_name, marker in _join_derived_markers(joins, source_key, table_name).items():
            existing = markers.get(col_name)
            if existing is None or existing == "unique":
                markers[col_name] = marker
    return markers


# Profile-stat thresholds that turn into agent-facing warning markers.
# Crossed columns get `[null]` / `[const]` in the overview so the agent
# knows the column can't carry a filter or contribute a meaningful
# projection. The 0.99 floor avoids false positives from small-sample
# noise; ``distinct_count == 1`` is the unambiguous constant case
# (zero-row tables get NULL distinct_count and skip the marker).
_NULL_RATIO_WARNING_FLOOR = 0.99

# STRING columns whose format_examples mostly match these patterns get a
# ``[str-date]`` / ``[str-datetime]`` / ``[str-time]`` marker in the
# ``columns_index`` so the agent knows to reach for date-wrap /
# year-extract / numeric-extract functions without probing values first.
# ``_DATE_RE`` and ``_DATETIME_RE`` are separately inspected by
# ``_date_format_hint`` to decide whether the sample carries a time
# component (boundary-precision-sensitive on string compare); ``_TIME_RE``
# is checked only when the sample carries no leading-date values.
_DATE_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?"  # YYYY-MM-DD / YYYY/MM/DD / YYYY-MM
    r"|^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}",  # YYYY-MM-DD HH:MM
)
_DATETIME_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}",  # YYYY-MM-DD HH:MM
)
# Pure time / duration patterns (no leading date): ``H:MM[:SS][.fff]`` and
# ``HH:MM[:SS][.fff]``. Covers lap times (``1:34.188``), elapsed times
# (``12:34:56``), wall-clock times (``08:00``), and response durations
# (``2:30.500``). Distinct from ``_DATETIME_RE`` (which requires a leading
# ``YYYY-MM-DD``) so a column whose values are *only* time/duration strings
# can be tagged ``[str-time]`` separately from date / datetime columns.
_TIME_RE = re.compile(
    r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$"  # 1:34.188 / 12:34:56 / 08:00
)


def _date_format_hint(col: dict[str, Any]) -> str | None:
    """Return the date-wrapping marker for *col*.

    Returns one of:
    - ``"str-date"`` — STRING-typed date column whose stored values are
      pure dates (``YYYY-MM-DD`` / ``YYYY/MM/DD`` / ``YYYY-MM``). Date
      functions (``YEAR``, ``MONTH``, ``TO_CHAR`` with format) return NULL
      silently on STRING in MaxCompute. The agent must reach for
      ``SUBSTR(col, 1, 4)``-style slicing or wrap with
      ``TO_DATE(col, 'yyyy-MM-dd')`` first. This is the single most common
      cause of "SQL ran but returned 0 rows" on this dialect.
    - ``"str-datetime"`` — STRING-typed temporal column whose stored values
      include a time component (``YYYY-MM-DD HH:MM[:SS[.f]]`` /
      ``YYYY-MM-DDTHH:MM…``). Same date-function trap as ``str-date``, plus
      a second trap: lexical comparison against a date-only literal
      (``col > '2014-09-01'``) mis-orders boundary rows — values like
      ``'2014-09-01 12:34:56'`` lex-compare as greater than ``'2014-09-01'``
      because the longer prefix-match sorts after, so a query meant to
      exclude that date silently includes it. Compare via
      ``SUBSTR(col, 1, 10) > 'YYYY-MM-DD'`` or
      ``TO_DATE(SUBSTR(col, 1, 10), 'yyyy-MM-dd')`` to recover date-level
      semantics.
    - ``"str-time"`` — STRING-typed pure-time / duration column whose
      stored values are clock or elapsed times without a leading date
      (``1:34.188`` lap times, ``12:34:56`` wall-clock, ``2:30.500``
      response-time durations). Two traps: (a) MaxCompute's date / time
      functions all return NULL on STRING — there is no
      ``HOUR(STRING_COL)`` recovery. (b) Lexical ORDER BY against a
      time string works only when every value has uniform width; a
      sample mixing ``'1:34.188'`` and ``'12:34.188'`` lex-sorts the
      one-digit-minute row first (``'1'`` < ``'2'`` byte-wise).
      Extract numeric components with ``SUBSTR`` /
      ``REGEXP_EXTRACT`` before comparing or aggregating; prefer the
      sibling ``*_ms`` / ``milliseconds`` BIGINT column when one exists.
    - ``"date"`` — non-STRING non-native-temporal column annotated as
      ``dim_type='time'`` (typically a unix-timestamp BIGINT that needs
      ``FROM_UNIXTIME`` wrapping). Distinct from ``str-date`` because the
      required wrap is different.
    - ``None`` — column is already native temporal (``:date`` / ``:datetime``
      type tag covers it), or doesn't look date-shaped.

    Two signal sources; the sample-driven heuristic wins over the
    confirmed annotation only on the date-vs-datetime variant choice
    (since ``dim_type='time'`` carries no precision info but
    ``format_examples`` do). When no samples are available the
    annotation alone decides at date-level precision.

    - Heuristic: STRING/VARCHAR/CHAR column whose ``format_examples``
      mostly look like dates. The 50%-majority threshold prevents false
      positives from a single coincidental match (e.g. a coded string
      like ``"2025-Q1"`` that happens to start with four digits). If
      *any* matching sample carries a time component, the column is
      conservatively marked ``str-datetime`` — even one boundary-sensitive
      row makes naive string-compare unsafe.
    - Confirmed: ``dim_type == 'time'`` on a non-native-temporal column.
      Native ``DATE`` / ``DATETIME`` / ``TIMESTAMP`` columns already render
      with their type tag, so a redundant marker would just add noise.
    """
    col_type = (col.get("type") or "").upper()
    head = col_type.split("(", 1)[0].split("<", 1)[0].strip()
    is_native_temporal = head in ("DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ")
    is_string = head in ("STRING", "VARCHAR", "CHAR")
    is_confirmed_time_dim = col.get("dim_type") == "time" and not is_native_temporal
    # Sample sniff runs first (only meaningful for STRING) — samples
    # carry sub-day precision info that the dim_type annotation does
    # not, so they get to refine the marker variant.
    if is_string:
        raw = col.get("sample_values_json")
        if raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    date_matches = 0
                    time_matches = 0
                    has_time = False
                    for v in parsed:
                        if not isinstance(v, str):
                            continue
                        vs = v.strip()
                        if _DATE_RE.match(vs):
                            date_matches += 1
                            if _DATETIME_RE.match(vs):
                                has_time = True
                        elif _TIME_RE.match(vs):
                            time_matches += 1
                    threshold = max(1, len(parsed) // 2)
                    # Date / datetime wins over pure-time when both
                    # patterns appear — a column with mostly dates and
                    # a few stray time-shaped values is still a date
                    # column for SQL purposes.
                    if date_matches >= threshold:
                        return "str-datetime" if has_time else "str-date"
                    if time_matches >= threshold:
                        return "str-time"
    # Fall back to confirmed annotation when samples didn't decide.
    if is_confirmed_time_dim:
        return "str-date" if is_string else "date"
    return None


def _stat_marker(col: dict[str, Any]) -> str | None:
    """Return the warning marker for a column whose profile stats say
    it can't be used as a filter or projection target.

    ``[null]`` for effectively-empty columns
    (``null_ratio >= _NULL_RATIO_WARNING_FLOOR``); ``[const]`` for
    columns with exactly one distinct non-NULL value. ``None`` when
    neither condition holds — the caller then falls back to the
    identifier marker. The warning markers take precedence because a
    PK/FK on a 100% NULL or constant column is meaningless to the
    agent's downstream SQL choice.
    """
    null_ratio = col.get("null_ratio")
    if null_ratio is not None and null_ratio >= _NULL_RATIO_WARNING_FLOOR:
        return "null"
    distinct = col.get("distinct_count")
    if distinct == 1:
        return "const"
    return None


def _resolve_column_marker(
    col: dict[str, Any],
    id_markers: dict[str, str],
) -> str | None:
    """Choose the marker to surface for *col* in ``columns_index``.

    Combines stat-derived warnings (``[null]`` / ``[const]``) with
    identifier markers (``[pk]`` / ``[fk]`` / ``[unique]``). Stat
    warnings normally win — see ``_stat_marker`` for the rationale.

    **Exception:** ``[const]`` derived from the 20-row ``LIMIT 20``
    sample (``phases.py``'s ``phase_column_sampling``) is unreliable
    for foreign-key columns. A FK column in a 1:n parent→child
    relationship naturally has many sampled rows pointing at the
    same parent ID — e.g. ``laboratory.id`` surfaces as ``[const]``
    from a sample where 20 lab rows happened to be for one patient,
    while the column actually carries hundreds of distinct patient
    IDs in the full table. When ``id_markers`` carries structural
    counter-evidence (``pk`` / ``fk`` / ``unique`` from confirmed
    annotation, ≥0.7 suggestion, or join-graph), the structural
    marker wins because constancy of an identifier is almost always
    a sampling artifact.

    ``[null]`` is not subject to this override — a 99% null sample
    is far more representative than a single-value batch of 20.
    """
    stat = _stat_marker(col)
    id_marker = id_markers.get(col["name"])
    if stat == "const" and id_marker in {"pk", "fk", "unique"}:
        return id_marker
    return stat or id_marker


# Wide-table ``columns_index`` cap. Telemetry / wide-flat tables can
# exceed 100 columns and would dominate every always-loaded overview
# read without a cap. 20 keeps the per-table entry compact while still
# fitting an annotated star-schema fact (typical: 1-2 PK / FK columns,
# 5-8 dimensions, 5-8 metrics).
_COLUMNS_INDEX_CAP = 20


def _signal_priority(col: dict[str, Any], id_marker: str | None) -> int:
    """Truncation priority for the wide-table ``columns_index`` cap.
    Lower wins (surfaces before the cap); stable sort preserves DDL
    order within each tier so a wide table without annotations is
    still rendered in its on-disk column order.

    The DDL column order is whatever order the table was created with;
    on catalog-style entity imports that puts alphabetically-early
    names first and buries semantically-important columns past the
    20-cap. Without this reorder, the agent reading
    ``_overview.md`` sees only the
    first-20-DDL slice and never learns those high-signal columns
    exist on the table — leading to wrong-table picks where the
    column the question is about lives on a partner table whose
    overview entry happens to surface it within the cap.

    Tiers:

    0. Confirmed ``semantic_role`` (``identifier`` / ``dimension`` /
       ``metric``) — the operator wrote this via ``mcs package apply``
       and the column is on the agent-facing semantic-layer surface.
       Must survive the cap.
    1. Identifier marker from suggestions / join graph
       (``pk`` / ``fk`` / ``unique``) — high-signal even without a
       confirmed annotation; the agent uses these for JOIN target
       selection.
    2. Carries a ``semantic_description`` without an annotation role
       (e.g. agent left a free-form comment but didn't classify).
    3. Default — no signal; falls to DDL order at the back of the line.
    """
    if col.get("semantic_role") in ("identifier", "dimension", "measure"):
        return 0
    if id_marker:
        return 1
    if col.get("semantic_description"):
        return 2
    return 3


# Inline-recipe table for date-format hints. The bare marker codes
# (``str-date`` etc.) were too compressed — empirically the agent saw
# ``[str-datetime]`` and still wrote ``col > '2014-09-01'`` (lex-compare
# trap) because the recipe lived in references/rules.md, not in the
# entry the agent was looking at. Expanding to ``<code>: <recipe>``
# inline puts the actionable wrap one token away from the column name.
# Keep recipes short — they share a single ``[ ... ]`` slot with the
# marker tag and have to remain scannable in 200-row columns_index
# dumps. Naming the wrap function explicitly is the load-bearing part;
# paraphrasing ("use a date function") undoes the lift.
_FORMAT_HINT_RECIPES: dict[str, str] = {
    "str-datetime": "compare via SUBSTR(c,1,10) > 'YYYY-MM-DD'",
    "str-date": "wrap with TO_DATE(c,'yyyy-MM-dd'); date fns return NULL on STRING",
    "str-time": "extract via SUBSTR/REGEXP_EXTRACT; HOUR(STRING) is NULL",
    "date": "wrap with FROM_UNIXTIME(c) when stored as BIGINT seconds",
}


def _format_hint_inline(hint: str) -> str:
    """Expand a bare hint code to ``<code>: <recipe>`` when known.

    Returns the bare code unchanged when no recipe is registered, so a
    future ``_date_format_hint`` variant lands in the entry as a marker
    even before its recipe is added — no crash, just a recipe gap.
    """
    recipe = _FORMAT_HINT_RECIPES.get(hint)
    return f"{hint}: {recipe}" if recipe else hint


def _format_columns_index_entry(
    col_name: str,
    col_type: str | None,
    marker: str | None,
    *,
    format_hint: str | None = None,
    description: str | None = None,
) -> str:
    """Render one ``columns_index`` entry.

    Formats: ``name[:type][ [marker]][ [format_hint]][  # description]``

    ``name`` alone is the default (STRING column, no hints). The
    ``:type`` suffix is added for actionable non-string types; the
    ``[marker]`` suffix is added when a high-confidence identifier
    suggestion exists; ``[date]`` is added when the column's format
    examples look like dates; a trailing ``  # description`` is added
    when a semantic_description carries meaning beyond the column name.
    """
    tag = _type_tag(col_type)
    parts = [col_name]
    if tag:
        parts.append(f":{tag}")
    if marker:
        parts.append(f" [{marker}]")
    if format_hint:
        parts.append(f" [{_format_hint_inline(format_hint)}]")
    if description:
        parts.append(f"  # {description}")
    return "".join(parts)


class MarkdownRenderer:
    """Reads PackageDB data and writes markdown projection files."""

    def __init__(
        self,
        db: PackageDB,
        profile: Profile,
        output_dir: Path,
        *,
        history_skipped: bool = False,
        tables_with_sample_sqls: int = 0,
        info_schema_source: str = "tenant",
    ) -> None:
        self._db = db
        self._profile = profile
        self._output_dir = output_dir
        self._history_skipped = history_skipped
        self._tables_with_sample_sqls = tables_with_sample_sqls
        self._info_schema_source = info_schema_source
        # Per-source tier cache: a multi-source profile may have a
        # 2-level source and a 3-level source coexisting, so each
        # source's tier is looked up from its own
        # ``tier_cache/<project>`` sentinel. The fallback for the
        # legacy ``.tier-level`` profile-wide sentinel is what
        # ``_read_tier_for_project`` does internally.
        self._tier_by_source_key: dict[str, str] = {
            src.source_key(): _read_tier_for_project(profile, src.project)
            for src in profile.sources
        }
        # The profile-level ``tier`` field shown in ``_overview.md``'s
        # frontmatter is the tier of the first source — kept as a
        # convenience for single-source users; multi-source users
        # should look at the per-source sections.
        self._primary_tier = (
            self._tier_by_source_key[profile.sources[0].source_key()] if profile.sources else "3"
        )

    def _frontmatter(self, data: dict) -> str:
        """Wrap a dict as YAML frontmatter between --- delimiters."""
        yaml_str = _yaml_dumps(data)
        # ruamel.yaml adds a trailing newline; trim it to avoid double newline
        # before the body separator.
        yaml_str = yaml_str.rstrip("\n")
        return f"---\n{yaml_str}\n---"

    def render_overview(self) -> None:
        """Write _overview.md with profile metadata, per-source table
        entries (with annotated tristate), and annotation_coverage.

        Frontmatter-only output (§6 body-drop), with one conditional
        exception: when the profile carries at least one top-level
        metric (``db.list_metrics()`` non-empty), a ``## Metrics``
        body section is appended after the closing ``---`` frontmatter
        delimiter so the agent sees the named metrics inventory in the
        always-loaded overview. Cold-start / no-metric profiles emit
        no body section, preserving the legacy shape.
        """
        all_tables = self._db.list_tables()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Annotation coverage rollup (§6)
        coverage = self._db.annotation_coverage(per_table=True)

        # Build a (source_key, table) → set[partner_table] map from the
        # joins table so each entry can carry its first-hop partners.
        # Without this the agent has to round-trip ``mcs show --table T``
        # per table just to discover which OTHER tables join in — and
        # may pick the wrong table for a question whose answer column
        # only exists on a join partner. Multi-source partners are
        # qualified as ``source_key.table`` so cross-source joins stay
        # unambiguous on bare-name lookup.
        #
        # Drop joins whose endpoint table doesn't actually exist in the
        # package — the ``loose_id`` heuristic (phases.py pattern 4)
        # emits "would join here" rows whose right_table is a stripped
        # base name like ``manager`` for ``employee.manager_id`` even
        # when no ``manager`` table exists in the profile. Surfacing
        # those phantoms in ``joins_to`` tells the agent a join is
        # available when it isn't — a semantic-layer correctness bug
        # that costs more than the missing diagnostic value.
        existing_tables: set[tuple[str, str]] = {(t["source_key"], t["name"]) for t in all_tables}
        # Materialize joins once — used both for the joins_by_endpoint
        # partner index and for per-column [pk]/[fk] marker derivation
        # in the per-table loop below. Calling ``list_joins`` per table
        # would O(tables × joins) scan the same SQLite-backed list.
        all_joins = self._db.list_joins()
        joins_by_endpoint: dict[tuple[str, str], set[str]] = {}
        for j in all_joins:
            left_key = (j["left_source_key"], j["left_table"])
            right_key = (j["right_source_key"], j["right_table"])
            if left_key not in existing_tables or right_key not in existing_tables:
                continue
            # cardinality is stored from the LEFT side's perspective:
            # ``1:n`` means left is the "1" parent, right is the "n"
            # fan-out child. For the RIGHT endpoint's entry we flip it
            # so the label always reads from the OWNING table's side.
            card_left = j.get("cardinality")
            card_right = _FLIP_CARDINALITY.get(card_left) if card_left else None
            joins_by_endpoint.setdefault(left_key, set()).add(
                _format_partner(
                    j["right_source_key"],
                    j["right_table"],
                    left_key[0],
                    j["left_col"],
                    cardinality=card_left,
                )
            )
            joins_by_endpoint.setdefault(right_key, set()).add(
                _format_partner(
                    j["left_source_key"],
                    j["left_table"],
                    right_key[0],
                    j["right_col"],
                    cardinality=card_right,
                )
            )

        # Build per-source tables array with annotated tristate (§6)
        sources_list: list[dict[str, Any]] = []
        for src in self._profile.sources:
            sk = src.source_key()
            src_tables = self._db.list_tables(source_key=sk)
            tier = self._tier_by_source_key[sk]
            table_entries: list[dict[str, Any]] = []
            for tbl in src_tables:
                cols = self._db.get_columns(tbl["id"])
                col_count = len(cols)
                partition_col = next((c["name"] for c in cols if c.get("is_partition")), None)
                partition_str = partition_col or "-"
                built_date = tbl["last_built_at"][:10] if tbl.get("last_built_at") else "-"
                # Annotated tristate from coverage per_table data (§6).
                # ``per_table`` is nested ``{source_key: {name: dict}}`` so
                # same-named tables across sources don't collide.
                tbl_name = tbl["name"]
                per_tbl = coverage.get("per_table", {}).get(sk, {}).get(tbl_name)
                annotated = per_tbl["tristate"] if per_tbl else "no"
                entry: dict[str, Any] = {
                    "name": tbl_name,
                    "columns_count": col_count,
                    "partition": partition_str,
                    "built": built_date,
                    "annotated": annotated,
                }
                # AI context — one-line description the annotation pass wrote
                # for this table. Surfacing it at overview level lets the
                # agent disambiguate candidate tables from the first round-
                # trip instead of paying per-table ``mcs show --table T``
                # calls just to read the context. Skipped when missing
                # (cold-start / pre-annotation overview).
                ai_ctx = tbl.get("ai_context")
                if ai_ctx:
                    entry["ai_context"] = ai_ctx
                # Column-name index — full list when small (≤ 20 cols),
                # otherwise the first 20 plus a single "..." sentinel.
                # The index turns "which table has a ``category`` column"
                # into a single overview read instead of a per-table
                # round-trip. 20-column cap keeps the overview compact
                # for the wide-table case (telemetry / wide-flat tables
                # sometimes exceed 100 cols and would blow up the
                # always-loaded payload otherwise).
                #
                # Entries carry three optional suffixes derived without
                # any external network call: a ``:type`` tag for non-
                # STRING types (``id:int``, ``created:datetime``,
                # ``amount:decimal``) so the agent reaches for
                # ``date(...)`` wraps and numeric casts without having
                # to round-trip ``mcs show --table T``; a
                # ``[pk]`` / ``[fk]`` / ``[unique]`` / ``[date]``
                # marker from identifier suggestions or format-example
                # inspection; and a trailing ``  # description`` comment
                # carrying the ``semantic_description`` (``a11:decimal
                #  # average salary``) so the agent sees column meaning
                # without drilling into per-table detail.
                user_cols = [c for c in cols if not c.get("is_partition")]
                id_markers = _identifier_markers_by_column(
                    self._db.list_annotation_suggestions(source_key=sk, table_name=tbl_name),
                    columns=user_cols,
                    joins=all_joins,
                    source_key=sk,
                    table_name=tbl_name,
                )
                # Wide-table reorder: when the table has more user
                # columns than the cap (20), DDL-order truncation would
                # silently drop annotated PK/FK/dim/metric columns that
                # happen to be defined past the cap. The stable sort by
                # ``_signal_priority`` lifts signal-bearing columns into
                # the surviving window while preserving DDL order
                # within each priority tier. Tables at/under the cap
                # skip the sort so cold-start narrow tables render in
                # exactly the DDL order they did before this change.
                if len(user_cols) > _COLUMNS_INDEX_CAP:
                    display_cols = sorted(
                        user_cols,
                        key=lambda c: _signal_priority(c, id_markers.get(c.get("name", ""))),
                    )
                else:
                    display_cols = user_cols
                col_entries = [
                    _format_columns_index_entry(
                        c["name"],
                        c.get("type"),
                        # Stat-derived warnings (``[null]`` / ``[const]``)
                        # normally take precedence over identifier markers
                        # — a PK or FK marker on an effectively-empty or
                        # constant column would tell the agent to reach
                        # for a column that can't carry filter or
                        # projection weight, which is worse than no
                        # marker at all.
                        #
                        # Exception: ``[const]`` derived from the 20-row
                        # ``LIMIT 20`` sample (phases.py phase_column_sampling)
                        # is unreliable for foreign-key columns. A FK
                        # column in a "1:n" parent→child relationship
                        # naturally has many sampled rows pointing at
                        # the same parent ID — e.g. ``laboratory.id``
                        # surfaces as ``[const]`` from a sample where all
                        # 20 lab tests happened to be for one patient,
                        # while the column actually has hundreds of
                        # distinct patient IDs in the full table. When
                        # ``id_markers`` has structural counter-evidence
                        # (``pk`` / ``fk`` / ``unique`` from confirmed
                        # annotation, ≥0.7 suggestion, or join-graph),
                        # the structural marker wins because constancy
                        # of an identifier is almost always a sampling
                        # artifact. ``[null]`` is not subject to this
                        # override — a 99% null sample is far more
                        # representative than distinct=1.
                        _resolve_column_marker(c, id_markers),
                        format_hint=_date_format_hint(c),
                        description=c.get("semantic_description"),
                    )
                    for c in display_cols
                ]
                if len(col_entries) > _COLUMNS_INDEX_CAP:
                    col_entries = col_entries[:_COLUMNS_INDEX_CAP] + ["..."]
                entry["columns_index"] = col_entries
                # First-hop join partners — sorted for stable output.
                partners = sorted(joins_by_endpoint.get((sk, tbl_name), set()))
                if partners:
                    entry["joins_to"] = partners
                table_entries.append(entry)
            sources_list.append(
                {
                    "source_key": sk,
                    "project": src.project,
                    "schema": src.schema,
                    "tier": f"{tier}-level",
                    "tables": table_entries,
                }
            )

        frontmatter: dict[str, Any] = {
            "compute_project": self._profile.compute_project,
            "endpoint": self._profile.endpoint,
            "tier": f"{self._primary_tier}-level",
            "tables": len(all_tables),
            "last_built": now,
            "annotation_coverage": {
                "tables_total": coverage["tables_total"],
                "tables_with_ai_context": coverage["tables_with_ai_context"],
                "tables_with_any_column_role": coverage["tables_with_any_column_role"],
                "columns_total": coverage["columns_total"],
                "columns_with_role": coverage["columns_with_role"],
            },
            "sources": sources_list,
        }

        # Surface the user's scenario in the overview frontmatter when set.
        if self._profile.description:
            frontmatter["description"] = self._profile.description

        # Frontmatter-only output (§6 body-drop), with the conditional
        # ``## Metrics`` exception described in the docstring.
        content = self._frontmatter(frontmatter) + "\n"
        metrics = self._db.list_metrics()
        if metrics:
            # Each entry: ``- **name** — `expression``` plus an optional
            # one-line description. Only the first line of any
            # description is inlined so a multi-line annotation doesn't
            # blow up the always-loaded overview.
            lines: list[str] = ["", "## Metrics", ""]
            for m in metrics:
                desc = m.get("description") or ""
                first_line = desc.split("\n", 1)[0].strip()
                lines.append(f"- **{m['name']}** — `{m['expression']}`")
                if first_line:
                    lines.append(f"  {first_line}")
            content += "\n".join(lines) + "\n"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        (self._output_dir / "_overview.md").write_text(content, encoding="utf-8")

    def render_table(self, source_key: str, table_name: str) -> None:
        """Write ``<output_dir>/<source_key>/<table>.md`` for a single
        ``(source, table)`` pair. Same-named tables under different
        sources land in distinct subdirs and don't collide.
        """
        table_row = self._db.get_table(source_key, table_name)
        if table_row is None:
            return

        cols = self._db.get_columns(table_row["id"])

        # Build YAML frontmatter columns list.
        fm_columns = []
        for col in cols:
            col_entry: dict[str, Any] = {
                "name": col["name"],
                "type": col["type"],
                "comment": col.get("comment") or "",
                "is_partition": bool(col.get("is_partition", 0)),
                "is_enum": bool(col.get("is_enum", 0)),
            }
            if col.get("null_ratio") is not None:
                col_entry["null_ratio"] = _round_null_ratio(col["null_ratio"])
            if col.get("distinct_count") is not None:
                col_entry["distinct_count"] = col["distinct_count"]
            if col.get("sample_values_json"):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    parsed_samples = json.loads(col["sample_values_json"])
                    if col.get("is_enum"):
                        col_entry["sample_values"] = parsed_samples
                    elif parsed_samples:
                        # For non-enum columns the full distinct set is not
                        # captured, but a couple of stored shapes still help
                        # the agent see the on-disk format — e.g. STRING-typed
                        # timestamps like ``'2014-09-01 12:34:56'`` vs bare
                        # dates ``'2014-09-01'``, or coded strings like
                        # ``'CZK'`` / ``'EUR'``. Capping at 3 keeps it a
                        # format hint, not a value enumeration.
                        col_entry["format_examples"] = parsed_samples[:3]
            # §3 annotation: semantic_description
            if col.get("semantic_description"):
                col_entry["semantic_description"] = col["semantic_description"]
            fmt_hint = _date_format_hint(col)
            if fmt_hint:
                col_entry["format_hint"] = fmt_hint
            fm_columns.append(col_entry)

        source = _source_for_key(self._profile, source_key)
        project = source.project if source else self._profile.compute_project
        schema = source.schema if source else "default"
        tier = self._tier_by_source_key.get(source_key, self._primary_tier)

        # Build annotation-derived top-level arrays (§5) via the shared
        # helper so the on-disk frontmatter matches what
        # ``mcs show --table`` emits in JSON.
        dimensions, metrics, identifiers = build_role_groups(cols)

        # Sample SQLs for this (source, table). Only ``user_verified``
        # entries land in either ``sample_sqls`` (literal, copyable) or
        # ``sample_sql_patterns`` (with shape/frequency metadata) —
        # mined patterns are dropped entirely below.
        sample_sql_entries = self._db.list_sample_sqls(
            source_key=source_key,
            table=table_name,
            limit=5,
        )
        # Only ``user_verified`` patterns are emitted. Earlier iterations
        # tried to keep mined patterns with progressively stronger
        # defenses (drop the literal ``sql`` field, redact projection
        # + JOIN ``ON`` columns to ``<col>``, suppress structured
        # ``join_edges``) so the agent could see workload-frequency
        # stats without copy-pasting bad SQL. Each layer still leaked:
        # the ``canonical_sql`` shape alone was enough for the agent
        # to template-match — smoke runs caught three with-history
        # regressions (over-joining to extra dimension tables when the
        # mined SQL did so, degenerate self-joins on a results table
        # when the mined SQL did so, copy-pasted ``district_id =
        # (SELECT ... LIMIT 1)`` subqueries that miss the surrounding
        # ``GROUP BY`` semantics) where the predicted SQL is
        # structurally isomorphic to a singleton mined pattern stored
        # in the same table's markdown. ``where_predicates`` is also
        # a leak vector:
        # e.g. ``sd.skill_name = ?`` showing up in ``entity.md``
        # tells the agent the table is commonly filtered by a column
        # it doesn't even own, biasing the agent toward joining the
        # owning table without semantic motivation.
        #
        # The agent retains workload signal from the ``joins`` block
        # (per-table relationship graph with explicit confidence) and
        # column-level descriptions / annotations — both are
        # column- or relationship-level facts, not query templates.
        # User-verified patterns survive because they're explicit
        # endorsements: someone ran ``mcs memory verify`` to mark
        # that SQL correct for a real question.
        sample_sqls: list[str] = []
        sample_sql_patterns: list[dict[str, Any]] = []
        for entry in sample_sql_entries:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                payload = json.loads(entry["payload_json"])
                if not isinstance(payload, dict):
                    continue
                confidence = payload.get("confidence", "mined_low")
                if confidence != "user_verified":
                    continue
                sql = payload.get("sql")
                if isinstance(sql, str) and sql:
                    sample_sqls.append(sql)
                if payload.get("shape_key"):
                    sample_sql_patterns.append(
                        {
                            "canonical_sql": payload.get("canonical_sql", ""),
                            "shape_key": payload.get("shape_key", ""),
                            "normalizer_version": int(payload.get("normalizer_version") or 0),
                            "frequency": int(payload.get("frequency") or 1),
                            "verified_count": int(payload.get("verified_count") or 0),
                            "confidence": confidence,
                            "provenance": payload.get("provenance", "user_verified"),
                            "where_predicates": payload.get("where_predicates") or [],
                            "join_edges": payload.get("join_edges") or [],
                            "sql": sql if isinstance(sql, str) and sql else "",
                        }
                    )
        sample_sql_patterns.sort(
            key=lambda item: (
                item["confidence"] != "user_verified",
                -item["verified_count"],
                -item["frequency"],
                item["shape_key"],
            ),
        )

        # Join candidates for this (source, table). Trim each row to
        # the agent-relevant keys — see ``trim_join_candidate``.
        #
        # Drop candidates whose right_table doesn't actually exist in
        # the package — same loose_id phantom problem as
        # ``render_overview`` / ``render_joins``: the heuristic emits
        # "would join here" rows whose right_table is a literal
        # stripped basename (e.g. ``manager`` for ``employee.manager_id``)
        # even when no ``manager`` table is in the profile. Without the
        # filter the agent's ``mcs show --table T`` returns join
        # candidates against tables that don't exist, which is worse
        # than no suggestion at all.
        existing_tables_for_jc: set[tuple[str, str]] = {
            (t["source_key"], t["name"]) for t in self._db.list_tables()
        }
        join_candidates = [
            trim_join_candidate(jc, owner_source_key=source_key)
            for jc in self._db.list_join_candidates(
                left_source_key=source_key,
                left_table=table_name,
            )
            if (jc.get("right_source_key", ""), jc.get("right_table", "")) in existing_tables_for_jc
        ]
        # Annotation suggestions for this (source, table). Trim each
        # row to the agent-relevant keys — see
        # ``trim_annotation_suggestion`` — and drop rows below the
        # ``_SUGGESTION_MIN_CONFIDENCE`` floor. The classifier emits a
        # ``pattern: fallback / source: name_heuristic`` row at ~0.35
        # confidence for every column it has no opinion on; those rows
        # carry no actionable signal but inflate the per-table .md so
        # much they push the high-signal entries past the agent's
        # always-loaded preview window.
        #
        # ``annotated_cols`` carries the set of columns the annotation
        # pass has already classified (dimension/metric/identifier).
        # For those columns we strip ``where_count`` from any
        # ``history_sql`` evidence entry — the role assignment in the
        # confirmed block carries the load-bearing signal, while
        # ``where_count`` on an already-confirmed column biases the
        # agent toward gratuitous WHERE clauses (the rtype over-filter
        # case in california_schools 0274).
        annotated_cols: set[str] = (
            {d["name"] for d in dimensions}
            | {m["name"] for m in metrics}
            | {i["name"] for i in identifiers}
        )
        suggestions: list[dict[str, Any]] = []
        for s in self._db.list_annotation_suggestions(
            source_key=source_key,
            table_name=table_name,
        ):
            if (s.get("confidence") or 0.0) < _SUGGESTION_MIN_CONFIDENCE:
                continue
            trimmed = trim_annotation_suggestion(
                s,
                owner_source_key=source_key,
                strip_filter_evidence=s["column_name"] in annotated_cols,
            )
            if trimmed is not None:
                suggestions.append(trimmed)

        # Key order mirrors the ``mcs show --table T`` JSON envelope —
        # annotations and identifiers come BEFORE the bulk ``columns``
        # array so the load-bearing semantic-layer signal lands inside
        # Claude Code's persisted-output preview window even when the
        # agent reads the on-disk file via ``cat`` / ``Read`` rather
        # than the structured JSON form. A 74-column ``cards`` table
        # used to bury ``identifiers[].type: primary`` past 13 KB of
        # YAML; this reorder keeps it near the top. Empty lists
        # (verified_queries, sample_sqls, sample_sql_patterns) are
        # dropped entirely so they don't push real fields down.
        partition_columns = [col["name"] for col in cols if col.get("is_partition")]
        frontmatter: dict[str, Any] = {
            "name": table_name,
            "source_key": source_key,
            "project": project,
            "schema": schema,
            "tier": f"{tier}-level",
            "schema_hash": table_row.get("schema_hash", ""),
        }
        ai_context_val = table_row.get("ai_context")
        if ai_context_val:
            frontmatter["ai_context"] = ai_context_val
        if dimensions:
            frontmatter["dimensions"] = dimensions
        if metrics:
            frontmatter["metrics"] = metrics
        if identifiers:
            frontmatter["identifiers"] = identifiers
        if partition_columns:
            frontmatter["partition_columns"] = partition_columns
        if join_candidates:
            frontmatter["join_candidates"] = join_candidates
        if suggestions:
            frontmatter["annotation_suggestions"] = suggestions
        if sample_sqls:
            frontmatter["sample_sqls"] = sample_sqls
        if sample_sql_patterns:
            frontmatter["sample_sql_patterns"] = sample_sql_patterns
        # ``columns`` lands last — the bulk array that would otherwise
        # eclipse the preview window on wide tables.
        frontmatter["columns"] = fm_columns

        # Frontmatter-only output (§5 body-drop)
        content = self._frontmatter(frontmatter) + "\n"
        # Per-source subdir so same-named tables under different sources
        # write to distinct files.
        out = self._output_dir / source_key
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{table_name}.md").write_text(content, encoding="utf-8")

    def render_joins(self) -> None:
        """Write _joins.md as frontmatter-only with relationships key
        (§6 body-drop). Cross-source pairs are qualified with source_key
        on both the ``from`` and ``to`` objects.

        Drops join rows whose endpoint table doesn't exist in the
        package (mirrors the ``joins_to`` filter in ``render_overview``)
        so the loose_id heuristic's "would join here" phantoms don't
        land in the agent-facing relationships list. The diagnostic
        value of those rows (FK-column identification in the annotation
        suggestion ranker) is preserved — they stay in the joins table
        for downstream consumers like ``join_candidates.py``.
        """
        existing_tables: set[tuple[str, str]] = {
            (t["source_key"], t["name"]) for t in self._db.list_tables()
        }
        joins = self._db.list_joins()
        relationships: list[dict[str, Any]] = []
        for j in joins:
            left_sk = j.get("left_source_key", "")
            right_sk = j.get("right_source_key", "")
            if (left_sk, j["left_table"]) not in existing_tables:
                continue
            if (right_sk, j["right_table"]) not in existing_tables:
                continue
            cross = left_sk and right_sk and left_sk != right_sk
            left_table = f"{left_sk}.{j['left_table']}" if cross else j["left_table"]
            right_table = f"{right_sk}.{j['right_table']}" if cross else j["right_table"]
            entry: dict[str, Any] = {
                "from": {"table": left_table, "column": j["left_col"]},
                "to": {"table": right_table, "column": j["right_col"]},
                "cardinality": j.get("cardinality") or "-",
                "inferred_via": j.get("kind") or "-",
                "confidence": _round_confidence(j.get("confidence", 0.0)),
                "cross_source": cross,
            }
            relationships.append(entry)

        frontmatter: dict[str, Any] = {
            "last_built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "relationships": relationships,
        }
        content = self._frontmatter(frontmatter) + "\n"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        (self._output_dir / "_joins.md").write_text(content, encoding="utf-8")

    def render_udfs(self) -> None:
        """Write _udfs.md as frontmatter-only with udfs key (§6 body-drop)."""
        udfs = self._db.list_udfs()
        udf_entries: list[dict[str, Any]] = []
        for u in udfs:
            udf_entries.append(
                {
                    "name": u["name"],
                    "kind": u["kind"],
                    "signature": u.get("signature") or "-",
                    "class_name": u.get("class_name") or "-",
                    "comment": u.get("description") or "-",
                }
            )
        frontmatter: dict[str, Any] = {
            "last_built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "udfs": udf_entries,
        }
        content = self._frontmatter(frontmatter) + "\n"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        (self._output_dir / "_udfs.md").write_text(content, encoding="utf-8")

    def render_state(self) -> None:
        """Write ``_state.json`` reflecting the current PackageDB state.

        Called by ``render_all()`` after the full bundle is written, and
        by the annotate commands after a single-table / column annotation
        so the eval verifier's ``annotate-arm`` polarity check sees
        updated ``annotation_coverage``.
        """
        # Read existing state to preserve fields set by the build
        # pipeline that we don't track locally (history_skipped,
        # tables_with_sample_sqls, info_schema_source, errors).
        existing: dict[str, Any] = {}
        state_path = self._output_dir / "_state.json"
        if state_path.exists():
            with contextlib.suppress(json.JSONDecodeError):
                existing = json.loads(state_path.read_text(encoding="utf-8"))

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sources_state: dict[str, Any] = {}
        for src in self._profile.sources:
            sk = src.source_key()
            src_tables = self._db.list_tables(source_key=sk)
            sources_state[sk] = {
                "project": src.project,
                "schema": src.schema,
                "tier": self._tier_by_source_key[sk],
                "tables_count": len(src_tables),
            }
        coverage = self._db.annotation_coverage(per_table=False)
        # ``joins_count`` must match what ``render_joins`` emits — drop
        # phantom-table edges (the loose_id heuristic stamps "would-join"
        # markers whose right-side endpoint doesn't exist in the
        # package). Otherwise ``_state.json`` over-reports vs the
        # actual ``relationships:`` list in ``_joins.md``.
        existing_tables_for_joins: set[tuple[str, str]] = {
            (t["source_key"], t["name"]) for t in self._db.list_tables()
        }
        joins_visible = sum(
            1
            for j in self._db.list_joins()
            if (j.get("left_source_key", ""), j["left_table"]) in existing_tables_for_joins
            and (j.get("right_source_key", ""), j["right_table"]) in existing_tables_for_joins
        )
        state: dict[str, Any] = {
            "version": 5,
            "last_built_at": now,
            "sources": sources_state,
            "udfs_count": len(self._db.list_udfs()),
            "joins_count": joins_visible,
            "history_skipped": existing.get(
                "history_skipped",
                self._history_skipped,
            ),
            "tables_with_sample_sqls": existing.get(
                "tables_with_sample_sqls",
                self._tables_with_sample_sqls,
            ),
            "info_schema_source": existing.get(
                "info_schema_source",
                self._info_schema_source,
            ),
            "annotation_coverage": {
                "tables_total": coverage["tables_total"],
                "tables_with_ai_context": coverage["tables_with_ai_context"],
                "tables_with_any_column_role": coverage["tables_with_any_column_role"],
                "columns_total": coverage["columns_total"],
                "columns_with_role": coverage["columns_with_role"],
            },
            "errors": existing.get("errors", []),
        }
        self._output_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, indent=2) + "\n",
            encoding="utf-8",
        )

    def render_all(self) -> None:
        """Render the full markdown bundle: ``_overview`` / ``_joins``
        / ``_udfs`` (top-level) plus per-(source, table) ``.md`` under
        the source_key subdir, plus the v4 ``_state.json``.
        """
        self.render_overview()
        self.render_joins()
        self.render_udfs()
        for tbl in self._db.list_tables():
            self.render_table(tbl["source_key"], tbl["name"])

        self.render_state()


def render_all(
    db: PackageDB,
    profile: Profile,
    *,
    history_skipped: bool = False,
    tables_with_sample_sqls: int = 0,
    info_schema_source: str = "tenant",
) -> None:
    """Module-level entry point: resolve output dir and delegate to MarkdownRenderer."""
    output_dir = profile_data_dir(profile)
    renderer = MarkdownRenderer(
        db,
        profile,
        output_dir,
        history_skipped=history_skipped,
        tables_with_sample_sqls=tables_with_sample_sqls,
        info_schema_source=info_schema_source,
    )
    renderer.render_all()
