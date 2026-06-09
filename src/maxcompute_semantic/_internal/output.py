# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Renderer: CLI output adapter for plain (human) and envelope (json/yaml) modes."""

from __future__ import annotations

import json
import sys
from io import StringIO
from typing import Any, TextIO

from maxcompute_semantic.mc_client.envelope import Envelope
from maxcompute_semantic.mc_client.errors import McsError

_ENVELOPE_FORMATS: frozenset[str] = frozenset({"json", "yaml"})


def _serialize_envelope(envelope_dict: dict[str, Any], format: str) -> str:
    """Serialize the envelope dict as JSON or YAML.

    YAML serializer is ruamel's safe form so envelope output isn't
    contaminated by the round-trip-mode comment preservation
    machinery the config-write path uses.
    """
    if format == "yaml":
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        yaml.default_flow_style = False
        yaml.allow_unicode = True
        buf = StringIO()
        yaml.dump(envelope_dict, buf)
        return buf.getvalue()
    return json.dumps(envelope_dict, ensure_ascii=False) + "\n"


class Renderer:
    """Format-aware output: success/error/table for plain or envelope mode.

    ``format`` is one of ``plain`` / ``json`` / ``yaml``. ``json`` and
    ``yaml`` both emit the same :class:`Envelope` shape (``status`` /
    ``data`` / ``error``), differing only in serializer — callers that
    branch on "envelope vs prose" should use :attr:`is_envelope`
    rather than comparing against ``"json"`` directly.
    """

    def __init__(
        self,
        format: str = "plain",
        quiet: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.format = format
        self.quiet = quiet
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr

    @property
    def is_envelope(self) -> bool:
        """True for envelope-emitting formats (``json`` / ``yaml``).

        Use this everywhere a call site branches "structured envelope
        on stdout" vs "human prose" — keeps yaml symmetric with json
        without each branch having to enumerate both names.
        """
        return self.format in _ENVELOPE_FORMATS

    def quiet_essential(self, data: dict[str, Any], key: str) -> None:
        """In quiet+plain mode, print just the essential identifier (one line).

        When format=json/yaml, quiet is ignored per spec (envelope is always emitted).
        When quiet=False, do nothing (regular success output handles it).
        """
        if self.is_envelope:
            # Envelope modes always emit via success(); quiet is irrelevant.
            return
        if not self.quiet:
            # Non-quiet plain mode: regular success output handles it.
            return
        value = data.get(key)
        if value is None:
            return
        self._stdout.write(str(value) + "\n")

    def success(self, data: dict[str, Any]) -> None:
        """Emit a success payload."""
        if self.is_envelope:
            env = Envelope.success(data)
            self._stdout.write(_serialize_envelope(env.to_dict(), self.format))
        elif self.quiet:
            # quiet mode: no output on success
            pass
        else:
            # Plain: simple key=value lines
            for key, value in data.items():
                self._stdout.write(f"{key}: {value}\n")

    def error(self, err: McsError) -> None:
        """Emit an error payload. Plain goes to stderr; envelope modes go to stdout."""
        if self.is_envelope:
            env = Envelope.from_error(err)
            self._stdout.write(_serialize_envelope(env.to_dict(), self.format))
        else:
            self._stderr.write(f"Error: {err.message}\n")
            if err.remediation:
                self._stderr.write(f"  Suggestion: {err.remediation}\n")

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        """Emit a table. Plain uses columnar formatting; envelope modes use the envelope."""
        if self.is_envelope:
            env = Envelope.success({"headers": headers, "rows": rows})
            self._stdout.write(_serialize_envelope(env.to_dict(), self.format))
        elif self.quiet:
            pass
        else:
            # Compute column widths
            col_widths = [len(h) for h in headers]
            for row in rows:
                for i, cell in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(cell))

            # Header row
            header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
            self._stdout.write(header_line + "\n")

            # Separator
            sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
            self._stdout.write(sep_line + "\n")

            # Data rows
            for row in rows:
                data_line = "  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row))
                self._stdout.write(data_line + "\n")
