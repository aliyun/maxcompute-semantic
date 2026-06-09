"""mcs profile subcommand group — manage named profiles and profile data (build, init, refresh)."""

from __future__ import annotations

import sys
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.errors import ProfileNotFoundError
from maxcompute_semantic.auth.profile_store import get, load_all, remove
from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, ProcessAuth, Profile
from maxcompute_semantic.commands._identity import live_identity
from maxcompute_semantic.mc_client.client import MaxComputeClient
from maxcompute_semantic.mc_client.errors import (
    McsError,
    NoBoundProfileError,
    WhoAmIFailedError,
)
from maxcompute_semantic.versioning import (
    ACTION_INIT,
    GitNotAvailable,
    GitRepo,
    commit_after_command,
    is_git_available,
    is_versioning_disabled,
)

if TYPE_CHECKING:
    from maxcompute_semantic.commands._import_creds import McsProfileCandidate


@click.group(name="profile")
def profile_group() -> None:
    """Manage named connection profiles."""


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(
        format=obj.get("format", "plain"),
        quiet=obj.get("quiet", False),
    )


def _confirm_imported_process_auth(
    creds,
    *,
    trust_process_command: bool = False,
    require_flag_without_tty: bool = False,
) -> bool:
    """Confirm adoption of an external ProcessAuth helper."""
    if not isinstance(creds.auth, ProcessAuth):
        return True

    from maxcompute_semantic.commands._import_creds import is_canonical_ncs_process_auth

    command = creds.auth.command
    if is_canonical_ncs_process_auth(creds.auth):
        return click.confirm(
            f"⚠️  Credential uses ncs process auth command:\n"
            f"    {command}\n"
            f"Adopt this command?",
            default=True,
        )

    if trust_process_command:
        click.echo(
            "⚠️  Trusting non-ncs process auth command because "
            "--trust-process-command was passed:",
            err=True,
        )
        click.echo(f"    {command}", err=True)
        return True

    if require_flag_without_tty and not sys.stdin.isatty():
        raise click.ClickException(
            "external credentials use a non-ncs process auth command:\n"
            f"  {command}\n"
            "rerun with --trust-process-command to adopt it"
        )

    click.echo(
        "  ⚠️  This external credential uses a non-ncs process auth command:\n"
        f"      {command}\n"
        "  Only adopt it if you trust this config file and helper.",
    )
    return click.confirm("  Trust and adopt this command?", default=False)


def _read_tier_label(profile: Profile) -> str:
    """Read tier cache for a profile's compute_project; return short
    label for ``mcs profile list``.

    Reads the per-(profile, project) tier cache for the profile's
    ``compute_project`` (the AK's home project — billing+compute).
    Missing or unreadable file → ``"?"``; valid cache → ``"3-level"``
    or ``"2-level"``. The per-source tiers (when sources span
    different MaxCompute projects with different tiers) are surfaced
    by ``mcs profile show``, not the table summary.
    """
    from maxcompute_semantic._internal.paths import tier_cache_path

    tier_path = tier_cache_path(profile, profile.compute_project)
    try:
        raw = tier_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "?"
    if raw == "3":
        return "3-level"
    if raw == "2":
        return "2-level"
    return "?"


@profile_group.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """Show all configured profiles."""
    r = _renderer(ctx)
    profiles = load_all()
    if not profiles:
        if r.is_envelope:
            r.success({"profiles": []})
        elif r.quiet:
            # No profiles → nothing to list
            pass
        else:
            click.echo("no profiles configured; run `mcs profile create` to add one")
        return

    # ``mcs profile list`` is an inventory view, not an active-context
    # resolver. Use ``mcs link status`` for the cwd-binding question
    # and ``mcs profile whoami`` for the live-identity question.
    headers = ["name", "compute_project", "sources", "endpoint", "auth", "tier"]
    rows: list[list[str]] = []
    for name, p in sorted(profiles.items()):
        auth_label = "process" if isinstance(p.auth, ProcessAuth) else "ak"
        tier_label = _read_tier_label(p)
        sources_label = str(len(p.sources)) if p.sources else "0"
        rows.append(
            [
                name,
                p.compute_project,
                sources_label,
                p.endpoint,
                auth_label,
                tier_label,
            ]
        )
    r.table(headers, rows)
    # quiet mode: print profile names one per line
    if r.quiet and not r.is_envelope:
        for name in sorted(profiles.keys()):
            r.quiet_essential({"name": name}, "name")


_REDACTED_MARKER = "***REDACTED***"


def _mask_ak_for_display(value: str) -> str:
    """Mask an AK id for human-readable display (``LTAI***Jr29`` style).

    Env-refs (``${env:VAR}``) pass through unchanged. Used for the
    rich-text ``mcs profile show`` output and the editor's section
    list — never for the round-trippable JSON/yaml form (which uses
    ``_maybe_redact_secret`` so the loader can detect the marker).
    """
    if value.startswith("${env:"):
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _maybe_redact_secret(value: str) -> str:
    """Redact a literal secret; keep ``${env:VAR}`` references intact.

    Env-var references aren't secrets — the resolver expands them at
    use time. The literal-secret form (used for hardcoded AKs) is the
    only thing that needs redaction.
    """
    if value.startswith("${env:"):
        return value
    return _REDACTED_MARKER


@profile_group.command("show")
@click.argument("name", required=False, default=None)
@click.pass_context
def show_cmd(ctx: click.Context, name: str | None) -> None:
    """Display a stored profile's configuration.

    The positional ``NAME`` is optional: when omitted, the active
    profile is resolved via the same chain the rest of the ``mcs``
    CLI uses (``MCS_PROFILE`` → cwd-link binding from ``mcs link`` →
    standard ``ALIBABA_CLOUD_*`` / ``MAXCOMPUTE_*`` env-var fallback).
    The env-vars-anonymous
    case — when the chain falls all the way through and the
    resolved profile has no saved name — is labelled ``(env-vars)``
    in the title banner so the reader sees "this is reading from
    your shell, not from disk".

    Default plain (``-f plain``, also the global default) renders a
    rich emoji-decorated summary of every section (compute_project /
    endpoint / auth / cost thresholds / tags / sources). It's a pure
    local read — no network round-trip — so it works offline and is
    sub-millisecond. For env-ref auth fields (``${env:NAME}``) each
    row picks up a small "(env var NAME set / NOT set in current
    shell)" tag so the reader can tell whether the named variable is
    currently exported, without echoing the resolved literal AK.

    The runtime identity question — "which RAM principal does
    this profile resolve to right now?" — is answered by the
    separate ``mcs profile whoami`` verb (a live ODPS whoami
    probe), not by ``show``. Keeping identity on the dedicated
    verb avoids embedding a network call in what users reach for
    as a quick config-inspection command.

    Pass the global ``-f json`` (or ``-f yaml``) for the
    round-trippable spec shape ``mcs profile update --from-spec`` /
    ``--from-file`` consumes — both wrap the same payload in the
    standard envelope shape (`{"status":"success","data":{...}}`).
    AK secret literals are redacted to ``***REDACTED***`` so the
    spec is safe to pipe through grep / cat / clipboards; env-refs
    pass through unchanged so the round-trip is exact.
    """
    r = _renderer(ctx)
    # Explicit alias raises ``ProfileNotFoundError`` (exit 3) on
    # miss; the bare form falls through the standard chain
    # (MCS_PROFILE → cwd-link → env-var anonymous).
    try:
        p = get(name) if name is not None else _resolve_profile_for_project(None, profile_name=None)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    if r.is_envelope:
        from maxcompute_semantic.auth.profile_store import _profile_to_dict

        data: dict = _profile_to_dict(p)
        data["name"] = p.name  # ``_profile_to_dict`` outputs the body only
        if isinstance(p.auth, AkAuth):
            data["auth"]["access_key_id"] = _maybe_redact_secret(p.auth.access_key_id)
            data["auth"]["access_key_secret"] = _maybe_redact_secret(p.auth.access_key_secret)
        # Both ``-f json`` and ``-f yaml`` wrap in the standard
        # envelope (`{"status":"success","data":{...}}`) and grow the
        # version-tail keys so an agent parsing the output sees the
        # fork-graph in one round-trip. Callers who want the bare
        # spec body for ``mcs profile update --from-file`` extract
        # ``.data`` (e.g. ``mcs -f json ... | jq .data`` or
        # ``mcs -f yaml ... | yq .data``).
        tail = _version_tail_for_show(p)
        if tail["kind"] == "fork":
            data["parent"] = tail["parent"]
            data["anchor"] = tail["anchor"]
        else:
            data["version"] = tail["version"]
            data["forks"] = tail["forks"]
        r.success(data)
        return

    # Rich plain mode — emoji + colors matching the editor's visual
    # style. Quiet mode falls back to a single-line summary.
    # ``_whoami_label`` substitutes ``(env-vars)`` when the
    # resolved profile is the env-var-anonymous fallback (empty
    # name from ``_resolve_profile_for_project``).
    display_name = _whoami_label(p)
    if r.quiet:
        click.echo(display_name)
        return

    # The title keeps a small dim ``(resolved via the active-profile
    # chain)`` suffix on bare-form invocations of ``mcs profile show``
    # (no positional argument given). It signals "this is whatever
    # the chain picked" without enumerating the slot; ``mcs link
    # status`` is the dedicated answer for cwd binding.
    via = (
        click.style("  (resolved via the active-profile chain)", dim=True)
        if name is None and p.name
        else ""
    )
    click.secho(f"📦 Profile '{display_name}'{via}", bold=True)
    click.echo("")
    click.echo(f"🎯 Compute project   {p.compute_project}")
    click.echo(f"🌐 Endpoint          {p.endpoint}")

    if isinstance(p.auth, ProcessAuth):
        click.echo(f"🔑 Auth              Process ({p.auth.command[:60]})")
        click.echo(f"                     timeout={p.auth.timeout}s")
    else:
        # AK fields. The display rule differs by case:
        #
        # - Literal id: masked as ``FIRST4***LAST4`` (matches
        #   ``maxc auth whoami``'s ``principal_masked`` shape) so
        #   the prefix-fingerprint is visible without leaking the
        #   whole AK to the terminal.
        # - Literal secret: replaced with ``***REDACTED***``
        #   wholesale — no fingerprint at all, since the secret is
        #   the secret.
        # - Env-ref id / secret: the ref string passes through
        #   unchanged (``${env:NAME}`` is a pointer name, not a
        #   secret) and the row gains a small "(env var NAME set /
        #   NOT set in current shell)" status annotation so the
        #   user can see, without echoing the resolved literal,
        #   whether the env var is currently exported.
        from maxcompute_semantic.commands._identity import env_ref_status

        ak_id_raw = p.auth.access_key_id
        ak_secret_raw = p.auth.access_key_secret
        ak_id_display = _mask_ak_for_display(ak_id_raw)
        ak_secret_display = _maybe_redact_secret(ak_secret_raw)

        def _env_status_tag(raw: str) -> str:
            status = env_ref_status(raw)
            if status is None:
                return ""
            label, is_set = status
            return "  " + click.style(label, fg="green" if is_set else "yellow", dim=True)

        id_tag = _env_status_tag(ak_id_raw)
        secret_tag = _env_status_tag(ak_secret_raw)
        click.echo("🔑 Auth              AK")
        click.echo(f"                     access_key_id     = {ak_id_display}{id_tag}")
        click.echo(f"                     access_key_secret = {ak_secret_display}{secret_tag}")

    click.echo(
        f"💰 Cost thresholds   "
        f"confirm {p.cost_thresholds.confirm_cny:g} CNY · "
        f"blocked {p.cost_thresholds.blocked_cny:g} CNY"
    )
    tags_str = ", ".join(p.tags) if p.tags else click.style("(none)", dim=True)
    click.echo(f"🏷  Tags              {tags_str}")
    desc_str = p.description if p.description else click.style("(none)", dim=True)
    click.echo(f"📝 Description       {desc_str}")

    if not p.sources:
        hint = click.style("(none yet — `mcs profile update` to add)", dim=True)
        click.echo(f"📚 Sources           {hint}")
    else:
        click.echo(f"📚 Sources ({len(p.sources)}):")
        for src in p.sources:
            if isinstance(src.tables, str):
                summary = click.style("wildcard '*' (all tables in schema)", fg="cyan")
                click.echo(f"   📁 {src.project}.{src.schema}  · {summary}")
                continue
            n = len(src.tables)
            full = sum(1 for ts in src.tables if ts.columns is None and not ts.columns_exclude)
            scoped = n - full
            parts = [f"{n} table(s)"]
            if full:
                parts.append(f"{full} full")
            if scoped:
                parts.append(f"{scoped} col-scoped")
            click.echo(f"   📁 {src.project}.{src.schema}  · {' · '.join(parts)}")
            for ts in src.tables:
                if ts.columns is not None:
                    icon = "🔒"
                    scope = f"whitelist: {len(ts.columns)} col(s)"
                elif ts.columns_exclude:
                    icon = "✂️ "
                    scope = f"hide {len(ts.columns_exclude)} col(s)"
                else:
                    icon = "📋"
                    scope = "all columns visible"
                click.echo(f"      {icon} {ts.name}  · {scope}")

    # Version tail — only added for non-quiet text mode (json / yaml
    # paths handled their own tail above; quiet mode bailed earlier).
    tail = _version_tail_for_show(p)
    if tail["kind"] == "fork":
        anchor = tail["anchor"]
        click.echo(
            f"🌿 Parent            {tail['parent']} @ {anchor['short_sha']} ({anchor['subject']})"
        )
    else:
        if not tail["versioned"]:
            note = click.style(
                "not versioned; run `mcs profile enable-versioning` to create the inaugural commit",
                dim=True,
            )
            click.echo(f"📜 Version           {note}")
        elif tail["version"] is None:
            note = click.style("(repo initialized, no commits yet)", dim=True)
            click.echo(f"📜 Version           {note}")
        else:
            v = tail["version"]
            click.echo(f"📜 Version           {v['short_sha']} ({v['subject']})")
        if tail["forks"]:
            click.echo(f"🌿 Forks             {', '.join(tail['forks'])}")


