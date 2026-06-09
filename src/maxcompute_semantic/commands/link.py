"""mcs link {status,unlink,bind} — cwd-to-profile binding commands."""

from __future__ import annotations

import os
import sys

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.auth.errors import (
    NoProfilesConfiguredError,
    ProfileNotFoundError,
    WorkingDirectoryError,
)
from maxcompute_semantic.auth.link_store import get_link, set_link
from maxcompute_semantic.auth.link_store import unlink as unlink_store
from maxcompute_semantic.auth.profile_store import load_all


@click.group(name="link", invoke_without_command=True)
@click.pass_context
def link_group(ctx: click.Context) -> None:
    """Bind current directory to a profile, or show/unlink the binding."""
    if ctx.invoked_subcommand is not None:
        return
    # Bare invocation: treat as "link bind" with interactive resolution
    _bind_action(ctx, name=None)


@link_group.command("bind")
@click.argument("name", required=False)
@click.pass_context
def bind_cmd(ctx: click.Context, name: str | None) -> None:
    """Bind cwd to a profile (interactive if no name given)."""
    _bind_action(ctx, name=name)


@link_group.command("status")
@click.option("--verbose", "-v", is_flag=True, help="show bound profile's source details")
@click.pass_context
def status_cmd(ctx: click.Context, verbose: bool) -> None:
    """Show current cwd binding."""
    r = _renderer(ctx)
    cwd = _cwd()
    bound = get_link(cwd)
    if bound is None:
        r.success({"cwd": cwd, "profile": None, "note": "no binding"})
        r.quiet_essential({"profile": "none"}, "profile")
        return

    # Check if profile still exists (warn if stale)
    try:
        from maxcompute_semantic.auth.profile_store import get

        profile = get(bound)
    except ProfileNotFoundError:
        r.success({"cwd": cwd, "profile": bound, "stale": True, "note": "profile no longer exists"})
        r.quiet_essential({"profile": bound}, "profile")
        return

    if verbose:
        source_list = [s.source_key() for s in profile.sources]
        r.success(
            {
                "cwd": cwd,
                "profile": bound,
                "source_count": len(profile.sources),
                "sources": source_list,
            }
        )
    else:
        r.success({"cwd": cwd, "profile": bound})
    r.quiet_essential({"profile": bound}, "profile")


@link_group.command("unlink")
@click.pass_context
def unlink_cmd(ctx: click.Context) -> None:
    """Remove cwd binding."""
    r = _renderer(ctx)
    cwd = _cwd()
    unlink_store(cwd)
    r.success({"unlinked": cwd})


def _bind_action(ctx: click.Context, name: str | None) -> None:
    """Core bind logic: resolve or prompt, then set link."""
    r = _renderer(ctx)
    cwd = _cwd()

    if name is None:
        # Interactive: show available profiles and let user pick.
        profiles = load_all()
        if not profiles:
            r.error(
                NoProfilesConfiguredError(
                    "no profiles configured",
                    remediation="run `mcs profile create` to add a profile",
                )
            )
            sys.exit(3)
        existing = get_link(cwd)
        choices = sorted(profiles.keys())
        from maxcompute_semantic.commands._source_picker import _pick_one

        prompt = "Select profile to bind"
        if existing is not None:
            prompt += f" (current: {existing})"
        name = _pick_one(prompt, choices=choices, default=existing)
        if name is None:
            click.echo("  (kept current binding)")
            return

    # Validate that the profile exists
    try:
        from maxcompute_semantic.auth.profile_store import get

        get(name)
    except ProfileNotFoundError as e:
        r.error(e)
        sys.exit(e.exit_code)

    set_link(cwd, name)
    r.success({"linked": cwd, "profile": name})
    r.quiet_essential({"profile": name}, "profile")


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(
        format=obj.get("format", "plain"),
        quiet=obj.get("quiet", False),
    )


def _cwd() -> str:
    """Return current working directory; raise WorkingDirectoryError if unavailable."""
    try:
        return os.getcwd()
    except OSError as e:
        raise WorkingDirectoryError(
            f"cannot determine current working directory: {e}",
            remediation="ensure cwd is accessible or use absolute paths",
        ) from e
