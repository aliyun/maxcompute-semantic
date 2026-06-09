"""Tests for _internal/logging_setup.py — logging configuration."""

from __future__ import annotations

import logging
import sys
import warnings

from maxcompute_semantic._internal.logging_setup import setup_logging
from maxcompute_semantic.versioning.errors import StaleLockClearedWarning


class TestSetupLogging:
    def test_default_level_is_warning(self) -> None:
        setup_logging()
        logger = logging.getLogger("maxcompute_semantic")
        assert logger.level == logging.WARNING

    def test_verbose_sets_info(self) -> None:
        setup_logging(verbose=True)
        logger = logging.getLogger("maxcompute_semantic")
        assert logger.level == logging.INFO

    def test_debug_sets_debug(self) -> None:
        setup_logging(debug=True)
        logger = logging.getLogger("maxcompute_semantic")
        assert logger.level == logging.DEBUG

    def test_handler_writes_to_stderr(self) -> None:
        # Clear existing handlers to get a clean test
        logger = logging.getLogger("maxcompute_semantic")
        logger.handlers.clear()
        setup_logging()
        stderr_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stderr_handlers) >= 1
        assert stderr_handlers[0].stream is sys.stderr

    def test_no_duplicate_handlers_on_repeated_calls(self) -> None:
        setup_logging()
        logger = logging.getLogger("maxcompute_semantic")
        count_before = len(logger.handlers)
        setup_logging()
        assert len(logger.handlers) == count_before

    def test_odps_namespace_also_configured(self) -> None:
        setup_logging(debug=True)
        odps_logger = logging.getLogger("odps")
        assert odps_logger.level == logging.DEBUG

    def test_json_mode_level_is_error(self) -> None:
        setup_logging(format="json")
        logger = logging.getLogger("maxcompute_semantic")
        assert logger.level == logging.ERROR

    def test_custom_level_overrides_verbose(self) -> None:
        setup_logging(level=logging.INFO)
        logger = logging.getLogger("maxcompute_semantic")
        assert logger.level == logging.INFO


class TestWarningsRouting:
    """``setup_logging`` silences mcs UserWarnings in ``-f json`` mode
    so the stderr stream carries only the error envelope. ``--debug``
    / ``--verbose`` keeps them visible.

    Each test uses ``warnings.catch_warnings()`` so the filter
    mutation does not leak across tests.
    """

    def test_json_mode_silences_stale_lock_warning(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("default")
            setup_logging(format="json")
            warnings.warn(
                StaleLockClearedWarning("cleared stale lockfile at /x"),
                stacklevel=1,
            )
        assert not any(isinstance(w.message, StaleLockClearedWarning) for w in captured)

    def test_plain_mode_preserves_stale_lock_warning(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("default")
            setup_logging(format="plain")
            warnings.warn(
                StaleLockClearedWarning("cleared stale lockfile at /x"),
                stacklevel=1,
            )
        assert any(isinstance(w.message, StaleLockClearedWarning) for w in captured)

    def test_json_mode_debug_keeps_stale_lock_warning(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("default")
            setup_logging(format="json", debug=True)
            warnings.warn(
                StaleLockClearedWarning("cleared stale lockfile at /x"),
                stacklevel=1,
            )
        assert any(isinstance(w.message, StaleLockClearedWarning) for w in captured)

    def test_json_mode_verbose_keeps_stale_lock_warning(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("default")
            setup_logging(format="json", verbose=True)
            warnings.warn(
                StaleLockClearedWarning("cleared stale lockfile at /x"),
                stacklevel=1,
            )
        assert any(isinstance(w.message, StaleLockClearedWarning) for w in captured)

    def test_json_mode_does_not_silence_non_mcs_warnings(self) -> None:
        """Filter targets ``module=maxcompute_semantic.*`` only so a
        third-party library's UserWarning still surfaces."""
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("default")
            setup_logging(format="json")
            # Synthesize a third-party origin via the ``stacklevel``
            # frame — emit from this test module, not from mcs.
            warnings.warn("third-party noise", UserWarning, stacklevel=1)
        assert any(str(w.message) == "third-party noise" for w in captured)
