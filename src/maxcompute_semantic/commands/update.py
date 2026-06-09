"""``mcs update`` — pull the latest wheel from OSS and reinstall in place.

The command picks the right installer based on how mcs is currently
installed:

  * **uv tool install** (``uv tool install maxcompute-semantic``)
    — the bin shim and the tool venv live under
    ``~/.local/share/uv/tools/<package>/``. Upgrade command:
    ``uv tool install --reinstall <wheel-url>``. uv parses the wheel
    filename to identify the package, so the package name is
    baked into the URL — we don't need to spell it out separately.
  * **pipx install** (``pipx install maxcompute-semantic``) — venvs
    under ``~/.local/pipx/venvs/<package>/``. Upgrade command:
    ``pipx install --force <wheel-url>``.
  * **pip --user** (``pip install --user maxcompute-semantic``) — the
    console_script lands in ``site.getuserbase() + '/bin/'`` and
    ``sys.executable`` is the system Python. Upgrade command:
    ``<sys.executable> -m pip install --user --upgrade <wheel-url>``.
  * **pip into a venv or system Python** — everything else that has
    a known Python interpreter. Upgrade command:
    ``<sys.executable> -m pip install --upgrade <wheel-url>``.
  * **UNKNOWN** — the install layout doesn't match any of the above
    patterns. The command prints a manual-remediation hint and exits
    non-zero rather than guessing.

After the install subprocess succeeds, the command kicks
``mcs skill update --all`` as a best-effort hook so the agent-side
SKILL.md re-symlinks pick up the new ``_skill/`` directory inside the
freshly installed wheel. The skill bundle is a symlink into
site-packages, so the OS guarantees the target points at the new
package; ``skill update`` exists to fix the case where the user has a
copy-install (not a symlink) on a platform like Windows where
``mklink`` requires elevation. The hook's exit code is logged but
does not change the overall command exit code — a busted skill
install isn't a CLI-upgrade failure.

Final step on Unix: ``os.execvp(sys.argv[0], [sys.argv[0],
'--version'])`` so the user sees the new version string. The exec
replaces the current process so the shell doesn't get a doubled
prompt. On Windows ``os.execvp`` has different semantics
(the running .exe's file is locked), so the command prints a "restart
your shell" notice and exits 0.

See the spec's "User flows / mcs update self-upgrade" for the full
sequence with the confirm-prompt and the ``--version <pin>`` option.
"""

from __future__ import annotations

import contextlib
import enum
import os
import shlex
import site
import subprocess
import sys
from pathlib import Path

import click

from maxcompute_semantic import __version__
from maxcompute_semantic._internal.update_check import (
    CacheEntry,
    LatestMetadata,
    _base_url,
    fetch_latest_metadata,
    write_cache,
)


class InstallMode(enum.Enum):
    """How the running mcs was installed. The value is the short tag
    that appears in user-facing messages ("upgrading via uv-tool")."""

    UV_TOOL = "uv-tool"
    PIPX = "pipx"
    PIP_USER = "pip-user"
    PIP = "pip"
    UNKNOWN = "unknown"


# Substring markers for the path-based detector. The order matters
# because a uv-tool install path is technically a venv under
# ``~/.local/share/uv/tools/<pkg>/`` and the pip-venv check would
# match if it ran first. We test the more specific markers first.
_UV_TOOL_MARKERS = ("/uv/tools/", os.sep + "uv" + os.sep + "tools" + os.sep)
"""Linux/macOS uses forward slashes regardless of platform on the uv
side because uv writes POSIX-style paths under ``~/.local/share`` /
``~/.local`` even on Windows. The ``os.sep`` form is the belt-and-
braces case for an exotic Windows uv layout."""

_PIPX_MARKERS = ("/pipx/venvs/", os.sep + "pipx" + os.sep + "venvs" + os.sep)


def _path_has_any(path: str, markers: tuple[str, ...]) -> bool:
    """True if any marker substring appears in the path (case-
    insensitive on Windows because NTFS paths are case-insensitive)."""
    p = path.lower() if sys.platform == "win32" else path
    return any((m.lower() if sys.platform == "win32" else m) in p for m in markers)


