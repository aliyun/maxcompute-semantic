"""Tests for memory/search.py — FTS5Searcher."""

from __future__ import annotations

from pathlib import Path

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.memory.search import FTS5Searcher


class TestFTS5Searcher:
    def test_search_returns_hit(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("verified_query", '{"q":1}', "card games have foil cards")
        db.upsert_memory("user_note", '{"q":2}', "dice rolls are random")
        results = searcher.search("card games foil")
        assert len(results) >= 1
        assert results[0]["retrieval_text"] == "card games have foil cards"

    def test_search_kind_filter_single(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("verified_query", '{"q":1}', "card games foil")
        db.upsert_memory("user_note", '{"q":2}', "card games preference")
        results = searcher.search("card games", kind_filter=["verified_query"])
        assert len(results) == 1
        assert results[0]["kind"] == "verified_query"

    def test_search_kind_filter_multi(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("verified_query", '{"q":1}', "card games foil")
        db.upsert_memory("user_note", '{"q":2}', "card games preference")
        db.upsert_memory("package_doc", '{"q":3}', "card games table")
        results = searcher.search("card games", kind_filter=["verified_query", "user_note"])
        kinds = {r["kind"] for r in results}
        assert kinds == {"verified_query", "user_note"}

    def test_search_top_k(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        for i in range(10):
            db.upsert_memory("user_note", f'{{"q":{i}}}', f"card games number {i}")
        results = searcher.search("card games", top_k=3)
        assert len(results) == 3

    def test_search_empty_db(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        assert searcher.search("anything") == []

    def test_search_no_hits(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("user_note", '{"q":1}', "dice rolls are random")
        assert searcher.search("quantum physics") == []

    def test_search_empty_query(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("user_note", '{"q":1}', "foo")
        assert searcher.search("") == []
        assert searcher.search("   ") == []

    def test_search_chinese_query_chinese_doc(self, tmp_path: Path) -> None:
        """jieba pre-tokenization makes CJK searchable as multi-char tokens."""
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("user_note", '{"q":1}', "事务日志分析报告")
        results = searcher.search("事务日志")
        assert len(results) >= 1

    def test_search_identifier_with_underscore(self, tmp_path: Path) -> None:
        """user_id stays as one token, not split on _."""
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("verified_query", '{"q":1}', "SELECT user_id FROM t_users")
        results = searcher.search("user_id")
        assert len(results) >= 1

    def test_search_query_with_fts5_reserved_word(self, tmp_path: Path) -> None:
        """Query like 'AND OR NOT' must not crash FTS5 MATCH parsing —
        double-quoted tokens neutralize operator semantics."""
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("user_note", '{"q":1}', "this contains and or not literally")
        results = searcher.search("AND OR NOT")
        assert isinstance(results, list)

    def test_search_score_positive_descending(self, tmp_path: Path) -> None:
        """bm25() returns negative numbers (smaller = better); searcher
        flips the sign so consumers see positive scores in descending order."""
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        db.upsert_memory("user_note", '{"q":1}', "card games card games card")
        db.upsert_memory("user_note", '{"q":2}', "card alone here")
        results = searcher.search("card games")
        assert len(results) == 2
        assert all(r["score"] > 0 for r in results)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_result_shape(self, tmp_path: Path) -> None:
        """Return shape must match the legacy BM25Searcher dict shape."""
        db = PackageDB(tmp_path / "package.db")
        searcher = FTS5Searcher(db)
        id_ = db.upsert_memory("verified_query", '{"q":1}', "card games foil")
        results = searcher.search("card")
        assert len(results) == 1
        r = results[0]
        assert r["id"] == id_
        assert r["kind"] == "verified_query"
        assert isinstance(r["score"], float)
        assert r["payload_json"] == '{"q":1}'
        assert r["retrieval_text"] == "card games foil"
        assert "created_at" in r
        assert "tags_json" in r
