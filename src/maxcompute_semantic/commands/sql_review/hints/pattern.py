"""Hint — SQL shape matches a previously-verified query in memory.

Fires when the input SQL has the same canonical shape (literal-insensitive
``shape_key`` from :func:`maxcompute_semantic.memory.sql_pattern.analyze_sql_pattern`)
as a ``verified_query`` memory entry. The verified entry has already been
marked correct by the user via ``mcs memory verify``, so a same-shape
match is a strong "this is a known-good pattern" signal — confidence is
fixed at ``high``.

Implementation notes:

- ``shape_key`` is **not** stored in the memory payload by the canonical
  writer (``mcs memory verify`` persists ``question`` / ``sql`` /
  ``table_refs`` / ``evidence_text`` only). The hint therefore recomputes
  the shape from ``payload["sql"]`` for each candidate row, mirroring how
  ``PackageDB.verified_shape_counts_for_source`` already derives the key
  on the storage side. Reading ``payload["shape_key"]`` directly would
  make the hint dead code against real verify output.
- Empty ``shape_key`` (an unparseable input SQL — see
  :func:`analyze_sql_pattern`'s fallback path) short-circuits with no
  hints. A SQL the analyzer cannot parse cannot be matched against any
  stored pattern in a meaningful way; the matching `dialect.*` /
  `schema.*` / `tier.*` rules will surface the underlying syntax issue.
- Per-hint confidence is fixed; multiple matching rows emit multiple
  hints. The dispatcher (Task 13) is the right place to dedupe if a
  noisy verified-memory store ever produces stacks of identical hints.
- Memory rows other than ``kind="verified_query"`` are filtered at the
  query layer via ``list_memories(kind="verified_query", ...)``.

Emission contract: this rule emits one hint per matching ``verified_query``
row — no collapsing, no "most-recent only" heuristic. The dispatcher
(``build_review_envelope`` in Task 13) is responsible for any
deduplication, capping, or "show top N" rendering when a single SQL
matches many verified queries; rule code stays single-purpose. To keep
that downstream story actionable, every emitted hint carries the
matching memory row's ``id`` as ``verified_id`` in ``evidence`` and
exposes an ``if_misleading`` string referencing
``mcs memory show <id>`` / ``mcs memory remove <id>`` so the agent has
a concrete handle on the prior entry without re-walking the memory
store.
"""

from __future__ import annotations

import json

from maxcompute_semantic.commands.sql_review.types import Hint, ReviewContext
from maxcompute_semantic.memory.sql_pattern import analyze_sql_pattern


def hint_verified_match(ctx: ReviewContext) -> list[Hint]:
    pattern = analyze_sql_pattern(ctx.sql)
    if not pattern.shape_key:
        return []
    hints: list[Hint] = []
    for row in ctx.db.list_memories(kind="verified_query", limit=200):
        verified_id = row.get("id")
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            continue
        verified_sql = payload.get("sql")
        if not isinstance(verified_sql, str) or not verified_sql:
            continue
        verified_pattern = analyze_sql_pattern(verified_sql)
        if not verified_pattern.shape_key:
            continue
        if verified_pattern.shape_key != pattern.shape_key:
            continue
        question = payload.get("question") or "<unknown>"
        # Only build the if_misleading reference when we actually have
        # an id to point the agent at — defensive against rows without
        # an id field. Setting it to None on the Hint ctor matches the
        # dataclass default and keeps to_dict() from emitting the key.
        kwargs: dict = {
            "kind": "pattern.verified-match",
            "message": (
                f'This SQL has the same shape as a previously-verified query for: "{question}"'
            ),
            "confidence": "high",
            "evidence": {
                "verified_id": verified_id,
                "verified_question": question,
                "verified_sql": verified_sql,
                "shape_key": pattern.shape_key,
            },
        }
        if verified_id is not None:
            kwargs["if_misleading"] = (
                f"if the previous verified query is wrong, inspect or "
                f"remove it: `mcs memory show {verified_id}` or "
                f"`mcs memory remove {verified_id}` "
                f"(see `mcs memory --help`)"
            )
        hints.append(Hint(**kwargs))
    return hints