def detect_install_mode() -> InstallMode:
    """Inspect ``sys.executable`` and ``sys.argv[0]`` to determine how
    mcs was installed.

    The decision tree:

      1. ``sys.executable`` contains ``"/uv/tools/"`` (or the platform
         separator equivalent) → ``UV_TOOL``. uv's tool layout is
         ``<uv-data>/tools/<package>/{bin,lib}/`` with a dedicated
         venv per tool; the python interpreter resolved by the bin
         shim is the tool-venv's python, which sits under
         ``tools/<package>/bin/python`` or ``Scripts\\python.exe``.
      2. ``sys.executable`` contains ``"/pipx/venvs/"`` → ``PIPX``.
         pipx's per-app venv layout matches the substring marker.
      3. ``sys.argv[0]`` is under ``site.getuserbase()`` — i.e., the
         user-base ``bin`` dir from PEP 370 — AND the current
         interpreter's prefix is *not* the user-base. That's the
         signature of ``pip install --user``: the console_script
         lives under the user prefix, but the interpreter is the
         shared system Python. → ``PIP_USER``.
      4. Anything else with a runnable Python interpreter → ``PIP``.
         This covers system pip, project venvs (the canonical
         ``.venv/bin/python``), and editable installs. The same
         ``python -m pip install --upgrade <wheel-url>`` argv works
         for all three because pip writes to ``sys.prefix`` by default.
      5. The interpreter path doesn't look like any of the above
         (frozen binary, exotic packaging) → ``UNKNOWN``. The
         command prints a manual-remediation hint.

    The decision is heuristic — there's no portable way for a Python
    process to ask "how were my console_scripts wired in" in stdlib —
    so the function is deliberately conservative: if the markers
    don't match a known install-manager layout, we fall through to
    ``UNKNOWN`` rather than guessing wrong and breaking the user's
    environment.
    """
    exec_path = sys.executable or ""
    argv0 = sys.argv[0] if sys.argv else ""

    # uv-tool wins ahead of pipx because both managers put the venv
    # under ~/.local/share or ~/.local; the uv subpath is more
    # specific.
    if _path_has_any(exec_path, _UV_TOOL_MARKERS) or _path_has_any(argv0, _UV_TOOL_MARKERS):
        return InstallMode.UV_TOOL

    if _path_has_any(exec_path, _PIPX_MARKERS) or _path_has_any(argv0, _PIPX_MARKERS):
        return InstallMode.PIPX

    # pip --user: argv[0] is in the user-base bin dir.
    try:
        user_bin = Path(site.getuserbase()) / "bin"
        user_bin_win = Path(site.getuserbase()) / "Scripts"
    except Exception:
        user_bin = Path("")
        user_bin_win = Path("")
    if argv0:
        argv0_path = Path(argv0)
        # Resolve symlinks so a system-bin symlink into ~/.local/bin
        # is recognized correctly.
        try:
            resolved = argv0_path.resolve(strict=False)
        except OSError:
            resolved = argv0_path
        for cand in (user_bin, user_bin_win):
            if not cand.parts:
                continue
            try:
                cand_resolved = cand.resolve(strict=False)
            except OSError:
                cand_resolved = cand
            if cand_resolved in resolved.parents or cand in argv0_path.parents:
                return InstallMode.PIP_USER

    # Plain pip case (system or venv). The "runnable interpreter"
    # check is "sys.executable is a real file we can call
    # ``-m pip`` on" — which is true for every standard CPython
    # install. A frozen binary (PyInstaller / py2app) sets
    # ``sys.frozen`` and points sys.executable at the bundle entry,
    # not a python; that case falls through to UNKNOWN.
    if getattr(sys, "frozen", False):
        return InstallMode.UNKNOWN
    if exec_path and Path(exec_path).name.startswith(("python", "Python")):
        return InstallMode.PIP

    return InstallMode.UNKNOWN


