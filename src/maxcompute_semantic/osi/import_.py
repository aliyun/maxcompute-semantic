"""OSI -> mcs import (deferred to v2)."""

from __future__ import annotations

from typing import Any


def from_osi_dict(data: dict[str, Any], db: Any) -> None:
    """Translate OSI YAML into PackageDB rows.

    Deferred to v2 per the adapter spec; see
    ``docs/superpowers/specs/2026-05-26-mcs-osi-adapter-design.md`` §11.
    """
    raise NotImplementedError(
        "OSI import deferred to v2; see docs/superpowers/specs/2026-05-26-mcs-osi-adapter-design.md"
    )