def _list_forks_of(parent_name: str) -> list[Profile]:
    """Return the alphabetically-sorted list of profiles in
    ``profiles.yaml`` whose ``kind == "fork"`` and ``parent_profile``
    matches ``parent_name``. Used by both ``mcs profile show <name>``'s
    tail block and ``mcs profile remove <name>``'s pre-check guard.
    """
    from maxcompute_semantic.auth.profile_store import load_all

    return sorted(
        (p for p in load_all().values() if p.kind == "fork" and p.parent_profile == parent_name),
        key=lambda p: p.name,
    )


def _commit_dict(short_sha: str, full_sha: str, subject: str) -> dict:
    """Compact representation of one git commit for JSON / text show
    output. The same shape is used by both the ``version`` key on a
    main-kind profile and the ``anchor`` key on a fork-kind profile.
    """
    return {"short_sha": short_sha, "full_sha": full_sha, "subject": subject}


def _version_tail_for_show(profile: Profile) -> dict:
    """Build the per-profile version-tail dict consumed by
    ``mcs profile show <name>`` for both the JSON envelope (merged
    into ``data``) and the rich-text trailer (the matching
    ``version:`` / ``forks:`` / ``parent:`` lines).

    Shape:
      - main-kind, versioned (data-dir has ``.git/``):
          {"kind": "main", "versioned": True,
           "version": {...}, "forks": ["<name>", ...]}
      - main-kind, unversioned:
          {"kind": "main", "versioned": False,
           "version": None, "forks": []}
      - fork-kind:
          {"kind": "fork", "versioned": True,
           "parent": "<parent-name>", "anchor": {...}}

    The fork-kind branch fetches the anchor commit's subject from
    the parent's git history via the parent's ``GitRepo`` wrapper
    (forks share the parent's object database). If the parent has
    been hand-removed from yaml (the double-orphan case the
    fork-list self-heal catches) or the anchor SHA is unreachable
    from the parent's HEAD anymore (the orphan case), the
    ``anchor`` dict's ``subject`` is the literal string
    ``"(anchor commit not reachable from parent)"`` so the show
    output still renders something useful.
    """
    from maxcompute_semantic.auth.errors import ProfileNotFoundError
    from maxcompute_semantic.mc_client.errors import McsError
    from maxcompute_semantic.versioning.errors import GitNotAvailable
    from maxcompute_semantic.versioning.forks import parent_repo

    pdir = profile_data_dir(profile)

    if profile.kind == "fork":
        anchor_sha = profile.git_sha or ""
        subject = "(anchor commit not reachable from parent)"
        short = anchor_sha[:12] if anchor_sha else "(unknown)"
        full = anchor_sha
        try:
            prepo = parent_repo(profile)
            full = prepo.rev_parse(anchor_sha) if anchor_sha else ""
            short = full[:12] if full else "(unknown)"
            subject = prepo.commit_subject(anchor_sha) if anchor_sha else subject
        except (ProfileNotFoundError, McsError, GitNotAvailable, ValueError):
            pass
        return {
            "kind": "fork",
            "versioned": True,
            "parent": profile.parent_profile,
            "anchor": _commit_dict(short, full, subject),
        }

    # main-kind branch — only "versioned" when the data-dir is a git
    # working tree (the ``.git/`` admin dir is the cheap on-disk
    # sentinel that survives across process boundaries).
    git_dir = pdir / ".git"
    forks = [f.name for f in _list_forks_of(profile.name)]
    if not git_dir.exists():
        return {
            "kind": "main",
            "versioned": False,
            "version": None,
            "forks": forks,
        }
    try:
        repo = GitRepo(pdir)
        rows = repo.log(limit=1)
    except (McsError, GitNotAvailable):
        rows = []
    if not rows:
        # Repo exists but unborn (init ran, no commits yet) — still
        # report as versioned so the text trailer doesn't tell the
        # user to run ``enable-versioning`` (they did; nothing has
        # been committed yet).
        return {
            "kind": "main",
            "versioned": True,
            "version": None,
            "forks": forks,
        }
    head = rows[0]
    return {
        "kind": "main",
        "versioned": True,
        "version": _commit_dict(head.short_sha, head.full_sha, head.message),
        "forks": forks,
    }


_ENV_ANON_LABEL = "(env-vars)"


def _whoami_label(profile: Profile) -> str:
    """Human label for a Profile in the whoami banner.

    Named saved profiles get their ``name`` back. The env-var
    anonymous fallback that ``_resolve_profile_for_project`` hands
    out has a ``name`` field equal to ``$MAXCOMPUTE_PROJECT`` (which
    is the empty string when even that env var is unset). For both
    of those cases we render a fixed ``(env-vars)`` tag instead of
    a raw empty quote.
    """
    return profile.name or _ENV_ANON_LABEL


@profile_group.command("whoami")
@click.argument("name", required=False, default=None)
@click.pass_context
def whoami_cmd(ctx: click.Context, name: str | None) -> None:
    """Print the live identity for a profile.

    Issues a live identity probe — for AK profiles
    ``odps.execute_security_query("whoami")`` returning the
    principal-display string (the same shape ``maxc auth whoami``
    emits as ``principal_display``, e.g.
    ``RAM$role-name:user-name``), and for ProcessAuth profiles the
    configured ncs helper's whoami returning
    ``"<identity_name> (employee.<id>)"``. Nothing is cached on
    disk — every invocation hits the live source, so the answer
    always reflects the current state of the credential.

    Profile selection:

    - ``mcs profile whoami NAME`` — look up the saved profile by
      that mcs alias and probe it. Missing alias exits with
      ``ProfileNotFoundError`` (exit code 3), same as
      ``mcs profile show``.
    - ``mcs profile whoami`` (no argument) — fall through the
      standard active-profile chain: ``MCS_PROFILE`` env var naming
      a saved profile → cwd-link binding from ``mcs link bind`` →
      standard ``ALIBABA_CLOUD_*`` / ``MAXCOMPUTE_*``
      env-vars anonymous fallback. The anonymous case (chain
      lands on the env-vars-constructed Profile with no saved
      name) is labelled ``(env-vars)`` in the output banner so
      the absence of a saved alias is explicit. The
      use ``mcs link bind`` for per-directory binding, or
      ``export MCS_PROFILE=<name>`` for per-shell binding.

    The dedicated ``--project`` / ``--profile`` flags the rest of
    the CLI carries are absent here on purpose: the bare positional
    is the entire surface, and the four-slot chain above already
    handles "what does my shell point at right now?" without flag
    soup. The retired ``mcs auth test`` end-to-end probe's role
    (resolve_credentials → tier probe → SELECT 1) falls out of any
    real verb's first call — when the credential is broken,
    ``mcs sql execute "select 1"`` produces a richer error
    envelope than a dedicated test-verb's per-step output ever
    did.

    JSON envelope on ``-f json``; quiet mode (``-q``) prints the
    bare identity string for shell pipelining.
    """
    r = _renderer(ctx)
    # Positional alias raises ``ProfileNotFoundError`` (exit 3) on
    # miss, matching ``mcs profile show``'s contract. The bare form
    # hands off to the standard active-profile chain (``MCS_PROFILE``
    # env var → cwd-link from ``mcs link bind`` → env-vars-
    # anonymous fallback). The env-vars-anonymous case comes back
    # as a Profile whose ``name`` is the value of
    # ``$MAXCOMPUTE_PROJECT`` — empty string when that env var is
    # itself unset, and ``_whoami_label`` substitutes the fixed
    # ``(env-vars)`` tag for that case.
    try:
        p = get(name) if name is not None else _resolve_profile_for_project(None, profile_name=None)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    label = _whoami_label(p)

    try:
        identity = live_identity(p)
    except McsError as e:
        # Classified error (AuthFailed, IdentityNotAuthorized,
        # EndpointUnreachable, etc.) — render with its own code
        # and remediation instead of folding into WhoAmIFailed.
        r.error(e)
        sys.exit(e.exit_code)

    if identity is None:
        # Standard failure envelope — non-zero exit, structured
        # error block for the JSON consumer, plain-text "Error:"
        # line for the human. The remediation hint adapts to the
        # named-vs-anonymous case so the example ``mcs sql execute``
        # invocation it suggests is one the reader can paste
        # verbatim.
        sql_hint = (
            f"`mcs sql execute --profile {p.name} 'select 1'`"
            if p.name
            else "`mcs sql execute 'select 1'` (against the env-var-resolved AK)"
        )
        err = WhoAmIFailedError(
            f"could not resolve a live identity for {label} — the "
            f"credential resolver or the ODPS whoami security query "
            f"returned no usable principal string.",
            remediation=(
                f"Confirm the credential is valid by running any "
                f"actual MaxCompute command ({sql_hint}). An auth "
                f"or permission error there is the canonical "
                f"diagnostic; a working SELECT means the whoami "
                f"security query is the one piece this endpoint "
                f"doesn't support."
            ),
        )
        r.error(err)
        sys.exit(err.exit_code)

    if r.quiet and not r.is_envelope:
        click.echo(identity)
        return

    data = {
        "profile": label,
        "auth_type": "process" if isinstance(p.auth, ProcessAuth) else "ak",
        "identity": identity,
    }
    if r.is_envelope:
        r.success(data)
        return

    styled_label = click.style(label, bold=True)
    styled_who = click.style(identity, fg="cyan", bold=True)
    click.echo(f"{styled_label}: {styled_who}")


# Active profile selection is split by scope: ``mcs link bind`` for a
# directory-scoped binding and ``MCS_PROFILE`` for a shell-scoped
# pointer. There is no profile subcommand that mutates a process- or
# machine-wide active profile.


# ── profile enable-versioning ────────────────────────────────────────────────
#
# Explicit user-facing entry point for upgrading a pre-versioning
# ("legacy") profile — one whose per-profile data directory has no
# ``.git/`` subdirectory yet — to the git-backed history that every
# write command since T8 expects. The auto-init-on-first-write branch
# of the auto-commit hook (``versioning/hook.py``'s
# ``commit_after_command``, sub-task T5) does the same upgrade
# implicitly the first time any write verb (``mcs build`` /
# the proposal workflow / ``mcs memory verify``) runs against a legacy
# profile. The explicit verb here lands the inaugural ``init: import
# existing data`` commit visible in the user's shell history rather
# than as a side effect of the next incidental write.
#
# Idempotent: a second invocation on an already-versioned profile is
# a no-op that reports the current HEAD. ``MCS_NO_VERSIONING=1`` in
# the environment short-circuits the body before the hook is reached
# and emits a "versioning disabled" notice with exit code 0. A
# ``kind="fork"`` profile prints a hint pointing at the parent (which
# owns the actual ``.git/`` — the fork is a detached worktree under
# the parent's repo) and also exits 0 without creating anything. A
# nonexistent ``--profile NAME`` raises the existing
# ``ProfileNotFoundError`` whose remediation text already names
# ``mcs profile list``.


