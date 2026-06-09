"""Multi-level file-browser-style profile editor.

Single entry point ``edit_profile(profile, client)`` — opens a
top-level menu listing every editable section of a ``Profile``
(compute_project / endpoint / auth / cost_thresholds / tags /
sources). User drills into a section, edits, ``↩ Back`` to the top
level. ``✓ Save and exit`` at top-level commits; ``✗ Cancel`` discards.

State is held as an immutable ``Profile`` draft in the entry function;
each section editor returns an updated draft (via
``dataclasses.replace``) or the unchanged one if the user backed out.
There is no shared mutable state — the draft is rebuilt each round.

The sources section is the only one that needs a ``MaxComputeClient``
(for live drill-down via the existing ``pick_source`` /
``_pick_columns_exclude`` helpers in ``commands/_source_picker.py``);
the other sections are pure prompts and don't touch the network.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import click
import questionary
from questionary import Style

from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    ProcessAuth,
    Profile,
    TableSpec,
)
from maxcompute_semantic.commands._identity import mask_ak_id
from maxcompute_semantic.commands._source_picker import (
    _pick_choice,
    _pick_columns_exclude,
    _pick_project,
    _pick_schema,
    _Spinner,
    last_fzf_query,
)
from maxcompute_semantic.mc_client.errors import McsError

# ``pick_source`` (the linear ADD-SOURCE wizard) is intentionally
# NOT imported here — earlier versions used it for the "+ Add new
# source" flow, but the v0.4.0a3 nav-style editor builds the source
# incrementally via ``_pick_project`` + ``_pick_schema`` then drops
# straight into ``_edit_source`` for table/column selection. The
# linear wizard remains in ``_source_picker.py`` for direct callers
# (currently the unit tests testing the helper in isolation).

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.client import MaxComputeClient


# Global questionary style — bold-green for ``selected`` (checkbox
# items the user has toggled on) makes them visually stand out. Bold-
# cyan ``pointer`` and ``highlighted`` track the cursor's current row.
# ``answer`` (the result echoed after a prompt confirms) is left at
# default so it doesn't fight with our own status-line styling. ``ESC``
# at any picker level returns ``None`` from ``.ask()`` — our editor
# loops treat ``None`` as "back to parent menu".
_EDITOR_STYLE = Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green bold"),  # checkbox-checked items
        ("separator", "fg:#6c757d"),
        ("instruction", "fg:#6c757d italic"),
    ]
)


class _CachedClient:
    """Session-lifetime catalog cache wrapping a ``MaxComputeClient``.

    The editor's source/table/column drill-down hits ``list_projects``
    / ``list_schemas`` / ``list_tables`` / ``describe_table`` repeatedly
    as the user navigates — without caching, each Add-table /
    Edit-cols round-trip re-fetches the same metadata, which on flaky
    networks compounds into multi-second pauses (and occasionally a
    transient DNS / connection error that resolves on retry).

    The cache lives for one ``edit_profile()`` invocation; closing
    the editor drops it. Catalog state changes between editor sessions
    are picked up on next launch. Pass-through for everything else
    (``execute_sql`` / ``cost_estimate`` etc. — the editor doesn't
    invoke them, but the type signature stays compatible with code
    that does).

    Methods cache by their identifying tuple:
    - ``list_projects()`` — singleton (no key)
    - ``list_schemas(project=)`` — keyed by project
    - ``list_tables(project=, schema=)`` — keyed by (project, schema)
    - ``describe_table(name, project=, schema=)`` — keyed by
      (project, schema, name)
    """

    def __init__(self, inner: MaxComputeClient) -> None:
        self._inner = inner
        self._projects: list[str] | None = None
        self._schemas: dict[str, list[str]] = {}
        self._tables: dict[tuple[str, str], list[str]] = {}
        self._descs: dict[tuple[str, str, str], dict[str, Any]] = {}

    @property
    def profile(self) -> Profile:  # for code paths reading client.profile
        return self._inner.profile

    def __getattr__(self, name: str) -> Any:
        # Pass-through for un-cached methods (execute_sql, cost_estimate, etc.).
        return getattr(self._inner, name)

    def list_projects(self) -> list[str]:
        if self._projects is None:
            self._projects = self._inner.list_projects()
        return self._projects

    def list_schemas(self, *, project: str | None = None) -> list[str]:
        proj = project or self._inner.profile.compute_project
        if proj not in self._schemas:
            self._schemas[proj] = self._inner.list_schemas(project=project)
        return self._schemas[proj]

    def list_tables(self, *, project: str | None = None, schema: str | None = None) -> list[str]:
        proj = project or self._inner.profile.compute_project
        sch = schema or "default"
        key = (proj, sch)
        if key not in self._tables:
            self._tables[key] = self._inner.list_tables(project=project, schema=schema)
        return self._tables[key]

    def describe_table(
        self,
        name: str,
        *,
        project: str | None = None,
        schema: str | None = None,
    ) -> dict[str, Any]:
        proj = project or self._inner.profile.compute_project
        sch = schema or "default"
        key = (proj, sch, name)
        if key not in self._descs:
            self._descs[key] = self._inner.describe_table(name, project=project, schema=schema)
        return self._descs[key]


# ── Top-level entry ───────────────────────────────────────────────────


def edit_profile(
    profile: Profile,
    client: MaxComputeClient,
    cached_projects: list[str] | None = None,
) -> Profile | None:
    """File-browser-style multi-level profile editor.

    Returns the updated ``Profile`` on Save, ``None`` on Cancel.
    Validation is deferred to the caller (after Save commits, the
    caller runs ``Profile.validate()`` + auth-test as needed).

    The draft is built up via ``dataclasses.replace``; each section
    editor is a pure function ``(draft) -> draft``. No global mutable
    state. Catalog calls (``list_projects`` / ``list_schemas`` /
    ``list_tables`` / ``describe_table``) are routed through
    ``_CachedClient`` for the duration of this editor session — each
    distinct (project, schema [, table]) tuple incurs at most one
    network round-trip per session.

    ``cached_projects`` pre-populates the client's project-list cache
    (from compute_project discovery in the create wizard), avoiding
    a redundant ``list_projects()`` call.
    """
    cached = _CachedClient(client)
    if cached_projects is not None:
        cached._projects = cached_projects

    draft = profile
    while True:
        try:
            action = _top_level_select(draft)
            if action is None:
                # Esc / Ctrl+C at top-level menu — silent no-op; user
                # must explicitly pick `✅ Save and exit` or `❌ Cancel`
                # to leave.
                continue
            if action == "CANCEL":
                if click.confirm("Discard all changes and exit?", default=False):
                    return None
                continue
            if action == "DONE":
                return draft
            if action == "compute_project":
                draft = _edit_compute_project(draft)
            elif action == "endpoint":
                draft = _edit_endpoint(draft)
            elif action == "auth":
                draft = _edit_auth(draft)
            elif action == "cost":
                draft = _edit_cost_thresholds(draft)
            elif action == "tags":
                draft = _edit_tags(draft)
            elif action == "sources":
                draft = _edit_sources(draft, cached)
        except (KeyboardInterrupt, click.exceptions.Abort):
            # Ctrl+C anywhere inside a section editor or subscreen —
            # same as Esc at top level: re-render the menu.
            continue


# ── Top-level menu ────────────────────────────────────────────────────


def _top_level_select(draft: Profile) -> str | None:
    """Render the top-level menu. Returns one of:
    ``"compute_project"`` | ``"endpoint"`` | ``"auth"`` | ``"cost"`` |
    ``"tags"`` | ``"sources"`` | ``"DONE"`` | ``"CANCEL"`` | ``None`` (Ctrl-C).
    """
    # Each section gets a distinct emoji so users can navigate by
    # icon shape at a glance: 🏷  / 🌐 / 🔑 / 💰 / 🏷  / 📚 . Save
    # uses ✅ (green check), Cancel ❌ (red X) — both are visually
    # distinct from the section emoji to make "commit / abandon"
    # actions obvious. Field labels are right-padded so the values
    # line up regardless of label length.
    choices = [
        questionary.Choice(
            title=f"🎯 Compute project   {draft.compute_project}",
            value="compute_project",
        ),
        questionary.Choice(
            title=f"🌐 Endpoint          {draft.endpoint}",
            value="endpoint",
        ),
        questionary.Choice(
            title=f"🔑 Auth              {_format_auth(draft.auth)}",
            value="auth",
        ),
        questionary.Choice(
            title=(
                f"💰 Cost thresholds   "
                f"{'enabled' if draft.cost_thresholds.enabled else 'disabled'} / "
                f"confirm {draft.cost_thresholds.confirm_cny:g} CNY / "
                f"blocked {draft.cost_thresholds.blocked_cny:g} CNY"
            ),
            value="cost",
        ),
        questionary.Choice(
            title=f"🏷  Tags              {_format_tags(draft.tags)}",
            value="tags",
        ),
        questionary.Choice(
            title=(
                f"📚 Sources ({len(draft.sources)})       {_format_sources_summary(draft.sources)}"
            ),
            value="sources",
        ),
        questionary.Separator(),
        questionary.Choice(title="✅ Save and exit", value="DONE"),
        questionary.Choice(title="❌ Cancel (discard changes)", value="CANCEL"),
    ]
    return _pick_choice(
        f"Editing profile `{draft.name}` — pick a section to edit:",
        choices=choices,
        style=_EDITOR_STYLE,
        instruction="(↑↓ to navigate, Enter to drill in, Esc to back out)",
    )


def _format_auth(auth: object) -> str:
    """Render the auth row in the editor's section list.

    AK literals are masked via ``commands._identity.mask_ak_id``
    (``FIRST4***LAST4`` — the same shape the masked AK takes in
    ``mcs profile show``). Env-refs pass through as the bare
    ``${env:NAME}`` pointer. There's no live RAM-principal annotation
    here on purpose: the editor stays a pure-config view, and the
    runtime identity is exposed by the dedicated ``mcs profile
    whoami NAME`` verb.
    """
    if isinstance(auth, AkAuth):
        return f"AK (id={mask_ak_id(auth.access_key_id)})"
    if isinstance(auth, ProcessAuth):
        return f"Process ({auth.command[:40]}{'…' if len(auth.command) > 40 else ''})"
    return type(auth).__name__


# Back-compat alias for the masking helper's previous module-local
# name — the canonical name is now ``commands._identity.mask_ak_id``,
# but the editor's regression tests patch ``_mask_ak_id`` and we'd
# rather not chase the rename across the test file.
_mask_ak_id = mask_ak_id


def _format_tags(tags: tuple[str, ...]) -> str:
    if not tags:
        return "(none)"
    return ", ".join(tags)


def _format_sources_summary(sources: tuple[DataSource, ...]) -> str:
    if not sources:
        return "(none yet)"
    return ", ".join(f"{s.project}.{s.schema}" for s in sources[:3]) + (
        f" + {len(sources) - 3} more" if len(sources) > 3 else ""
    )


def _table_status_icon(ts: TableSpec) -> str:
    """Three-state icon for a single TableSpec's column-scope."""
    if ts.columns is not None:
        return "🔒"  # whitelist mode (locked-down)
    if ts.columns_exclude:
        return "✂️ "  # blacklist mode (some cols hidden)
    return "📋"  # full visibility


