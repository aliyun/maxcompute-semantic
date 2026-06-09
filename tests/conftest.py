# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

# Modules that did ``from maxcompute_semantic.commands._source_picker
# import _pick_*`` at module-import time. Because those names are
# **rebound** into the importing module's globals, patching only the
# ``_source_picker`` module misses these call sites — the importer's
# local binding still points at the original function. We must also
# patch the names where they were re-bound. Inline ``from ... import``
# inside functions (e.g. ``commands/link.py``, ``commands/profile.py``)
# re-evaluate on every call, so patching ``_source_picker`` is enough
# for those — they don't need to appear here.
_PICKER_REBIND_SITES = ("maxcompute_semantic.commands._profile_editor",)

_PICKER_FN_NAMES = ("_pick_one", "_pick_many", "_pick_choice", "_pick_columns_to_hide")


def _apply_picker_patch(monkeypatch: pytest.MonkeyPatch, stub_factory) -> None:
    """Install ``stub_factory(name)`` for each picker fn at every binding
    site (source module + re-bind sites). ``stub_factory`` is called per
    function name and must return a callable to substitute."""
    import importlib

    from maxcompute_semantic.commands import _source_picker

    for fn in _PICKER_FN_NAMES:
        monkeypatch.setattr(_source_picker, fn, stub_factory(fn), raising=False)

    for module_path in _PICKER_REBIND_SITES:
        mod = importlib.import_module(module_path)
        for fn in _PICKER_FN_NAMES:
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, stub_factory(fn), raising=False)


