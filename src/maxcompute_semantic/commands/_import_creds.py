# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Parse external MaxCompute credential files into ``ImportedCreds``.

Two well-known credential storage formats are supported:

- **maxc** (``maxc-cli``): config lives at a fixed home-relative
  path ``~/.maxc/config.yaml``. Shape::

    auth:
      provider: access_key
      access_id: LTAI5...
      secret_access_key: ...
      project: <name>
      endpoint: https://service.<region>.maxcompute.aliyun.com/api
    default_project: <name>

  Process auth is also importable::

    auth:
      provider: external
      project: <name>
      endpoint: https://service.<region>.maxcompute.aliyun.com/api
      external:
        process_command: ncs create credential odpsuser --employee-id <id> -o template -t odpscmd
        process_timeout: 60

- **odpscmd** (the official Aliyun MaxCompute CLI): config path is
  **relative to the install directory** — per Aliyun docs the default
  is ``<install_root>/conf/odps_config.ini`` where ``<install_root>``
  is wherever the user extracted the ``odpscmd_public.zip`` tarball
  (no canonical home-relative path). To find it we resolve the
  ``odpscmd`` binary via ``shutil.which``, follow symlinks, and walk
  up from ``<install_root>/bin/odpscmd`` to the sibling ``conf/``
  directory. The file is a Java properties file (key=value per line,
  no ``[section]`` header). Two auth modes are importable::

    project_name=<name>
    access_id=LTAI5...
    access_key=...
    end_point=https://service.<region>.maxcompute.aliyun.com/api

    # Process auth (ncs/Akless CLI):
    account_provider=external
    processCommand=ncs create credential odpsuser --employee-id <id> -o template -t odpscmd
    processCommandTimeout=20
    project_name=<name>
    end_point=https://service.<region>.maxcompute.aliyun.com/api

These are the authoritative places ``mcs profile create``'s wizard
auto-discovery looks at, and the inputs ``mcs profile import-creds``
parses. Both yield an ``ImportedCreds`` dataclass that
``mcs profile create`` turns into a ``Profile`` shell.

