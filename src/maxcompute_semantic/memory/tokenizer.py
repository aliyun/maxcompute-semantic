# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MemoryTokenizer — jieba + Latin regex pre-tokenizer for FTS5 unicode61.

Returns space-separated token strings consumable by FTS5's default
unicode61 tokenizer. Index and query paths share the same tokenize
logic to guarantee match-ability.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_JIEBA_MODULE: Any = None
_JIEBA_TRIED: bool = False


def _get_jieba() -> Any:
    """Return jieba module, or None if unavailable.

    jieba is a hard dep but we still go through try/import so a
    corrupted install or platform-incompatible wheel degrades to
    Latin-only tokenization instead of crashing.
    """
    global _JIEBA_MODULE, _JIEBA_TRIED
    if _JIEBA_TRIED:
        return _JIEBA_MODULE
    _JIEBA_TRIED = True
    try:
        import jieba

        _JIEBA_MODULE = jieba
    except ImportError:
        _JIEBA_MODULE = None
    return _JIEBA_MODULE


_CJK_RANGE = re.compile(r"[一-鿿㐀-䶿豈-﫿⺀-⻿　-〿]")
_LATIN_TOKEN = re.compile(r"[a-z0-9_]+")


class MemoryTokenizer:
    """Dual-mode tokenizer: jieba for CJK + ASCII regex for Latin."""

    def _tokenize_to_list(self, text: str) -> list[str]:
        if not text:
            return []
        try:
            terms: list[str] = []
            has_cjk = bool(_CJK_RANGE.search(text))
            if has_cjk:
                jieba = _get_jieba()
                if jieba is not None:
                    for seg in jieba.lcut(text):
                        if _CJK_RANGE.search(seg):
                            cleaned = seg.lower().replace('"', "")
                            if cleaned:
                                terms.append(cleaned)
            for t in _LATIN_TOKEN.findall(text.lower()):
                cleaned = t.replace('"', "")
                if cleaned:
                    terms.append(cleaned)
            return terms
        except Exception as exc:  # noqa: BLE001
            logger.debug("tokenizer error, falling back to Latin-only: %s", exc)
            return [t for t in _LATIN_TOKEN.findall(text.lower()) if t]

    def tokenize_for_index(self, text: str) -> str:
        return " ".join(self._tokenize_to_list(text))

    def tokenize_for_query(self, text: str) -> str:
        return self.tokenize_for_index(text)
