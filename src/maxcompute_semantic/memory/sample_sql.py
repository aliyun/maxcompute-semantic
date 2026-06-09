"""Persist mined SQL from TASKS_HISTORY as sample_sql memory entries.

Called by the build pipeline after phase_mine_history succeeds.
Only clears and rebuilds sample_sql entries for the target source_key;
entries for other sources in multi-source profiles are preserved.

Unlike ``verified_queries`` (which are user-confirmed correct queries
stored via ``mcs memory verify``), sample_sql entries are unverified
historical queries mined from INFORMATION_SCHEMA.TASKS_HISTORY. Mined
candidates with the same normalized SQL shape are aggregated into one
pattern entry carrying ``frequency`` and ``verified_count`` so the
agent can rank trust before reusing them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from maxcompute_semantic.memory.sql_pattern import SqlPattern, analyze_sql_pattern


@dataclass(frozen=True)
class PersistSampleSqlResult:
    created: int
    touched_tables: set[str]


def confidence_for_counts(*, frequency: int, verified_count: int) -> str:
    if verified_count > 0:
        return "user_verified"
    if frequency >= 3:
        return "mined_high"
    if frequency == 2:
        return "mined_medium"
    return "mined_low"


def sample_sql_retrieval_text(payload: dict[str, Any]) -> str:
    prefix = f"{payload['source_key']}:{payload['table']}"
    return (
        f"sample_sql for {prefix}: "
        f"confidence={payload['confidence']} "
        f"frequency={payload['frequency']} "
        f"verified_count={payload['verified_count']} "
        f"canonical={payload['canonical_sql']} "
        f"representative={payload['representative_sql']}"
    )


def persist_sample_sqls(
    db: Any,
    sample_sql_candidates: dict[str, list[str]],
    source_key: str,
) -> PersistSampleSqlResult:
    """Persist mined SQL as grouped, source-scoped sample_sql patterns."""
    old_tables = db.sample_sql_table_names_for_source(source_key)
    db.clear_sample_sqls_for_source(source_key)
    verified_counts = db.verified_shape_counts_for_source(source_key)

    grouped: dict[tuple[str, str], list[tuple[str, SqlPattern]]] = defaultdict(list)
    for table_name, sqls in sample_sql_candidates.items():
        for sql in sqls:
            pattern = analyze_sql_pattern(sql)
            grouped[(table_name, pattern.shape_key)].append((sql, pattern))

    count = 0
    new_tables: set[str] = set()
    for (table_name, shape_key), items in sorted(grouped.items()):
        sqls = [sql for sql, _pattern in items]
        pattern = items[0][1]
        frequency = len(items)
        verified_count = verified_counts.get((table_name, shape_key), 0)
        payload = {
            "table": table_name,
            "source_key": source_key,
            "sql": sqls[0],
            "representative_sql": sqls[0],
            "representative_sqls": sqls[:3],
            "canonical_sql": pattern.canonical_sql,
            "shape_key": shape_key,
            "normalizer_version": pattern.normalizer_version,
            "frequency": frequency,
            "verified_count": verified_count,
            "confidence": confidence_for_counts(
                frequency=frequency,
                verified_count=verified_count,
            ),
            "provenance": "mined_history",
            "tables": list(pattern.tables),
            "where_predicates": list(pattern.where_predicates),
            "join_edges": list(pattern.join_edges),
            "parse_error": pattern.parse_error,
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        db.upsert_memory("sample_sql", payload_json, sample_sql_retrieval_text(payload))
        new_tables.add(table_name)
        count += 1

    return PersistSampleSqlResult(
        created=count,
        touched_tables=old_tables | new_tables,
    )
