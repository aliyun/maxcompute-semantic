# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Inference-logic version stamp.

Bump :data:`INFERENCE_LOGIC_VERSION` whenever a change to the *post-
sample* inference layer would make existing on-disk profiles produce
different output if re-derived from the same column / sample inputs.

The stamp is written into ``package_settings.inference_logic_version``
by ``BuildPipeline._run_full`` and read by ``BuildPipeline._run_refresh``
to decide whether the refresh path should additionally re-run Phase 7c
+ re-render the per-table markdown bundle offline (no MaxCompute
round-trips). It is also surfaced by ``mcs doctor`` so a user who has
just upgraded the CLI can see at a glance which profiles need a
``mcs build --refresh`` to reconcile.

Bump for changes to:

- :mod:`maxcompute_semantic.build.semantic_suggestions` — Phase 7c
  classification (numeric→metric, string→dimension, etc.)
- :mod:`maxcompute_semantic.build._naming` — column-name regex
  heuristics consumed by Phase 7c
- :mod:`maxcompute_semantic.build.markdown` — per-table render logic
  (column hint formatting, sample SQL ordering, frontmatter shape)
- :mod:`maxcompute_semantic.build.workload` — aggregation thresholds
  (``min_shape_frequency``, top-k cutoffs) that feed Phase 7c

Do **not** bump for changes to: sampling / profiling SQL, MaxCompute
client code, CLI surface, doctor checks, or anything that requires
re-hitting MaxCompute to take effect (those changes need a full
``mcs build``, not the offline refresh path).
"""

from __future__ import annotations

INFERENCE_LOGIC_VERSION = 1