@profile_group.command("enable-versioning")
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help=(
        "target profile; defaults to the resolved profile per the "
        "standard --profile / MCS_PROFILE / cwd-link / env-var chain. "
        "Run ``mcs profile list`` to see what's available."
    ),
)
@click.pass_context
def enable_versioning_cmd(ctx: click.Context, profile_name: str | None) -> None:
    """Upgrade a pre-versioning profile to a git-backed history.

    The very first ``mcs build`` / the proposal workflow / ``mcs memory
    verify`` invocation against a profile whose data directory has
    no ``.git/`` triggers the same auto-init upgrade automatically
    (the auto-commit hook's auto-init-on-first-write branch from
    T5). This verb is the explicit form: the user runs it once on
    a profile inherited from a pre-T5 mcs install so the inaugural
    ``init: import existing data`` commit shows up as a deliberate
    line in the shell history rather than buried inside the next
    incidental write command.

    Idempotency: running the verb a second time on the same profile
    is a no-op — the hook's byte-deterministic dump short-circuit
    sees that the staged tree matches HEAD's tree and the action
    prefix matches HEAD's action prefix (both ``init``), so it
    returns without writing a second commit. The terminal message
    in the no-op case names the current HEAD's short SHA and the
    subject line of the existing inaugural commit.

    Fork-kind profiles: a profile with ``kind="fork"`` shares its
    parent's ``.git/`` via a detached ``git worktree``; there is
    nothing to enable on the fork itself. The verb prints a hint
    pointing at the parent's name and exits 0 without touching
    disk. Run the verb against the named parent to upgrade the
    real repository if the parent is also a pre-versioning legacy
    profile.

    Env-disabled mode: ``MCS_NO_VERSIONING=1`` (the env knob the
    eval harness uses to keep per-case sandbox profiles
    unversioned for the EX-numbers-stay-comparable contract)
    short-circuits before the hook is reached. The verb prints a
    notice naming the env var and exits 0. The profile's on-disk
    files are untouched in this branch — only the would-be
    ``.git/`` history is not created.

    Errors: a ``--profile`` flag naming a profile that doesn't
    exist in ``profiles.yaml`` surfaces the standard
    ``ProfileNotFoundError`` (exit code 3, remediation pointing
    at ``mcs profile list``). A missing ``git`` binary on PATH
    surfaces ``GitNotAvailable`` whose remediation hint mentions
    both the install-git step and the ``MCS_NO_VERSIONING=1``
    opt-out. Both errors are the same shapes any other write verb
    surfaces in those environmental conditions.

    Forward reference: ``mcs profile log`` (sub-task T10) is the
    command for inspecting the history this verb opens, and
    ``mcs profile reset --to <sha>`` (T13) is the way to roll the
    history back. Neither is required for the upgrade itself —
    the hook is the only piece that runs here.
    """
    r = _renderer(ctx)

    # Resolution chain: the explicit ``--profile NAME`` wins,
    # otherwise the standard ``MCS_PROFILE`` → cwd-link → env-vars
    # fallback applies. ``ProfileNotFoundError`` from a named-and-
    # missing case carries its own remediation ("run ``mcs profile
    # list``") which the standard ``r.error(e)`` + ``sys.exit`` shape
    # renders to stderr (which click 8.3 merges into the runner's
    # ``result.output`` for the test-side substring assertion).
    try:
        profile = _resolve_profile_for_project(None, profile_name=profile_name)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    # Env-knob short-circuit. The hook itself also honours
    # ``MCS_NO_VERSIONING`` (step 1 of the algorithm in
    # ``versioning/hook.py``'s ``commit_after_command``), so the
    # user-visible behavior would already be a no-op without this
    # outer check — the difference is that the outer check lets us
    # phrase the message as "the explicit upgrade you asked for was
    # skipped because the env knob is set" rather than the hook's
    # generic silent return on the same condition.
    if is_versioning_disabled():
        r.success(
            {
                "profile": profile.name,
                "action": "enable-versioning",
                "result": "env-disabled",
                "env_var": "MCS_NO_VERSIONING",
                "message": (
                    f"versioning is disabled in this environment "
                    f"(``MCS_NO_VERSIONING`` is set). Profile "
                    f"{profile.name!r} was not upgraded. Unset the "
                    f"env var and re-run ``mcs profile enable-"
                    f"versioning --profile {profile.name}`` to land "
                    f"the inaugural commit. The profile's on-disk "
                    f"data files are unchanged either way — only the "
                    f"``.git/`` history would have been created."
                ),
            }
        )
        r.quiet_essential({"profile": profile.name}, "profile")
        ctx.exit(0)
        return

    # Fork-kind short-circuit. A fork is a detached-HEAD worktree
    # under the parent profile's repository — the parent owns the
    # ``.git/`` directory and the parent is what
    # ``enable-versioning`` would target. The validate() invariants
    # on the Profile dataclass guarantee that a ``kind="fork"``
    # profile has a non-None ``parent_profile`` field, so the
    # hint message can name the parent verbatim.
    if profile.kind == "fork":
        parent_name = profile.parent_profile
        r.success(
            {
                "profile": profile.name,
                "action": "enable-versioning",
                "result": "fork-skipped",
                "parent_profile": parent_name,
                "message": (
                    f"profile {profile.name!r} is a fork of "
                    f"{parent_name!r}, which is the actual versioned "
                    f"profile. The fork shares the parent's history "
                    f"as a detached ``git worktree``; there is no "
                    f"separate ``.git/`` to enable on the fork side. "
                    f"To target the parent, run ``mcs profile enable-"
                    f"versioning --profile {parent_name}``."
                ),
            }
        )
        r.quiet_essential({"profile": profile.name}, "profile")
        ctx.exit(0)
        return

    # Explicit-verb git probe. ``enable-versioning`` is a versioning
    # verb whose entire purpose is to write to ``.git/``; the soft-
    # skip-on-missing-git contract that applies to the auto-commit
    # hook (so build/annotate/memory/udf still succeed when git isn't
    # installed) does not apply here. Raise ``GitNotAvailable`` so
    # the user sees the actionable remediation instead of the hook's
    # silent return leaving the verb's defensive fall-through arm
    # holding an empty bag.
    if not is_git_available():
        raise GitNotAvailable(
            "the `git` binary is not on PATH; ``mcs profile "
            "enable-versioning`` cannot create the inaugural commit.",
            remediation=(
                "Install git (macOS: `brew install git` or "
                "`xcode-select --install`; Debian/Ubuntu: `apt-get "
                "install git`; RHEL/CentOS/Fedora: `yum install git`; "
                "Windows: `winget install --id Git.Git` or download "
                "from https://git-scm.com/download/win), or set "
                "MCS_NO_VERSIONING=1 to keep this profile unversioned."
            ),
        )

    # Capture the pre-hook on-disk state. The four variables here
    # drive the final-state branching after the hook returns —
    # the hook's return value alone doesn't distinguish "the
    # auto-init branch landed the inaugural inside the hook and
    # then the action-marker step byte-deterministic-short-
    # circuited" (legacy upgrade case, the hook returns ``None``)
    # from "the working tree was clean against an existing-and-
    # matching HEAD" (canonical idempotent case, the hook also
    # returns ``None``). The pre-vs-post head comparison is the
    # load-bearing observation that tells those two ``None``
    # cases apart.
    #
    # The defensive empty-log branch handles a half-initialized
    # ``.git/`` (the post-``git init`` pre-first-commit window
    # that the hook's algorithm never leaves the disk in under
    # normal operation, but a hand-aborted prior run could
    # conceivably have left there). The fall-through into the
    # hook closes that gap by landing the missing inaugural on
    # top of the empty repository state.
    pdir = profile_data_dir(profile)
    repo_pre = GitRepo(pdir)
    pre_existing_git_dir = repo_pre.exists()
    pre_head_sha: str | None = None
    pre_head_short: str | None = None
    pre_head_subject: str | None = None
    if pre_existing_git_dir:
        rows_pre = repo_pre.log(limit=1)
        if rows_pre:
            pre_head_sha = rows_pre[0].full_sha
            pre_head_short = rows_pre[0].short_sha
            pre_head_subject = rows_pre[0].message

    # The hook call. The auto-init-on-no-``.git/`` branch lands
    # the inaugural ``init: import existing data`` commit on a
    # legacy profile (the ``_INAUGURAL_COMMIT_SUMMARY`` constant
    # in ``versioning/hook.py`` hardcodes that subject regardless
    # of the ``summary`` kwarg we pass — passing the matching
    # literal here is what makes the second-call action-marker
    # step's byte-deterministic short-circuit fire on the
    # idempotent re-run). The hook's return value is the SHA of
    # the *action-marker* commit at the top of the algorithm —
    # which is ``None`` whenever that step finds no new delta
    # against the existing HEAD, regardless of whether the
    # auto-init branch inside the same call wrote an inaugural
    # underneath. The pre-vs-post head comparison below
    # disambiguates the two "the hook returned ``None``" arms.
    #
    # Errors out of the hook (the canonical example is
    # ``GitNotAvailable`` when the ``git`` binary is missing)
    # are left to propagate as ``McsError``. The runner's
    # default ``catch_exceptions=True`` stuffs them into
    # ``result.exception`` for the test side, matching the T6
    # ``test_profile_create_failure_does_not_leave_half_
    # versioned_state`` pattern; the production ``mcs`` console-
    # script wrapper in ``cli.py``'s ``main()`` catches the
    # same ``McsError`` and renders the standard JSON envelope
    # on stderr with the wrapper's ``install git / set
    # MCS_NO_VERSIONING=1`` remediation hint.
    hook_return_sha = commit_after_command(
        profile, action=ACTION_INIT, summary="import existing data"
    )

    # Re-read the on-disk state. The HEAD comparison drives the
    # four-cell decision table:
    #
    # | pre_existing | pre_head_sha | post_head_sha | meaning              |
    # |--------------|--------------|---------------|----------------------|
    # | False        | None         | <new>         | legacy upgrade       |
    # | True         | <H>          | <H>           | idempotent no-op     |
    # | True         | None         | <new>         | interrupted-init     |
    # | True         | <H>          | <H'> != H     | recovery-snapshot    |
    # | True         | <H>          | None          | (shouldn't happen)   |
    # | False        | None         | None          | (shouldn't happen)   |
    #
    # The "shouldn't happen" cells are defensive — the hook's
    # algorithm always leaves the repo with at least the
    # inaugural after a successful return, so any post-state
    # with an empty log against a non-error return is a contract
    # violation worth surfacing as the generic "no changes to
    # commit" wording rather than the canonical happy-path
    # phrasings.
    repo_post = GitRepo(pdir)
    post_head_sha: str | None = None
    post_head_short: str | None = None
    post_head_subject: str | None = None
    if repo_post.exists():
        rows_post = repo_post.log(limit=1)
        if rows_post:
            post_head_sha = rows_post[0].full_sha
            post_head_short = rows_post[0].short_sha
            post_head_subject = rows_post[0].message

    # The hook returned a SHA for the action-marker step that
    # wrote a logical end-of-command marker (the "an action
    # different from HEAD's action prefix" branch of the hook's
    # algorithm, plus the recovery-snapshot branch that writes
    # an ``init: import existing data`` marker on top of an
    # interrupted prior write). In the standard
    # ``action=ACTION_INIT, summary="import existing data"``
    # call this verb makes, the hook's byte-deterministic
    # short-circuit fires whenever HEAD already carries the
    # same action prefix and summary, so the action-marker step
    # itself returns ``None`` even when the auto-init branch
    # underneath wrote a fresh inaugural — that's the contract
    # the spec's pseudocode at plan lines 4539-4546 papered
    # over. The pre-vs-post head SHA comparison is the
    # disambiguator.
    head_changed = pre_head_sha != post_head_sha
    if not pre_existing_git_dir and post_head_sha is not None:
        # Cell 1: no ``.git/`` before, exactly one (or more)
        # commit(s) after. The auto-init branch of the hook
        # wrote the canonical inaugural. The ``hook_return_sha``
        # is ``None`` because the action-marker step then byte-
        # deterministic-short-circuited on the same-action-
        # same-summary match against the just-landed inaugural,
        # so the visible "new" SHA is the post-state's HEAD.
        result_label = "upgraded"
        short_sha = hook_return_sha[:12] if hook_return_sha else (post_head_short or "")
        full_sha = hook_return_sha or post_head_sha or ""
        message = (
            f"profile {profile.name!r} is now versioned. Inaugural "
            f"commit: {short_sha} {post_head_subject!r}. Use "
            f"``mcs profile log`` to see the history going forward."
        )
        r.success(
            {
                "profile": profile.name,
                "action": "enable-versioning",
                "result": result_label,
                "inaugural_sha": short_sha,
                "inaugural_full_sha": full_sha,
                "inaugural_subject": post_head_subject,
                "message": message,
            }
        )
        r.quiet_essential({"profile": profile.name}, "profile")
        return

    if pre_existing_git_dir and pre_head_sha is None and post_head_sha is not None:
        # Cell 3: ``.git/`` was there but log was empty (the
        # defensive interrupted-prior-init branch the comment
        # block above documents); the fall-through-to-the-hook
        # path closed the gap by landing the missing inaugural.
        short_sha = hook_return_sha[:12] if hook_return_sha else (post_head_short or "")
        full_sha = hook_return_sha or post_head_sha or ""
        message = (
            f"profile {profile.name!r}: interrupted prior "
            f"enable-versioning closed by landing the missing "
            f"inaugural commit at {short_sha} "
            f"{post_head_subject!r}. Use ``mcs profile log`` to "
            f"see the history going forward."
        )
        r.success(
            {
                "profile": profile.name,
                "action": "enable-versioning",
                "result": "recovered",
                "inaugural_sha": short_sha,
                "inaugural_full_sha": full_sha,
                "inaugural_subject": post_head_subject,
                "message": message,
            }
        )
        r.quiet_essential({"profile": profile.name}, "profile")
        return

    if pre_existing_git_dir and pre_head_sha is not None and not head_changed:
        # Cell 2: canonical idempotent no-op. Both pre- and
        # post-state HEAD point at the same commit; the working
        # tree was clean against the existing HEAD's tree and
        # the action prefix matched, so the hook's byte-
        # deterministic-dump short-circuit returned ``None``
        # without writing anything new.
        message = (
            f"profile {profile.name!r} is already versioned at "
            f"{pre_head_short} ({pre_head_subject!r}). No changes "
            f"to commit — the working tree matches the existing "
            f"HEAD and the action prefix matches, so the auto-"
            f"commit hook short-circuited."
        )
        r.success(
            {
                "profile": profile.name,
                "action": "enable-versioning",
                "result": "no-op",
                "head_sha": pre_head_short,
                "head_full_sha": pre_head_sha,
                "head_subject": pre_head_subject,
                "message": message,
            }
        )
        r.quiet_essential({"profile": profile.name}, "profile")
        return

    if (
        pre_existing_git_dir
        and pre_head_sha is not None
        and head_changed
        and post_head_sha is not None
    ):
        # Cell 4: the HEAD advanced — the recovery-snapshot
        # branch of the hook (which writes a ``recover:
        # uncommitted state from <timestamp>`` marker before
        # the action-marker step) wrote a new commit. The
        # ``hook_return_sha`` is the action-marker commit's
        # full SHA (the hook's standard return when the action-
        # marker step does emit a commit, which is the
        # different-action-prefix-from-HEAD's path; for the
        # ``ACTION_INIT`` action against an already-``init``
        # HEAD the canonical answer is the recovery commit
        # rather than a duplicate inaugural).
        short_sha = hook_return_sha[:12] if hook_return_sha else (post_head_short or "")
        full_sha = hook_return_sha or post_head_sha or ""
        message = (
            f"profile {profile.name!r}: the auto-commit hook "
            f"advanced the history. Previous HEAD: "
            f"{pre_head_short} ({pre_head_subject!r}); new HEAD: "
            f"{short_sha} ({post_head_subject!r}). This is the "
            f"recovery-snapshot branch closing over uncommitted "
            f"state. Use ``mcs profile log`` to inspect the "
            f"history."
        )
        r.success(
            {
                "profile": profile.name,
                "action": "enable-versioning",
                "result": "advanced",
                "previous_sha": pre_head_short,
                "previous_subject": pre_head_subject,
                "head_sha": short_sha,
                "head_full_sha": full_sha,
                "head_subject": post_head_subject,
                "message": message,
            }
        )
        r.quiet_essential({"profile": profile.name}, "profile")
        return

    # Defensive fall-through. The decision table's "shouldn't
    # happen" cells (``.git/`` was there with a HEAD but the
    # post-state has no log entries; or no ``.git/`` pre-call
    # and still no post-head) land here. The hook's algorithm
    # doesn't have a path that leaves the repo in either of
    # these shapes without raising — every successful return
    # from ``commit_after_command`` advances HEAD to either the
    # auto-init's inaugural or the recovery-snapshot commit, so
    # ``post_head_sha is None`` after a successful hook return
    # is an invariant violation. The env-disabled and fork-kind
    # short-circuits at the top of the verb already returned
    # before reaching this point, so neither of those is the
    # cause. Raise rather than emit a misleading "success"
    # envelope: the user-visible stack trace through the CLI
    # wrapper is the correct signal for "the hook's contract
    # broke under us".
    raise RuntimeError(
        f"unreachable: enable-versioning for profile {profile.name!r} "
        f"reached the defensive fall-through arm. pre_existing_git_dir="
        f"{pre_existing_git_dir!r}, pre_head_sha={pre_head_short!r}, "
        f"post_head_sha={post_head_short!r}, hook_return_sha="
        f"{hook_return_sha!r}. The auto-commit hook returned without "
        f"leaving HEAD in a determinate state, which violates the "
        f"``commit_after_command`` post-condition."
    )