def _format_table_scope(ts: TableSpec) -> str:
    """Human-readable scope label paired with ``_table_status_icon``."""
    if ts.columns is not None:
        return f"whitelist: {len(ts.columns)} col(s)"
    if ts.columns_exclude:
        return f"hide {len(ts.columns_exclude)} col(s)"
    return "all columns visible"


def _source_summary_with_breakdown(src: DataSource) -> str:
    """Source-row summary including a tri-state breakdown of tables.

    Output examples:
    - ``wildcard '*' (all current + future tables visible)``
    - ``5 tables · 4 full · 1 col-scoped``
    - ``no tables yet``
    """
    if isinstance(src.tables, str):
        return "wildcard '*' (all current + future tables visible)"
    if not src.tables:
        return "no tables yet"
    n = len(src.tables)
    full = sum(1 for ts in src.tables if ts.columns is None and not ts.columns_exclude)
    scoped = n - full
    parts = [f"{n} table(s)"]
    if full:
        parts.append(f"{full} full")
    if scoped:
        parts.append(f"{scoped} col-scoped")
    return " · ".join(parts)


# ── Section editors ───────────────────────────────────────────────────


def _edit_compute_project(draft: Profile) -> Profile:
    """Re-prompt compute_project with current value as default.

    Ctrl+C propagates as ``click.exceptions.Abort`` so the user can
    exit the whole editor flow cleanly from any prompt.
    """
    while True:
        answer = click.prompt(
            "Compute project (the AK's home project where SQL jobs run)",
            default=draft.compute_project,
        )
        answer = (answer or "").strip()
        if answer:
            return dataclasses.replace(draft, compute_project=answer)
        click.echo("  Compute project must be non-empty.", err=True)


