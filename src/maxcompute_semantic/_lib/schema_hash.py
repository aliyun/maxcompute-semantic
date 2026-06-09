"""Stable schema hash for refresh-diff detection."""

from __future__ import annotations

import hashlib
import json


def schema_hash(columns: list[dict]) -> str:
    """SHA-256 hex of a canonicalized column list.

    ``columns`` is the list returned in ``mcs meta describe-table``'s
    ``data.table.schema`` (or ``partition_columns``). Only ``name`` +
    ``type`` participate — we intentionally ignore comments so a
    doc-only edit doesn't trip refresh. Order-independent: columns
    are sorted by name first.
    """
    canonical = [
        {"name": c.get("name", ""), "type": (c.get("type") or "").upper()} for c in columns
    ]
    canonical.sort(key=lambda c: c["name"])
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
