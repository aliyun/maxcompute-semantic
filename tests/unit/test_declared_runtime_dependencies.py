# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Runtime-dependency declaration guard (issue #31).

Every third-party module imported **at import time** must be declared in the
project's runtime dependencies. This class of miss is invisible from inside
the repo: ``uv sync --extra dev`` installs mypy, which pulls
``typing_extensions``, so every local and CI test run resolves it — while a
user who installs the wheel on an interpreter where no declared dependency
happens to provide it gets ``ModuleNotFoundError`` before ``mcs`` prints
anything. That is exactly how 0.18.1 stopped starting on Python 3.13:
``versioning/lock.py`` and ``commands/_source_picker.py`` gained a
module-level ``from typing_extensions import Self``, and
``typing_extensions`` had never been a runtime dependency (nor was it
installed on 3.13, where ``httpx``'s transitive pin only requires it below
that version).

The check is a source scan, not an import, so its verdict does not depend on
what happens to be installed in the running environment.

Scope of the scan — what counts as "import time":
- module-level statements, and imports inside top-level ``if`` / ``try`` /
  ``with`` / ``class`` blocks, since those execute during import;
- **not** imports inside a function body (lazy by design, e.g. the pyodps
  startup-cost work);
- **not** imports inside ``if TYPE_CHECKING:`` (erased at runtime, which is
  how ``Self`` is supposed to be referenced here).

Optional backends listed in ``_OPTIONAL_IMPORT_ROOTS`` are exempt — but only
when the import is genuinely guarded by a ``try`` whose handler catches
``ImportError``, which the second assertion enforces so the allowlist cannot
quietly absorb a real dependency.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "maxcompute_semantic"
PYPROJECT = SRC_ROOT.parent.parent / "pyproject.toml"

#: Distribution name → top-level import name, for the pairs where they differ.
_IMPORT_ALIASES = {
    "pyodps": "odps",
    "ruamel.yaml": "ruamel",
}

#: Extras-grade backends: declared as optional dependencies, imported under a
#: guard, and skipped by the runtime that lacks them.
_OPTIONAL_IMPORT_ROOTS = frozenset({"sentence_transformers", "sqlite_vec"})

_STDLIB = set(sys.stdlib_module_names)


def _declared_import_roots() -> set[str]:
    """Top-level import names of every runtime dependency.

    Read with a regex rather than ``tomllib``: ``tomllib`` is 3.11+ and this
    project supports 3.10, and a dependency guard that itself needs an
    unavailable module would be the very bug it exists to catch. The parse is
    pinned by :func:`test_declared_dependency_parse_is_not_vacuous` so a
    broken pattern cannot silently downgrade the guard to a no-op.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    project_block = re.search(r"^\[project\]\n(.*?)^\[", text, re.DOTALL | re.MULTILINE)
    deps_block = re.search(
        r"^dependencies = \[(.*?)\]", project_block.group(1) if project_block else "", re.DOTALL | re.MULTILINE
    )
    specs = re.findall(r'"([^"]+)"', deps_block.group(1) if deps_block else "")
    roots: set[str] = set()
    for spec in specs:
        dist = re.split(r"[<>=!~;\[ ]", spec.strip(), maxsplit=1)[0].strip().lower()
        dist = dist.replace("-", "_")
        roots.add(_IMPORT_ALIASES.get(dist, dist))
    return roots


def _imports_of(test: ast.expr) -> list[str]:
    if isinstance(test, ast.Import):
        return [alias.name.split(".")[0] for alias in test.names]
    if isinstance(test, ast.ImportFrom) and test.level == 0 and test.module:
        return [test.module.split(".")[0]]
    return []


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _catches_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            return True
        names = (
            [handler.type]
            if isinstance(handler.type, ast.Name | ast.Attribute)
            else list(handler.type.elts)
            if isinstance(handler.type, ast.Tuple)
            else []
        )
        for name in names:
            leaf = name.attr if isinstance(name, ast.Attribute) else getattr(name, "id", "")
            if leaf in {"ImportError", "ModuleNotFoundError"}:
                return True
    return False


def _import_time_roots(path: Path) -> list[tuple[int, str, bool]]:
    """Return ``(lineno, import_root, guarded_by_import_error)`` at import time."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, bool]] = []

    def visit(stmts: list[ast.stmt], guarded: bool) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Import | ast.ImportFrom):
                for root in _imports_of(stmt):
                    if root not in _STDLIB and root != "maxcompute_semantic":
                        found.append((stmt.lineno, root, guarded))
            elif isinstance(stmt, ast.If):
                if _is_type_checking_guard(stmt.test):
                    continue
                visit(stmt.body, guarded)
                visit(stmt.orelse, guarded)
            elif isinstance(stmt, ast.Try):
                now_guarded = guarded or _catches_import_error(stmt)
                visit(stmt.body, now_guarded)
                for handler in stmt.handlers:
                    visit(handler.body, now_guarded)
                visit(stmt.orelse, now_guarded)
                visit(stmt.finalbody, now_guarded)
            elif isinstance(stmt, ast.With | ast.ClassDef):
                visit(stmt.body, guarded)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                continue

    visit(list(tree.body), False)
    return found


