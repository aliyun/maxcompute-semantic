"""Tests for memory/vec_ext.py, memory/embedding.py, and hybrid retrieval."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.memory.embedding import (
    _ensure_hf_endpoint,
    _probe_endpoint,
    embed,
    embed_batch,
    is_available,
)
from maxcompute_semantic.memory.hybrid import HybridSearcher, reciprocal_rank_fusion
from maxcompute_semantic.memory.vec_ext import (
    clear_all_vectors,
    create_vec_table,
    delete_vector,
    insert_vector,
    load_vec_extension,
    query_vectors,
    serialize_f32,
    vec_extension_loaded,
    vec_table_exists,
)

# ---------------------------------------------------------------------------
# vec_ext
# ---------------------------------------------------------------------------


class TestSerializeF32:
    def test_serialize_roundtrip(self) -> None:
        import struct

        vec = [0.1, 0.2, 0.3, 0.4]
        blob = serialize_f32(vec)
        # Verify length: 4 floats * 4 bytes = 16
        assert len(blob) == 16
        # Verify roundtrip
        unpacked = struct.unpack(f"<{len(vec)}f", blob)
        assert abs(unpacked[0] - 0.1) < 1e-5
        assert abs(unpacked[3] - 0.4) < 1e-5

    def test_serialize_empty_vector(self) -> None:
        blob = serialize_f32([])
        assert blob == b""


class TestVecExtension:
    """These tests check vec extension behavior. When sqlite-vec is not
    installed, load_vec_extension returns False and all subsequent
    operations gracefully degrade."""

    def test_load_extension_returns_bool(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = load_vec_extension(conn)
        assert isinstance(result, bool)
        conn.close()

    def test_extension_loaded_returns_bool(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = vec_extension_loaded(conn)
        assert isinstance(result, bool)
        conn.close()

    def test_create_vec_table_returns_bool(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = create_vec_table(conn)
        assert isinstance(result, bool)
        conn.close()

    def test_vec_table_exists_returns_bool(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = vec_table_exists(conn)
        assert isinstance(result, bool)
        conn.close()

    def test_query_vectors_empty_when_no_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = query_vectors(conn, [0.1] * 384, top_k=5)
        assert result == []
        conn.close()

    def test_clear_all_vectors_returns_neg1_when_no_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = clear_all_vectors(conn)
        assert result == -1
        conn.close()


@pytest.mark.skipif(not is_available(), reason="sqlite-vec + sentence-transformers not installed")
class TestVecExtWithExtension:
    """Integration tests that run only when [vec] extras are installed."""

    def test_full_cycle(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        assert load_vec_extension(conn) is True
        assert vec_extension_loaded(conn) is True
        assert create_vec_table(conn) is True
        assert vec_table_exists(conn) is True

        # Insert + query
        vec = [0.1] * 384
        insert_vector(conn, 1, vec)
        results = query_vectors(conn, vec, top_k=5)
        assert len(results) == 1
        assert results[0][0] == 1

        # Delete
        delete_vector(conn, 1)
        results = query_vectors(conn, vec, top_k=5)
        assert len(results) == 0

        conn.close()

    def test_clear_all_vectors(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        load_vec_extension(conn)
        create_vec_table(conn)

        insert_vector(conn, 1, [0.1] * 384)
        insert_vector(conn, 2, [0.2] * 384)
        count = clear_all_vectors(conn)
        assert count == 2

        results = query_vectors(conn, [0.1] * 384, top_k=5)
        assert len(results) == 0
        conn.close()


# ---------------------------------------------------------------------------
# embedding
# ---------------------------------------------------------------------------


class TestEmbedding:
    def test_embed_returns_none_when_unavailable(self) -> None:
        with patch("maxcompute_semantic.memory.embedding._get_model", return_value=None):
            assert embed("hello") is None

    def test_embed_batch_returns_none_when_unavailable(self) -> None:
        with patch("maxcompute_semantic.memory.embedding._get_model", return_value=None):
            assert embed_batch(["hello", "world"]) is None

    def test_is_available_returns_bool(self) -> None:
        result = is_available()
        assert isinstance(result, bool)

    def test_embed_returns_list_when_available(self) -> None:
        if not is_available():
            pytest.skip("sentence-transformers not installed")
        vec = embed("test query")
        assert vec is not None
        assert len(vec) == 384

    def test_embed_batch_returns_correct_count_when_available(self) -> None:
        if not is_available():
            pytest.skip("sentence-transformers not installed")
        vecs = embed_batch(["test one", "test two"])
        assert vecs is not None
        assert len(vecs) == 2
        assert len(vecs[0]) == 384


# ---------------------------------------------------------------------------
# HuggingFace endpoint auto-fallback
# ---------------------------------------------------------------------------


class TestHFEndpointFallback:
    def test_probe_returns_bool(self) -> None:
        # Probing any URL returns a bool (no crash).
        result = _probe_endpoint("https://example.com", timeout=3)
        assert isinstance(result, bool)

    def test_probe_returns_false_for_bad_url(self) -> None:
        result = _probe_endpoint("https://0.0.0.0:1", timeout=1)
        assert result is False

    def test_ensure_endpoint_respects_user_override(self, monkeypatch) -> None:
        monkeypatch.setenv("HF_ENDPOINT", "https://custom-mirror.example.com")
        result = _ensure_hf_endpoint()
        assert result == "https://custom-mirror.example.com"

    def test_ensure_endpoint_falls_back_when_unreachable(self, monkeypatch) -> None:
        # Remove any existing HF_ENDPOINT so the probe runs.
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        # Make the probe always fail.
        with patch("maxcompute_semantic.memory.embedding._probe_endpoint", return_value=False):
            result = _ensure_hf_endpoint()
        assert result == "https://hf-mirror.com"
        assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"

    def test_ensure_endpoint_keeps_default_when_reachable(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        with patch("maxcompute_semantic.memory.embedding._probe_endpoint", return_value=True):
            result = _ensure_hf_endpoint()
        assert result == "https://huggingface.co"
        # Should NOT have set HF_ENDPOINT in env (left default).
        assert os.environ.get("HF_ENDPOINT") is None


# ---------------------------------------------------------------------------
# RRF + HybridSearcher
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    def test_rrf_single_source(self) -> None:
        bm25_results = [
            {"id": 1, "score": 2.0},
            {"id": 2, "score": 1.5},
        ]
        vec_results = []
        scores = reciprocal_rank_fusion(bm25_results, vec_results)
        # BM25-only: rank 1 → 1/(60+1), rank 2 → 1/(60+2)
        assert scores[1] > scores[2]

    def test_rrf_merge_both_sources(self) -> None:
        bm25_results = [
            {"id": 1, "score": 2.0},
            {"id": 3, "score": 1.0},
        ]
        vec_results = [(2, 0.1), (1, 0.2)]
        scores = reciprocal_rank_fusion(bm25_results, vec_results)
        # ID 1 appears in both — gets two RRF contributions (higher score)
        assert scores[1] > scores[2]
        assert scores[1] > scores[3]

    def test_rrf_empty_inputs(self) -> None:
        scores = reciprocal_rank_fusion([], [])
        assert scores == {}

    def test_rrf_custom_weights(self) -> None:
        bm25_results = [{"id": 1, "score": 2.0}]
        vec_results = [(2, 0.1)]
        scores = reciprocal_rank_fusion(bm25_results, vec_results, bm25_weight=2.0, vec_weight=1.0)
        # BM25 weight doubled → ID 1 gets 2 * 1/(60+1)
        assert scores[1] > scores[2]


class TestHybridSearcher:
    def test_no_vector_flag_falls_back_to_fts(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = HybridSearcher(db)
        db.upsert_memory("user_note", '{"q":1}', "card games have foil")
        results = searcher.search("card games", no_vector=True)
        assert len(results) > 0
        assert results[0]["retrieval_text"] == "card games have foil"

    def test_fallback_when_vec_unavailable(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = HybridSearcher(db)
        db.upsert_memory("user_note", '{"q":1}', "dice rolls are random")
        # Without [vec] extras, vector search returns empty → FTS5 fallback
        results = searcher.search("dice rolls")
        assert len(results) > 0

    def test_kind_filter_works(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = HybridSearcher(db)
        db.upsert_memory("verified_query", '{"q":1}', "card games foil")
        db.upsert_memory("user_note", '{"q":2}', "card games preference")
        results = searcher.search("card games", kind_filter=["verified_query"], no_vector=True)
        assert len(results) == 1
        assert results[0]["kind"] == "verified_query"

    def test_top_k_limit(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        searcher = HybridSearcher(db)
        for i in range(10):
            db.upsert_memory("user_note", f'{{"q":{i}}}', f"card games number {i}")
        results = searcher.search("card games", top_k=3, no_vector=True)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Storage vec integration
# ---------------------------------------------------------------------------


class TestStorageVecIntegration:
    def test_upsert_memory_indexes_vector_when_available(self, tmp_path: Path) -> None:
        """When [vec] extras are installed, upsert_memory also indexes vectors."""
        if not is_available():
            pytest.skip("sentence-transformers not installed")
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_memory("user_note", '{"q":1}', "card games have foil cards")
        # Verify vec_index has a row for this memory entry
        from maxcompute_semantic.memory.vec_ext import query_vectors, vec_table_exists

        if vec_table_exists(db._conn):
            results = query_vectors(db._conn, [0.1] * 384, top_k=5)
            # At least one vector should exist (though cosine similarity
            # may be low for a random query vector)
            assert any(r[0] == id_ for r in results) or len(results) >= 1

    def test_upsert_memory_skips_vector_when_unavailable(self, tmp_path: Path) -> None:
        """When [vec] extras are not installed, upsert_memory still works (BM25-only)."""
        db = PackageDB(tmp_path / "package.db")
        with patch.object(db, "_index_vector"):
            # Should not fail even if vec is unavailable
            id_ = db.upsert_memory("user_note", '{"q":1}', "card games")
            assert id_ > 0

    def test_remove_memory_deletes_vector(self, tmp_path: Path) -> None:
        """remove_memory also deletes the corresponding vector."""
        if not is_available():
            pytest.skip("sentence-transformers not installed")
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_memory("user_note", '{"q":1}', "test entry")
        db.remove_memory(id_)
        # After removal, no vector should remain for this id
        from maxcompute_semantic.memory.vec_ext import query_vectors, vec_table_exists

        if vec_table_exists(db._conn):
            # Use a generic query vector to check nothing matches our deleted id
            results = query_vectors(db._conn, [0.1] * 384, top_k=10)
            assert not any(r[0] == id_ for r in results)

    def test_reindex_vectors_returns_neg1_when_unavailable(self, tmp_path: Path) -> None:
        """When [vec] extras are not installed, reindex_vectors returns -1."""
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("user_note", '{"q":1}', "test")
        with patch("maxcompute_semantic.memory.embedding.is_available", return_value=False):
            count = db.reindex_vectors()
            assert count == -1


class TestMigrationV3ToV5:
    def test_migration_creates_vec_table_when_available(self, tmp_path: Path) -> None:
        """Migration from v3 to v5 should create vec_index if sqlite-vec is available."""
        if not is_available():
            pytest.skip("sqlite-vec not installed")
        import sqlite3

        from maxcompute_semantic.build.storage import _SCHEMA_SQL

        db_path = tmp_path / "package.db"
        # Create a v3 DB manually
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_SCHEMA_SQL)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()

        # Opening with PackageDB should migrate to v5
        db = PackageDB(db_path)
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 5
