"""``@profile_command`` — click subcommand decorator that injects a
:class:`ProfileContext` into the verb body.

Collapses the seven-line ladder that gates ~45 click subcommands
today (declare ``--project`` / ``--profile`` / ``--schema`` flags,
build a renderer from ``ctx.obj``, call
``_resolve_profile_for_project``, gate writes with
``reject_if_fork``, catch :class:`McsError` and exit with its
``exit_code``, fire ``commit_after_command`` at the success-path
tail) into a single decorator. The verb body becomes a function of
``ProfileContext + click.argument(s)``; the decorator owns
everything else.

Spec: ``docs/superpowers/specs/2026-05-25-mcs-profile-context-design.md``.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from typing import Any, TypeVar

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.errors.base import McsError
from maxcompute_semantic.versioning import commit_after_command

F = TypeVar("F", bound=Callable[..., Any])


def profile_command(
    group: click.Group,
    name: str,
    *,
    action: str | None = None,
    accepts_schema: bool = True,
    writeable: bool | None = None,
) -> Callable[[F], click.Command]:
    """Click subcommand decorator that injects a :class:`ProfileContext`.

    The decorated function takes ``pctx: ProfileContext`` as its
    first parameter; the click flag triple
    (``--project`` / ``--profile`` and optional ``--schema``) is
    added automatically. The decorator runs profile resolution,
    constructs the renderer from ``ctx.obj``, gates writes with
    ``reject_if_fork``, and wraps the body in the
    ``McsError → renderer.error → sys.exit`` ladder. When ``action``
    is set and the body calls ``pctx.success(..., commit_summary=...)``,
    the decorator fires :func:`commit_after_command` at the
    success-path tail.

    Args:
        group: the click group to attach to (e.g. ``memory_group``).
        name: subcommand name (e.g. ``"note"``).
        action: commit-action prefix (``ACTION_MEMORY_PREFIX``,
            ``ACTION_PACKAGE_PREFIX``, etc.). ``None`` means "read
            verb, no commit hook".
        accepts_schema: if True (default), the ``--schema`` flag is
            added. Set False for verbs that don't take a schema
            (memory / profile-management verbs).
        writeable: if True (the default for verbs with ``action``
            set), the decorator runs ``pctx.reject_if_fork()`` after
            resolution. ``None`` (default) means "infer from
            ``action``": writeable when ``action`` is set, read-only
            otherwise.

    The decorated function is registered as a click subcommand via
    ``group.command(name)`` — callers should not write
    ``@group.command()`` themselves.
    """

    if writeable is None:
        writeable = action is not None

    def deco(fn: F) -> click.Command:
        @functools.wraps(fn)
        def wrapper(
            ctx: click.Context,
            project: str | None,
            profile: str | None,
            **kwargs: Any,
        ) -> None:
            schema: str | None = kwargs.pop("schema", None) if accepts_schema else None
            renderer = _renderer_from_ctx(ctx)
            try:
                pctx = ProfileContext.resolve(
                    profile_name=profile,
                    project=project,
                    schema=schema,
                    renderer=renderer,
                )
                if writeable:
                    pctx.reject_if_fork()
            except McsError as exc:
                renderer.error(exc)
                sys.exit(exc.exit_code)
                return  # unreachable; satisfies type-checkers

            holder: dict[str, str] = {}
            pctx_held = pctx._with_commit_holder(holder)

            try:
                fn(pctx_held, **kwargs)
            except McsError as exc:
                renderer.error(exc)
                sys.exit(exc.exit_code)

            if action is not None:
                summary = holder.get("summary")
                if summary is not None:
                    commit_after_command(pctx.profile, action=action, summary=summary)

        # Decorators apply bottom-up. We want the rendered command
        # surface to list flags in the order ``--project`` /
        # ``--profile`` / (optional) ``--schema``, matching the
        # convention in the existing verbs — so apply the click
        # decorators in reverse declaration order.
        decorated: Callable[..., Any] = wrapper
        if accepts_schema:
            decorated = click.option(
                "--schema",
                default=None,
                help="schema (omit for 2-level; required for 3-level)",
            )(decorated)
        decorated = click.option(
            "--profile",
            default=None,
            help="profile name override",
        )(decorated)
        decorated = click.option(
            "--project",
            default=None,
            help="MaxCompute project name",
        )(decorated)
        decorated = click.pass_context(decorated)
        return group.command(name)(decorated)

    return deco


def _renderer_from_ctx(ctx: click.Context) -> Renderer:
    """Build a :class:`Renderer` from the click context's root ``obj``.

    The root ``obj`` is populated by the top-level ``mcs`` group
    callback in ``commands/__init__.py`` from the ``--format`` /
    ``--quiet`` flags. Verbs reach for it via ``ctx.find_root().obj``
    today; this helper centralizes the lookup so the nine duplicate
    ``_renderer(ctx)`` definitions across command modules collapse
    to one. PR2 of the migration removes the per-module copies.
    """
    obj = ctx.find_root().obj or {}
    return Renderer(
        format=obj.get("format", "plain"),
        quiet=obj.get("quiet", False),
    )
