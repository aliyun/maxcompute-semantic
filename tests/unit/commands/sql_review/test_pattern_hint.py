"""Tests for pattern hint generator (`hints/pattern.py`).

The hint fires when the input SQL has the same shape (literal-insensitive
canonical form, see ``memory.sql_pattern.analyze_sql_pattern``) as a
``verified_query`` memory entry. The shape_key for the stored entry is
recomputed from ``payload["sql"]`` rather than read from the payload —
``mcs memory verify`` (the canonical writer) does not currently persist
``shape_key`` into the payload (it stores ``question``, ``sql``,
``table_refs``, ``evidence_text``), so reading ``payload["shape_key"]``
would make the hint dead code against real verify output. Mirrors how
``PackageDB.verified_shape_counts_for_source`` already derives the key
from the stored SQL.
"""

from __future__ import annotations

import json

from maxcompute_semantic.commands.sql_review.hints.pattern import (
    hint_verified_match,
)


def _verified_payload(question: str, sql: str, table: str = "orders") -> str:
    """Render a payload mirroring ``mcs memory verify`` output."""
    return json.dumps(
        {
            "question": question,
            "sql": sql,
            "table_refs": [{"source_key": "rev_proj__default", "table": table}],
            "evidence_text": "",
        }
    )


class TestVerifiedMatch:
    def test_matching_shape_emits_hint(self, make_review_package, make_review_ctx) -> None:
        """Same shape modulo literals → one hint quoting the question."""
        verified_sql = "SELECT * FROM orders WHERE id = 1"
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[
                {
                    "kind": "verified_query",
                    "payload_json": _verified_payload("fetch order by id", verified_sql),
                    "retrieval_text": verified_sql,
                    "tags_json": "[]",
                }
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM orders WHERE id = 42", profile, db_path)
        try:
            hints = hint_verified_match(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "pattern.verified-match"
            assert hints[0].confidence == "high"
            assert "fetch order by id" in hints[0].message
            assert hints[0].evidence["verified_question"] == "fetch order by id"
            assert hints[0].evidence["verified_sql"] == verified_sql
            assert hints[0].evidence["shape_key"]
        finally:
            db.close()

    def test_evidence_carries_verified_id(self, make_review_package, make_review_ctx) -> None:
        """The dispatcher in Task 13 needs verified_id to format actionable
        references; pin it here so a future refactor that drops the field
        surfaces immediately rather than at envelope-assembly time."""
        verified_sql = "SELECT * FROM orders WHERE id = 1"
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[
                {
                    "kind": "verified_query",
                    "payload_json": _verified_payload("fetch order by id", verified_sql),
                    "retrieval_text": verified_sql,
                    "tags_json": "[]",
                }
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM orders WHERE id = 42", profile, db_path)
        try:
            hints = hint_verified_match(ctx)
            assert len(hints) == 1
            assert "verified_id" in hints[0].evidence
            assert hints[0].evidence["verified_id"] is not None
            assert hints[0].if_misleading is not None
            assert "mcs memory show" in hints[0].if_misleading
            assert "mcs memory remove" in hints[0].if_misleading
            # The id surfaced in if_misleading must be the same one
            # in evidence — agents will copy/paste from either.
            assert str(hints[0].evidence["verified_id"]) in hints[0].if_misleading
        finally:
            db.close()

    def test_no_match_no_hint(self, make_review_package, make_review_ctx) -> None:
        """Empty memories → empty hint list."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[],
        )
        db, ctx = make_review_ctx("SELECT * FROM orders WHERE id = 42", profile, db_path)
        try:
            assert hint_verified_match(ctx) == []
        finally:
            db.close()

    def test_different_shape_no_hint(self, make_review_package, make_review_ctx) -> None:
        """Confirm shape comparison is real, not 'any verified query'."""
        verified_sql = "SELECT * FROM orders"  # no WHERE
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[
                {
                    "kind": "verified_query",
                    "payload_json": _verified_payload("list orders", verified_sql),
                    "retrieval_text": verified_sql,
                    "tags_json": "[]",
                }
            ],
        )
        db, ctx = make_review_ctx("SELECT id FROM orders WHERE id > 1", profile, db_path)
        try:
            assert hint_verified_match(ctx) == []
        finally:
            db.close()

    def test_other_kinds_ignored(self, make_review_package, make_review_ctx) -> None:
        """A non-verified_query memory whose SQL would match must not fire."""
        verified_sql = "SELECT * FROM orders WHERE id = 1"
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[
                {
                    "kind": "failed_query",
                    "payload_json": _verified_payload("would-match question", verified_sql),
                    "retrieval_text": verified_sql,
                    "tags_json": "[]",
                }
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM orders WHERE id = 42", profile, db_path)
        try:
            assert hint_verified_match(ctx) == []
        finally:
            db.close()

    def test_multiple_matches_all_emit(self, make_review_package, make_review_ctx) -> None:
        """Per-row iteration: two same-shape verified rows → two hints."""
        sql_a = "SELECT * FROM orders WHERE id = 1"
        sql_b = "SELECT * FROM orders WHERE id = 99"
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[
                {
                    "kind": "verified_query",
                    "payload_json": _verified_payload("question A", sql_a),
                    "retrieval_text": sql_a,
                    "tags_json": "[]",
                },
                {
                    "kind": "verified_query",
                    "payload_json": _verified_payload("question B", sql_b),
                    "retrieval_text": sql_b,
                    "tags_json": "[]",
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM orders WHERE id = 42", profile, db_path)
        try:
            hints = hint_verified_match(ctx)
            assert len(hints) == 2
            questions = sorted(h.evidence["verified_question"] for h in hints)
            assert questions == ["question A", "question B"]
        finally:
            db.close()
