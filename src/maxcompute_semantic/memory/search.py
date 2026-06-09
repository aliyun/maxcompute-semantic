"""FTS5Searcher — memory retrieval via SQLite FTS5 + bm25() ranking.

Query path:
  1. Tokenize the user's query via MemoryTokenizer (same path as index).
  2. Each token is double-quoted to keep FTS5 reserved words (AND, OR,
     NOT, NEAR, etc.) from being parsed as operators.
  3. Tokens are joined with `OR`, applied to memory_fts MATCH.
  4. bm25() returns negative scores (smaller = better); we flip the
     sign so callers see positive scores in descending relevance order
     — matching the legacy BM25Searcher contract.

Kind filtering is pushed into SQL via `IN (...)` so FTS5 candidate
selection runs once instead of post-filtering in Python.
"""

from __future__ import annotations

from typing import Any

from maxcompute_semantic.memory.tokenizer import MemoryTokenizer


class FTS5Searcher:
    """BM25 retrieval over memory_entries via the memory_fts FTS5 virtual table."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._tokenizer = MemoryTokenizer()

    def search(
        self,
        query: str,
        kind_filter: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        fts_query = self._tokenizer.tokenize_for_query(query)
        tokens = [t for t in fts_query.split() if t]
        if not tokens:
            return []
        match_expr = " OR ".join(f'"{t}"' for t in tokens)

        sql = (
            "SELECT m.id, m.kind, m.payload_json, m.retrieval_text, "
            "m.tags_json, m.created_at, bm25(memory_fts) AS raw_score "
            "FROM memory_fts "
            "JOIN memory_entries m ON m.id = memory_fts.rowid "
            "WHERE memory_fts MATCH ?"
        )
        params: list[Any] = [match_expr]
        if kind_filter:
            placeholders = ",".join("?" for _ in kind_filter)
            sql += f" AND m.kind IN ({placeholders})"
            params.extend(kind_filter)
        sql += " ORDER BY raw_score LIMIT ?"
        params.append(top_k)

        rows = self._db._conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "score": round(-r["raw_score"], 6),
                "payload_json": r["payload_json"],
                "retrieval_text": r["retrieval_text"],
                "tags_json": r["tags_json"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
