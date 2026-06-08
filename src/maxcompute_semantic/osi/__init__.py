# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""OSI (Open Semantic Interchange) interop adapter.

This package translates between mcs's internal vocabulary
(tables / columns / joins / semantic_role / dim_type / ...) and
OSI YAML at the export / import boundary only. mcs internals
keep their existing names; OSI vocabulary appears only in the
output of ``mcs profile export --osi`` and the input of the
v2 ``mcs profile import --osi``.

Pinned OSI schema version: see ``OSI_SCHEMA_VERSION``.
"""

from maxcompute_semantic.osi.export import dump_yaml, to_osi_dict
from maxcompute_semantic.osi.vocabulary import OSI_SCHEMA_VERSION

__all__ = ["OSI_SCHEMA_VERSION", "dump_yaml", "to_osi_dict"]