def _all_import_time_roots() -> list[tuple[str, int, str, bool]]:
    rows: list[tuple[str, int, str, bool]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = str(path.relative_to(SRC_ROOT.parent.parent))
        for lineno, root, guarded in _import_time_roots(path):
            rows.append((rel, lineno, root, guarded))
    return rows


def test_every_import_time_dependency_is_declared() -> None:
    declared = _declared_import_roots()
    undeclared = [
        (rel, lineno, root)
        for rel, lineno, root, _guarded in _all_import_time_roots()
        if root not in declared and root not in _OPTIONAL_IMPORT_ROOTS
    ]
    assert not undeclared, (
        "module-level imports of packages missing from "
        "pyproject [project.dependencies] — these raise ModuleNotFoundError on a "
        "clean install whenever no declared dependency happens to provide them "
        "(issue #31). Declare the dependency, or move the import under "
        "'if TYPE_CHECKING:' / into the function that needs it:\n"
        + "\n".join(f"  {rel}:{lineno}  {root}" for rel, lineno, root in sorted(undeclared))
    )


def test_typing_extensions_is_not_an_import_time_dependency() -> None:
    """The concrete 0.18.1 regression, named so it cannot quietly return.

    ``typing_extensions`` is not, and will not be, a runtime dependency:
    ``Self`` is stdlib since 3.11, and on 3.13 nothing in the resolved runtime
    set provides the package at all.
    """
    hits = [
        (rel, lineno)
        for rel, lineno, root, _guarded in _all_import_time_roots()
        if root == "typing_extensions"
    ]
    assert not hits, (
        "typing_extensions imported at module scope; annotate the use site "
        "under 'if TYPE_CHECKING:' instead:\n"
        + "\n".join(f"  {rel}:{lineno}" for rel, lineno in sorted(hits))
    )


def test_declared_dependency_parse_is_not_vacuous() -> None:
    """Pin the pyproject parse the guard depends on.

    If the pattern stops matching, ``_declared_import_roots`` returns an empty
    set and the guard's other assertions degrade into noise — which is exactly
    how a guard gets deleted rather than fixed. This fails loudly instead.
    """
    declared = _declared_import_roots()
    assert {"click", "odps", "ruamel", "sqlglot", "questionary"} <= declared, declared
    assert "typing_extensions" not in declared, declared


def test_optional_backend_imports_are_guarded() -> None:
    """The allowlist only earns its keep if those imports really are optional."""
    unguarded = [
        (rel, lineno, root)
        for rel, lineno, root, guarded in _all_import_time_roots()
        if root in _OPTIONAL_IMPORT_ROOTS and not guarded
    ]
    assert not unguarded, (
        "optional-backend import at module scope is not inside a "
        "try/except ImportError, so it is a hard dependency of the import path "
        "and must be declared:\n"
        + "\n".join(f"  {rel}:{lineno}  {root}" for rel, lineno, root in sorted(unguarded))
    )
