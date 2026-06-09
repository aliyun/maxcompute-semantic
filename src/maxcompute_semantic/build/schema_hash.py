# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""SHA-256 column hash for refresh diff — only name+type participate."""

from __future__ import annotations

import hashlib


def schema_hash(columns: list[dict]) -> str:
    """Compute stable SHA-256 hash of column list.

    Only ``name`` + ``type`` (case-normalized to upper) participate.
    Comments, sample_values, and other enrichment fields are excluded
    so that doc-only edits don't trigger a rebuild.
    """
    canonical = sorted((c["name"].lower(), c["type"].upper()) for c in columns)
    payload = "|".join(f"{n}:{t}" for n, t in canonical)
    return hashlib.sha256(payload.encode()).hexdigest()
