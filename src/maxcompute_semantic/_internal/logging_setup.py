"""Logging setup for mcs CLI: stderr handler, no duplicates, per-format levels."""

from __future__ import annotations

import logging
import sys
import warnings


def setup_logging(
    *,
    debug: bool = False,
    verbose: bool = False,
    level: int | None = None,
    format: str = "plain",
) -> None:
    """Configure the maxcompute_semantic + odps logger namespace.

    - debug=True sets DEBUG on both namespaces.
    - verbose=True sets INFO on both namespaces.
    - Default level: WARNING for plain, ERROR for json (json mode only
      emits errors to stdout).
    - explicit level overrides debug/verbose/format defaults.
    - All handlers stream to stderr.
    - No duplicate handlers on repeated calls.
    - In ``format="json"`` mode, also silences mcs ``UserWarning``s
      (e.g. ``StaleLockClearedWarning``) so the stderr stream carries
      only the error envelope. Explicit ``--debug`` / ``--verbose``
      keeps them visible for diagnosis.
    """
    if level is not None:
        effective_level = level
    elif debug:
        effective_level = logging.DEBUG
    elif verbose:
        effective_level = logging.INFO
    elif format == "json":
        effective_level = logging.ERROR
    else:
        effective_level = logging.WARNING

    _configure_namespace("maxcompute_semantic", effective_level)
    _configure_namespace("odps", effective_level)
    _configure_warnings(format=format, debug=debug, verbose=verbose)


def _configure_warnings(*, format: str, debug: bool, verbose: bool) -> None:
    """Silence mcs recovery-class warnings in ``-f json`` mode.

    The stderr stream in json mode is reserved for the error envelope
    (``{"status":"error", ...}`` emitted by the CLI shield in
    ``cli.py``). Recovery-class warnings like
    ``StaleLockClearedWarning`` are non-actionable noise next to that
    envelope and would break tools that scrape stderr for the JSON
    payload. ``--debug`` / ``--verbose`` keep them visible for
    diagnosis.

    Imported lazily so this module has no dependency on
    ``versioning``.
    """
    if format != "json" or debug or verbose:
        return
    from maxcompute_semantic.versioning.errors import StaleLockClearedWarning

    warnings.filterwarnings("ignore", category=StaleLockClearedWarning)


def _configure_namespace(name: str, level: int) -> None:
    """Set level and add a stderr StreamHandler if none exists."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Only add a handler if one doesn't already exist (avoid duplicates
    # on repeated calls to setup_logging)
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and h.stream is sys.stderr:
            # Already have a stderr handler; just update its level
            h.setLevel(level)
            return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logger.addHandler(handler)
