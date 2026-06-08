# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Drill-down source picker — used by ``commands/_profile_editor`` to
build a ``DataSource`` interactively (project → schema → tables →
columns), with fzf fuzzy matching for all selection steps and
questionary checkbox for column visibility (which needs "pre-checked,
uncheck to exclude" semantics fzf can't express).

The picker is the **human side** of source construction; for
non-interactive callers (agents, scripts), ``mcs profile update
PROFILE --from-spec '<json>'`` takes the full-profile spec including
sources and is the canonical machine-readable entry point — see
``commands/_profile_editor.edit_profile`` and ``commands/profile.update_cmd``
for the ``edit_profile``-driven user flow and the ``--from-spec`` /
``--from-file`` non-interactive path.

The two LIST verbs that back the picker (``mcs meta list-projects``
and ``list-schemas``) live in ``commands/profile.py`` for both
interactive and agent use; ``mcs meta list-tables`` /
``describe-table`` cover the inner two levels of the same drill-down.
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING, Any

import click
import questionary
from questionary import Style

from maxcompute_semantic.auth.schema import DataSource, TableSpec
from maxcompute_semantic.mc_client.errors import McsError

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.client import MaxComputeClient

# ── fzf availability ──────────────────────────────────────────────────
#
# ``iterfzf`` bundles its own ``fzf`` binary in the wheel — no
# separate system install required.  When the package is installed
# (it's a regular dependency in pyproject.toml) we use it for ALL
# selection steps — no threshold switching.  When it's not importable
# (shouldn't happen in production, but defensive) we fall back to
# questionary select/checkbox with search filter.

try:
    from iterfzf import iterfzf as _iterfzf  # type: ignore[import-untyped]
except ImportError:
    _iterfzf = None


# Same style as ``_profile_editor._EDITOR_STYLE`` — duplicated here
# rather than imported to avoid an import cycle (the editor module
# imports helpers from this one). Keep them in sync if either is
# touched.
_PICKER_STYLE = Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green bold"),
        ("separator", "fg:#6c757d"),
        ("instruction", "fg:#6c757d italic"),
    ]
)

_COLUMN_OTHER_SENTINEL = "<other: type a column name to hide>"


# ── Dev/prod naming heuristic ─────────────────────────────────────────
#
# Convention: in DataWorks standard mode the dev project is named
# "<base>_dev" and the corresponding production project is "<base>".
# This is a convention, not a rule — projects without "_dev" suffix
# are treated as "we don't know" and the heuristic returns None /
# leaves order unchanged. The wizard uses this only to nudge picker
# defaults; the user can always override.

_DEV_SUFFIX = "_dev"


def _is_dev_name(name: str) -> bool:
    """True when ``name`` ends with the ``_dev`` suffix and has a non-empty stem."""
    return name.endswith(_DEV_SUFFIX) and len(name) > len(_DEV_SUFFIX)


def _prod_counterpart(name: str) -> str | None:
    """Return the prod counterpart of a ``*_dev`` project name, or None.

    ``acme_dev`` → ``acme``. ``acme`` → None. ``""`` → None. The result
    is *the conventional name* of the prod project; whether such a
    project actually exists / is accessible is a caller concern.
    """
    if not _is_dev_name(name):
        return None
    return name[: -len(_DEV_SUFFIX)]


def _reorder_for_role(
    projects: list[str],
    *,
    role: str | None,
    default: str | None,
) -> list[str]:
    """Re-order ``projects`` so the role-appropriate candidates come first.

    fzf ignores ``default=`` for single-select pickers — it only respects
    the order of ``choices`` and lands the cursor on row 0. So this
    function is the *only* lever for "highlight the right candidate".

    - ``role="compute"``: ``*_dev`` projects rise to the top (relative
      order preserved); other projects follow. The user usually wants
      a dev project as their SQL-execution environment.
    - ``role="source"``: if ``default`` is a ``*_dev`` name, the prod
      counterpart goes first (when present in the list), then dev
      itself, then everything else. The user usually wants prod data
      to query but should also be able to pick dev with one keystroke.
    - ``role=None`` or unrecognized: unchanged.
    """
    if role == "compute":
        dev = [p for p in projects if _is_dev_name(p)]
        rest = [p for p in projects if not _is_dev_name(p)]
        return dev + rest

    if role == "source" and default is not None:
        prod = _prod_counterpart(default)
        if prod is None:
            return list(projects)
        head: list[str] = []
        if prod in projects:
            head.append(prod)
        if default in projects and default not in head:
            head.append(default)
        tail = [p for p in projects if p not in head]
        return head + tail

    return list(projects)