@profile_group.command("remove")
@click.argument("name")
@click.option("--yes", is_flag=True, help="skip confirmation prompt")
@click.option("--purge", is_flag=True, help="also delete the per-profile data directory")
@click.pass_context
def remove_cmd(ctx: click.Context, name: str, yes: bool, purge: bool) -> None:
    """Delete a profile (idempotent for nonexistent names)."""
    r = _renderer(ctx)
    # Idempotent: if profile doesn't exist, succeed silently
    try:
        profile = get(name)
    except ProfileNotFoundError:
        # nonexistent profile: idempotent success
        r.success({"removed": name, "note": "profile did not exist"})
        return

    # Fork-kind delegation: ``mcs profile remove <fork-name>`` is the
    # alias-equivalent of ``mcs profile fork-remove <fork-name>`` so
    # the parent's ``.git/worktrees/<short>/`` admin entry gets swept
    # by ``git worktree remove`` instead of leaking behind a default
    # ``shutil.rmtree`` of the worktree dir.
    if profile.kind == "fork":
        if not yes:
            confirmed = click.confirm(f"remove fork profile '{name}'?", default=False)
            if not confirmed:
                click.echo("aborted")
                return
        import contextlib

        from maxcompute_semantic.versioning.errors import GitNotAvailable
        from maxcompute_semantic.versioning.forks import parent_repo, unregister_fork

        worktree_path = profile_data_dir(profile)
        try:
            prepo = parent_repo(profile)
            if worktree_path.exists():
                prepo.worktree_remove(worktree_path, force=False)
            else:
                # ghost-fork: worktree already gone, sweep parent's
                # admin-side entry then drop the yaml row.
                with contextlib.suppress(McsError, GitNotAvailable):
                    prepo.worktree_prune()
        except ProfileNotFoundError:
            # double-orphan: parent yaml already gone, leave on-disk
            # state alone (no parent to worktree-remove against).
            pass
        except (McsError, GitNotAvailable) as e:
            r.error(
                McsError(
                    f"failed to remove fork worktree at {worktree_path}: {e}",
                    remediation=(
                        "retry with ``mcs profile fork-remove "
                        f"{name} --force`` if the worktree has "
                        "uncommitted markdown edits inside it."
                    ),
                )
            )
            sys.exit(1)
        unregister_fork(name)
        r.success(
            {
                "removed": name,
                "kind": "fork",
                "delegated_to": "fork-remove",
            }
        )
        return

    # Main-kind: refuse to remove a profile whose .git/ holds live
    # fork worktrees — losing the parent's object database would
    # orphan every fork's working tree into the registry.
    live_forks = _list_forks_of(name)
    if live_forks:
        names = ", ".join(f.name for f in live_forks)
        r.error(
            McsError(
                f"{name!r} has {len(live_forks)} live fork(s): {names}",
                remediation=(
                    f"run ``mcs profile fork-remove`` on each fork "
                    f"before removing the parent — removing the "
                    f"parent's git repository would orphan their "
                    f"worktrees. See ``mcs profile fork-list --profile "
                    f"{name}`` for the full list."
                ),
            )
        )
        sys.exit(1)

    if not yes:
        confirmed = click.confirm(f"remove profile '{name}'?", default=False)
        if not confirmed:
            click.echo("aborted")
            return

    data_dir = str(profile_data_dir(profile))
    try:
        remove(name, delete_data_dir=purge)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)
    r.success(
        {
            "removed": name,
            "data_dir_preserved": None if purge else data_dir,
        }
    )


# ── Discovery / list-* data API ─────────────────────────────────────────────
#
# The agent-facing catalog-discovery verbs that used to sit here
# (``mcs meta list-projects`` and ``mcs meta list-schemas``)
# moved to the top-level ``mcs meta`` group in the post-v0.4 CLI
# cleanup, alongside the six verbs that came from the old
# ``mcs sql meta`` sub-group. Their function bodies live verbatim
# in ``commands/meta.py`` now. See the CHANGELOG entry under the
# `[Unreleased]` "Changed (breaking)" section for the rationale.

_SPEC_TEMPLATE = """\
# mcs profile spec — feed this back via:
#   mcs profile create --from-file @<path>
#   mcs profile update <name> --from-file @<path>
#
# The spec accepts both YAML and JSON (json is a yaml subset, the
# loader uses ruamel.yaml.YAML(typ='safe') which handles both
# transparently). Pick whichever is easier for your workflow:
# - YAML for hand-edits / dotfiles (this template uses yaml)
# - JSON for scripted / agent construction (`json.dumps(spec_dict)`)
#
# Same shape as `mcs profile show <name> --format json` output,
# modulo auth-secret redaction (literals come back as
# ***REDACTED***; on PUT, the marker substitutes to the existing
# stored value, so the agent never sees the secret).

# Identity. ``name`` must equal the PROFILE arg of create / update.
name: my-profile

# Compute project — the AK's home project where SQL jobs run + billing
# accrues. Used by ``mcs sql execute / cost / explain``.
compute_project: my_project

# MaxCompute API endpoint. Public form:
#   https://service.<region>.maxcompute.aliyun.com/api  (e.g. cn-shanghai)
# Internal (Alibaba intranet) presets are listed in the wizard.
endpoint: https://service.cn-shanghai.maxcompute.aliyun.com/api

# Auth — pick one type. The ``${env:VAR}`` reference form keeps secrets
# out of the on-disk yaml; literal AK values land in the file as-is
# (and are redacted to ***REDACTED*** in `show --format json`).
#
# AK type — static AccessKey pair (env-var references or literals):
auth:
  type: ak                                          # "ak" or "process"
  access_key_id: ${env:ALIBABA_CLOUD_ACCESS_KEY_ID}
  access_key_secret: ${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}
#
# Process type — subprocess that returns a JSON payload on stdout in
# the Alibaba Cloud STS AssumeRole response format (AccessKeyId,
# AccessKeySecret, SecurityToken, optional Expiration).  The canonical
# command is the ncs CLI (Akless Credential CLI):
# auth:
#   type: process
#   command: ncs create credential odpsuser --employee-id <YOUR_EMP_ID> -o template -t odpscmd
#   timeout: 60                                    # optional, 1-600 seconds

# Optional: cost thresholds (CNY). Defaults shown.
cost_thresholds:
  enabled: true        # set false to disable the execution-time cost gate
  confirm_cny: 10.0     # `mcs sql cost` asks user above this
  blocked_cny: 100.0    # `mcs sql cost` refuses above this

# Optional: free-form tags for grouping / search.
tags: []

# Optional: scenario / purpose for this profile, in your own words —
# what questions you want to answer, the domain, the metrics and time
# grain you care about. The agent uses this to recommend which tables
# to include during onboarding, and `mcs build` records it in the
# package overview.
description: ""

# Data sources — what the AK can read from. Each is a (project, schema)
# pair plus a tables filter. ``tables: '*'`` is the wildcard
# (future-table inclusive); ``tables: [...]`` is an enumerated list
# mixing bare strings and dict entries with column scope.
sources:
  - project: my_project
    schema: default
    tables: '*'
  # Multi-source / column-scoped example:
  # - project: data_lake_prod
  #   schema: events
  #   tables:
  #     - page_views                                # bare string = unscoped
  #     - name: user_sessions
  #       columns_exclude: [raw_user_agent, ip]     # blacklist mode
  #     # Or whitelist mode (mutually exclusive with columns_exclude):
  #     - name: orders
  #       columns: [id, qty, total]
"""


@profile_group.command("spec-template")
def spec_template_cmd() -> None:
    """Print a fillable profile yaml template to stdout.

    Pipe to a file (``mcs profile spec-template > p.yaml``), edit
    the placeholder values, and feed back via ``mcs profile create
    --from-file @p.yaml`` / ``mcs profile update NAME --from-file
    @p.yaml``. The same schema works for ``--from-spec`` (inline JSON
    or yaml) — JSON is just a yaml subset.

    The output mirrors the shape of ``mcs profile show NAME
    --format json`` so the GET-mutate-PUT loop is symmetric.
    """
    click.echo(_SPEC_TEMPLATE, nl=False)


@profile_group.command("import-creds")
@click.option(
    "--source",
    type=click.Choice(["maxc", "odpscmd", "auto"]),
    default="auto",
    help=(
        "credential source to import: 'maxc' reads ~/.maxc/config.yaml, "
        "'odpscmd' reads ~/.odpscmd/odps_config.ini, 'auto' (default) "
        "scans both default locations and picks the first match."
    ),
)
@click.option(
    "--config-path",
    default=None,
    help="override the default file path for --source (use to import non-default locations)",
)
@click.option("--alias", default=None, help="profile name (defaults to source's project name)")
@click.option("--no-test", is_flag=True, help="skip auth-test after creating the profile")
@click.option(
    "--trust-process-command",
    is_flag=True,
    help=(
        "adopt a non-ncs ProcessAuth command from external config without "
        "an interactive trust prompt"
    ),
)
@click.pass_context
def import_creds_cmd(
    ctx: click.Context,
    source: str,
    config_path: str | None,
    alias: str | None,
    no_test: bool,
    trust_process_command: bool,
) -> None:
    """Import auth credentials from an existing odpscmd / maxc config file.

    Convenience for users already authenticated via ``odpscmd`` or
    ``maxc-cli``: reads the AK + endpoint + project from the source
    file and creates an mcs profile from them, skipping the wizard's
    endpoint / auth prompts. Auto-discovers the source file at
    ``~/.maxc/config.yaml`` / ``~/.odpscmd/odps_config.ini`` when
    ``--source auto`` (the default); ``--config-path`` overrides for
    non-default locations.

    The imported AK secret lands in ``profiles.yaml`` as a literal
    (matching how the source stored it). Convert to ``${env:VAR}``
    references later via ``mcs profile update <name>`` → Auth
    section if you'd rather not have the secret on disk.

    A single source matching ``compute_project`` is created with
    one wildcard data source. Add more sources / column scoping
    via ``mcs profile update`` afterward.
    """
    from pathlib import Path as _Path

    from maxcompute_semantic.auth.profile_store import upsert
    from maxcompute_semantic.auth.schema import DataSource, Profile
    from maxcompute_semantic.commands._import_creds import (
        ImportedCreds,
        _maxc_default_config_path,
        _odpscmd_default_config_path,
        discover_creds,
        parse_creds_at,
    )

    r = _renderer(ctx)

    # Resolve the candidate creds.
    creds: ImportedCreds | None
    if source == "auto":
        candidates = discover_creds()
        if not candidates:
            click.echo(
                "no credentials discovered.\n"
                "  - maxc:    ~/.maxc/config.yaml not found\n"
                "  - odpscmd: `odpscmd` not on PATH (config lives at "
                "<install_root>/conf/odps_config.ini relative to the "
                "binary)\n"
                "Pass `--source <maxc|odpscmd>` with `--config-path PATH` "
                "to import from a non-default location.",
                err=True,
            )
            sys.exit(4)
        if len(candidates) == 1:
            creds = candidates[0]
        else:
            from maxcompute_semantic.commands._source_picker import _pick_one

            items = [f"🔑 {c.display()}" for c in candidates]
            choice = _pick_one("Multiple credential sources detected:", choices=items)
            if choice is None:
                click.echo("  (credential import skipped)")
                return
            else:
                idx = items.index(choice)
                creds = candidates[idx]
    else:
        if config_path:
            path: _Path | None = _Path(config_path).expanduser()
        elif source == "maxc":
            path = _maxc_default_config_path()
        else:  # odpscmd
            path = _odpscmd_default_config_path()
        if path is None:
            hint = (
                "the file ~/.maxc/config.yaml does not exist"
                if source == "maxc"
                else (
                    "`odpscmd` is not on PATH so the install root "
                    "couldn't be located. Pass --config-path "
                    "<install_root>/conf/odps_config.ini explicitly."
                )
            )
            click.echo(f"could not locate {source!r} default config — {hint}", err=True)
            sys.exit(4)
        creds = parse_creds_at(source, path)
        if creds is None:
            click.echo(
                f"could not parse {source!r} credentials at {path} — "
                f"file malformed, or uses a non-AK auth provider.",
                err=True,
            )
            sys.exit(4)
    assert creds is not None

    if not _confirm_imported_process_auth(
        creds,
        trust_process_command=trust_process_command,
        require_flag_without_tty=True,
    ):
        click.secho("🚫 aborted", fg="red")
        return

    name = alias or creds.compute_project
    existing = set(load_all().keys())
    if name in existing and not click.confirm(
        click.style(f"⚠️  profile {name!r} already exists; overwrite?", fg="yellow"),
        default=False,
    ):
        click.secho("🚫 aborted (pick a different name with --alias)", fg="red")
        return

    profile = Profile(
        name=name,
        compute_project=creds.compute_project,
        endpoint=creds.endpoint,
        auth=creds.auth,
        sources=(DataSource(project=creds.compute_project, schema="default", tables="*"),),
    )
    try:
        profile.validate()
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    if not no_test:
        from maxcompute_semantic.commands._auth_probe import _run_auth_test

        test_ok = _run_auth_test(profile, r, emit_summary=False)
        if test_ok != 0 and not click.confirm(
            "Auth test failed. Save profile anyway?", default=False
        ):
            return

    upsert(profile)
    click.secho(
        f"📦 imported {creds.source_label} credentials into profile '{name}' "
        f"(compute_project={creds.compute_project}).",
        fg="green",
    )
    r.success(
        {
            "imported": name,
            "from": creds.source_label,
            "source_path": str(creds.source_path),
            "compute_project": creds.compute_project,
        }
    )


