# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Bidirectional vocabulary map between mcs internal terms and OSI YAML.

Used by ``export.py`` (mcs -> OSI) and (in v2) ``import_.py`` (OSI -> mcs).
The map is the single source of truth; both directions read from the same
dict so symmetry is enforced by construction.
"""

from __future__ import annotations

OSI_SCHEMA_VERSION = "0.2.0.dev0"

# Maps mcs internal field name -> OSI field name. Used by export.
# Concepts that have no OSI native slot (semantic_role, id_type, etc.)
# are NOT in this dict — they go into custom_extensions; see export.py.
MCS_TO_OSI: dict[str, str] = {
    # PackageDB table -> OSI top-level
    "tables": "datasets",
    "columns": "fields",
    "joins": "relationships",
    # Field-level direct renames where OSI has a native slot
    "semantic_description": "description",
    # Join column conventions (mcs left_/right_ -> OSI from/to)
    "left_table": "from_dataset",
    "left_col": "from_columns",
    "right_table": "to_dataset",
    "right_col": "to_columns",
    # Dataset-level direct
    "source_key": "source",
}

# Reverse map for v2 import.
OSI_TO_MCS: dict[str, str] = {osi: mcs for mcs, osi in MCS_TO_OSI.items()}

# mcs concepts that travel inside OSI custom_extensions[].data because
# OSI has no native slot for them. Listed here so the export and (v2)
# import layers stay in lockstep.
CUSTOM_EXTENSION_FIELDS: frozenset[str] = frozenset(
    {
        "semantic_role",
        "id_type",
        "references_target",
        "physical_type",
        "is_partition",
        # v10 — measure-specific data nested under custom_extensions.data.measure
        # so a consumer that wants the "aggregable via SUM" semantics doesn't
        # have to parse role-string overload. ``agg`` is kept at top level too
        # for one release of back-compat with the v0.11 reader (none exists
        # yet — slated for removal in v0.13).
        "measure",
        "agg",
    }
)

CUSTOM_EXTENSION_VENDOR: str = "MAXCOMPUTE_SEMANTIC"
