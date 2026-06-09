# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""DEPRECATED: import from :mod:`maxcompute_semantic.errors` instead.

This module is a thin re-export shim kept for one release cycle so
existing callers don't break during the errors-consolidation
migration. PR2 of the consolidation will delete it.
"""

from __future__ import annotations

from maxcompute_semantic.errors.base import McsError  # noqa: F401
from maxcompute_semantic.errors.versioning import (  # noqa: F401
    GitNotAvailable,
    LockedByOtherProcessError,
    PackageSqlCorrupt,
    ProfileReadOnly,
    StaleLockClearedWarning,
)
