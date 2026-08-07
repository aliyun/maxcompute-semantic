# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Startup-import guard: importing the CLI must not pull pyodps (and its
pandas / numpy / pyarrow tail) or sqlglot (plus the MaxCompute dialect
package built on it) into the process, so local-only commands
(``mcs profile list``, ``mcs link``, ``mcs --help``) stay fast. pyodps
and sqlglot are imported lazily inside the functions that need them."""

from __future__ import annotations

import subprocess
import sys


def test_cli_import_does_not_import_pyodps() -> None:
    code = (
        "import sys\n"
        "import maxcompute_semantic.cli\n"
        "leaked = sorted(\n"
        "    {m.split('.')[0] for m in sys.modules}\n"
        "    & {'odps', 'pandas', 'numpy', 'pyarrow', 'sqlglot'}\n"
        ")\n"
        "assert not leaked, f'imported at CLI startup: {leaked}'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