Auth providers other than ``access_key`` and ``external`` (e.g., maxc's
STS / RAM role flows) are intentionally not imported — those need a
different auth flow on the mcs side and should be configured via the
wizard.
"""

from __future__ import annotations

import configparser
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from maxcompute_semantic.auth.profile_store import load_all
from maxcompute_semantic.auth.schema import AkAuth, DataSource, ProcessAuth


@dataclass(frozen=True)
class ImportedCreds:
    """Subset of an external creds file mcs needs to bootstrap a profile."""

    source_label: str  # "maxc" | "odpscmd"
    source_path: Path
    auth: AkAuth | ProcessAuth
    compute_project: str
    endpoint: str

    def display(self) -> str:
        """Short label for picker UIs (`mcs profile import-creds` /
        wizard auto-detect prompt)."""
        auth_tag = "AK" if isinstance(self.auth, AkAuth) else "process"
        return (
            f"{self.source_label} ({self.source_path}) "
            f"— [{auth_tag}] project={self.compute_project}, "
            f"endpoint={self.endpoint}"
        )


def _odpscmd_default_config_path() -> Path | None:
    """Locate odpscmd's config via the binary on PATH.

    Per Aliyun's official docs (alibabacloud.com/help/zh/maxcompute/
    user-guide/maxcompute-client), the default config path is
    ``<install_root>/conf/odps_config.ini`` where install_root is
    wherever the user extracted ``odpscmd_public.zip``. The binary
    lives at ``<install_root>/bin/odpscmd``, so we walk up from the
    resolved binary path to find the sibling ``conf/`` directory.

    Returns the resolved config path if both ``odpscmd`` is on PATH
    and the sibling ``conf/odps_config.ini`` exists; ``None`` otherwise
    (caller falls back to ``--config-path PATH`` for non-standard
    layouts).
    """
    binary = shutil.which("odpscmd")
    if binary is None:
        return None
    install_root = Path(binary).resolve().parent.parent
    config_path = install_root / "conf" / "odps_config.ini"
    return config_path if config_path.exists() else None


def _maxc_default_config_path() -> Path | None:
    """Locate maxc-cli's config at the fixed ``~/.maxc/config.yaml``.

    Returns the path if it exists; ``None`` otherwise.
    """
    path = Path("~/.maxc/config.yaml").expanduser()
    return path if path.exists() else None


def _default_locations() -> list[tuple[str, Path]]:
    """All default credential sources discovered at runtime.

    Resolved lazily so that PATH changes / tarball moves between
    invocations are picked up without a process restart. Order
    determines the wizard's auto-detect prompt's display order.
    """
    out: list[tuple[str, Path]] = []
    maxc = _maxc_default_config_path()
    if maxc is not None:
        out.append(("maxc", maxc))
    odpscmd = _odpscmd_default_config_path()
    if odpscmd is not None:
        out.append(("odpscmd", odpscmd))
    return out


def parse_maxc_config(path: Path) -> ImportedCreds | None:
    """Read maxc-cli's ``~/.maxc/config.yaml`` and return imported creds.

    Two auth modes are importable:

    - **AK** (``provider: access_key``): ``access_id`` +
      ``secret_access_key`` present.
    - **Process** (``provider: external``): nested
      ``external.process_command`` present — the command must return
      STS AssumeRole JSON on stdout. ``external.process_timeout`` is
      optional (defaults to 60).

    Returns ``None`` if the file uses an unsupported auth provider
    (STS / RAM role) or the file's auth shape doesn't match (missing
    fields, unknown structure). Callers should treat ``None`` as
    "not importable" and surface a friendly skip message.
    """
    if not path.exists():
        return None
    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    auth = raw.get("auth")
    if not isinstance(auth, dict):
        return None
    provider = auth.get("provider")

    # Process auth (maxc-cli's `external` provider)
    if provider == "external":
        external = auth.get("external")
        if not isinstance(external, dict):
            return None
        command = external.get("process_command")
        if not (isinstance(command, str) and command):
            return None
        timeout_raw = external.get("process_timeout")
        try:
            timeout = int(timeout_raw) if timeout_raw is not None else 60
        except (ValueError, TypeError):
            timeout = 60
        project = auth.get("project") or raw.get("default_project")
        endpoint = auth.get("endpoint")
        if not all(isinstance(v, str) and v for v in (project, endpoint)):
            return None
        return ImportedCreds(
            source_label="maxc",
            source_path=path,
            auth=ProcessAuth(command=command, timeout=timeout),
            compute_project=str(project),
            endpoint=str(endpoint),
        )

    # AK auth
    if provider != "access_key":
        return None
    access_id = auth.get("access_id")
    secret = auth.get("secret_access_key")
    project = auth.get("project") or raw.get("default_project")
    endpoint = auth.get("endpoint")
    if not all(isinstance(v, str) and v for v in (access_id, secret, project, endpoint)):
        return None
    return ImportedCreds(
        source_label="maxc",
        source_path=path,
        auth=AkAuth(access_key_id=str(access_id), access_key_secret=str(secret)),
        compute_project=str(project),
        endpoint=str(endpoint),
    )


def parse_odpscmd_config(path: Path) -> ImportedCreds | None:
    """Read odpscmd's config and return imported creds.

    odpscmd's config is a Java-style properties file — key=value per
    line, no ``[section]`` header. ``configparser`` needs a header to
    parse, so we synthesize a ``[DEFAULT]`` one before parsing.
    Comments (``#``) and blank lines are tolerated.

    Two auth modes are importable:

    - **AK** (default): ``access_id`` + ``access_key`` present.
    - **Process** (``account_provider=external``): ``processCommand``
      present — the command must return STS AssumeRole JSON on stdout.
      ``access_id`` / ``access_key`` are not required in this mode.

    Returns ``None`` on parse failure / missing required fields.
    """
    if not path.exists():
        return None
    try:
        text = "[DEFAULT]\n" + path.read_text(encoding="utf-8")
        parser = configparser.ConfigParser(strict=False)
        parser.read_string(text)
    except Exception:
        return None
    section = parser["DEFAULT"]
    project = section.get("project_name")
    endpoint = section.get("end_point")

    # Process auth: account_provider=external + processCommand
    provider = section.get("account_provider")
    process_cmd = section.get("processCommand")
    if provider == "external" and process_cmd:
        timeout_str = section.get("processCommandTimeout") or "60"
        try:
            timeout = int(timeout_str)
        except (ValueError, TypeError):
            timeout = 60
        if not (project and endpoint):
            return None
        return ImportedCreds(
            source_label="odpscmd",
            source_path=path,
            auth=ProcessAuth(command=process_cmd, timeout=timeout),
            compute_project=project,
            endpoint=endpoint,
        )

    # AK auth (default / missing provider)
    access_id = section.get("access_id")
    secret = section.get("access_key")
    if not all(v for v in (access_id, secret, project, endpoint)):
        return None
    return ImportedCreds(
        source_label="odpscmd",
        source_path=path,
        auth=AkAuth(access_key_id=access_id or "", access_key_secret=secret or ""),
        compute_project=project or "",
        endpoint=endpoint or "",
    )


_PARSERS: dict[str, callable] = {  # type: ignore[type-arg]
    "maxc": parse_maxc_config,
    "odpscmd": parse_odpscmd_config,
}


def parse_creds_at(label: str, path: Path) -> ImportedCreds | None:
    """Dispatch to the right parser for the given source label.

    ``label`` must be one of ``"maxc"`` / ``"odpscmd"``. Used by
    ``mcs profile import-creds --source <label> --config-path PATH``
    when the user wants to import a non-default-location file.
    """
    parser = _PARSERS.get(label)
    if parser is None:
        return None
    return parser(path)


def is_canonical_ncs_process_auth(auth: AkAuth | ProcessAuth) -> bool:
    """True for the known ncs ODPS credential helper command shape."""
    if not isinstance(auth, ProcessAuth):
        return False
    try:
        parts = shlex.split(auth.command)
    except ValueError:
        return False
    return parts[:4] == ["ncs", "create", "credential", "odpsuser"]


def _classify_auth_kind(auth: AkAuth | ProcessAuth) -> str:
    """Three-way classifier for the ``auth_kind`` field returned by
    ``mcs profile suggest-creds``.

    - ``"ak"`` — :class:`AkAuth` (literal or ``${env:VAR}`` references).
    - ``"ncs"`` — :class:`ProcessAuth` whose tokenized command begins with
      ``ncs create credential odpsuser`` (matches both the wizard's
      ``--employee-id`` template form at
      :data:`commands.profile._NCS_COMMAND_TEMPLATE` and the
      ``--buc-user-id`` form generated when the wizard's identity
      picker resolves a live ncs authorization).
    - ``"process"`` — any other :class:`ProcessAuth` (custom STS
      commands, etc.).

    Used by ``suggest-creds`` so the agent can mirror the wizard's
    Step 1.5 / Step 4 branching without inspecting auth internals.
    External maxc / odpscmd configs can also produce ``ProcessAuth``
    via ``provider: external`` / ``account_provider=external`` (see
    :func:`parse_maxc_config` / :func:`parse_odpscmd_config`), so the
    classifier applies uniformly to existing-mcs and external sources.
    """
    if isinstance(auth, AkAuth):
        return "ak"
    if is_canonical_ncs_process_auth(auth):
        return "ncs"
    return "process"


def discover_creds() -> list[ImportedCreds]:
    """Scan the default file locations and return all importable creds.

    Skips locations that don't exist, fail to parse, or use a
    non-AK auth provider. The returned list preserves
    ``_default_locations()`` order so the wizard's auto-detect
    prompt is deterministic.
    """
    found: list[ImportedCreds] = []
    for label, path in _default_locations():
        creds = parse_creds_at(label, path)
        if creds is not None:
            found.append(creds)
    return found


@dataclass(frozen=True)
class McsProfileCandidate:
    """One existing mcs profile available as a template for a new profile.

    Returned by ``discover_mcs_profiles()`` and surfaced in the
    ``mcs profile create`` wizard's Step 1.5 picker (alongside
    ``ImportedCreds`` from external maxc / odpscmd configs).

    Unlike ``ImportedCreds`` — which the wizard bulk-imports and
    short-circuits out with — this candidate triggers a per-field
    y/n prompt flow (auth / endpoint / compute_project / data
    sources). The fields cloned end up on the new profile; the
    rest fall through to the wizard's normal steps.
    """

    name: str
    auth: AkAuth | ProcessAuth
    endpoint: str
    compute_project: str  # "" for build-in-progress shells
    sources: tuple[DataSource, ...]  # () when none yet

    def display(self) -> str:
        """Short label for picker UIs."""
        auth_tag = "AK" if isinstance(self.auth, AkAuth) else "process"
        proj = self.compute_project or "(no project yet)"
        return f"mcs:{self.name} — [{auth_tag}] project={proj}, endpoint={self.endpoint}"


def discover_mcs_profiles(*, exclude_name: str | None = None) -> list[McsProfileCandidate]:
    """Return every profile in ``profiles.yaml`` as a candidate template.

    Skips the profile named in ``exclude_name`` (defense-in-depth so the
    user can't clone from the profile they're about to overwrite — the
    wizard's Step 1 alias check is the primary guard).

    Profiles with an empty ``compute_project`` or empty ``sources`` are
    still returned; the per-field reuse prompt downstream auto-skips
    the corresponding question for those fields.

    Returns ``[]`` when ``profiles.yaml`` is missing or has no
    profiles — matches ``profile_store.load_all()`` semantics.
    """
    profiles = load_all()
    out: list[McsProfileCandidate] = []
    for name, p in profiles.items():
        if exclude_name is not None and name == exclude_name:
            continue
        out.append(
            McsProfileCandidate(
                name=name,
                auth=p.auth,
                endpoint=p.endpoint,
                compute_project=p.compute_project,
                sources=p.sources,
            )
        )
    return out


__all__ = [
    "ImportedCreds",
    "McsProfileCandidate",
    "discover_creds",
    "discover_mcs_profiles",
    "is_canonical_ncs_process_auth",
    "parse_creds_at",
    "parse_maxc_config",
    "parse_odpscmd_config",
]