def build_upgrade_argv(mode: InstallMode, *, wheel_url: str) -> list[str] | None:
    """Return the argv list for the installer subprocess, or ``None``
    when the mode is ``UNKNOWN`` (the caller is expected to print
    a manual remediation line and exit non-zero in that case).

    The wheel URL is positional and the same for every mode — both
    ``uv tool install`` and ``pip install --upgrade`` accept a wheel
    URL directly as the requirement specifier, and parse the wheel
    filename for the package name (PEP 427). That means the URL
    encodes the version, which is how the ``mcs update
    --version <pinned>`` flag works: the caller rewrites the URL
    path to point at the older wheel filename, hands the result
    here, and the installer pulls the pinned wheel.

    The argv is the list form for ``subprocess.run(..., shell=False)``
    so URL characters that would otherwise need shell-quoting (the
    ``+`` in PEP 440 local version segments, for instance) are passed
    through unmangled. The ``--reinstall`` (uv) / ``--force`` (pipx)
    flags handle the "already-installed at the same version" case so
    a ``mcs update --version <current>`` no-op still re-runs the
    install pipeline. Plain pip's ``--upgrade`` covers the
    same-version reinstall through pip's dependency resolver.

    The ``--user`` flag on the pip-user form is what keeps the
    install in the user-site dir (otherwise pip would write to the
    system Python). On Windows ``--user`` redirects to
    ``%APPDATA%/Python/Scripts``, which is the same target the
    original install landed in.

    Wire details of each mode's argv shape are documented in the
    spec's "commands/update.py" / "Upgrade command per mode" table
    and live in one place there so this docstring stays canonical to
    the code.

    The function does NOT exec — it returns the argv list and lets
    the caller decide between ``subprocess.run`` (the normal path)
    and ``subprocess.Popen`` (a hypothetical future "show progress
    bar" mode). Easy unit testing.
    """
    py = sys.executable or "python3"
    if mode is InstallMode.UV_TOOL:
        # `uv tool install <url>` parses the wheel filename for the
        # package name, registers the tool by that name (so subsequent
        # `uv tool list` etc. find it), and replaces the venv contents
        # in place. `--reinstall` makes the operation idempotent
        # against a same-version no-op. See uv docs:
        # https://docs.astral.sh/uv/concepts/tools/#installing-tools.
        return ["uv", "tool", "install", "--reinstall", wheel_url]
    if mode is InstallMode.PIPX:
        # pipx's analogous flag is `--force`. The package name is
        # parsed from the wheel filename the same way uv does it.
        return ["pipx", "install", "--force", wheel_url]
    if mode is InstallMode.PIP_USER:
        # `<python> -m pip install --user --upgrade <url>` matches the
        # invocation the user typed at install time. `--upgrade`
        # forces a same-version reinstall via pip's resolver. The
        # user-site bin dir already contains the `mcs` console_script
        # symlink/wrapper from the original install; pip rewrites it
        # in place when the new wheel's `[project.scripts]` table is
        # processed.
        return [
            py,
            "-m",
            "pip",
            "install",
            "--user",
            "--upgrade",
            wheel_url,
        ]
    if mode is InstallMode.PIP:
        # The fall-through pip case — system pip or a venv. The
        # interpreter is the same one that imported this module, so
        # `pip` resolves to the same prefix the original install used.
        return [py, "-m", "pip", "install", "--upgrade", wheel_url]
    # UNKNOWN: the caller prints the manual hint.
    assert mode is InstallMode.UNKNOWN, f"unhandled InstallMode {mode!r}"
    return None


def manual_upgrade_hint(wheel_url: str) -> str:
    """Multi-line text the caller prints when ``detect_install_mode``
    came back ``UNKNOWN``. Lists the four canonical commands so the
    user can pick the one that matches their install method. The
    URL is interpolated into each command. Returns a string ending
    without a trailing newline."""
    py = shlex.quote(sys.executable or "python3")
    url = shlex.quote(wheel_url)
    lines = [
        "Could not detect how mcs was installed — please pick one of:",
        f"  uv tool install --reinstall {url}",
        f"  pipx install --force {url}",
        f"  {py} -m pip install --upgrade {url}",
        f"  {py} -m pip install --user --upgrade {url}",
        "Then run `mcs --version` to confirm.",
    ]
    return "\n".join(lines)