@profile_group.command("suggest-creds")
@click.option(
    "--exclude-name",
    default=None,
    help=(
        "skip a profile name (defense-in-depth so the agent doesn't "
        "recommend cloning a profile about to be overwritten)"
    ),
)
@click.pass_context
def suggest_creds_cmd(ctx: click.Context, exclude_name: str | None) -> None:
    """Discover credential candidates without importing them.

    Read-only equivalent of the wizard's Step 1.5 picker. Returns the
    same data the wizard scans (existing mcs profiles + external maxc /
    odpscmd configs) so the agent can present candidates and let the
    user pick before any write happens.

    Empty result is not an error — returns ``{"existing_mcs": [],
    "external": []}`` and exits 0.

    Secrets are never serialized. Only ``auth_kind`` (ak / ncs /
    process), endpoint, compute_project, and a display label.
    """
    from maxcompute_semantic.commands._import_creds import (
        _classify_auth_kind,
        discover_creds,
        discover_mcs_profiles,
    )

    r = _renderer(ctx)

    existing = [
        {
            "name": c.name,
            "auth_kind": _classify_auth_kind(c.auth),
            "endpoint": c.endpoint,
            "compute_project": c.compute_project,
            "sources_count": len(c.sources),
            "display": c.display(),
        }
        for c in discover_mcs_profiles(exclude_name=exclude_name)
    ]
    external = [
        {
            "source": c.source_label,
            "path": str(c.source_path),
            "auth_kind": _classify_auth_kind(c.auth),
            "endpoint": c.endpoint,
            "compute_project": c.compute_project,
            "display": c.display(),
        }
        for c in discover_creds()
    ]
    r.success({"existing_mcs": existing, "external": external})


@profile_group.command("endpoint-presets")
@click.pass_context
def endpoint_presets_cmd(ctx: click.Context) -> None:
    """List endpoint presets the wizard's Environment picker exposes.

    Static knowledge dump for agent use:

    - ``public_region_template`` — the URL template ``_build_endpoint_from_region``
      uses for any cn-* / ap-* / us-* / eu-* MaxCompute region.
    - ``common_regions`` — a few canonical public regions to seed the
      agent's prompt to the user (not exhaustive — any string the user
      provides is acceptable in the template).
    - ``internal`` — the named intranet endpoints from
      ``_INTERNAL_ENDPOINTS``. Adding / removing presets in that
      constant flows through automatically.

    Pure local read; no network, no disk write.
    """
    r = _renderer(ctx)
    r.success(
        {
            "public_region_template": ("https://service.<region>.maxcompute.aliyun.com/api"),
            "common_regions": [
                "cn-shanghai",
                "cn-beijing",
                "cn-hangzhou",
                "cn-shenzhen",
                "cn-zhangjiakou",
            ],
            "internal": [
                {"label": label, "url": url} for label, url in _INTERNAL_ENDPOINTS.values()
            ],
        }
    )


@profile_group.command("list-ncs-identities")
@click.pass_context
def list_ncs_identities_cmd(ctx: click.Context) -> None:
    """Enumerate ncs ODPS authorizations for agent identity selection.

    Mirrors the wizard's Step 4 ncs identity picker. Returns three
    fields:

    - ``available`` — true iff the agent should present the picker
      (binary on PATH AND list non-empty). False otherwise; agent
      should fall back to collecting ``employee_id``.
    - ``identities`` — list of ``{buc_user_id, buc_user_type,
      buc_account_name}`` dicts. Empty when ``available=false``.
    - ``reason`` — human-readable disambiguator when
      ``available=false`` (binary missing / list empty / probe
      failed). Absent when ``available=true``.

    All failure modes return shape-stable JSON; ncs subprocess
    exceptions are caught and converted, never raised.
    """
    from maxcompute_semantic.auth import ncs as ncs_mod

    r = _renderer(ctx)

    if not ncs_mod.is_available():
        r.success(
            {
                "available": False,
                "reason": "ncs binary not found on PATH",
                "identities": [],
            }
        )
        return

    try:
        auths = ncs_mod.list_odps_authorizations()
    except Exception:
        r.success(
            {
                "available": False,
                "reason": "ncs probe failed",
                "identities": [],
            }
        )
        return

    if not auths:
        r.success(
            {
                "available": False,
                "reason": "ncs returned no ODPS authorizations",
                "identities": [],
            }
        )
        return

    r.success(
        {
            "available": True,
            "identities": [
                {
                    "buc_user_id": a.buc_user_id,
                    "buc_user_type": a.buc_user_type,
                    "buc_account_name": a.buc_account_name,
                }
                for a in auths
            ],
        }
    )


def _make_client_for_project(
    project: str | None = None, profile_name: str | None = None
) -> MaxComputeClient:
    """Create a MaxComputeClient from a named profile or the env.

    The active-profile resolution chain, in priority order:

      1. ``profile_name`` explicitly given (the ``--profile NAME``
         flag at the CLI layer) → use that saved profile.
      2. The ``MCS_PROFILE`` environment variable, when set to a
         non-empty string, names a saved profile in
         ``profiles.yaml``. This is the shell-scoped active-profile
         pointer, useful for CI bots and one-off shells.
      3. The cwd-link binding written by ``mcs link bind <NAME>``
         (stored as a cwd→name mapping in ``link.json`` under the
         config dir) → resolve the bound name and use it.
      4. The standard ODPS env-var-anonymous fallback: build an
         in-memory unnamed ``Profile`` from
         ``ALIBABA_CLOUD_ACCESS_KEY_ID`` /
         ``ALIBABA_CLOUD_ACCESS_KEY_SECRET`` /
         ``MAXCOMPUTE_ENDPOINT`` / ``MAXCOMPUTE_PROJECT`` (with the
         ``--project`` CLI arg overriding the env's project value).
         No on-disk profile entry; the resulting ``Profile.name``
         is the empty string when ``$MAXCOMPUTE_PROJECT`` is also
         unset.

    ``--project`` selects the target MaxCompute project for commands
    that accept it. It does not select a saved profile by matching the
    profile alias.
    """
    from maxcompute_semantic.auth.context import make_client_for_project

    return make_client_for_project(project, profile_name=profile_name)


def _resolve_profile_for_project(
    project: str | None = None, profile_name: str | None = None
) -> Profile:
    """Resolve a Profile object without creating a client.

    Same resolution order as _make_client_for_project but returns just
    the Profile. Useful for commands that only need profile metadata
    (e.g. status, build) without hitting MaxCompute.

    When ``project`` is supplied and a saved profile is resolved, the
    profile's ``compute_project`` is rewritten to the CLI-supplied
    value so commands that submit SQL or hit metadata endpoints
    target the user's chosen project rather than silently running
    against ``profile.compute_project``. The override leaves every
    other profile field untouched (sources, auth, endpoint), which
    matches the agent's mental model of "same credential, different
    target project".
    """
    from maxcompute_semantic.auth.context import resolve_profile_for_project

    return resolve_profile_for_project(project, profile_name=profile_name)


# ── profile create ──────────────────────────────────────────────────────────

_PUBLIC_ENDPOINT_TEMPLATE = "https://service.{region}.maxcompute.aliyun.com/api"

_INTERNAL_ENDPOINTS = {
    "1": ("Lazada (SG)", "http://service-all.ali-sg-lazada.odps.aliyun-inc.com/api"),
    "2": ("CN Hangzhou (corp)", "http://service-corp.odps.aliyun-inc.com/api"),
    "3": ("Singapore", "http://service-sg.odps.aliyun-inc.com/api"),
    "4": ("Germany", "http://service-corp.de-internal.odps.aliyun-inc.com/api"),
    "5": ("US Ant", "http://service-corp-us.odps.aliyun-inc.com/api"),
    "6": ("Vietnam Ant", "http://service-all.vn-ant.odps.aliyun-inc.com/api"),
}

_ENV_TYPE_CHOICES = {
    "1": "public",
    "2": "internal",
    "3": "custom",
}

_NCS_COMMAND_TEMPLATE = (
    "ncs create credential odpsuser --employee-id {employee_id} -o template -t odpscmd"
)


def _auth_summary(auth: AkAuth | ProcessAuth) -> str:
    """Short, secret-redacted label for the Step 1.5 reuse prompt.

    AkAuth: ``AK xxxxxxxx<last 5 of access_key_id>``. When the ID is a
    ``${env:VAR}`` reference, show the reference verbatim — env-var
    names aren't sensitive and masking them would just hide which env
    var the user is reusing.

    ProcessAuth: ``process: <command>``, truncated to 40 chars of the
    command body with an ellipsis when longer.
    """
    if isinstance(auth, AkAuth):
        ak_id = auth.access_key_id
        if ak_id.startswith("${env:"):
            return f"AK {ak_id}"
        tail = ak_id[-5:] if len(ak_id) >= 5 else ak_id
        return f"AK xxxxxxxx{tail}"
    # ProcessAuth
    cmd = auth.command
    if len(cmd) > 40:
        return f"process: {cmd[:40]}…"
    return f"process: {cmd}"


def _sources_summary(sources: tuple[DataSource, ...]) -> str:
    """``N sources, M tables`` summary for the data-sources reuse prompt.

    Counts a wildcard ('*') source as 1 table — matches how
    ``mcs status`` reports it pre-build (before the wildcard is
    expanded against the live catalog).
    """
    n_sources = len(sources)
    n_tables = 0
    for s in sources:
        if isinstance(s.tables, str):
            n_tables += 1  # wildcard "*"
        else:
            n_tables += len(s.tables)
    return f"{n_sources} sources, {n_tables} tables"


@dataclass
class ReuseDecisions:
    """What the user agreed to clone from an existing mcs profile.

    Each field is the cloned value or a "no" sentinel:

    - ``auth`` / ``endpoint``: ``None`` means "no, run the wizard's
      Step 3 / Step 2 to collect this field".
    - ``compute_project``: empty string ``""`` means "no, fall through
      to the create_cmd auto-discovery via list_projects" (same
      semantics as the wizard returning a shell with no project today).
    - ``sources``: empty tuple ``()`` means "no, drop into the
      file-browser editor to add sources" (same semantics as today).

    Used by the wizard at Step 1.5 to communicate per-field reuse
    decisions to the rest of the wizard flow.
    """

    auth: AkAuth | ProcessAuth | None = None
    endpoint: str | None = None
    compute_project: str = ""
    sources: tuple[DataSource, ...] = ()


def _reuse_existing_profile(src: McsProfileCandidate) -> ReuseDecisions:
    """Run four y/n prompts (auth / endpoint / compute_project / sources)
    and return a ReuseDecisions struct.

    Default values reflect the typical "new scenario" intent:
    Y / Y / N / N. The compute_project and sources prompts are
    auto-skipped (treated as N) when the source has nothing to
    clone there — a build-in-progress shell has no project,
    a fresh profile has no sources.
    """
    click.secho(f"  📋 cloning from mcs:{src.name}", fg="green")

    reuse_auth = click.confirm(
        f"  ↪ Reuse auth ({_auth_summary(src.auth)})?",
        default=True,
    )
    reuse_endpoint = click.confirm(
        f"  ↪ Reuse endpoint ({src.endpoint})?",
        default=True,
    )
    reuse_project = bool(
        src.compute_project
        and click.confirm(
            f"  ↪ Reuse compute_project ({src.compute_project})?",
            default=False,
        )
    )
    reuse_sources = bool(
        src.sources
        and click.confirm(
            f"  ↪ Reuse data sources ({_sources_summary(src.sources)})?",
            default=False,
        )
    )

    return ReuseDecisions(
        auth=src.auth if reuse_auth else None,
        endpoint=src.endpoint if reuse_endpoint else None,
        compute_project=src.compute_project if reuse_project else "",
        sources=src.sources if reuse_sources else (),
    )


def _build_endpoint_from_region(region: str) -> str:
    """Build public-cloud endpoint URL from a region string.

    If the input already starts with ``http``, return it as-is.
    Otherwise, interpolate into ``_PUBLIC_ENDPOINT_TEMPLATE``.
    """
    if region.startswith("http://") or region.startswith("https://"):
        return region
    return _PUBLIC_ENDPOINT_TEMPLATE.format(region=region)


def _classify_endpoint(endpoint: str) -> tuple[str, str]:
    """Reverse-map an endpoint URL to (env_type, region_or_key_or_url).

    Returns:
        ("public", region)   — matches the public template
        ("internal", key)    — matches an internal preset
        ("internal", url)    — host ends in .aliyun-inc.com (user-typed variant)
        ("custom", url)      — anything else
    """
    for key, (_, url) in _INTERNAL_ENDPOINTS.items():
        if endpoint == url:
            return "internal", key

    prefix = _PUBLIC_ENDPOINT_TEMPLATE.split("{region}")[0]
    suffix = _PUBLIC_ENDPOINT_TEMPLATE.split("{region}")[1]
    if endpoint.startswith(prefix) and endpoint.endswith(suffix):
        region = endpoint[len(prefix) : -len(suffix)]
        return "public", region

    # Any URL hosted on the corp intranet domain → internal. Covers
    # user-typed variants such as
    # `http://service.cn-shanghai-corp.odps.aliyun-inc.com/api` that
    # are not in the preset list. The second tuple element is the
    # URL itself; downstream callers only consume the first element.
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.hostname and parsed.hostname.endswith(".aliyun-inc.com"):
        return "internal", endpoint

    return "custom", endpoint


