"""Tests for memory/tokenizer.py — MemoryTokenizer (jieba + Latin regex)."""

from __future__ import annotations

from maxcompute_semantic.memory.tokenizer import MemoryTokenizer


class TestMemoryTokenizer:
    def test_tokenize_for_index_returns_string(self) -> None:
        tok = MemoryTokenizer()
        result = tok.tokenize_for_index("card games have foil cards")
        assert isinstance(result, str)
        parts = result.split()
        assert "card" in parts
        assert "games" in parts
        assert "foil" in parts

    def test_tokenize_for_index_lowercases(self) -> None:
        tok = MemoryTokenizer()
        parts = tok.tokenize_for_index("Card Games FOIL").split()
        assert "card" in parts
        assert "games" in parts
        assert "foil" in parts
        assert "Card" not in parts

    def test_tokenize_for_index_handles_chinese(self) -> None:
        tok = MemoryTokenizer()
        result = tok.tokenize_for_index("数据分析报告")
        parts = result.split()
        assert len(parts) > 0
        # At least one multi-char segment proves jieba ran (not single-char fallback)
        assert any(len(p) > 1 for p in parts)

    def test_tokenize_for_index_mixed_cjk_and_latin(self) -> None:
        tok = MemoryTokenizer()
        parts = tok.tokenize_for_index("使用card_games表分析").split()
        assert "card_games" in parts
        assert any(any("一" <= c <= "鿿" for c in p) for p in parts)

    def test_tokenize_for_index_empty_string(self) -> None:
        tok = MemoryTokenizer()
        assert tok.tokenize_for_index("") == ""

    def test_tokenize_for_index_whitespace_normalized(self) -> None:
        """Tabs, newlines, multiple spaces collapse to single space."""
        tok = MemoryTokenizer()
        result = tok.tokenize_for_index("a\t\tb\n\nc   d")
        parts = result.split()
        assert parts == ["a", "b", "c", "d"]

    def test_tokenize_for_index_preserves_identifier_underscore(self) -> None:
        tok = MemoryTokenizer()
        parts = tok.tokenize_for_index("SELECT user_id FROM t_users").split()
        assert "user_id" in parts
        assert "t_users" in parts

    def test_tokenize_for_index_strips_double_quotes(self) -> None:
        """Double-quotes would confuse FTS5 MATCH; strip on insert too."""
        tok = MemoryTokenizer()
        parts = tok.tokenize_for_index('he said "hi"').split()
        for p in parts:
            assert '"' not in p

    def test_tokenize_for_index_preserves_numbers(self) -> None:
        tok = MemoryTokenizer()
        parts = tok.tokenize_for_index("select count 10 from table42").split()
        assert "10" in parts
        assert "table42" in parts

    def test_tokenize_for_query_symmetric_with_index(self) -> None:
        """Same text run through both paths produces identical token sequence."""
        tok = MemoryTokenizer()
        text = "查询user_id from card_games表"
        assert tok.tokenize_for_index(text) == tok.tokenize_for_query(text)

    def test_tokenize_for_query_empty_returns_empty(self) -> None:
        tok = MemoryTokenizer()
        assert tok.tokenize_for_query("") == ""
        assert tok.tokenize_for_query("   ").strip() == ""
