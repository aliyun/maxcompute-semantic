# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client.sql_preprocess.split_set_hints."""

from __future__ import annotations

from maxcompute_semantic.mc_client.sql_preprocess import split_set_hints


class TestSplitSetHints:
    def test_extracts_set_and_preserves_select_verbatim(self) -> None:
        sql = "SET odps.sql.mapper.split.size = 4096; SELECT a, b FROM t WHERE ds='20240101'"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT a, b FROM t WHERE ds='20240101'"
        assert hints == {"odps.sql.mapper.split.size": "4096"}

    def test_standalone_set_yields_empty_sql(self) -> None:
        stripped, hints = split_set_hints("SET odps.sql.mapper.split.size=4096")
        assert stripped == ""
        assert hints == {"odps.sql.mapper.split.size": "4096"}

    def test_multiple_sets_all_extracted(self) -> None:
        sql = "SET odps.sql.allow.fullscan = true; SET odps.sql.reducer.memory = 4096; SELECT x FROM t"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT x FROM t"
        assert hints == {"odps.sql.allow.fullscan": "TRUE", "odps.sql.reducer.memory": "4096"}

    def test_semicolon_inside_string_literal_is_not_a_separator(self) -> None:
        sql = "SET a=1; SELECT x FROM t WHERE s='a;b;c'"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT x FROM t WHERE s='a;b;c'"
        assert hints == {"a": "1"}

    def test_set_label_is_not_extracted_stays_verbatim(self) -> None:
        sql = "SET LABEL tbl TO user; SELECT 1"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SET LABEL tbl TO user; SELECT 1"
        assert hints == {}

    def test_no_set_returns_unchanged(self) -> None:
        stripped, hints = split_set_hints("SELECT 1 FROM dual")
        assert stripped == "SELECT 1 FROM dual"
        assert hints == {}

    def test_non_select_preserved_verbatim_no_function_rewrite(self) -> None:
        # TO_CHAR must NOT be rewritten to CAST — regeneration is lossy.
        sql = "set odps.sql.type.system.odps2 = true; SELECT TO_CHAR(d, 'yyyyMMdd') FROM t"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT TO_CHAR(d, 'yyyyMMdd') FROM t"
        assert hints == {"odps.sql.type.system.odps2": "TRUE"}

    def test_trailing_semicolon_handled(self) -> None:
        stripped, hints = split_set_hints("SET a=1; SELECT 1;")
        assert stripped == "SELECT 1"
        assert hints == {"a": "1"}

    def test_duplicate_set_keys_last_wins(self) -> None:
        sql = "SET a=1; SET a=2; SELECT 1"
        stripped, hints = split_set_hints(sql)
        assert stripped == "SELECT 1"
        assert hints == {"a": "2"}

    def test_multi_statement_no_set_preserved_verbatim(self) -> None:
        # No SETs extracted -> original SQL returned untouched (newlines
        # between statements preserved, not reformatted to "; ").
        sql = "SELECT 1;\nSELECT 2"
        stripped, hints = split_set_hints(sql)
        assert stripped == sql
        assert hints == {}