def _prompt_required(text: str, *, default: str | None = None, hide_input: bool = False) -> str:
    """Prompt until a non-empty value is given."""
    while True:
        value = click.prompt(text, default=default, hide_input=hide_input)
        if value is not None and str(value).strip():
            return str(value).strip()
        click.echo("value cannot be empty")


def _read_secret_line_from_stdin() -> str:
    if sys.stdin.isatty():
        raise click.ClickException(
            "--ak-secret-stdin expects the secret on stdin; use the interactive "
            "literal prompt when running in a TTY"
        )
    value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        raise click.ClickException("--ak-secret-stdin received an empty secret")
    return value


def _validate_ak_secret_flags(
    *,
    ak_secret: str | None,
    ak_secret_stdin: bool,
    ak_literal: bool,
    ak_id: str | None,
    ak_id_env: str | None,
    ak_secret_env: str | None,
    auth_type: str | None,
    from_file: str | None,
    from_spec: str | None,
) -> None:
    if ak_secret is not None:
        click.secho(
            "Warning: --ak-secret may expose the secret in shell history; "
            "prefer --ak-secret-stdin, --ak-secret-env, or ProcessAuth.",
            fg="yellow",
            err=True,
        )
        if ak_secret_env:
            raise click.ClickException("--ak-secret cannot be used with --ak-secret-env")
        if ak_id_env:
            raise click.ClickException("--ak-secret cannot be used with --ak-id-env")
        if not ak_literal and ak_id is None:
            raise click.ClickException("--ak-secret requires --ak-id or --ak-literal")
        if ak_literal and not ak_id:
            raise click.ClickException("--ak-secret with --ak-literal requires --ak-id")
    if ak_secret_stdin and ak_secret is not None:
        raise click.ClickException("--ak-secret-stdin cannot be used with --ak-secret")
    if ak_secret_stdin and not ak_literal:
        raise click.ClickException("--ak-secret-stdin requires --ak-literal")
    if ak_secret_stdin and not ak_id:
        raise click.ClickException("--ak-secret-stdin requires --ak-id")
    if ak_secret_stdin and ak_secret_env:
        raise click.ClickException("--ak-secret-stdin cannot be used with --ak-secret-env")
    if ak_secret_stdin and ak_id_env:
        raise click.ClickException("--ak-secret-stdin cannot be used with --ak-id-env")
    if ak_secret_stdin and (from_file is not None or from_spec is not None):
        raise click.ClickException(
            "--ak-secret-stdin cannot be used with --from-file or --from-spec"
        )
    if ak_secret is not None and (from_file is not None or from_spec is not None):
        raise click.ClickException("--ak-secret cannot be used with --from-file or --from-spec")
    if auth_type in {"ncs", "process"}:
        if ak_secret is not None:
            raise click.ClickException(f"--ak-secret cannot be used with --auth-type {auth_type}")
        if ak_secret_stdin:
            raise click.ClickException(
                f"--ak-secret-stdin cannot be used with --auth-type {auth_type}"
            )


@profile_group.command("create")
@click.option("--project", help="MaxCompute project name")
@click.option("--endpoint", help="override endpoint URL (overrides --region)")
@click.option("--region", help="public-cloud region (e.g. cn-shanghai); auto-builds endpoint")
@click.option("--auth-type", type=click.Choice(["ak", "ncs", "process"]), help="auth method")
@click.option("--employee-id", help="employee ID for ncs process auth")
@click.option("--alias", help="profile name (skip the alias prompt)")
@click.option("--ncs-command", help="override ncs command for process auth")
@click.option("--ak-id-env", help="env var name for AK access key ID")
@click.option("--ak-secret-env", help="env var name for AK secret")
@click.option("--ak-literal", is_flag=True, help="store AK values directly (not env var refs)")
@click.option("--ak-id", help="literal AK access key ID (requires --ak-literal)")
@click.option("--ak-secret", help="literal AK secret (requires --ak-literal)")
@click.option(
    "--ak-secret-stdin",
    is_flag=True,
    help="read literal AK secret from one stdin line (requires --ak-literal and --ak-id)",
)
@click.option("--tag", multiple=True, help="tag labels (repeatable)")
@click.option("--no-test", is_flag=True, help="skip auth validation + auto-discovery")
@click.option(
    "--confirm-cny", type=float, default=None, help="cost confirmation threshold (default: 10.0)"
)
@click.option(
    "--blocked-cny", type=float, default=None, help="cost blocking threshold (default: 100.0)"
)
@click.option(
    "--show-advanced", is_flag=True, help="show cost thresholds and tags prompts in wizard"
)
@click.option(
    "--from-file",
    "from_file",
    default=None,
    help=(
        "non-interactive: load complete profile spec from a file (yaml or "
        "json — the loader accepts both, json is a yaml subset). "
        "Curl-style '@path' allowed. See `mcs profile spec-template` for "
        "the schema."
    ),
)
@click.option(
    "--from-spec",
    "from_spec",
    default=None,
    help=(
        "non-interactive: load complete profile spec from inline string "
        "(yaml or json — same loader). See `mcs profile spec-template` "
        "for a fillable template."
    ),
)
@click.pass_context
def create_cmd(
    ctx: click.Context,
    project: str | None,
    endpoint: str | None,
    region: str | None,
    auth_type: str | None,
    employee_id: str | None,
    alias: str | None,
    ncs_command: str | None,
    ak_id_env: str | None,
    ak_secret_env: str | None,
    ak_literal: bool,
    ak_id: str | None,
    ak_secret: str | None,
    ak_secret_stdin: bool,
    tag: tuple[str, ...],
    no_test: bool,
    confirm_cny: float | None,
    blocked_cny: float | None,
    show_advanced: bool,
    from_file: str | None,
    from_spec: str | None,
) -> None:
    """Create a new profile.

    \b
    Two modes:

    \b
    1. ``--from-file @profile.yaml`` / ``--from-spec '<inline JSON>'`` —
       load complete-profile spec from yaml/json (same shape as
       ``mcs profile show NAME --format json``). The canonical
       non-interactive entry point for CI / scripts / agents. Run
       ``mcs profile spec-template`` to dump a minimal fillable yaml
       template to stdout. Same loader as ``mcs profile update
       --from-file/--from-spec``; ``create`` rejects an existing-
       profile name, ``update`` requires one.

    \b
       Spec example::

         name: my-profile
         compute_project: acme
         endpoint: https://service.cn-shanghai.maxcompute.aliyun.com/api
         auth:
           type: ak
           access_key_id: ${env:ALIBABA_CLOUD_ACCESS_KEY_ID}
           access_key_secret: ${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}
         sources:
           - project: acme
             schema: default
             tables: '*'

    \b
    2. Interactive (default) — wizard collects alias + endpoint +
       auth, auto-discovers ``compute_project`` via the AK's
       ``list_projects()`` (with ``--no-test`` falling back to a
       manual prompt), runs auth-test, saves the shell, and drops
       into the file-browser editor for cost thresholds / tags /
       sources. Cancel in the editor leaves the bare shell saved.

    Per-prompt flags (``--alias`` / ``--project`` / ``--endpoint`` /
    ``--region`` / ``--auth-type`` / ``--ak-id-env`` / etc.) skip
    individual wizard prompts when provided — useful for partial
    automation.
    """
    from maxcompute_semantic.auth.profile_store import upsert

    r = _renderer(ctx)
    _validate_ak_secret_flags(
        ak_secret=ak_secret,
        ak_secret_stdin=ak_secret_stdin,
        ak_literal=ak_literal,
        ak_id=ak_id,
        ak_id_env=ak_id_env,
        ak_secret_env=ak_secret_env,
        auth_type=auth_type,
        from_file=from_file,
        from_spec=from_spec,
    )

    # ── Mode 1: --from-file / --from-spec (full-profile spec) ──────
    if from_file is not None or from_spec is not None:
        if from_file is not None and from_spec is not None:
            raise click.UsageError("--from-file and --from-spec are mutually exclusive")
        # Resolve the name from --alias if the spec doesn't carry it
        # yet — but the spec MUST have a top-level ``name`` field.
        # We need to know the name *before* loading to check
        # "doesn't already exist", and to validate the spec name
        # matches. The spec is the source of truth here; --alias is
        # just a hint we surface as a "name mismatch" if both are
        # given and disagree.
        # Two-pass: peek the name from the spec, then full-load.
        from pathlib import Path as _Path

        from ruamel.yaml import YAML

        if from_file is not None:
            try:
                raw_text = _Path(from_file.lstrip("@")).read_text(encoding="utf-8")
            except OSError as e:
                raise click.UsageError(f"could not read --from-file: {e}") from e
        else:
            assert from_spec is not None
            raw_text = from_spec
        try:
            peek = YAML(typ="safe").load(raw_text)
        except Exception as e:
            raise click.UsageError(f"could not parse spec as yaml/json: {e}") from e
        if not isinstance(peek, dict):
            raise click.UsageError(f"spec must be a yaml mapping (got {type(peek).__name__})")
        spec_name = peek.get("name")
        if spec_name is None:
            raise click.UsageError("spec missing required 'name' field")
        if alias and alias != spec_name:
            raise click.UsageError(f"--alias {alias!r} does not match spec name {spec_name!r}")
        # Refuse to clobber an existing profile.
        try:
            get(spec_name)
        except ProfileNotFoundError:
            pass  # good — name available
        else:
            raise click.UsageError(
                f"profile {spec_name!r} already exists; use `mcs profile update` "
                f"to modify it instead of `create`"
            )
        try:
            new_profile = _load_full_profile_spec(spec_name, from_file, from_spec)
        except McsError as e:
            r.error(e)
            sys.exit(1)
        if not no_test:
            from maxcompute_semantic.commands._auth_probe import _run_auth_test

            test_ok = _run_auth_test(new_profile, r, emit_summary=False)
            if test_ok != 0 and not click.confirm(
                "Auth test failed. Save profile anyway?", default=False
            ):
                return
        upsert(new_profile)
        r.success(
            {
                "created": new_profile.name,
                "compute_project": new_profile.compute_project,
                "sources": [s.source_key() for s in new_profile.sources],
            }
        )
        r.quiet_essential({"created": new_profile.name}, "created")
        # Born-versioned: trigger the auto-commit hook so the brand-new
        # profile's data dir lands its inaugural ``init: import existing
        # data`` commit (the auto-init branch of the hook does the
        # ``git init`` + ``.gitignore`` write + first commit in one go;
        # see ``versioning/hook.py`` for the algorithm). ``MCS_NO_VERSIONING=1``
        # short-circuits inside the hook so the eval-harness sandbox
        # path stays unchanged. Any failure (e.g. missing ``git``
        # binary → ``GitNotAvailable``) propagates as ``McsError`` to
        # the click harness's exception envelope.
        commit_after_command(new_profile, action=ACTION_INIT, summary=new_profile.name)
        return

    # ── Mode 2: interactive wizard ─────────────────────────────────
    profile = _create_wizard(
        project=project,
        endpoint=endpoint,
        region=region,
        auth_type=auth_type,
        employee_id=employee_id,
        alias=alias,
        ncs_command=ncs_command,
        ak_id_env=ak_id_env,
        ak_secret_env=ak_secret_env,
        ak_literal=ak_literal,
        ak_id=ak_id,
        ak_secret=ak_secret,
        ak_secret_stdin=ak_secret_stdin,
        tag=tag,
        confirm_cny=confirm_cny,
        blocked_cny=blocked_cny,
        show_advanced=show_advanced,
        r=r,
    )
    if profile is None:
        return

    # Wizard returns ``compute_project=""`` unless ``--project`` was
    # explicitly provided; fill it in via either auto-discovery (the
    # auth-touching ``list_projects`` path) or a manual prompt when
    # ``--no-test`` asks us not to touch the network.
    cached_projects: list[str] | None = None
    if not profile.compute_project:
        if no_test:
            picked: str | None = click.prompt(
                "MaxCompute project name (auto-discovery skipped by --no-test)"
            )
        else:
            click.echo("Discovering accessible MaxCompute projects via the AK...")
            picked, cached_projects = _discover_compute_project(profile, r)
        if not picked:
            return
        import dataclasses as _dc

        profile = _dc.replace(profile, compute_project=picked)

    # Validate the now-complete shell before auth-test.
    try:
        profile.validate()
    except McsError as e:
        r.error(e)
        return

    # Auth test before saving (Phase 1 gate). ``list_projects`` already
    # verified the catalog API works, so this ``SELECT 1`` is the
    # belt-and-braces "session can actually execute on the compute
    # project" check.
    if not no_test:
        from maxcompute_semantic.commands._auth_probe import _run_auth_test

        test_ok = _run_auth_test(profile, r, emit_summary=False)
        if test_ok != 0 and not click.confirm(
            "Auth test failed. Save profile anyway?", default=False
        ):
            return

    # Phase 1 commit: bare profile shell (wizard returns sources=()).
    upsert(profile)
    click.secho(
        f"📦 Profile '{profile.name}' created (no sources yet).",
        fg="green",
    )

    # Phase 2: drop into the file-browser editor so the user can fill
    # in cost thresholds / tags / sources / any other tweaks before
    # the final commit. Cancel in the editor leaves the bare shell
    # saved (Phase 1 already committed it).
    if click.confirm(
        click.style("🛠  Configure now (sources, tags, etc.)?", fg="cyan"),
        default=True,
    ):
        from maxcompute_semantic.commands._profile_editor import edit_profile
        from maxcompute_semantic.mc_client.client import MaxComputeClient

        client = MaxComputeClient(profile)
        # Pass cached_projects from discovery so the editor's
        # "Add source" path doesn't re-query list_projects().
        # ``no_test`` skips discovery → cached_projects is None.
        edited = edit_profile(profile, client, cached_projects=cached_projects)
        if edited is not None:
            try:
                edited.validate()
            except McsError as e:
                r.error(e)
                # Phase 1 shell is already saved; surface the error
                # and exit non-zero so the caller knows the editor's
                # changes were rejected.
                sys.exit(1)
            upsert(edited)
            profile = edited

    r.success(
        {
            "created": profile.name,
            "compute_project": profile.compute_project,
            "sources": [s.source_key() for s in profile.sources],
        }
    )
    r.quiet_essential({"created": profile.name}, "created")
    # Born-versioned: see the Mode-1 sibling above for the hook's
    # algorithm. The wizard runs ``profile_store.upsert(profile)``
    # up to twice on this branch (the Phase-1 bare-shell save and
    # the optional Phase-2 editor-revised save). Both writes go to
    # ``profiles.yaml`` under ``MCS_CONFIG_DIR``, which by design
    # sits outside the per-profile data dir that the hook scopes
    # its commit to (the hook's only path-helper imports in
    # ``versioning/hook.py`` are the four data-dir-rooted ones —
    # ``profile_data_dir`` / ``profile_gitignore_path`` /
    # ``profile_package_sql_path`` / ``profile_lock_path``). The
    # inaugural ``init: import existing data`` commit's tree is
    # therefore the contents of the data dir at this point: the
    # canonical ``.gitignore`` the auto-init branch wrote, plus
    # any incidental files the wizard's live probes dropped along
    # the way (the ``tier_cache/<project>`` sentinel that the live
    # tier-probe call leaves behind is the standard example).
    commit_after_command(profile, action=ACTION_INIT, summary=profile.name)