def _edit_endpoint(draft: Profile) -> Profile:
    """Re-prompt endpoint URL; ``Profile.validate()`` enforces the
    ``http://`` / ``https://`` prefix at commit time."""
    while True:
        answer = click.prompt("Endpoint URL", default=draft.endpoint)
        answer = (answer or "").strip()
        if not answer:
            click.echo("  Endpoint must be non-empty.", err=True)
            continue
        if not answer.startswith(("http://", "https://")):
            click.echo("  Endpoint must start with http:// or https://", err=True)
            continue
        return dataclasses.replace(draft, endpoint=answer)


def _edit_auth(draft: Profile) -> Profile:
    """Sub-picker: pick auth type, then fill type-specific fields.

    Currently supports ``AkAuth`` (access_key_id + access_key_secret;
    both ``${env:VAR}`` references and literals accepted) and
    ``ProcessAuth`` (command + timeout). The dataclass union is
    closed: adding a new variant requires touching this picker AND
    ``Profile.validate()``.
    """
    current_type = "ak" if isinstance(draft.auth, AkAuth) else "process"
    # questionary.select's ``default=`` matches against ``Choice.value``
    # (or the bare-string choice form), NOT the title. The previous
    # title-based default raised "Invalid `default` value passed"
    # at section-entry time.
    type_choice = _pick_choice(
        "Auth type:",
        choices=[
            questionary.Choice(title="🔑 AK (AccessKey pair — id + secret)", value="ak"),
            questionary.Choice(
                title="🛡 Process (subprocess returning JSON token on stdout)",
                value="process",
            ),
            questionary.Separator(),
            questionary.Choice(title="↩  Back (no changes)", value="BACK"),
        ],
        default=current_type,
        style=_EDITOR_STYLE,
        instruction="(Esc to back out)",
        echo_label="Auth type",
        echo_emoji="🔑",
    )
    if type_choice in (None, "BACK"):
        return draft

    if type_choice == "ak":
        return _edit_ak_auth(draft)
    if type_choice == "process":
        return _edit_process_auth(draft)
    return draft


