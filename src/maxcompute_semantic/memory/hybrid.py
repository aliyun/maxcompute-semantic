# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""HybridSearcher — BM25 (FTS5) + vector retrieval via Reciprocal Rank Fusion.

Falls back to FTS5-only when vector search is unavailable (no
sentence-transformers, no sqlite-vec extension, or no vec_index
table). The lexical component is :class:`FTS5Searcher`; the vector
component is wired through :mod:`memory.vec_ext` + :mod:`memory.embedding`.
"""

from __future__ import annotations

from typing import Any

from maxcompute_semantic.memory.search import FTS5Searcher


def reciprocal_rank_fusion(
    bm25_results: list[dict[str, Any]],
    vec_results: list[tuple[int, float]],
    k: int = 60,
    bm25_weight: float = 1.0,
    vec_weight: float = 1.0,
) -> dict[int, float]:
    """Merge two ranked result sets via Reciprocal Rank Fusion.

    For each result list, contribution to memory_id ``mid`` at rank
    ``r`` (1-based) is ``weight / (k + r)``. The standard RRF constant
    ``k=60`` damps the head while keeping tail signal.
    """
    scores: dict[int, float] = {}
    for rank, result in enumerate(bm25_results, 1):
        mid = result["id"]
        scores[mid] = scores.get(mid, 0.0) + bm25_weight / (k + rank)
    for rank, (rowid, _distance) in enumerate(vec_results, 1):
        scores[rowid] = scores.get(rowid, 0.0) + vec_weight / (k + rank)
    return scores


class HybridSearcher:
    """FTS5 BM25 + vector search merged via Reciprocal Rank Fusion.

    Falls back to FTS5-only when vector search is unavailable.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._fts = FTS5Searcher(db)

    def search(
        self,
        query: str,
        kind_filter: list[str] | None = None,
        top_k: int = 5,
        no_vector: bool = False,
    ) -> list[dict[str, Any]]:
        """Return top-K results using hybrid FTS5 + vector search.

        When ``no_vector=True`` or vector search is unavailable, falls
        back to pure FTS5.
        """
        fts_results = self._fts.search(query, kind_filter=kind_filter, top_k=top_k * 3)

        if no_vector:
            return fts_results[:top_k]

        vec_results = self._vector_search(query, top_k=top_k * 3)
        if not vec_results:
            return fts_results[:top_k]

        rrf_scores = reciprocal_rank_fusion(fts_results, vec_results)

        candidate_ids = set(rrf_scores.keys())
        entries: dict[int, dict] = {}
        for mid in candidate_ids:
            entry = self._db.get_memory(mid)
            if entry is None:
                continue
            if kind_filter and entry["kind"] not in kind_filter:
                continue
            entries[mid] = entry

        scored = [
            {
                "id": mid,
                "kind": entries[mid]["kind"],
                "score": round(rrf_scores[mid], 6),
                "payload_json": entries[mid]["payload_json"],
                "retrieval_text": entries[mid]["retrieval_text"],
                "tags_json": entries[mid]["tags_json"],
                "created_at": entries[mid]["created_at"],
            }
            for mid in sorted(rrf_scores, key=lambda mid: rrf_scores[mid], reverse=True)
            if mid in entries
        ]
        return scored[:top_k]

    def _vector_search(
        self,
        query: str,
        top_k: int = 15,
    ) -> list[tuple[int, float]]:
        """Run vector search, return (rowid, distance) pairs or empty list."""
        from maxcompute_semantic.memory.embedding import embed
        from maxcompute_semantic.memory.vec_ext import query_vectors

        query_embedding = embed(query)
        if query_embedding is None:
            return []

        return query_vectors(self._db._conn, query_embedding, top_k=top_k)
