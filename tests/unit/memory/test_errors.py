# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory/errors.py — MemoryNotFoundError."""

from __future__ import annotations

from maxcompute_semantic.memory.errors import MemoryNotFoundError


class TestMemoryNotFoundError:
    def test_inherits_mcs_error(self) -> None:
        err = MemoryNotFoundError("entry 42 not found")
        # Wire code normalized to PascalCase ("MemoryNotFound") in the
        # errors-consolidation work; was "MEMORY_NOT_FOUND" before.
        assert err.code == "MemoryNotFound"
        assert err.exit_code == 1
        assert "entry 42 not found" in str(err)

    def test_is_mcs_error_subclass(self) -> None:
        from maxcompute_semantic.auth.errors import McsError

        assert issubclass(MemoryNotFoundError, McsError)