def _edit_ak_auth(draft: Profile) -> Profile:
    """Prompt for AK id + secret. Existing values pre-fill."""
    cur_id = draft.auth.access_key_id if isinstance(draft.auth, AkAuth) else ""
    cur_secret = draft.auth.access_key_secret if isinstance(draft.auth, AkAuth) else ""
    new_id = click.prompt(
        "Access Key ID (or '${env:NAME}' env reference)",
        default=cur_id or "",
    ).strip()
    new_secret = click.prompt(
        "Access Key Secret (or '${env:NAME}' env reference)",
        default=cur_secret or "",
        hide_input=not (cur_secret.startswith("${env:") if cur_secret else False),
    ).strip()
    if not new_id or not new_secret:
        click.echo("  AK id and secret must both be non-empty.", err=True)
        return draft
    return dataclasses.replace(
        draft, auth=AkAuth(access_key_id=new_id, access_key_secret=new_secret)
    )


def _edit_process_auth(draft: Profile) -> Profile:
    """Prompt for ProcessAuth command + timeout.

    The command must emit a JSON payload on stdout in the Alibaba Cloud
    STS AssumeRole format (AccessKeyId, AccessKeySecret, SecurityToken,
    optional Expiration).  The canonical form is the ncs CLI:
    ``ncs create credential odpsuser --employee-id <id> -o template -t odpscmd``.
    """
    cur_cmd = draft.auth.command if isinstance(draft.auth, ProcessAuth) else ""
    cur_timeout = draft.auth.timeout if isinstance(draft.auth, ProcessAuth) else 60
    new_cmd = click.prompt(
        "Auth command (must return STS AssumeRole JSON: AccessKeyId, "
        "AccessKeySecret, SecurityToken, optional Expiration)",
        default=cur_cmd or "",
    ).strip()
    new_timeout = click.prompt("Timeout (seconds, 1-600)", default=cur_timeout, type=int)
    if not new_cmd:
        click.echo("  Auth command must be non-empty.", err=True)
        return draft
    if not (1 <= new_timeout <= 600):
        click.echo("  Timeout must be 1-600 seconds.", err=True)
        return draft
    return dataclasses.replace(draft, auth=ProcessAuth(command=new_cmd, timeout=new_timeout))