@pytest.fixture
def mock_picker(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """FIFO queue of picker returns. Patches the 4 ``_source_picker`` entry points.

    ``_source_picker`` wraps ``iterfzf`` (which opens ``/dev/tty`` directly),
    so any test that hits a picker in a non-TTY environment (CI, headless
    `pytest` runs) would otherwise crash with ``inappropriate ioctl for
    device`` or hang waiting for a keypress that never arrives. Both
    failure modes are masked by the autouse ``_block_picker_unless_mocked``
    guard below; requesting *this* fixture is the opt-in for tests that
    legitimately exercise a picker code path.

    Append picker returns in call order before invoking the CLI::

        def test_foo(mock_picker, isolated_config):
            mock_picker.append("meta-dev")    # _pick_one returns "meta-dev"
            mock_picker.append(["t1", "t2"])  # _pick_many returns 2 tables
            mock_picker.append("INCLUDE_ALL") # _pick_choice returns this value
            result = invoke(...)

    A queued ``None`` simulates the user pressing Esc / Ctrl+C.

    There is no leftover-queue assertion at teardown — error paths
    legitimately exit before consuming every queued return.
    """
    queue: list[object] = []

    def _make(name: str):
        def _stub(*_a: object, **_kw: object) -> object:
            assert queue, (
                f"mock_picker: {name}() called without a queued response. "
                f"Append the expected return value before invoking the CLI."
            )
            return queue.pop(0)

        return _stub

    _apply_picker_patch(monkeypatch, _make)
    return queue


@pytest.fixture(autouse=True)
def _block_picker_unless_mocked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Convert unmocked picker access from hang/ioctl-error → loud test failure.

    Tests that intentionally exercise the picker request the ``mock_picker``
    fixture (which does its own patching). Tests that don't get a clear
    failure if they accidentally hit a picker code path instead of either
    hanging (local TTY) or crashing with ``inappropriate ioctl for device``
    (CI / no-TTY).

    Picker-module unit tests (``test_source_picker.py``) are exempt — they
    exercise the picker functions directly and mock ``_iterfzf`` (the TTY
    boundary) themselves, so the guard's patching at the picker function
    layer would shadow the very functions under test.
    """
    if "mock_picker" in request.fixturenames:
        return
    if request.node.path.name == "test_source_picker.py":
        return

    def _make_refuse(name: str):
        def _refuse(*_a: object, **_kw: object) -> object:
            pytest.fail(
                f"{request.node.name} triggered {name}() without "
                f"requesting the `mock_picker` fixture. Add it to fixture args "
                f"and append expected returns: mock_picker.append(...)."
            )

        return _refuse

    _apply_picker_patch(monkeypatch, _make_refuse)


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point MCS at a tmp config dir and tmp data dir; clear all MCS env overrides.

    MCS_DATA_DIR points at ``tmp_path`` itself (not ``tmp_path/data``) so
    that ``data_root()`` resolves to ``tmp_path/data/`` — the test paths
    that assert ``tmp_path/data/<profile_name>/...`` line up with what
    the build pipeline writes.

    Also forces ``commands._import_creds.discover_creds()`` to return
    an empty list so the wizard's auto-detect prompt doesn't fire on
    test machines that happen to have ``~/.maxc/config.yaml`` /
    ``odpscmd`` configured. Tests that exercise the import path mock
    this explicitly.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("MCS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MCS_PROFILE", raising=False)
    monkeypatch.delenv("MCS_TIER_OVERRIDE", raising=False)
    monkeypatch.delenv("MCS_PROFILES_DIR", raising=False)
    # Suppress the wizard's import-discovery hook — tests that need to
    # exercise it patch the module's discover_creds directly.
    monkeypatch.setattr(
        "maxcompute_semantic.commands._import_creds.discover_creds",
        lambda: [],
    )
    return tmp_path


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def latest_json_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stand up a stdlib HTTP server on a random port that serves a
    settable ``/latest.json`` payload, point
    ``MCS_UPDATE_BASE_URL`` at it, and yield a control handle.

    Also redirects ``MCS_CACHE_DIR`` to a per-test tmpdir so any test
    that triggers ``start_background_probe`` (e.g. ``mcs doctor``,
    ``mcs update``) writes its synthetic ``9.9.9`` (or other test)
    payload into the tmpdir instead of the user's real cache at
    ``~/Library/Caches/maxcompute-semantic/update_check.json``. Earlier
    versions of this fixture stubbed only the HTTP fetch — the cache
    write still landed on the host, which produced the "✨ A new
    release of mcs is available: 0.10.12 → 9.9.9" banner on the
    developer's terminal after running the test suite.

    Yields a ``(base_url, setter)`` pair:

      * ``base_url`` is the ``http://127.0.0.1:<port>`` root.
      * ``setter`` takes one argument:
          - a ``dict`` to serve as JSON,
          - a ``str`` to serve as the raw response body (for the
            invalid-JSON case),
          - an ``int`` HTTP status (4xx/5xx for the failure cases),
          - ``None`` to make the server hang up the connection without
            a response (the "DNS-style outright failure" approximation).

    The fixture stops the server on teardown.

    The fixture is intentionally stdlib-only (no ``pytest-httpserver``
    dependency) — the test surface is one endpoint with no header /
    content-negotiation requirements. See spec
    §"Testing strategy / Stub server for tests"."""
    import socketserver
    from http.server import BaseHTTPRequestHandler
    from threading import Thread

    state: dict[str, object] = {"payload": None}

    class Handler(BaseHTTPRequestHandler):
        # Silence the default stderr access log so test output stays clean.
        def log_message(self, *_a, **_kw) -> None:  # noqa: D401
            return

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler interface)
            if self.path != "/latest.json":
                self.send_error(404, "not found")
                return
            payload = state["payload"]
            if payload is None:
                # Simulate a server that closes the connection without
                # writing anything — closest stdlib analogue to a DNS
                # failure for the consumer's error path.
                self.wfile.close()
                return
            if isinstance(payload, int):
                self.send_response(payload)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if isinstance(payload, str):
                body = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            assert isinstance(payload, dict)
            body = __import__("json").dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # ThreadingHTTPServer would also work; ThreadingTCPServer + the BHR
    # is the canonical stdlib pair on every supported Python.
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    host, port = server.server_address
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    monkeypatch.setenv("MCS_UPDATE_BASE_URL", base_url)
    # Relax _base_url() validation for the test-local HTTP server:
    # allow http scheme and the 127.0.0.1 host.
    import maxcompute_semantic._internal.update_check as _uc

    monkeypatch.setattr(_uc, "_ALLOWED_HOSTS", frozenset({*_uc._ALLOWED_HOSTS, str(host)}))

    def _test_base_url() -> str:
        import os as _os

        raw = _os.environ.get("MCS_UPDATE_BASE_URL", _uc.DEFAULT_BASE_URL).rstrip("/")
        return raw

    monkeypatch.setattr(_uc, "_base_url", _test_base_url)
    # Also patch modules that import _base_url by name at the top level.
    import maxcompute_semantic.commands.doctor as _doc
    import maxcompute_semantic.commands.update as _upd

    monkeypatch.setattr(_doc, "_base_url", _test_base_url)
    monkeypatch.setattr(_upd, "_base_url", _test_base_url)
    # Also sandbox the on-disk update-check cache. ``mcs doctor`` and
    # ``mcs update`` both fire ``start_background_probe(force=True)``
    # which calls ``write_cache(...)`` regardless of the test's
    # explicit MCS_CACHE_DIR setting.
    monkeypatch.setenv("MCS_CACHE_DIR", str(tmp_path / "_update_cache"))

    def set_payload(payload: object) -> None:
        state["payload"] = payload

    try:
        yield base_url, set_payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