def _discover_compute_project(
    profile_shell: Profile, r: Renderer
) -> tuple[str | None, list[str] | None]:
    """Auto-discover ``compute_project`` from the AK's accessible projects.

    Builds a MaxComputeClient with the wizard's profile shell (which
    has ``compute_project=""``), calls ``list_projects()`` with a
    spinner, and lets the user pick from the returned list via the
    same ``_pick_project`` helper used by the editor. On
    ``list_projects`` failure (auth scope problems on internal
    endpoints, network errors, etc.) falls back to manual entry —
    the same fallback the editor uses.

    Returns ``(picked_project, project_list)`` — the project_list is
    passed to ``edit_profile`` as ``cached_projects`` so the source-
    adding flow doesn't re-query ``list_projects()``.
    """
    from maxcompute_semantic.commands._source_picker import _pick_project, _Spinner

    # Build a temp client. The empty compute_project on the shell is
    # not used by ``list_projects`` — that call hits the catalog API
    # via the AK, not a project-bound ODPS connection. We keep the
    # shell unvalidated until the picked project is filled in.
    try:
        client = MaxComputeClient(profile_shell)
    except McsError as e:
        r.error(e)
        return (None, None)

    # Query list_projects with spinner, keep the result for caching.
    try:
        with _Spinner("Listing projects..."):
            project_list = client.list_projects()
    except Exception:
        project_list = None

    picked = _pick_project(
        client,
        default=None,
        existing=None,
        cached_projects=project_list,
        role="compute",
    )
    return (picked, project_list)


def _create_wizard(
    project: str | None,
    endpoint: str | None,
    region: str | None,
    auth_type: str | None,
    employee_id: str | None,
    alias: str | None,
    ncs_command: str | None,
    ak_id_env: str | None,
    ak_secret_env: str | None,
    ak_literal: bool,
    ak_id: str | None,
    ak_secret: str | None,
    ak_secret_stdin: bool,
    tag: tuple[str, ...],
    confirm_cny: float | None,
    blocked_cny: float | None,
    show_advanced: bool,
    r: Renderer,
) -> Profile | None:
    """4-step interactive wizard (cost/tags hidden unless --show-advanced).

    Order: alias → endpoint → auth → return shell with empty
    compute_project (caller discovers + fills it via ``list_projects``).

    The wizard collects the bare-minimum identity + auth + endpoint
    needed to construct a MaxComputeClient. ``create_cmd`` then
    discovers ``compute_project`` from the AK's accessible projects
    via ``client.list_projects()`` (with manual-entry fallback),
    runs auth-test against the picked project, saves the shell,
    and drops into the file-browser editor for the rest.

    Optional fields (cost thresholds / tags) come via flags or
    ``--show-advanced``.
    """
    # Step 1: profile name (alias) — FIRST
    name = alias or _prompt_required("Profile name (alias)")
    env_type: str | None = None  # set in Step 2 or inferred later
    existing_names = set(load_all().keys())
    if name in existing_names and not click.confirm(
        click.style(f"⚠️  profile {name!r} already exists; overwrite?", fg="yellow"),
        default=False,
    ):
        click.secho(
            "🚫 aborted (pick a different name with --alias or via the prompt)",
            fg="yellow",
        )
        return None

    # Step 1.5: credential source discovery — offers existing mcs
    # profiles (📋) and external maxc / odpscmd configs (🔑) as
    # candidates. 📋 selections route to the per-field reuse flow;
    # 🔑 selections bulk-import and short-circuit out of the wizard.
    # User flags ``--endpoint`` / ``--auth-type`` etc. mean they
    # want to drive the wizard manually, so skip the entire picker.
    skip_import_offer = bool(
        endpoint
        or region
        or auth_type
        or ak_id_env
        or ak_secret_env
        or ak_id
        or ak_secret_stdin
        or ak_literal
        or ncs_command
        or employee_id
    )
    reuse_decisions: ReuseDecisions | None = None
    if not skip_import_offer:
        from maxcompute_semantic.commands._import_creds import (
            discover_creds,
            discover_mcs_profiles,
        )

        ext_candidates = discover_creds()
        mcs_candidates = discover_mcs_profiles(exclude_name=name)

        if ext_candidates or mcs_candidates:
            from maxcompute_semantic.commands._source_picker import _pick_one

            mcs_items = [f"📋 {c.display()}" for c in mcs_candidates]
            ext_items = [f"🔑 {c.display()}" for c in ext_candidates]
            items = mcs_items + ext_items + ["➡️  skip — configure manually"]
            choice = _pick_one(
                "Pick a credential source:",
                choices=items,
                echo_label="Credential",
            )

            if choice is not None and choice != "➡️  skip — configure manually":
                if choice.startswith("📋 "):
                    src = mcs_candidates[mcs_items.index(choice)]
                    reuse_decisions = _reuse_existing_profile(src)
                elif choice.startswith("🔑 "):
                    creds = ext_candidates[ext_items.index(choice)]
                    adopted = _confirm_imported_process_auth(creds)
                    if not adopted:
                        click.echo("  Skipped — configure manually.")
                    if adopted:
                        click.secho(
                            f"  📥 importing {creds.source_label} credentials "
                            f"(skipping endpoint / auth prompts)",
                            fg="green",
                        )
                        return Profile(
                            name=name,
                            compute_project=creds.compute_project,
                            endpoint=creds.endpoint,
                            auth=creds.auth,
                            sources=(),
                            cost_thresholds=CostThresholds(
                                confirm_cny=confirm_cny if confirm_cny is not None else 10.0,
                                blocked_cny=blocked_cny if blocked_cny is not None else 100.0,
                            ),
                            tags=tag,
                        )

    # Step 2: endpoint — four-tier (reuse decision wins, then flags, then prompts)
    if reuse_decisions is not None and reuse_decisions.endpoint is not None:
        chosen_endpoint = reuse_decisions.endpoint
        env_type = _classify_endpoint(chosen_endpoint)[0]
    elif endpoint:
        chosen_endpoint = endpoint
    elif region:
        chosen_endpoint = _build_endpoint_from_region(region)
    else:
        from maxcompute_semantic.commands._source_picker import _pick_one

        env_items = list(_ENV_TYPE_CHOICES.values())  # ["public", "internal", "custom"]
        env_choice = _pick_one(
            "Environment:", choices=env_items, echo_label="Environment", echo_emoji="🌍"
        )
        if env_choice is None:
            return None
        env_type = env_choice

        if env_type == "public":
            region_input = _prompt_required("Region (e.g. cn-shanghai)")
            chosen_endpoint = _build_endpoint_from_region(region_input)
        elif env_type == "internal":
            ep_items = [f"{label} ({url})" for label, url in _INTERNAL_ENDPOINTS.values()]
            choice = _pick_one(
                "Internal endpoint:",
                choices=ep_items,
                echo_label="Endpoint",
                echo_emoji="🌐",
            )
            if choice is None:
                return None
            # Find the matching endpoint by display string.
            url = next(
                (
                    url
                    for label, url in _INTERNAL_ENDPOINTS.values()
                    if f"{label} ({url})" == choice
                ),
                None,
            )
            if url is None:
                click.echo("invalid choice")
                return None
            chosen_endpoint = url
        else:  # custom
            chosen_endpoint = _prompt_required("Custom endpoint URL")

    # Step 3 + 4: auth method + credentials
    chosen_auth: ProcessAuth | AkAuth
    if reuse_decisions is not None and reuse_decisions.auth is not None:
        chosen_auth = reuse_decisions.auth
    else:
        # Step 3: auth method — default depends on environment:
        #   internal / custom → ncs (ncs is standard on intranet)
        #   public → ak (AK is more common on public endpoints)
        # When Step 2 was skipped via flags, infer env_type from endpoint.
        if env_type is None:
            env_type = _classify_endpoint(chosen_endpoint)[0]
        if env_type == "internal":
            auth_default = "ncs"
        elif env_type == "custom":
            auth_default = "process"
        else:
            auth_default = "ak"
        chosen_auth_type = auth_type or _pick_one(
            "Auth method:",
            choices=["ak", "ncs", "process"],
            default=auth_default,
        )

        # Step 4: credentials
        if chosen_auth_type == "ak":
            if ak_secret_stdin:
                chosen_auth = AkAuth(
                    access_key_id=ak_id or "",
                    access_key_secret=_read_secret_line_from_stdin(),
                )
            elif ak_literal or (ak_id is not None and ak_secret is not None):
                # Literal mode via flags
                chosen_auth = AkAuth(
                    access_key_id=ak_id or "",
                    access_key_secret=ak_secret or "",
                )
            elif ak_id_env and ak_secret_env:
                # Env-var mode via flags
                chosen_auth = AkAuth(
                    access_key_id=f"${{env:{ak_id_env}}}",
                    access_key_secret=f"${{env:{ak_secret_env}}}",
                )
            else:
                # Interactive: choose AK mode
                mode_choice = _pick_one(
                    "AK auth mode:",
                    choices=[
                        "Env var reference — store env var names, not secrets",
                        "Literal values — store AK directly in profiles.yaml",
                    ],
                    default="Env var reference — store env var names, not secrets",
                )
                if mode_choice == "Env var reference — store env var names, not secrets":
                    ak_id_val = click.prompt(
                        "Access Key ID env var", default="ALIBABA_CLOUD_ACCESS_KEY_ID"
                    )
                    ak_secret_val = click.prompt(
                        "Access Key Secret env var", default="ALIBABA_CLOUD_ACCESS_KEY_SECRET"
                    )
                    chosen_auth = AkAuth(
                        access_key_id=f"${{env:{ak_id_val}}}",
                        access_key_secret=f"${{env:{ak_secret_val}}}",
                    )
                else:
                    ak_id_val = _prompt_required("Access Key ID")
                    ak_secret_val = _prompt_required("Access Key Secret", hide_input=True)
                    chosen_auth = AkAuth(access_key_id=ak_id_val, access_key_secret=ak_secret_val)
        elif chosen_auth_type == "ncs":
            # ncs auth — auto-discover ODPS identities via ncs CLI
            from maxcompute_semantic.auth import ncs as ncs_mod

            # Preflight: surface the install-docs hint once if the
            # binary is missing. The flow continues either way
            # (manual employee-id fallback path is unchanged).
            if not ncs_mod.is_available():
                click.secho(ncs_mod.install_hint(), fg="yellow")

            if ncs_command:
                chosen_auth = ProcessAuth(command=ncs_command)
            elif ncs_mod.is_available() and employee_id is None:
                auths = ncs_mod.list_odps_authorizations()
                if auths:
                    id_items = [f"{a.buc_account_name} ({a.buc_user_type})" for a in auths]
                    choice = _pick_one("Select ODPS identity:", choices=id_items)
                    if choice is not None:
                        selected = next(
                            (
                                a
                                for a in auths
                                if f"{a.buc_account_name} ({a.buc_user_type})" == choice
                            ),
                            None,
                        )
                        if selected is not None:
                            cmd = (
                                f"ncs create credential odpsuser "
                                f"--buc-user-id {selected.buc_user_id} "
                                "-o template -t odpscmd"
                            )
                            chosen_auth = ProcessAuth(command=cmd)
                        else:
                            eid = _prompt_required("Employee ID")
                            chosen_auth = ProcessAuth(
                                command=_NCS_COMMAND_TEMPLATE.format(employee_id=eid)
                            )
                    else:
                        # User cancelled fzf or no auths discovered
                        eid = employee_id or _prompt_required("Employee ID")
                        chosen_auth = ProcessAuth(
                            command=_NCS_COMMAND_TEMPLATE.format(employee_id=eid)
                        )
                else:
                    eid = employee_id or _prompt_required("Employee ID")
                    chosen_auth = ProcessAuth(command=_NCS_COMMAND_TEMPLATE.format(employee_id=eid))
            else:
                eid = employee_id or _prompt_required("Employee ID")
                chosen_auth = ProcessAuth(command=_NCS_COMMAND_TEMPLATE.format(employee_id=eid))
        else:
            # process auth — user provides a custom command that returns
            # STS AssumeRole JSON (AccessKeyId, AccessKeySecret, SecurityToken)
            custom_cmd = ncs_command or _prompt_required(
                "Process command (must return STS AssumeRole JSON on stdout)"
            )
            custom_timeout = click.prompt("Timeout (seconds, 1-600)", type=int, default=60)
            chosen_auth = ProcessAuth(command=custom_cmd, timeout=custom_timeout)

    # Advanced: cost thresholds + tags (hidden unless --show-advanced or flags)
    _confirm = confirm_cny
    _blocked = blocked_cny
    if show_advanced or confirm_cny is not None or blocked_cny is not None:
        if _confirm is None:
            _confirm = click.prompt("Cost confirm threshold (CNY)", type=float, default=10.0)
        if _blocked is None:
            _blocked = click.prompt("Cost blocked threshold (CNY)", type=float, default=100.0)
    if _confirm is None:
        _confirm = 10.0
    if _blocked is None:
        _blocked = 100.0
    cost = CostThresholds(confirm_cny=_confirm, blocked_cny=_blocked)

    chosen_tags = tag
    if (show_advanced or tag) and not chosen_tags:
        tag_input = click.prompt("Tags (comma-separated, or blank)", default="", show_default=False)
        if tag_input.strip():
            chosen_tags = tuple(t.strip() for t in tag_input.split(",") if t.strip())

    # Wizard returns the Profile shell with an empty ``compute_project``
    # — the caller (``create_cmd``) discovers it via the auth's
    # ``list_projects()`` and fills it in. The legacy ``--project P``
    # flag, when given, short-circuits the discovery and is applied
    # before validation; passed through here as ``project``.
    profile = Profile(
        name=name,
        compute_project=(project or (reuse_decisions.compute_project if reuse_decisions else "")),
        endpoint=chosen_endpoint,
        auth=chosen_auth,
        sources=(reuse_decisions.sources if reuse_decisions else ()),
        cost_thresholds=cost,
        tags=chosen_tags,
    )
    # Skip Profile.validate() here because compute_project may still
    # be empty pending discovery. ``create_cmd`` validates after the
    # discovery + assignment.
    return profile