def _edit_cost_thresholds(draft: Profile) -> Profile:
    """Prompt for confirm / blocked CNY thresholds."""
    enabled = click.confirm(
        "Enable execution-time cost gate?",
        default=draft.cost_thresholds.enabled,
    )
    new_confirm = click.prompt(
        "Confirm threshold (CNY) — `mcs sql cost` asks user above this",
        default=draft.cost_thresholds.confirm_cny,
        type=float,
    )
    new_blocked = click.prompt(
        "Blocked threshold (CNY) — `mcs sql cost` refuses above this",
        default=draft.cost_thresholds.blocked_cny,
        type=float,
    )
    if enabled and new_confirm >= new_blocked:
        click.echo(
            "  confirm must be strictly less than blocked.",
            err=True,
        )
        return draft
    return dataclasses.replace(
        draft,
        cost_thresholds=CostThresholds(
            confirm_cny=new_confirm,
            blocked_cny=new_blocked,
            enabled=enabled,
        ),
    )


def _edit_tags(draft: Profile) -> Profile:
    """Prompt for comma-separated tag list. Empty string → empty tuple."""
    answer = click.prompt(
        "Tags (comma-separated; empty = no tags)",
        default=", ".join(draft.tags),
        show_default=bool(draft.tags),
    )
    raw = (answer or "").strip()
    if not raw:
        return dataclasses.replace(draft, tags=())
    new_tags = tuple(t.strip() for t in raw.split(",") if t.strip())
    return dataclasses.replace(draft, tags=new_tags)


# ── Sources sub-picker (nested) ───────────────────────────────────────


def _edit_sources(draft: Profile, client: MaxComputeClient) -> Profile:
    """Sources sub-picker: list current + Add new + Back.

    Pick an existing source row → drills into ``_edit_source(idx)``.
    Pick "+ Add new source" → ``_pick_project`` + ``_pick_schema``,
    creates an empty ``DataSource(tables=())``, drops directly into
    ``_edit_source`` so the user adds tables / cols using the same
    navigation flow they use for editing existing sources. (Earlier
    versions ran a linear ADD-SOURCE wizard with multi-checkboxes;
    the navigation form is what users expect from a "directory
    browser" UX.)
    """
    while True:
        choices: list[questionary.Choice | questionary.Separator] = []
        for i, src in enumerate(draft.sources):
            choices.append(
                questionary.Choice(
                    title=(
                        f"  📁 {src.project}.{src.schema}  · {_source_summary_with_breakdown(src)}"
                    ),
                    value=i,
                )
            )
        if draft.sources:
            choices.append(questionary.Separator())
        choices.append(questionary.Choice(title="➕ Add new source", value="ADD"))
        choices.append(questionary.Separator())
        choices.append(questionary.Choice(title="↩  Back to profile menu", value="BACK"))

        action = _pick_choice(
            f"📚 Sources for `{draft.name}` ({len(draft.sources)} total)",
            choices=choices,
            style=_EDITOR_STYLE,
            instruction="(Esc to back out)",
        )

        if action in (None, "BACK"):
            return draft
        if action == "ADD":
            # Pick (project, schema) up front, then drop into the
            # nav-style _edit_source so the user adds tables / cols
            # through the same flow they use to edit existing sources.
            project = _pick_project(
                client,
                default=draft.compute_project,
                existing=None,
                cached_projects=None,
            )
            if project is None:
                continue  # ESC at project picker → back to sources list
            schema = _pick_schema(client, project=project, existing=None)
            if schema is None:
                continue
            # Reject duplicate (project, schema) before append.
            new_src = DataSource(project=project, schema=schema, tables=())
            if any(s.source_key() == new_src.source_key() for s in draft.sources):
                click.secho(
                    f"  ⚠ A source for {project}.{schema} already exists; "
                    f"pick it from the list to edit instead.",
                    fg="yellow",
                    err=True,
                )
                continue
            draft = dataclasses.replace(draft, sources=draft.sources + (new_src,))
            # Drop into the navigation-style table/column editor so
            # the user fills in tables interactively.
            draft = _edit_source(draft, len(draft.sources) - 1, client)
            continue
        if isinstance(action, int):
            draft = _edit_source(draft, action, client)


