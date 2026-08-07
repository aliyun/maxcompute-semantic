# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the errors consolidation (PR1 of the consolidation spec).

Three guarantees this PR has to keep:

1. Every concrete ``McsError`` subclass has a unique wire code (the JSON
   envelope's ``error.code`` field). Renames or merges that violate this
   silently break the eval harness's per-code dashboards.
2. The old import paths (``mc_client.errors``, ``auth.errors``,
   ``build.errors``, ``memory.errors``, ``versioning.errors``) still
   resolve to the same classes as the canonical
   ``maxcompute_semantic.errors`` location. The deprecation shims live
   for one minor version cycle so internal callers can migrate without
   churn.
3. The ``@maps_pyodps_errors`` decorator reclassifies pyodps
   ``ODPSError`` exceptions into the right ``McsError`` subclass and
   preserves the original via ``__cause__``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


def _all_mcs_error_subclasses() -> Iterator[type]:
    """Yield every concrete McsError subclass reachable from the package."""
    # Force import of every errors submodule so subclasses are registered.
    import maxcompute_semantic.errors
    import maxcompute_semantic.errors.annotate
    import maxcompute_semantic.errors.auth
    import maxcompute_semantic.errors.build
    import maxcompute_semantic.errors.mc
    import maxcompute_semantic.errors.memory
    import maxcompute_semantic.errors.versioning  # noqa: F401
    from maxcompute_semantic.errors.base import McsError

    seen: set[type] = set()

    def walk(cls: type) -> Iterator[type]:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            yield sub
            yield from walk(sub)

    yield from walk(McsError)


def test_every_error_class_has_unique_wire_code():
    """No two McsError subclasses may share a wire code."""
    by_code: dict[str, list[str]] = {}
    for cls in _all_mcs_error_subclasses():
        # ``code`` is an ErrorCode enum on the canonical classes; its
        # ``value`` is the string contract that ships in the envelope.
        code = cls.code
        code_value = code.value if hasattr(code, "value") else str(code)
        by_code.setdefault(code_value, []).append(cls.__name__)

    duplicates = {code: names for code, names in by_code.items() if len(names) > 1}
    assert not duplicates, f"duplicate wire codes: {duplicates}"


@pytest.mark.parametrize(
    "old_path, new_path, names",
    [
        (
            "maxcompute_semantic.mc_client.errors",
            "maxcompute_semantic.errors",
            [
                "McsError",
                "AuthFailedError",
                "TableNotFoundError",
                "PermissionDeniedError",
                "map_pyodps_exception",
            ],
        ),
        (
            "maxcompute_semantic.auth.errors",
            "maxcompute_semantic.errors",
            ["AuthFailedError", "InvalidProfileError"],
        ),
        (
            "maxcompute_semantic.build.errors",
            "maxcompute_semantic.errors",
            ["BuildPhaseError", "RebuildRequiredError"],
        ),
        (
            "maxcompute_semantic.memory.errors",
            "maxcompute_semantic.errors",
            ["MemoryNotFoundError"],
        ),
        (
            "maxcompute_semantic.versioning.errors",
            "maxcompute_semantic.errors",
            [
                "LockedByOtherProcessError",
                "GitNotAvailable",
                "ProfileReadOnly",
                "PackageSqlCorrupt",
            ],
        ),
    ],
)
def test_old_import_paths_still_resolve(old_path, new_path, names):
    """Each name imported from the deprecation shim is the same class as the
    one imported from the canonical ``maxcompute_semantic.errors`` location."""
    import importlib

    old_mod = importlib.import_module(old_path)
    new_mod = importlib.import_module(new_path)
    for name in names:
        assert hasattr(old_mod, name), f"{old_path} missing {name}"
        assert hasattr(new_mod, name), f"{new_path} missing {name}"
        assert getattr(old_mod, name) is getattr(new_mod, name), (
            f"{name}: {old_path} and {new_path} resolve to different objects"
        )


def test_maps_pyodps_errors_decorator_translates_table_not_found():
    """``ODPSError`` with ``code=NoSuchObject`` becomes ``TableNotFoundError``,
    ``__cause__`` preserves the original, and the SQL kwarg flows through."""
    from odps import errors as odps_errors

    from maxcompute_semantic.errors import (
        TableNotFoundError,
        maps_pyodps_errors,
    )

    @maps_pyodps_errors(sql_arg="sql")
    def fake_call(self, sql: str) -> None:
        # Mimic pyodps raising NoSuchObject on a missing table.
        exc = odps_errors.ODPSError("Table 'foo' not found")
        exc.code = "NoSuchObject"  # ODPSError lets us set this
        raise exc

    with pytest.raises(TableNotFoundError) as excinfo:
        fake_call(self=None, sql="SELECT * FROM foo")

    err = excinfo.value
    assert isinstance(err.__cause__, odps_errors.ODPSError)
    # ``map_pyodps_exception`` forwards sql via the keyword path so the
    # context dict carries it.
    assert err.context.get("sql") == "SELECT * FROM foo"


def test_maps_pyodps_errors_decorator_skips_mcs_errors():
    """McsError raised inside the wrapped fn must propagate as-is, not be
    double-wrapped (the decorator's _should_catch returns False for
    McsError)."""
    from maxcompute_semantic.errors import (
        CostBlockedError,
        maps_pyodps_errors,
    )

    @maps_pyodps_errors(sql_arg="sql")
    def fake_call(sql: str) -> None:
        raise CostBlockedError("over budget")

    with pytest.raises(CostBlockedError) as excinfo:
        fake_call(sql="SELECT 1")

    # Exact identity — not wrapped, not transformed.
    assert excinfo.value.message == "over budget"
    assert excinfo.value.__cause__ is None


def test_to_envelope_has_canonical_shape():
    """The ``to_envelope`` method emits the JSON envelope the cli boundary
    serializes."""
    from maxcompute_semantic.errors import AuthFailedError

    e = AuthFailedError("invalid AK", remediation="re-run mcs profile create")
    env = e.to_envelope()
    assert env == {
        "status": "error",
        "error": {
            "code": "AuthFailed",
            "message": "invalid AK",
            "remediation": "re-run mcs profile create",
        },
    }


def test_to_envelope_omits_remediation_when_empty():
    from maxcompute_semantic.errors import UnknownError

    e = UnknownError("oops")
    env = e.to_envelope()
    assert "remediation" not in env["error"]