# ── profile update ──────────────────────────────────────────────────────────
#
# Single verb covering all profile edits. Two paths:
#
# - ``mcs profile update PROFILE`` (no flags) → interactive
#   file-browser-style editor (``commands/_profile_editor.edit_profile``)
#   covering compute_project / endpoint / auth / cost_thresholds / tags /
#   sources. Save commits, Cancel discards.
# - ``mcs profile update PROFILE --from-file @profile.yaml`` or
#   ``--from-spec '<inline JSON>'`` → non-interactive: load complete
#   profile spec from yaml/json, validate, run auth-test, upsert
#   (full-replace).
#
# The yaml/json shape matches the on-disk ``profiles.yaml`` per-profile
# block plus a top-level ``name`` field (which must match the PROFILE
# arg). Use ``mcs profile show NAME --format json`` to GET the current
# state in this exact shape, then mutate locally and PUT back via
# ``--from-spec``.


def _load_full_profile_spec(
    name: str,
    from_file: str | None,
    from_spec: str | None,
    *,
    existing: Profile | None = None,
) -> Profile:
    """Parse ``--from-file`` / ``--from-spec`` into a validated Profile.

    Accepts either:
    - ``from_file``: path (with optional leading ``@``); content is
      yaml or json (json is a valid yaml subset).
    - ``from_spec``: inline string; parsed as yaml/json.

    Exactly one must be provided (caller-checked). The spec must
    contain a ``name`` field that matches ``name``; otherwise a
    ``click.UsageError`` is raised (exit 2) so users don't
    accidentally overwrite one profile with another's spec.

    If ``existing`` is provided, ``***REDACTED***`` markers in
    ``auth.access_key_id`` / ``auth.access_key_secret`` are
    substituted with the existing profile's stored values. This
    is the GET-mutate-PUT preserve-auth path — agents read
    ``mcs profile show NAME --format json``, mutate non-auth
    fields, and PUT back via ``update --from-spec``; the redacted
    AK fields they never saw get re-applied here. Only valid
    when the existing profile's auth type matches the spec's auth
    type (both AK); type-changing PUTs must supply real secrets.
    """
    from pathlib import Path as _Path

    from ruamel.yaml import YAML

    if from_file is not None:
        path_str = from_file.lstrip("@")
        try:
            raw_text = _Path(path_str).read_text(encoding="utf-8")
        except OSError as e:
            raise click.UsageError(f"could not read --from-file {path_str!r}: {e}") from e
    else:
        assert from_spec is not None  # caller guarantees
        raw_text = from_spec

    try:
        raw = YAML(typ="safe").load(raw_text)
    except Exception as e:
        raise click.UsageError(f"could not parse spec as yaml/json: {e}") from e

    if not isinstance(raw, dict):
        raise click.UsageError(f"spec must be a yaml mapping (got {type(raw).__name__})")

    spec_name = raw.get("name")
    if spec_name is None:
        raise click.UsageError(f"spec missing required 'name' field (must equal {name!r})")
    if spec_name != name:
        raise click.UsageError(f"spec name {spec_name!r} does not match PROFILE arg {name!r}")

    # ``***REDACTED***`` substitution: if the spec's auth section
    # carries the show-output marker, look up the existing profile and
    # splice the real value in. This makes the GET-mutate-PUT loop work
    # without the agent ever seeing the secret.
    auth_raw = raw.get("auth")
    if (
        isinstance(auth_raw, dict)
        and auth_raw.get("type") == "ak"
        and (
            auth_raw.get("access_key_id") == _REDACTED_MARKER
            or auth_raw.get("access_key_secret") == _REDACTED_MARKER
        )
    ):
        if existing is None or not isinstance(existing.auth, AkAuth):
            raise click.UsageError(
                f"spec uses {_REDACTED_MARKER!r} marker but no AK auth to "
                f"substitute from (the existing profile has no AK auth, or "
                f"this is a `create` invocation — `create` requires real "
                f"secrets, not redaction markers)"
            )
        if auth_raw.get("access_key_id") == _REDACTED_MARKER:
            auth_raw["access_key_id"] = existing.auth.access_key_id
        if auth_raw.get("access_key_secret") == _REDACTED_MARKER:
            auth_raw["access_key_secret"] = existing.auth.access_key_secret

    from maxcompute_semantic.auth.profile_store import _profile_from_dict

    profile = _profile_from_dict(name, raw)
    profile.validate()
    return profile


@profile_group.command("update")
@click.argument("name", required=False, default=None)
@click.option(
    "--from-file",
    "from_file",
    default=None,
    help=(
        "path to a file with the complete profile spec (yaml or json — "
        "the loader accepts both). Curl-style '@path' allowed. "
        "See `mcs profile spec-template` for the schema."
    ),
)
@click.option(
    "--from-spec",
    "from_spec",
    default=None,
    help=(
        "inline string with the complete profile spec (yaml or json — "
        "same loader). See `mcs profile spec-template`."
    ),
)
@click.option("--no-test", is_flag=True, help="skip auth validation after update")
@click.pass_context
def update_cmd(
    ctx: click.Context,
    name: str | None,
    from_file: str | None,
    from_spec: str | None,
    no_test: bool,
) -> None:
    # NO-HOOK: ``profile update`` only mutates ``profiles.yaml`` (auth,
    # endpoint, source list, tags, cost thresholds). None of those live
    # inside the per-profile git repo — the repo tracks ``package_path``
    # contents (package.db, _state.json, _overview.md, etc.). Calling
    # ``commit_after_command`` here would create empty commits on every
    # auth rotation. The hook is wired into write verbs that touch the
    # package data dir (build, refresh, annotate, memory, udf, import).
    """Edit a profile.

    The positional ``NAME`` is optional in the interactive form:
    when omitted, the active profile resolved by the standard
    chain (MCS_PROFILE → cwd-link → env-vars) is the target. The
    env-vars-anonymous case has no on-disk yaml entry to update,
    so the bare invocation in a shell that has no active saved
    profile errors out with a clear message rather than silently
    creating a new one.

    The ``--from-file`` / ``--from-spec`` non-interactive forms
    carry the target name *inside the spec itself*: the spec's
    top-level ``name`` field is the alias of the profile being
    overwritten. The positional ``NAME``, when given alongside
    these flags, is cross-checked against the spec's name and
    must match.

    Interactive (no flags): opens the file-browser-style
    multi-level editor over compute_project / endpoint / auth /
    cost thresholds / tags / sources. Save (top-level ✓) commits
    via ``upsert``; Cancel (top-level ✗) discards.

    Non-interactive: ``--from-file @path`` or ``--from-spec
    '<inline>'`` full-replace the profile from yaml / json. The
    spec shape matches ``mcs profile show NAME --format yaml``'s
    output (the round-trip is a fixed point). AK secret literals
    come back as ``***REDACTED***`` in the GET, and the PUT loader
    substitutes the ``***REDACTED***`` sentinel with the existing
    profile's stored value so the round-trip preserves auth
    without ever exposing it.
    """
    r = _renderer(ctx)
    try:
        if name is not None:
            old = get(name)
        else:
            # Bare invocation: route through the standard active-
            # profile chain to find the target. The env-vars
            # anonymous Profile (empty name, no on-disk yaml entry)
            # is not a valid update target — refuse before the
            # editor opens, so the user doesn't end up authoring a
            # ghost profile named after the current
            # ``$MAXCOMPUTE_PROJECT`` value.
            resolved = _resolve_profile_for_project(None, profile_name=None)
            if not resolved.name:
                err = NoBoundProfileError(
                    "no saved profile is bound to the current "
                    "directory and MCS_PROFILE does not point to a "
                    "saved profile, so "
                    "there's nothing for `mcs profile update` to "
                    "edit. The active credential chain resolved to "
                    "the env-vars-anonymous fallback, which has no "
                    "on-disk yaml entry.",
                    remediation=(
                        "Either pass an explicit alias "
                        "(``mcs profile update NAME``), or create a "
                        "named profile first with ``mcs profile "
                        "create`` / ``mcs profile import-creds`` and "
                        "then bind the current directory to it with "
                        "``mcs link NAME``."
                    ),
                )
                r.error(err)
                sys.exit(err.exit_code)
            old = resolved
            # The downstream code uses ``name`` as the alias to
            # ``upsert`` under; thread the resolved alias back into
            # the local so the rest of the function reads coherently.
            name = old.name
    except ProfileNotFoundError as e:
        r.error(e)
        sys.exit(e.exit_code)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    if from_file is not None and from_spec is not None:
        raise click.UsageError("--from-file and --from-spec are mutually exclusive")

    if from_file is not None or from_spec is not None:
        try:
            new_profile = _load_full_profile_spec(name, from_file, from_spec, existing=old)
        except McsError as e:
            r.error(e)
            sys.exit(1)
    else:
        # Interactive editor.
        from maxcompute_semantic.commands._profile_editor import edit_profile

        client = MaxComputeClient(old)
        result = edit_profile(old, client)
        if result is None:
            click.secho("🚫 aborted (cancelled — no changes saved)", fg="yellow")
            return
        try:
            result.validate()
        except McsError as e:
            r.error(e)
            sys.exit(e.exit_code)
        new_profile = result

    # Auth-test only when auth actually changed. Non-interactive
    # callers pass a complete new spec (where the new auth's
    # validity should be confirmed before persisting); interactive
    # callers go through the editor which leaves ``auth`` untouched
    # unless the user explicitly entered the Auth section.
    auth_changed = new_profile.auth != old.auth
    if not no_test and auth_changed:
        from maxcompute_semantic.commands._auth_probe import _run_auth_test

        test_ok = _run_auth_test(new_profile, r, emit_summary=False)
        if test_ok != 0 and not click.confirm(
            "Auth test failed. Save profile anyway?", default=False
        ):
            return

    from maxcompute_semantic.auth.profile_store import upsert

    upsert(new_profile)
    r.success({"updated": new_profile.name})
    r.quiet_essential({"updated": new_profile.name}, "updated")


# ── profile export / import ───────────────────────────────────────────────
#
# Implementation lives in commands/profile_export.py; we just register
# the click commands under ``mcs profile <verb>`` here so the user-facing
# vocabulary stays grouped while keeping the implementation file
# focused on archive plumbing.

from maxcompute_semantic.commands.profile_export import (  # noqa: E402, I001
    export_cmd as _export_cmd,  # noqa: I001
    import_cmd as _import_cmd,  # noqa: I001
)

profile_group.add_command(_export_cmd, name="export")
profile_group.add_command(_import_cmd, name="import")


# ── history-inspection read verbs + rollback (per-profile git layer) ──
#
# Implementation lives in commands/profile_history.py so the verbs
# share the resolve / fork-redirect / ref-resolution helpers
# without bloating this file. ``log-show`` (rather than ``show``)
# avoids colliding with the existing ``mcs profile show <name>``
# config-dump verb above. ``reset`` is the destructive rollback
# half of the recovery story.

from maxcompute_semantic.commands.profile_fork import (  # noqa: E402, I001
    cmd_profile_fork as _fork_cmd,  # noqa: I001
    cmd_profile_fork_list as _fork_list_cmd,  # noqa: I001
    cmd_profile_fork_remove as _fork_remove_cmd,  # noqa: I001
)
from maxcompute_semantic.commands.profile_history import (  # noqa: E402, I001
    cmd_profile_diff as _diff_cmd,  # noqa: I001
    cmd_profile_log as _log_cmd,  # noqa: I001
    cmd_profile_reset as _reset_cmd,  # noqa: I001
    cmd_profile_show_sha as _log_show_cmd,  # noqa: I001
)

profile_group.add_command(_log_cmd, name="log")
profile_group.add_command(_log_show_cmd, name="log-show")
profile_group.add_command(_diff_cmd, name="diff")
profile_group.add_command(_reset_cmd, name="reset")
profile_group.add_command(_fork_cmd, name="fork")
profile_group.add_command(_fork_list_cmd, name="fork-list")
profile_group.add_command(_fork_remove_cmd, name="fork-remove")