# ── Spinner ───────────────────────────────────────────────────────────


class _Spinner:
    """Thread-based braille spinner printed to stderr.

    Wraps slow MaxCompute API calls (list_projects, list_schemas,
    list_tables, describe_table) so the terminal doesn't appear frozen.
    Clears itself on exit via ANSI ``\\r\\x1b[K``.
    """

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text: str) -> None:
        self._text = text
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def __enter__(self) -> _Spinner:
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        sys.stderr.write("\r\x1b[K")
        sys.stderr.flush()

    def _spin(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            sys.stderr.write(f"\r{frame} {self._text}")
            sys.stderr.flush()
            idx += 1
            self._stop.wait(0.08)


# ── fzf query persistence ─────────────────────────────────────────────


class _LastFzfQuery:
    value: str = ""


_last_fzf_query = _LastFzfQuery()


def last_fzf_query() -> str:
    """Return the query the user typed in the most recent fzf picker."""
    return _last_fzf_query.value


# ── Unified pickers ───────────────────────────────────────────────────


__all__ = ["pick_source", "_pick_choice", "_pick_one", "_pick_many", "_Spinner", "last_fzf_query"]


def _format_echo_value(value: object) -> str:
    """Render a single value or sequence for the post-pick echo line."""
    if isinstance(value, list):
        if not value:
            return "(none)"
        n = len(value)
        if n <= 3:
            return f"({n}): {', '.join(value)}"
        return f"({n}): {', '.join(value[:3])}, …"
    return str(value)


def _echo_pick(label: str, emoji: str | None, value: object) -> None:
    """Print a single green confirmation line to stdout after a successful fzf pick.

    Skipped when label is None (caller didn't opt in) or when running through
    the questionary fallback (questionary echoes its own answer line).
    """
    rendered = _format_echo_value(value)
    if isinstance(value, list):
        msg = f"  ✓ {emoji + ' ' if emoji else ''}{label} {rendered}"
    else:
        msg = f"  ✓ {emoji + ' ' if emoji else ''}{label}: {rendered}"
    click.secho(msg, fg="green")


def _pick_one(
    question: str,
    choices: list[str],
    *,
    default: str | None = None,
    echo_label: str | None = None,
    echo_emoji: str | None = None,
    header: str | None = None,
) -> str | None:
    """Select one item. Always fzf when available, questionary fallback.

    ``default`` is only used by the questionary fallback — fzf has no
    pre-selection concept for single-select.

    ``header`` is a multi-line hint shown above the choice list. fzf
    renders it via its native ``--header`` flag (visible during the
    full-screen selection); the questionary fallback prints it once
    to stderr before the picker opens.
    """
    if _iterfzf is not None:
        # Esc returns None, Ctrl+C raises KeyboardInterrupt → caught
        # and collapsed to None (same as Esc — both mean "back").
        try:
            result = _iterfzf(choices, prompt=question, header=header or "")
        except KeyboardInterrupt:
            return None
        if result is not None and echo_label is not None:
            _echo_pick(echo_label, echo_emoji, result)
        return result  # type: ignore[no-any-return]

    # Questionary fallback: .ask() returns None on both Esc and Ctrl+C.
    if header:
        click.secho(header, fg="cyan", err=True)
    res = questionary.select(
        question,
        choices=choices,
        default=default,
        style=_PICKER_STYLE,
        use_search_filter=True,
        use_jk_keys=False,
        instruction="(type to filter · Esc to back out)",
    ).ask()
    return res  # type: ignore[no-any-return]


def _pick_many(
    prompt: str,
    items: list[str],
    *,
    pre_selected: set[str] | None = None,
    echo_label: str | None = None,
    echo_emoji: str | None = None,
) -> list[str] | None:
    """Multi-select. Always fzf multi when available, questionary fallback.

    fzf multi returns only Tab-marked items; ``pre_selected`` items not
    re-marked are unioned back in so existing selections aren't silently
    dropped.  Result is ``sorted()`` for deterministic order.

    questionary fallback: checkbox with search filter, ``pre_selected``
    items start checked.
    """
    if _iterfzf is not None:
        # Esc returns None, Ctrl+C raises KeyboardInterrupt → caught
        # and collapsed to None (same as Esc — both mean "back").
        try:
            result = _iterfzf(items, prompt=prompt, multi=True)
        except KeyboardInterrupt:
            return None
        if result is None:
            return None
        effective = pre_selected or set()
        merged = sorted(set(result) | effective)
        if echo_label is not None:
            _echo_pick(echo_label, echo_emoji, merged)
        return merged

    # Questionary fallback: .ask() returns None on both Esc and Ctrl+C.
    effective = pre_selected or set()
    choices = [questionary.Choice(title=t, value=t, checked=(t in effective)) for t in items]
    res = questionary.checkbox(
        prompt,
        choices=choices,
        style=_PICKER_STYLE,
        use_jk_keys=False,
        use_search_filter=True,
        instruction=("(type to filter · space=toggle · a=all · i=invert · Enter=confirm)"),
    ).unsafe_ask()
    return res  # type: ignore[no-any-return]


def _pick_columns_to_hide(
    *,
    cols: list[dict[str, Any]],
    part_cols: set[str],
    table_name: str,
    pre_excluded: tuple[str, ...] = (),
) -> list[str] | None:
    """fzf-multi 'mark cols to HIDE' column picker.

    fzf path: marked items become the hide list directly; empty marks → keep
    all visible. Manual-entry sentinel (``<other:>``) handled in
    ``_pick_columns_exclude``'s post-processing — see Task 4.

    questionary fallback: keeps the historical 'all pre-checked, uncheck to
    hide' semantic (questionary supports pre-checked; flipping its semantic
    would only hurt the fallback path).

    Returns the list of column names to exclude, or None on Esc.
    """
    items: list[str] = []
    for c in cols:
        title = f"{c['name']:<30} {c['type']}"
        if c["name"] in part_cols:
            title += "  [partition]"
        if c.get("comment"):
            title += f"  — {c['comment'][:40]}"
        items.append(title)

    if pre_excluded:
        click.secho(
            f"  ⓘ Currently hiding {len(pre_excluded)} col(s): "
            f"{', '.join(pre_excluded)} — re-mark the full set you want "
            f"hidden in the new state.",
            fg="cyan",
            err=True,
        )

    click.secho(
        "  ⓘ Column filter is an agent-VIEW filter, not access control. "
        "Server-side data access is gated by MaxCompute LabelSecurity / GRANT.",
        fg="cyan",
    )

    if _iterfzf is not None:
        items_with_sentinel = items + [_COLUMN_OTHER_SENTINEL]
        prompt = (
            f"Tab cols to hide from agent for `{table_name}` "
            f"(Enter without marking = keep all visible)"
        )
        try:
            result = _iterfzf(items_with_sentinel, prompt=prompt, multi=True)
        except KeyboardInterrupt:
            return None
        if result is None:
            return None
        marked = list(result)
        wants_manual = _COLUMN_OTHER_SENTINEL in marked
        excluded = [r.split()[0] for r in marked if r != _COLUMN_OTHER_SENTINEL]
        if wants_manual:
            excluded.extend(_prompt_manual_columns())
        if excluded:
            click.secho(
                f"  ✓ ✂️ Hide cols ({len(excluded)}): {', '.join(excluded[:3])}"
                f"{', …' if len(excluded) > 3 else ''}",
                fg="green",
            )
        else:
            click.secho("  ✓ 📋 Hide cols: none (all visible)", fg="green")
        return excluded

    # Questionary fallback: 'all pre-checked, uncheck to hide' (legacy semantic).
    pre_excluded_set = set(pre_excluded)
    choices = [
        questionary.Choice(
            title=items[i],
            value=cols[i]["name"],
            checked=(cols[i]["name"] not in pre_excluded_set),
        )
        for i in range(len(cols))
    ]
    visible = questionary.checkbox(
        f"Columns visible to agent for `{table_name}`",
        choices=choices,
        style=_PICKER_STYLE,
        use_jk_keys=False,
        use_search_filter=True,
        instruction=(
            "(type to filter · space=toggle · a=all · i=invert · "
            "checked = visible to agent · partitions kept regardless · "
            "Enter=confirm)"
        ),
    ).ask()
    if visible is None:
        return None
    all_names = [c["name"] for c in cols]
    return [n for n in all_names if n not in set(visible)]


def _pick_choice(
    question: str,
    choices: list[questionary.Choice | questionary.Separator],
    *,
    default: Any | None = None,
    style: Style | None = None,
    instruction: str = "(Esc to back out)",
    echo_label: str | None = None,
    echo_emoji: str | None = None,
    query: str = "",
) -> Any | None:
    """Select one from ``questionary.Choice`` objects (title ≠ value).

    For fzf: display titles, map back to values via title→value dict.
    Separators and disabled choices are skipped.  For questionary
    fallback: use ``questionary.select`` directly (which handles
    separators, disabled, and title≠value natively).

    ``query`` pre-fills the fzf search box, useful for preserving the
    user's filter across drill-in/drill-out cycles.

    Used by the profile editor's action menus where emoji-rich titles
    differ from short-string values (e.g. title="✅ Include with all
    columns visible", value="INCLUDE_ALL").
    """
    if _iterfzf is not None:
        fzf_items: list[str] = []
        title_to_value: dict[str, Any] = {}
        for c in choices:
            if isinstance(c, questionary.Separator):
                continue
            if c.disabled:
                continue
            title = str(c.title)
            fzf_items.append(title)
            title_to_value[title] = c.value
        # iterfzf returns None on Esc/no-match, the selected string on Enter.
        # KeyboardInterrupt (Ctrl+C) is caught and collapsed to None
        # (same as Esc — both mean "go back one level").
        try:
            raw = _iterfzf(
                fzf_items, prompt=question, query=query, print_query=True,
            )
        except KeyboardInterrupt:
            return None
        if raw is None:
            return None
        # print_query=True → raw is (query_typed, selected_item | None).
        # Older iterfzf/test doubles return just the selected string; keep
        # that shape working, but only query persistence is unavailable.
        if isinstance(raw, tuple) and len(raw) == 2:
            query_typed, selected = raw
        else:
            query_typed, selected = query, raw
        _last_fzf_query.value = query_typed or ""
        if selected is None:
            return None
        if echo_label is not None:
            _echo_pick(echo_label, echo_emoji, selected)
        return title_to_value.get(selected)

    # Questionary fallback: .ask() returns None on both Esc and Ctrl+C.
    effective_style = style or _PICKER_STYLE
    res = questionary.select(
        question,
        choices=choices,
        default=default,
        style=effective_style,
        instruction=instruction,
    ).ask()
    return res


def pick_source(
    client: MaxComputeClient,
    *,
    default_project: str | None = None,
    existing: DataSource | None = None,
    cached_projects: list[str] | None = None,
) -> DataSource | None:
    """Drill-down picker: project → schema → tables → columns.

    Args:
        client: Authenticated MaxComputeClient (auth must already work
            — caller is responsible for that). Used for live LIST
            queries via the v0.4 ``project=`` / ``schema=`` kwargs.
        default_project: Pre-fill suggestion for the project prompt
            — typically ``profile.compute_project`` on the first source
            (most users have AK home project = data project).
        existing: Pre-fill picker selections from an existing
            DataSource (used by the interactive ``mcs profile update``
            editor when re-entering an existing source). When set,
            the picker offers the existing values as the current
            selection and the user can keep / modify.
        cached_projects: Pre-fetched project list from a prior
            ``list_projects()`` call (e.g. from compute_project
            discovery). Avoids re-querying the API.

    Returns:
        The picked ``DataSource``, or ``None`` if the user aborts
        (Ctrl-C, or selects "cancel" in any step).
    """
    project = _pick_project(
        client,
        default=default_project,
        existing=existing,
        cached_projects=cached_projects,
        role="source",
    )
    if project is None:
        return None

    schema = _pick_schema(client, project=project, existing=existing)
    if schema is None:
        return None

    tables = _pick_tables(client, project=project, schema=schema, existing=existing)
    if tables is None:
        return None

    return DataSource(project=project, schema=schema, tables=tables)


# ── Internal drill-down helpers ───────────────────────────────────────


def _prompt_project_name(suggested: str | None) -> str | None:
    """Prompt for a project name, offering ``suggested`` as a one-keystroke
    accept-default if non-empty.

    When ``suggested`` is provided (typically ``compute_project`` —
    the AK's home project), first asks "Use same as compute_project
    ('<x>')? [Y/n]" so the common case (AK reads from its own home
    project) is a single keystroke. If user declines, falls through
    to a manual-entry prompt with empty-string guard.

    Bare ``click.prompt(default="")`` accepts a blank Enter as ``""``,
    which downstream blows up on ``list_schemas(project="")`` with an
    opaque pyodps error. The manual fallback loops until the user
    types a non-empty value or aborts via Ctrl-C (caught and surfaced
    as ``None``).
    """
    if suggested:
        try:
            if click.confirm(f"Use same as compute_project ({suggested!r})?", default=True):
                return suggested
        except click.exceptions.Abort:
            return None
    while True:
        try:
            answer = click.prompt("Project name (type explicitly)")
        except click.exceptions.Abort:
            return None
        answer = (answer or "").strip()
        if answer:
            return answer
        click.echo("  Project name must be non-empty.", err=True)


def _pick_project(
    client: MaxComputeClient,
    *,
    default: str | None,
    existing: DataSource | None,
    cached_projects: list[str] | None,
    role: str | None = None,
) -> str | None:
    """Step 1: pick the data project.

    Order:
      - existing.project (when updating) is the suggested default
      - else default kwarg (compute_project on first wizard call)
      - else first project from list_projects()

    On ``list_projects()`` failure (e.g. internal endpoint without
    project-list API): warn + fall back to manual ``click.prompt``
    text input. This is the only "soft" failure tier; downstream
    list_schemas / list_tables / describe_table failures hard-abort.

    ``cached_projects`` skips the ``list_projects()`` call when the
    caller already has a project list (e.g. from compute_project
    discovery in the create wizard).

    ``role`` (optional) tunes the picker for the *purpose* of the pick
    so the user gets a relevant tip and the most likely candidate
    leads the choice list. ``"compute"`` (SQL execution environment —
    usually a ``*_dev`` project) and ``"source"`` (data source — usually
    the prod counterpart of the compute project) are the two recognized
    values; anything else (including the default ``None``) leaves the
    picker behavior untouched. See ``_reorder_for_role`` for the
    ordering rules.
    """
    suggested = (existing.project if existing else None) or default

    # Use cached projects when available (avoids re-querying the API).
    if cached_projects is not None:
        projects = cached_projects
    else:
        try:
            with _Spinner("Listing projects..."):
                projects = client.list_projects()
        except McsError as e:
            click.echo(
                f"⚠ Could not list projects [{e.code}]: {e.message}\n"
                f"  {e.remediation}\n"
                f"  Falling back to manual entry — your AK can still read from\n"
                f"  any project it has Select permission on, you just can't enumerate.",
                err=True,
            )
            return _prompt_project_name(suggested)
        except Exception as e:
            click.echo(
                f"⚠ Could not list projects ({type(e).__name__}: {e}).\n"
                f"  Falling back to manual entry — your AK can still read from\n"
                f"  any project it has Select permission on, you just can't enumerate.",
                err=True,
            )
            return _prompt_project_name(suggested)

    if not projects:
        click.echo(
            "⚠ list_projects returned 0 projects. This is normal for AKs "
            "with project-scoped\n"
            "  permissions (most LTAI keys lack catalog-level enumeration "
            "rights). Your AK can\n"
            "  still SELECT from any project it has Read permission on; "
            "type the project name\n"
            "  below to add it as a source.",
            err=True,
        )
        return _prompt_project_name(suggested)

    # Picker path: list discovered projects + an always-on "<other:>"
    # escape hatch for projects the AK can SELECT but not enumerate.
    other_row = "<other: type project name manually>"

    # Role-aware reorder: for "compute", *_dev rises to top; for
    # "source", prod-counterpart-of-default leads. <other:> always
    # stays last so the escape hatch isn't visually mistaken for a
    # data project. See _reorder_for_role for details.
    reordered = _reorder_for_role(list(projects), role=role, default=suggested)
    choices = reordered + [other_row]

    # Role-aware prompt label + header. The header is rendered by fzf
    # *during* selection (via fzf's --header flag) so the hint is
    # visible while the user is choosing — printing it to stderr
    # before _iterfzf would scroll past the user under fzf's
    # full-screen UI.
    if role == "compute":
        prompt_label = "Compute project (where SQL executes — usually a *_dev project):"
        header_text: str | None = (
            "ⓘ This project is your SQL execution environment — usually a "
            "dev project (often '*_dev'),\n"
            "  where your AK has permission to run jobs and write scratch tables."
        )
    elif role == "source":
        prompt_label = "Data source (the project whose tables you'll query — usually production):"
        header_text = (
            "ⓘ The data source is the project whose tables you'll query — "
            "usually production\n"
            "  (the same name without '_dev'), serving as a read-only real "
            "data source.\n"
            "  Picking dev is fine for self-contained workspaces."
        )
    else:
        prompt_label = "Project (data source's MaxCompute project):"
        header_text = None

    answer = _pick_one(
        prompt_label,
        choices=choices,
        default=suggested if suggested in projects else None,
        echo_label="Project",
        echo_emoji="🎯",
        header=header_text,
    )

    if answer is None:
        return None  # Esc / Ctrl+C
    if answer == other_row:
        return _prompt_project_name(suggested)
    return answer


def _pick_schema(
    client: MaxComputeClient,
    *,
    project: str,
    existing: DataSource | None,
) -> str | None:
    """Step 2: pick the schema within the project.

    Three failure tiers:
    - **2-level project** (the project doesn't have a schema layer at
      all — MaxCompute returns ``"is not 3-tier model project"``):
      auto-pick ``"default"`` without prompting. This is the standard
      MC convention for 2-level projects: bare-table refs land in the
      synthetic ``default`` slot.
    - **list_schemas raises any other error** (auth scope, network):
      offer a manual-entry prompt with ``existing.schema`` (or
      ``"default"``) as the default. AKs with project-scoped permission
      may not see the catalog API but can still address tables in
      schemas they have Read on.
    - **list_schemas returns 0 schemas** (rare): same fallback as
      raise — manual-entry prompt.

    Successful enumeration: 1 schema → auto-pick it; 2+ → fzf picker
    with an "<other: type a name>" escape hatch for the case where the
    user knows of a schema not returned by ``list_schemas`` (common
    when the AK has read permission on a specific schema but not
    catalog-listing permission across the whole project).
    """
    try:
        with _Spinner("Listing schemas..."):
            schemas = client.list_schemas(project=project)
    except McsError as e:
        msg = e.message
        if "not 3-tier" in msg or "not a 3-tier" in msg or "is not 3-tier" in msg:
            click.secho(
                f"  ⓘ {project!r} is a 2-level project (no schema layer). Using 'default' slot.",
                fg="cyan",
                err=True,
            )
            return "default"
        click.secho(
            f"  ⚠ list_schemas denied [{e.code}]: {e.message}\n"
            f"  {e.remediation}\n"
            f"  Falling back to manual entry.",
            fg="yellow",
            err=True,
        )
        return _prompt_schema_name(existing.schema if existing else "default")
    except Exception as e:
        msg = str(e)
        if "not 3-tier" in msg or "not a 3-tier" in msg or "is not 3-tier" in msg:
            click.secho(
                f"  ⓘ {project!r} is a 2-level project (no schema layer). Using 'default' slot.",
                fg="cyan",
                err=True,
            )
            return "default"
        click.secho(
            f"  ⚠ list_schemas failed ({type(e).__name__}: {e}).\n  Falling back to manual entry.",
            fg="yellow",
            err=True,
        )
        return _prompt_schema_name(existing.schema if existing else "default")

    if not schemas:
        click.secho(
            f"  ⚠ list_schemas returned 0 schemas for {project!r}.\n"
            f"  This is normal for AKs with project-scoped permissions.\n"
            f"  Type the schema name below.",
            fg="yellow",
            err=True,
        )
        return _prompt_schema_name(existing.schema if existing else "default")

    # 2-level case: just one schema. Auto-pick.
    if len(schemas) == 1:
        click.echo(f"  Schema: {schemas[0]} (only schema in {project})")
        return schemas[0]

    suggested = existing.schema if existing else "default"
    # Always offer an "<other:>" escape hatch so users can type a
    # schema name not returned by list_schemas (catalog scope gaps).
    choices = list(schemas) + ["<other: type a schema name>"]
    answer = _pick_one(
        f"Schema (within {project}):",
        choices=choices,
        default=suggested if suggested in schemas else None,
        echo_label="Schema",
        echo_emoji="🗂",
    )
    if answer is None:
        return None
    if answer.startswith("<other:"):
        return _prompt_schema_name(suggested)
    return answer


def _prompt_schema_name(suggested: str) -> str | None:
    """Manual-entry schema prompt with empty-string guard.

    Used when ``list_schemas`` failed / returned empty / the user
    picked the "<other: type a schema name>" escape hatch in the
    discovered-list picker.
    """
    while True:
        try:
            answer = click.prompt(
                "Schema name (type explicitly)",
                default=suggested,
                show_default=True,
            )
        except click.exceptions.Abort:
            return None
        answer = (answer or "").strip()
        if answer:
            return answer
        click.echo("  Schema name must be non-empty.", err=True)


def _pick_tables(
    client: MaxComputeClient,
    *,
    project: str,
    schema: str,
    existing: DataSource | None,
) -> tuple[TableSpec, ...] | str | None:
    """Step 3: pick tables (with optional per-table column scoping).

    First asks "include all tables (wildcard) or pick specific?"
    Wildcard returns ``"*"`` — lazy, future tables auto-included on
    next ``mcs build``.

    Specific mode: fzf (if available) or questionary checkbox.
    fzf's multi-select naturally supports accumulation across
    filter changes — Tab marks, change query, Tab more, Enter.

    For each picked table, asks "configure column visibility?" — if
    yes, opens another picker with all columns pre-checked, the
    unchecked set goes into ``columns_exclude`` (matching the natural
    "uncheck the sensitive ones" mental model — new columns added
    later remain visible by default).
    """
    with _Spinner("Listing tables..."):
        table_objs = client.list_tables(project=project, schema=schema)
    if not table_objs:
        click.echo(
            f"⚠ list_tables returned 0 tables in {project}.{schema}; "
            f"defaulting to wildcard '*' (will pick up tables when they're added).",
            err=True,
        )
        return "*"

    mode_choices = ["all (wildcard '*' — future tables auto-included)", "pick specific tables"]
    existing_is_enumerated = existing is not None and not isinstance(existing.tables, str)
    default_mode = mode_choices[1] if existing_is_enumerated else mode_choices[0]
    mode = _pick_one(
        "Include which tables?",
        choices=mode_choices,
        default=default_mode,
    )
    if mode is None:
        return None
    if mode.startswith("all"):
        return "*"

    # Specific picker — fzf multi or questionary checkbox.
    pre_checked: set[str] = set()
    pre_excluded: dict[str, tuple[str, ...]] = {}  # table_name → cols_exclude
    pre_whitelisted: dict[str, tuple[str, ...]] = {}  # table_name → cols whitelist
    if existing_is_enumerated:
        for ts in existing.tables:  # type: ignore[union-attr]
            pre_checked.add(ts.name)  # type: ignore[union-attr]
            if ts.columns_exclude:  # type: ignore[union-attr]
                pre_excluded[ts.name] = ts.columns_exclude  # type: ignore[union-attr]
            if ts.columns is not None:  # type: ignore[union-attr]
                pre_whitelisted[ts.name] = ts.columns  # type: ignore[union-attr]

    selected_names = _pick_many(
        f"Tables in {project}.{schema} ({len(table_objs)} total)",
        items=list(table_objs),
        pre_selected=pre_checked,
    )

    if selected_names is None:
        return None
    if not selected_names:
        click.echo(
            "⚠ No tables selected. Defaulting to wildcard '*'.",
            err=True,
        )
        return "*"

    # Per-table column scoping (opt-in).
    table_specs: list[TableSpec] = []
    for tname in selected_names:
        # Whitelist (``columns=[...]``) is only set via the ``--source-spec``
        # JSON path; the TUI only edits blacklist (``columns_exclude``).
        # Entering the blacklist picker on a whitelisted table would
        # silently drop the whitelist (the picker pre-checks all columns,
        # diffs the unchecked set, returns columns_exclude — losing the
        # whitelist constraint entirely). Preserve as-is and tell the
        # user how to edit it.
        if tname in pre_whitelisted:
            click.echo(
                f"  ⓘ `{tname}` has a column whitelist (`columns=[...]`); "
                f"the TUI only edits blacklist (`columns_exclude`). "
                f"Preserving whitelist as-is — use `--source-spec` JSON "
                f"to edit it.",
                err=True,
            )
            table_specs.append(TableSpec(name=tname, columns=pre_whitelisted[tname]))
            continue

        has_blacklist = tname in pre_excluded
        prompt = f"Configure column visibility for `{tname}`?"
        if has_blacklist:
            prompt += " (currently has scope — answer y to modify, n to keep as-is)"
        configure_cols = click.confirm(prompt, default=has_blacklist)
        if not configure_cols:
            # Preserve existing blacklist scope when updating + user said no.
            if has_blacklist:
                table_specs.append(TableSpec(name=tname, columns_exclude=pre_excluded[tname]))
            else:
                table_specs.append(TableSpec(name=tname))
            continue

        cols_exclude = _pick_columns_exclude(
            client,
            project=project,
            schema=schema,
            table_name=tname,
            pre_excluded=pre_excluded.get(tname, ()),
        )
        if cols_exclude is None:
            # Cancelled this table's column picker — keep table without scope.
            table_specs.append(TableSpec(name=tname))
            continue
        table_specs.append(
            TableSpec(name=tname, columns_exclude=tuple(cols_exclude))
            if cols_exclude
            else TableSpec(name=tname)
        )

    return tuple(table_specs)


def _pick_columns_exclude(
    client: MaxComputeClient,
    *,
    project: str,
    schema: str,
    table_name: str,
    pre_excluded: tuple[str, ...],
) -> list[str] | None:
    """Wrapper: describe_table + delegate to _pick_columns_to_hide."""
    try:
        with _Spinner(f"Describing columns of {table_name}..."):
            desc = client.describe_table(table_name, project=project, schema=schema)
    except McsError as e:
        click.secho(
            f"  ⚠ describe_table denied [{e.code}]: {e.message}\n"
            f"  {e.remediation}\n"
            f"  Falling back to manual entry — type column name(s) to hide.",
            fg="yellow",
            err=True,
        )
        return _prompt_manual_columns()
    cols = desc["table"]["schema"]
    part_cols = {c["name"] for c in desc["table"].get("partition_columns") or []}
    return _pick_columns_to_hide(
        cols=cols,
        part_cols=part_cols,
        table_name=table_name,
        pre_excluded=pre_excluded,
    )


def _prompt_manual_columns() -> list[str]:
    """Loop click.prompt → click.confirm until the user says no.

    Used by both the ``<other:>`` sentinel branch and the describe-denied
    fallback. Returns the list of typed column names; empty if the user
    aborts the very first prompt.
    """
    names: list[str] = []
    while True:
        try:
            name = click.prompt("Column name to hide (type explicitly)").strip()
        except click.exceptions.Abort:
            return names
        if not name:
            click.echo("  Column name must be non-empty.", err=True)
            continue
        names.append(name)
        if not click.confirm("Add another?", default=False):
            return names


# ── Display helpers ───────────────────────────────────────────────────


def _summarize_tables(tables: tuple[TableSpec, ...] | str) -> str:
    if isinstance(tables, str):
        return "wildcard '*'"
    n = len(tables)
    n_scoped = sum(1 for ts in tables if ts.columns is not None or ts.columns_exclude)
    if n_scoped:
        return f"{n} tables, {n_scoped} with column scope"
    return f"{n} tables"