def _edit_source(draft: Profile, idx: int, client: MaxComputeClient) -> Profile:
    """One-source editor — directory-browser style.

    Shows ALL discoverable tables in the source's (project, schema)
    pair, each annotated with its current selection state:
    - ⬜ not in source
    - 📋 in source, all columns visible
    - ✂️  in source, blacklist (some cols hidden)
    - 🔒 in source, whitelist (only listed cols visible)

    User picks a table → opens an action sub-menu with state-
    appropriate options (Include / Refine / Edit / Remove).
    Catalog-listing failures fall back to "show only currently-
    selected tables + manual-add" so users with permission-restricted
    AKs still have a path forward.
    """
    # Fetch list_tables once at entry (enumerated-mode sources only).
    # Cached so the error banner prints once, not on every loop iteration.
    src = draft.sources[idx]
    _listing_failed = False
    _available: list[str] = []
    if not isinstance(src.tables, str):
        try:
            with _Spinner("Listing tables..."):
                _available = client.list_tables(project=src.project, schema=src.schema)
        except McsError as e:
            _listing_failed = True
            click.secho(
                f"  ⚠ list_tables denied [{e.code}]: {e.message}\n"
                f"  {e.remediation}\n"
                f"  Showing only currently-selected tables; use `Add table by "
                f"name` to add more manually.",
                fg="yellow",
                err=True,
            )
        except Exception as e:
            _listing_failed = True
            click.secho(
                f"  ⚠ list_tables failed ({type(e).__name__}: {e}).\n"
                f"  Showing only currently-selected tables; use `Add table by "
                f"name` to add more manually.",
                fg="yellow",
                err=True,
            )

    _table_query = ""
    while True:
        src = draft.sources[idx]
        if isinstance(src.tables, str):
            # Wildcard mode — only the switch-to-enumerated knob.
            choices: list[questionary.Choice | questionary.Separator] = [
                questionary.Choice(
                    title="(wildcard '*' — all tables in schema, future ones auto-included)",
                    value="WILDCARD_INFO",
                    disabled="info",
                ),
                questionary.Separator(),
                questionary.Choice(
                    title="Switch to enumerated tables (lose wildcard)",
                    value="SWITCH_TO_ENUM",
                ),
                questionary.Separator(),
                questionary.Choice(title="🗑  Remove this source", value="REMOVE"),
                questionary.Choice(title="↩  Back to sources list", value="BACK"),
            ]
        else:
            # Directory-browser mode — use cached listing result.
            available = _available
            listing_failed = _listing_failed

            # Map of in-source tables (TableSpec) for state lookup.
            selected_specs: dict[str, TableSpec] = {ts.name: ts for ts in src.tables}
            # All names: discoverable + any selected ones not in catalog
            # (e.g. perm-scoped AK can read but not list).
            available_set = set(available)
            ordered_names = list(available)
            for name in selected_specs:
                if name not in available_set:
                    ordered_names.append(name)

            choices = []
            if not ordered_names and listing_failed:
                choices.append(
                    questionary.Choice(
                        title="(no tables selected yet — use `Add table by name` below)",
                        value="EMPTY_INFO",
                        disabled="info",
                    )
                )
                choices.append(questionary.Separator())
            for name in ordered_names:
                ts = selected_specs.get(name)
                if ts is None:
                    icon = "⬜"
                    label = "(not in source)"
                else:
                    icon = _table_status_icon(ts)
                    label = _format_table_scope(ts)
                choices.append(
                    questionary.Choice(
                        title=f"  {icon} {name}  — {label}",
                        value=("table", name),
                    )
                )
            if ordered_names:
                choices.append(questionary.Separator())
            choices.append(
                questionary.Choice(
                    title="📝 Add table by name (manual entry)",
                    value="MANUAL_ADD",
                )
            )
            choices.append(
                questionary.Choice(
                    title="🌟 Switch to wildcard '*' (include all tables)",
                    value="SWITCH_TO_WILDCARD",
                )
            )
            if available:
                choices.append(
                    questionary.Choice(
                        title=(
                            f"✅ Include all listed tables ({len(available)})  "
                            f"— snapshot, future tables NOT auto-included"
                        ),
                        value="INCLUDE_ALL_LISTED",
                    )
                )
            choices.append(questionary.Separator())
            choices.append(questionary.Choice(title="🗑  Remove this source", value="REMOVE"))
            choices.append(questionary.Choice(title="↩  Back to sources list", value="BACK"))

        action = _pick_choice(
            f"📁 Source `{src.project}.{src.schema}` · {_source_summary_with_breakdown(src)}",
            choices=choices,
            style=_EDITOR_STYLE,
            instruction="(↑↓ navigate · Enter drill in · Esc back)",
            query=_table_query,
        )
        _table_query = last_fzf_query()

        if action in (None, "BACK"):
            return draft
        if action == "REMOVE":
            confirm_msg = (
                f"Remove source {src.project}.{src.schema}? ({_source_summary_with_breakdown(src)})"
            )
            if click.confirm(confirm_msg, default=False):
                new_sources = draft.sources[:idx] + draft.sources[idx + 1 :]
                return dataclasses.replace(draft, sources=new_sources)
            continue
        if action == "SWITCH_TO_WILDCARD":
            if click.confirm(
                "Replace enumerated table list with wildcard '*'? "
                "This drops all per-table column scoping.",
                default=False,
            ):
                new_src = dataclasses.replace(src, tables="*")
                draft = _replace_source_at(draft, idx, new_src)
            continue
        if action == "SWITCH_TO_ENUM":
            try:
                with _Spinner("Listing tables..."):
                    table_names = client.list_tables(project=src.project, schema=src.schema)
                _available = table_names
                _listing_failed = False
            except McsError as e:
                click.secho(
                    f"  ⚠ list_tables denied [{e.code}]: {e.message}\n  {e.remediation}",
                    fg="yellow",
                    err=True,
                )
                _available = []
                _listing_failed = True
                continue
            except Exception as e:
                click.secho(f"  ⚠ list_tables failed: {e}", fg="yellow", err=True)
                _available = []
                _listing_failed = True
                continue
            if not table_names:
                click.secho(
                    "  ⚠ No tables found — can't materialize wildcard. Source kept as wildcard.",
                    fg="yellow",
                    err=True,
                )
                continue
            new_tables = tuple(TableSpec(name=n) for n in table_names)
            new_src = dataclasses.replace(src, tables=new_tables)
            draft = _replace_source_at(draft, idx, new_src)
            continue
        if action == "MANUAL_ADD":
            try:
                table_name = click.prompt(
                    "Table name (type explicitly — for tables not in catalog list)"
                ).strip()
            except click.exceptions.Abort:
                continue
            if not table_name:
                continue
            existing_names = (
                {ts.name for ts in src.tables} if not isinstance(src.tables, str) else set()
            )
            if table_name in existing_names:
                click.secho(
                    f"  ⚠ {table_name!r} is already in the source.",
                    fg="yellow",
                    err=True,
                )
                continue
            existing_specs = src.tables if not isinstance(src.tables, str) else ()
            new_tables = tuple(existing_specs) + (TableSpec(name=table_name),)
            new_src = dataclasses.replace(src, tables=new_tables)
            draft = _replace_source_at(draft, idx, new_src)
            continue
        if action == "INCLUDE_ALL_LISTED":
            existing_specs = (
                {ts.name: ts for ts in src.tables} if not isinstance(src.tables, str) else {}
            )
            added = 0
            preserved = 0
            new_specs: list[TableSpec] = []
            for name in available:
                if name in existing_specs:
                    new_specs.append(existing_specs[name])
                    preserved += 1
                else:
                    new_specs.append(TableSpec(name=name))
                    added += 1
            new_src = dataclasses.replace(src, tables=tuple(new_specs))
            draft = _replace_source_at(draft, idx, new_src)
            if preserved == 0:
                click.secho(
                    f"  ✓ 📄 Tables: included all {added} listed table(s)",
                    fg="green",
                )
            else:
                click.secho(
                    f"  ✓ 📄 Tables: added {added} new table(s) "
                    f"({preserved} already present preserved)",
                    fg="green",
                )
            continue
        if isinstance(action, tuple) and action[0] == "table":
            table_name = action[1]
            draft = _table_action_menu(draft, idx, table_name, client)


