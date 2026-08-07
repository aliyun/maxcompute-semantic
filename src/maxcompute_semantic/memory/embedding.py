# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Embedding computation for vector search.

Lazy-loads sentence-transformers on first call. Returns None when
the optional [vec] extras are not installed, which signals the caller
to skip vector indexing/search entirely.

HuggingFace auto-fallback: on first model load we probe
``huggingface.co`` with a short HEAD request. If the probe fails
or is slow (>5 s), we auto-switch to ``hf-mirror.com`` by setting
the ``HF_ENDPOINT`` env var before sentence-transformers downloads.
If the user has already set ``HF_ENDPOINT``, we respect their choice
and skip the probe entirely.
"""

from __future__ import annotations

import http.client
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension
_MODEL_NAME = "all-MiniLM-L6-v2"
_HF_DEFAULT = "https://huggingface.co"
_HF_MIRROR = "https://hf-mirror.com"
_PROBE_TIMEOUT_SEC = 5

# Module-level cached handles (same pattern as bm25.py's _JIEBA_MODULE).
_ST_MODEL: Any = None
_ST_TRIED: bool = False


def _probe_endpoint(url: str, timeout: int = _PROBE_TIMEOUT_SEC) -> bool:
    """Return True if *url* responds to a HEAD request within *timeout* seconds."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return bool(resp.status < 400)
    except (OSError, ValueError, http.client.HTTPException):
        return False


def _ensure_hf_endpoint() -> str:
    """Decide which HuggingFace endpoint to use.

    - If ``HF_ENDPOINT`` is already set, return it unchanged (user override).
    - Otherwise probe ``huggingface.co``; if unreachable or slow, set
      ``HF_ENDPOINT`` to ``hf-mirror.com`` and return that.
    - If the probe succeeds, leave ``HF_ENDPOINT`` unset (default endpoint).
    """
    existing = os.environ.get("HF_ENDPOINT")
    if existing:
        return existing

    if _probe_endpoint(_HF_DEFAULT):
        logger.debug("HF probe OK — using default endpoint")
        return _HF_DEFAULT

    logger.info(
        "huggingface.co unreachable or slow (> %ds) — "
        "switching to hf-mirror.com for model download",
        _PROBE_TIMEOUT_SEC,
    )
    os.environ["HF_ENDPOINT"] = _HF_MIRROR
    return _HF_MIRROR


def _get_model() -> Any:
    """Return the SentenceTransformer model, or None if not available."""
    global _ST_MODEL, _ST_TRIED
    if _ST_TRIED:
        return _ST_MODEL
    _ST_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer

        _ensure_hf_endpoint()
        _ST_MODEL = SentenceTransformer(_MODEL_NAME)
    except ImportError:
        _ST_MODEL = None
    return _ST_MODEL


def embed(text: str) -> list[float] | None:
    """Compute embedding for a single text string.

    Returns a 384-dim float list, or None if sentence-transformers
    is not installed.
    """
    model = _get_model()
    if model is None:
        return None
    vec = model.encode(text, normalize_embeddings=True)
    return list(vec.tolist())


def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """Compute embeddings for a batch of texts.

    Returns None if sentence-transformers is not installed.
    Batch encoding is significantly faster than per-item encoding
    for the build pipeline (which may embed 30+ entries at once).
    """
    model = _get_model()
    if model is None:
        return None
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]


def is_available() -> bool:
    """Check whether vector search deps are available (without triggering
    the model download)."""
    try:
        import sentence_transformers  # noqa: F401
        import sqlite_vec  # noqa: F401

        return True
    except ImportError:
        return False