def _wheel_url_for_version(version: str) -> str:
    """Construct the canonical wheel URL for a pinned version.

    The publisher uploads wheels under ``<base>/wheels/<filename>``
    where the filename is the PEP 427-normalized form
    ``maxcompute_semantic-<version>-py3-none-any.whl`` (hyphens in
    the package name become underscores). The ``--version`` CLI flag
    on ``mcs update`` accepts a PEP 440 version string verbatim and
    feeds it through here. No validation is done up front — if the
    version doesn't exist on OSS the install subprocess will fail
    with a 404 and the user sees the installer's stderr."""
    return f"{_base_url()}/wheels/maxcompute_semantic-{version}-py3-none-any.whl"


def _resolve_target_metadata(pinned: str | None) -> LatestMetadata | str | None:
    """Decide which version to install.

    Returns one of:

      * ``LatestMetadata`` — the publisher's metadata when no
        ``--version`` was supplied. The caller uses
        ``metadata.wheel_url`` (which may differ from the
        constructed URL if the publisher relocated wheels — e.g.
        moved to PyPI). And ``metadata.disabled`` /
        ``metadata.min_supported`` are consulted for the
        "running version is itself disabled" edge case.
      * A bare ``str`` — the user's ``--version <pinned>`` value.
        The wheel URL is built locally; the publisher's metadata is
        bypassed entirely, so the version-pin path also works when
        ``latest.json`` is unreachable. The disabled/min_supported
        guard doesn't apply (the user is explicitly asking for a
        specific version, presumably to *escape* a bad latest).
      * ``None`` — the fetch failed and no pin was given.
        ``cmd_update`` prints the canonical "could not fetch
        ``latest.json``" message and exits non-zero.
    """
    if pinned is not None:
        return pinned
    md = fetch_latest_metadata()
    return md


def _stamp_cache_after_fetch(md: LatestMetadata) -> None:
    """Side-effect: write the cache so any banner the new process
    prints after the exec reflects the freshly fetched state. Best
    effort — failure here doesn't change the command's exit code."""
    # Best-effort: the cache stamp is purely advisory for the next
    # foreground process's banner. Any failure here (disk full,
    # read-only HOME, weird permission setup) must not block the
    # update.
    with contextlib.suppress(Exception):
        write_cache(CacheEntry.from_fetch(md, current_version=__version__))


