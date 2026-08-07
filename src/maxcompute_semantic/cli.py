# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Click root for the `mcs` CLI.

Global flags:
  --config <dir>          override config directory (also MCS_CONFIG_DIR env)
  -f, --format plain|json|yaml output format
  -q, --quiet             only essential identifiers
  --debug                 DEBUG-level logging
  --verbose               INFO-level logging
"""

from __future__ import annotations

import contextlib
import json
import sys
import traceback

import click

from maxcompute_semantic import __version__
from maxcompute_semantic._internal.logging_setup import setup_logging
from maxcompute_semantic._internal.update_check import (
    banner_suppressed,
    format_banner,
    is_hard_block,
    read_cache,
    start_background_probe,
)
from maxcompute_semantic.commands.build import build_cmd
from maxcompute_semantic.commands.doctor import doctor_cmd
from maxcompute_semantic.commands.link import link_group
from maxcompute_semantic.commands.memory import memory_group
from maxcompute_semantic.commands.meta import meta_group
from maxcompute_semantic.commands.metric import metric_group
from maxcompute_semantic.commands.package import package_group
from maxcompute_semantic.commands.profile import profile_group
from maxcompute_semantic.commands.show import show_cmd
from maxcompute_semantic.commands.skill import skill_group
from maxcompute_semantic.commands.sql import sql_group
from maxcompute_semantic.commands.status import status_cmd
from maxcompute_semantic.commands.udf import udf_group
from maxcompute_semantic.commands.update import cmd_update as update_cmd

_GLOBAL_FLAG_NAMES: frozenset[str] = frozenset(
    {
        "-f",
        "--format",
        "-q",
        "--quiet",
        "--debug",
        "--verbose",
        "--config",
    }
)
_GLOBAL_FLAG_TAKES_VALUE: frozenset[str] = frozenset({"-f", "--format", "--config"})


def _command_accepts_option(tokens_before: list[str], option: str) -> bool:
    """Return True when the deepest command path seen so far owns *option*.

    The argv rewriter runs before Click has parsed the command tree, but
    hoisting every token that matches a root option name is too broad:
    ``mcs link status --verbose`` uses ``--verbose`` as a command-specific
    flag, while root ``mcs --verbose profile list`` uses the same spelling.
    Walk only the leading command tokens already emitted to ``out`` and ask
    the deepest Click command whether it declares the option.
    """
    root = globals().get("cli")
    current = root
    if current is None:
        return False

    for tok in tokens_before:
        if tok == "--":
            break
        commands = getattr(current, "commands", None)
        if commands and tok in commands:
            current = commands[tok]
            continue
        break

    for param in getattr(current, "params", ()):
        if option in getattr(param, "opts", ()) or option in getattr(param, "secondary_opts", ()):
            return True
    return False


def _hoist_global_flags(argv: list[str]) -> list[str]:
    """Move global flags to before the first subcommand token.

    Click's strict option placement rejects ``mcs show -f json`` because
    ``show`` doesn't declare ``-f`` — only the root group does. Agents
    routinely mis-place the flag (historical benchmark transcripts show
    ``mcs show -f json`` failing and forcing a fallback to a 43 KB YAML
    dump that pushed identifiers out of Claude Code's preview window).
    Pre-process argv: scan past the first non-option token (the
    subcommand name), and pull any subsequent ``--debug`` /
    ``-f json`` / ``--config DIR`` / ``-q`` / ``--verbose`` /
    ``--format json`` occurrences to the front so the position-tolerant
    UX matches what real users expect from a CLI.

    Flags that take values (``-f``, ``--format``, ``--config``) take
    the next token as their value. ``--key=value`` form is hoisted as
    a single token. Unknown options pass through untouched so command-
    specific flags still parse normally.

    A flag that already sits before the subcommand is left in place; we
    only move occurrences that fall after the subcommand boundary.
    """
    hoisted: list[str] = []
    pre_cmd: list[str] = []
    cmd_idx: int | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return argv
        if not tok.startswith("-"):
            cmd_idx = i
            break
        pre_cmd.append(tok)
        base = tok.split("=", 1)[0] if "=" in tok else tok
        if base in _GLOBAL_FLAG_TAKES_VALUE and "=" not in tok and i + 1 < len(argv):
            pre_cmd.append(argv[i + 1])
            i += 2
            continue
        i += 1
    if cmd_idx is None:
        return argv
    remaining: list[str] = list(argv[cmd_idx:])
    out: list[str] = []
    i = 0
    while i < len(remaining):
        tok = remaining[i]
        # Stop scanning at ``--``: everything after is positional by
        # POSIX convention, even if it looks like an option.
        if tok == "--":
            out.extend(remaining[i:])
            break
        base = tok.split("=", 1)[0] if "=" in tok else tok
        if base in _GLOBAL_FLAG_NAMES and not _command_accepts_option(out, base):
            if base in _GLOBAL_FLAG_TAKES_VALUE and "=" not in tok:
                if i + 1 < len(remaining):
                    hoisted.extend([tok, remaining[i + 1]])
                    i += 2
                    continue
                # Missing value — let Click emit the canonical error.
                hoisted.append(tok)
                i += 1
                continue
            hoisted.append(tok)
            i += 1
            continue
        out.append(tok)
        i += 1
    return pre_cmd + hoisted + out


def _cli_main() -> None:
    """Entry point with global exception shielding and the update-check
    banner harness.

    Three nested layers (inside-out):

      1. Click dispatch via ``cli(standalone_mode=False)`` raises
         ``click.exceptions.Exit`` for normal exits and
         ``ClickException`` / ``Abort`` for errors. Each is mapped to
         an ``inner_exit_code`` so the finally block still runs.

      2. The finally block reads the cached update metadata, calls
         ``format_banner``, and prints the result on stderr. The
         hard-block flag overrides the env-opt-out and no-TTY
         suppression vectors so a disabled-version gate is visible in
         pipelines. A successful inner exit (0) becomes 2 when
         hard_block is True so calling scripts can detect the gate
         via ``$?`` without parsing stderr.

      3. Pre-dispatch: a daemon-thread probe is spawned (fire-and-
         forget) unless ``banner_suppressed`` says to skip. Spawn
         errors are swallowed — the banner is best-effort opt-out.
    """
    import sys as _sys

    sys.argv = [sys.argv[0]] + _hoist_global_flags(sys.argv[1:])

    # ---- pre-dispatch: daemon probe ----
    # The banner is best-effort opt-out; spawn errors are swallowed.
    with contextlib.suppress(Exception):
        if not banner_suppressed(sys.argv):
            start_background_probe(current_version=__version__)

    inner_exit_code: int | None = None
    try:
        try:
            cli(standalone_mode=False)
        except click.exceptions.Exit as e:
            inner_exit_code = e.exit_code
        except click.exceptions.Abort:
            inner_exit_code = 1
        except click.ClickException as e:
            e.show()
            inner_exit_code = e.exit_code
        except SystemExit as e:
            inner_exit_code = (
                int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
            )
        except Exception as exc:  # noqa: BLE001 — global shield: map anything, never crash
            from maxcompute_semantic.errors import (
                McsError,
                map_pyodps_exception,
            )

            mcs_exc: McsError | None
            if isinstance(exc, McsError):
                mcs_exc = exc
            else:
                try:
                    mcs_exc = map_pyodps_exception(exc)
                except Exception:  # noqa: BLE001 — the error path itself must not raise
                    mcs_exc = None

            if mcs_exc is not None:
                print(
                    json.dumps(mcs_exc.to_envelope(), ensure_ascii=False),
                    file=sys.stderr,
                )
                inner_exit_code = mcs_exc.exit_code
            else:
                print(f"mcs internal error: {exc}", file=sys.stderr)
                if "--debug" in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                inner_exit_code = 1
    finally:
        # ---- post-dispatch: banner render ----
        # The banner is best-effort; render errors are swallowed.
        with contextlib.suppress(Exception):
            cache = read_cache()
            hard = is_hard_block(cache, current=__version__)
            if not banner_suppressed(sys.argv, hard_block=hard):
                banner = format_banner(cache, current=__version__)
                if banner is not None:
                    print(banner, file=sys.stderr)
            if (
                hard
                and (inner_exit_code is None or inner_exit_code == 0)
                and not banner_suppressed(sys.argv, hard_block=True)
            ):
                inner_exit_code = 2

    if inner_exit_code is None:
        inner_exit_code = 0
    _sys.exit(inner_exit_code)


_COMMAND_ORDER = [
    # Query (daily)
    "show",
    "sql",
    "meta",
    # Config (setup)
    "profile",
    "link",
    # Build (maintenance)
    "build",
    "status",
    # Semantic (package + metric)
    "package",
    "metric",
    # Memory (ongoing)
    "memory",
    # Skill (install)
    "skill",
    # UDF (rare)
    "udf",
    # Self-upgrade (rare)
    "update",
    # Diagnostic (debug)
    "doctor",
]


class _OrderedGroup(click.Group):
    """Click group that lists commands in a user-defined order instead of
    alphabetical. Unknown commands (not in _COMMAND_ORDER) appear at the
    end in alphabetical order."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        known = [c for c in _COMMAND_ORDER if c in self.commands]
        unknown = sorted(c for c in self.commands if c not in _COMMAND_ORDER)
        return known + unknown


@click.group(cls=_OrderedGroup)
@click.option("--config", envvar="MCS_CONFIG_DIR", help="config directory override")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["plain", "json", "yaml"]),
    default="plain",
    help=(
        "output format: 'plain' for human prose / tables (default), "
        "'json' for the structured envelope on stdout, 'yaml' for the "
        "same envelope serialized as YAML."
    ),
)
@click.option("-q", "--quiet", is_flag=True, help="only output essential identifiers")
@click.option("--debug", is_flag=True, help="enable DEBUG logging")
@click.option("--verbose", is_flag=True, help="enable INFO logging")
@click.version_option(__version__, "--version", "-V", prog_name="mcs")
@click.pass_context
def cli(
    ctx: click.Context,
    config: str | None,
    output_format: str,
    quiet: bool,
    debug: bool,
    verbose: bool,
) -> None:
    """maxcompute-semantic -- semantic-layer-aware MaxCompute CLI."""
    setup_logging(debug=debug, verbose=verbose, format=output_format)
    if config is not None:
        import os

        os.environ["MCS_CONFIG_DIR"] = config
    ctx.ensure_object(dict)
    ctx.obj["format"] = output_format
    ctx.obj["quiet"] = quiet


# Query (daily)
cli.add_command(show_cmd)
cli.add_command(sql_group)
cli.add_command(meta_group)
# Config (setup)
cli.add_command(profile_group)
cli.add_command(link_group)
# Build (maintenance)
cli.add_command(build_cmd)
cli.add_command(status_cmd)
# Semantic (package + metric)
cli.add_command(package_group)
cli.add_command(metric_group)
# Memory (ongoing)
cli.add_command(memory_group)
# Skill (install)
cli.add_command(skill_group)
# UDF (rare)
cli.add_command(udf_group)
# Self-upgrade (rare)
cli.add_command(update_cmd, name="update")
# Diagnostic (debug)
cli.add_command(doctor_cmd)