def _table_action_menu(
    draft: Profile,
    src_idx: int,
    table_name: str,
    client: MaxComputeClient,
) -> Profile:
    """Sub-menu for an ENTERed table row in the source view.

    Different options based on the table's current state:
    - **Not in source (⬜)**: Include all cols / Refine cols (drill into
      column picker, exclude some) / Back.
    - **In source, full (📋)**: Refine cols (open col picker pre-checked
      with all cols) / Remove from source / Back.
    - **In source, blacklist (✂️)**: Edit cols / Reset to all cols /
      Remove from source / Back.
    - **In source, whitelist (🔒)**: read-only (TUI doesn't edit
      whitelists) — only Remove / Back.
    """
    src = draft.sources[src_idx]
    if isinstance(src.tables, str):
        return draft  # defensive

    selected_specs: dict[str, TableSpec] = {ts.name: ts for ts in src.tables}
    ts = selected_specs.get(table_name)

    if ts is None:
        # ⬜ not in source
        action = _pick_choice(
            f"📄 {table_name} — not in source. Pick action:",
            choices=[
                questionary.Choice(
                    title="✅ Include with all columns visible", value="INCLUDE_ALL"
                ),
                questionary.Choice(
                    title="✏️  Include with refined columns (pick which to hide)",
                    value="INCLUDE_REFINED",
                ),
                questionary.Choice(title="↩  Back", value="BACK"),
            ],
            style=_EDITOR_STYLE,
            instruction="(Esc to back out)",
        )
        if action in (None, "BACK"):
            return draft
        if action == "INCLUDE_ALL":
            new_ts = TableSpec(name=table_name)
            new_tables = src.tables + (new_ts,)
            return _replace_source_at(draft, src_idx, dataclasses.replace(src, tables=new_tables))
        if action == "INCLUDE_REFINED":
            cols_exclude = _pick_columns_exclude(
                client,
                project=src.project,
                schema=src.schema,
                table_name=table_name,
                pre_excluded=(),
            )
            if cols_exclude is None:
                return draft
            new_ts = (
                TableSpec(name=table_name, columns_exclude=tuple(cols_exclude))
                if cols_exclude
                else TableSpec(name=table_name)
            )
            new_tables = src.tables + (new_ts,)
            return _replace_source_at(draft, src_idx, dataclasses.replace(src, tables=new_tables))

    # In source — different menu per scope mode.
    if ts.columns is not None:
        # 🔒 whitelist: TUI is read-only on whitelist scope.
        click.secho(
            f"  ⓘ `{table_name}` has a column whitelist (`columns=[...]`); the TUI "
            f"only edits blacklists. Use `mcs profile update --from-spec` JSON to "
            f"edit whitelist scope.",
            fg="cyan",
            err=True,
        )
        action = _pick_choice(
            f"📄 {table_name} (🔒 whitelist; read-only)",
            choices=[
                questionary.Choice(title="🗑  Remove from source", value="REMOVE"),
                questionary.Choice(title="↩  Back", value="BACK"),
            ],
            style=_EDITOR_STYLE,
            instruction="(Esc to back out)",
        )
    else:
        # 📋 full or ✂️ blacklist
        scope = _format_table_scope(ts)
        icon = _table_status_icon(ts)
        choices_list = [
            questionary.Choice(title="✏️  Edit column visibility", value="EDIT"),
        ]
        if ts.columns_exclude:
            choices_list.append(
                questionary.Choice(title="🔄 Reset to all columns visible", value="RESET")
            )
        choices_list.append(questionary.Choice(title="🗑  Remove from source", value="REMOVE"))
        choices_list.append(questionary.Choice(title="↩  Back", value="BACK"))
        action = _pick_choice(
            f"📄 {table_name} · {icon} {scope}",
            choices=choices_list,
            style=_EDITOR_STYLE,
            instruction="(Esc to back out)",
        )

    if action in (None, "BACK"):
        return draft

    if action == "REMOVE":
        new_tables = tuple(t for t in src.tables if t.name != table_name)
        return _replace_source_at(draft, src_idx, dataclasses.replace(src, tables=new_tables))

    if action == "RESET":
        # Replace ts with a bare TableSpec (drops columns_exclude).
        new_tables = tuple(
            TableSpec(name=t.name) if t.name == table_name else t for t in src.tables
        )
        return _replace_source_at(draft, src_idx, dataclasses.replace(src, tables=new_tables))

    if action == "EDIT":
        cols_exclude = _pick_columns_exclude(
            client,
            project=src.project,
            schema=src.schema,
            table_name=table_name,
            pre_excluded=ts.columns_exclude,
        )
        if cols_exclude is None:
            return draft
        new_ts = (
            TableSpec(name=table_name, columns_exclude=tuple(cols_exclude))
            if cols_exclude
            else TableSpec(name=table_name)
        )
        new_tables = tuple(new_ts if t.name == table_name else t for t in src.tables)
        return _replace_source_at(draft, src_idx, dataclasses.replace(src, tables=new_tables))

    return draft


# ── Helpers ───────────────────────────────────────────────────────────


def _replace_source_at(draft: Profile, idx: int, new_src: DataSource) -> Profile:
    new_sources = draft.sources[:idx] + (new_src,) + draft.sources[idx + 1 :]
    return dataclasses.replace(draft, sources=new_sources)


__all__ = ["edit_profile"]
