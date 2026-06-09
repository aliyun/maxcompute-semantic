"""Memory-subsystem error classes."""

from __future__ import annotations

from maxcompute_semantic.errors.base import ErrorCode, McsError


class MemoryNotFoundError(McsError):
    """Requested memory entry does not exist.

    Wire code normalized to ``MemoryNotFound`` (PascalCase) in the
    errors-consolidation work — the previous value
    ``MEMORY_NOT_FOUND`` (CONSTANT_CASE) was inconsistent with every
    other class in the codebase. No known consumer was bucket-matching
    on the old value.
    """

    code = ErrorCode.MEMORY_NOT_FOUND
    exit_code = 1