def _post_install_skill_update(argv0: str) -> None:
    """Re-link skill bundles in every agent slot that has one
    installed. The wheel just landed in site-packages so the
    ``_skill/`` symlink target on disk is the new version
    automatically — the explicit ``skill update --all`` call here is
    for the platforms (Windows without admin) where the install
    landed as a copy rather than a symlink and the agent-side path
    still points at the previous tree.

    Best-effort: a non-zero exit prints the captured stderr but
    doesn't change the outer ``mcs update`` exit code. The skill
    side is a soft dependency."""
    try:
        result = subprocess.run(
            [argv0, "skill", "update", "--all"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            click.echo(
                f"Warning: post-upgrade `mcs skill update --all` exited "
                f"{result.returncode}: {stderr}",
                err=True,
            )
    except FileNotFoundError as exc:
        # argv[0] isn't on PATH any more — odd but possible if the
        # install rewrote the console_script under a different name.
        click.echo(
            f"Warning: post-upgrade skill update could not find "
            f"the new mcs binary at {argv0!r}: {exc}",
            err=True,
        )


def _final_verify_exec_unix(argv0: str) -> None:
    """Replace the current process with the newly installed mcs
    running ``--version`` so the user sees the new version string
    and the shell prompt comes back without a doubled-frame
    appearance. ``os.execvp`` searches PATH for the basename if
    argv[0] doesn't contain a directory separator, so the call
    works whether the bin is in PATH or referenced absolutely.

    Unix-only — on Windows the in-place exec semantics differ
    because the running .exe is file-locked. The Windows path
    instead prints a "restart shell" hint and returns; see the
    branch in ``cmd_update`` itself.
    """
    os.execvp(argv0, [argv0, "--version"])
    # os.execvp does not return on success. If we reach the next
    # line it means execvp failed (e.g. the new binary isn't on
    # PATH any more), and we surface that.
    raise click.ClickException(
        f"upgrade installed but could not exec the new {argv0} --version; "
        "open a new shell and run `mcs --version` manually."
    )


@click.command("update")
@click.option(
    "--check/--no-check",
    default=True,
    help=("Ask y/N before running the installer (default). Pass --no-check to run unattended."),
)
@click.option(
    "--version",
    "target_version",
    default=None,
    metavar="VERSION",
    help=(
        "Pin a specific PEP 440 version (e.g. 0.4.0a40). "
        "When set, the publisher's latest.json is bypassed and the "
        "wheel URL is constructed locally — useful for downgrades or "
        "for installing a release the publisher hasn't yet announced."
    ),
)
def cmd_update(check: bool, target_version: str | None) -> None:
    """Pull the latest mcs wheel and reinstall in place.

    The installer is picked from how the running mcs was originally
    installed — ``uv tool install``, ``pipx install``, or
    ``pip install --user`` / plain ``pip install``. The wheel URL
    comes from ``MCS_UPDATE_BASE_URL/wheels/maxcompute_semantic-
    <version>-py3-none-any.whl`` (default base
    ``https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com``), so
    the same command works for a PyPI move later by flipping the
    base URL via env var.

    After the install, the skill bundle is re-linked in every agent
    slot (``mcs skill update --all``) so Claude Code / Codex /
    Cursor pick up the new ``SKILL.md`` immediately without a manual
    ``mcs skill install``. On Unix the command finishes by
    ``execvp`` ing the new mcs's ``--version`` so the user sees the
    upgraded banner before the shell prompt returns.

    Exit codes:

      * 0 — upgraded successfully, or the running version already
        equals the latest, or the user answered "n" to the
        confirmation prompt and the command was aborted by the
        user. (The "no-op already on latest" and "user said no"
        cases are both green from the shell's perspective.)
      * 1 — anything else: metadata fetch failed and no pin was
        provided; the install subprocess returned non-zero;
        ``detect_install_mode`` returned UNKNOWN; the publisher
        marked the running version as the latest and as disabled
        (a withdrawn-latest with no replacement).

    The skill-update step is best-effort: if it fails, a warning is
    printed but the overall exit code reflects only the install
    subprocess. The verifying ``--version`` exec at the very end is
    cosmetic — by the time it runs, the upgrade is on disk.

    Environment variables consumed:

      * ``MCS_UPDATE_BASE_URL`` — overrides the OSS base URL. The
        same variable is read by the metadata fetcher; the wheel URL
        construction here uses the same base so a custom mirror
        works end-to-end.
    """
    current = __version__
    click.echo(f"Current mcs version: {current}", err=True)

    target = _resolve_target_metadata(target_version)
    if target is None:
        raise click.ClickException(
            "Could not fetch latest.json metadata and no --version pin "
            "was given. Check network connectivity to the publisher "
            "base URL (MCS_UPDATE_BASE_URL or the default OSS host); "
            "see `mcs doctor` for the channel-reachable probe."
        )

    if isinstance(target, LatestMetadata):
        # Disabled-running-version edge case (the publisher pulled the
        # current build and the published latest IS the current
        # build — nothing newer available).
        if target.latest_version == current and current in target.disabled:
            raise click.ClickException(
                f"The publisher has marked mcs {current} as disabled, "
                f"but the published latest_version is also {current} "
                f"(notice: {target.notice!r}). There is no replacement "
                f"wheel to install yet. Watch the publisher channel and "
                f"re-run `mcs update` when a new version lands."
            )

        # The publisher's wheel URL is the authority — could differ
        # from the locally-constructed URL if wheels live somewhere
        # else (the PyPI migration seam). For the OSS host, the URL
        # in the metadata equals the constructed URL.
        target_version_str = target.latest_version
        wheel_url = target.wheel_url
        sha256_expected = target.sha256
        # Validate the URL starts with the trusted base URL and contains
        # the version string — not just a substring check.
        from maxcompute_semantic._internal.update_check import _base_url

        expected_prefix = _base_url() + "/wheels/"
        expected_wheel_url = _wheel_url_for_version(target_version_str)
        if not wheel_url.startswith(expected_prefix):
            raise click.ClickException(
                f"latest.json wheel_url={wheel_url!r} does not start with "
                f"trusted base URL {expected_prefix!r}; refusing to install."
            )
        if wheel_url != expected_wheel_url:
            raise click.ClickException(
                f"latest.json wheel_url={wheel_url!r} does not match the "
                f"canonical wheel URL {expected_wheel_url!r}; refusing to install."
            )
        # Refresh the cache so the next foreground mcs sees the same
        # state and the soft banner reflects "we just learned of N+1".
        _stamp_cache_after_fetch(target)
    else:
        assert isinstance(target, str)
        target_version_str = target
        wheel_url = _wheel_url_for_version(target_version_str)
        sha256_expected = ""

    if target_version_str == current:
        click.echo(f"Already on the latest version of mcs ({current}).", err=True)
        sys.exit(0)

    mode = detect_install_mode()
    if mode is InstallMode.UNKNOWN:
        click.echo(manual_upgrade_hint(wheel_url), err=True)
        sys.exit(1)

    argv = build_upgrade_argv(mode, wheel_url=wheel_url)
    assert argv is not None  # UNKNOWN already handled above.

    click.echo(
        f"Upgrading mcs from {current} → {target_version_str} via {mode.value}.",
        err=True,
    )
    click.echo(f"  Wheel: {wheel_url}", err=True)
    click.echo(f"  Command: {' '.join(shlex.quote(a) for a in argv)}", err=True)

    if check:
        click.confirm("Proceed with upgrade?", default=False, abort=True, err=True)

    # SHA256 verification: if the publisher included a hash in
    # latest.json, download the wheel to a temp file, verify the
    # digest, then replace the remote URL in argv with the local path.
    if sha256_expected:
        import hashlib
        import tempfile
        import urllib.request

        click.echo("  Verifying SHA256 digest...", err=True)
        with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            urllib.request.urlretrieve(wheel_url, tmp_path)
            with open(tmp_path, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
            if actual != sha256_expected:
                os.unlink(tmp_path)
                raise click.ClickException(
                    f"SHA256 mismatch: expected {sha256_expected}, got {actual}. "
                    f"The wheel at {wheel_url} may have been tampered with; "
                    f"refusing to install."
                )
            click.echo(f"  SHA256 OK: {actual}", err=True)
            argv = [a if a != wheel_url else tmp_path for a in argv]
        except urllib.error.URLError as e:
            os.unlink(tmp_path)
            raise click.ClickException(
                f"Failed to download wheel for SHA256 verification: {e}"
            ) from e

    # Run the installer. ``capture_output=True`` keeps the user's
    # terminal clean and lets us prefix the installer's stderr with
    # context on failure. ``check=False`` so we control the exit
    # behavior instead of subprocess raising CalledProcessError.
    result = subprocess.run(argv, capture_output=False, check=False)
    if sha256_expected and os.path.exists(tmp_path):
        os.unlink(tmp_path)
    if result.returncode != 0:
        raise click.ClickException(
            f"Installer ``{argv[0]}`` exited {result.returncode}. "
            f"See its output above for details. The previous mcs "
            f"install (version {current}) is still in place — pip /uv "
            f"/pipx replace site-packages atomically, so a failed "
            f"upgrade leaves the working tree intact."
        )

    # Best-effort re-link of agent-side SKILL.md bundles. argv[0] is
    # the script path that started us; after the install subprocess
    # rewrote the console_script entry, the same path on disk now
    # points at the new wheel's entry point. The subprocess we spawn
    # here goes through that path so the new code runs the
    # ``skill update --all`` verb.
    argv0 = sys.argv[0] if sys.argv else "mcs"
    _post_install_skill_update(argv0)

    click.echo(f"mcs upgraded {current} → {target_version_str}.", err=True)
    # Heads-up about the inference-logic stamp: a CLI upgrade may
    # have shipped a newer ``INFERENCE_LOGIC_VERSION`` than the
    # version your built profiles last derived under. ``mcs build
    # --refresh`` reconciles that offline (no MaxCompute round-trips)
    # — see ``mcs doctor`` for which profiles are stale.
    click.echo(
        "Your built profiles may now have a stale inference layer — "
        "run `mcs build --refresh` on each to reconcile (offline, "
        "no MaxCompute calls).",
        err=True,
    )

    if sys.platform == "win32":
        click.echo(
            "Note: Windows file-locks the running mcs.exe, so the "
            "in-place verify step is skipped. Restart your shell, "
            "then run `mcs --version` to confirm.",
            err=True,
        )
        sys.exit(0)

    _final_verify_exec_unix(argv0)
