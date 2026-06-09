# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""profiles.yaml read/write using ruamel.yaml."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ruamel.yaml import YAMLError
from ruamel.yaml.comments import CommentedMap

from maxcompute_semantic._internal.paths import profile_data_dir, profiles_yaml_path
from maxcompute_semantic._internal.yaml_io import dump_yaml, load_yaml
from maxcompute_semantic.auth.errors import (
    IncompatibleProfileVersionError,
    InvalidProfileError,
    InvalidProfileFileError,
    ProfileNotFoundError,
)
from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    ProcessAuth,
    Profile,
    TableSpec,
)

# File-level version of the on-disk yaml envelope, whose two
# top-level keys are ``version`` (integer, this constant) and
# ``profiles`` (mapping from profile-name to per-profile body).
# The loader canonicalizes the envelope on read: unknown top-level
# keys are ignored, and any subsequent write emits only the two
# canonical keys above.
#
# The per-profile body shape — ``compute_project`` /
# ``endpoint`` / ``auth`` / ``sources`` / ``cost_thresholds`` /
# ``tags`` / ``package_path`` — is defined by the ``Profile``
# dataclass in ``schema.py``; the round-trip helpers below
# (``_profile_to_dict`` / ``_profile_from_dict``) handle the
# dict-of-strings ↔ dataclass-of-strings conversion via ruamel
# (the ``ruamel.yaml.YAML(typ='safe')`` loader and dumper, which
# share their semantics with PyYAML's safe-loader but preserve
# key order and comments on round-trip when ``typ='rt'`` is used
# for human-edited files).
_SUPPORTED_VERSION = 1


def _empty_raw() -> CommentedMap:
    return CommentedMap({"version": _SUPPORTED_VERSION, "profiles": CommentedMap()})


def _read_raw() -> CommentedMap:
    """Return ruamel CommentedMap of profiles.yaml; new empty doc if missing."""
    path = profiles_yaml_path()
    if not path.exists():
        return _empty_raw()
    try:
        data = load_yaml(path)
    except YAMLError as e:
        raise InvalidProfileFileError(
            f"profiles.yaml at {path} is not valid YAML",
            remediation=f"fix manually; parse error: {e}",
        ) from e
    if data is None:
        return _empty_raw()
    version = data.get("version")
    if version != _SUPPORTED_VERSION:
        raise IncompatibleProfileVersionError(
            f"profiles.yaml version {version!r} unsupported; expected {_SUPPORTED_VERSION}",
            remediation="upgrade mcs or re-create profiles via `mcs profile create`",
        )
    if "profiles" not in data:
        data["profiles"] = CommentedMap()
    for key in list(data.keys()):
        if key not in {"version", "profiles"}:
            del data[key]
    return data  # type: ignore[no-any-return]


def _plain_str(v: Any) -> str:
    """Coerce ruamel ScalarString subclasses (DoubleQuotedScalarString,
    SingleQuotedScalarString, PlainScalarString, …) back into a plain
    ``str``. ruamel's 'rt' loader keeps the original quote style as a
    typed wrapper around str so a re-dump preserves it; that's useful
    for round-tripping comments but it leaks ruamel-only types into
    the Profile dataclass. Downstream consumers that go through
    ``YAML(typ='safe')`` (e.g. ``build/markdown._yaml_dumps`` writing
    package overview frontmatter) raise ``RepresenterError`` on those
    subclasses, so we strip the wrapping at the boundary.
    """
    return "" if v is None else str(v)


def _tablespec_from_yaml(raw: Any, *, source_label: str) -> TableSpec:
    """Convert one yaml entry from a source's ``tables`` list into a
    TableSpec. Bare-string entries (e.g. ``- orders``) mean
    "no column scoping, take all columns of that table". Dict
    entries (e.g. ``- {name: orders, columns: [id, customer_id]}``)
    carry the optional whitelist or blacklist.
    """
    if isinstance(raw, str):
        return TableSpec(name=_plain_str(raw))
    if isinstance(raw, dict):
        name = _plain_str(raw.get("name", ""))
        columns_raw = raw.get("columns")
        columns_exclude_raw = raw.get("columns_exclude") or ()
        columns: tuple[str, ...] | None = None
        if columns_raw is not None:
            columns = tuple(_plain_str(c) for c in columns_raw)
        columns_exclude = tuple(_plain_str(c) for c in columns_exclude_raw)
        ts = TableSpec(
            name=name,
            columns=columns,
            columns_exclude=columns_exclude,
        )
        ts.validate()
        return ts
    raise InvalidProfileError(
        f"source {source_label!r}: tables entry is neither a bare table "
        f"name (string) nor a {{name, columns|columns_exclude}} dict "
        f"(got {type(raw).__name__}: {raw!r})",
        remediation="the yaml grammar is: tables: '*' for the whole "
        "schema, or a list whose entries are either bare table names "
        "or dicts with a 'name' key plus optional 'columns' (whitelist) "
        "or 'columns_exclude' (blacklist) keys.",
    )


def _datasource_from_yaml(raw: dict[str, Any], *, profile_name: str) -> DataSource:
    project = _plain_str(raw.get("project") or "")
    schema = _plain_str(raw.get("schema") or "default")
    tables_raw = raw.get("tables", "*")
    tables: tuple[TableSpec, ...] | str
    if tables_raw is None:
        # An explicit ``tables: null`` is treated as the wildcard.
        # The yaml form "tables: '*'" is canonical, but a missing
        # tables key (or a null) also means "everything".
        tables = "*"
    elif isinstance(tables_raw, str):
        # ``tables: "*"`` is the only legal string value; anything
        # else fails the DataSource validator.
        tables = _plain_str(tables_raw)
    elif isinstance(tables_raw, (list, tuple)):
        src_label = f"{project}.{schema}"
        tables = tuple(_tablespec_from_yaml(item, source_label=src_label) for item in tables_raw)
    else:
        raise InvalidProfileError(
            f"profile {profile_name!r}: source {project}.{schema}'s "
            f"'tables' field must be '*' or a list "
            f"(got {type(tables_raw).__name__}: {tables_raw!r})",
        )
    ds = DataSource(project=project, schema=schema, tables=tables)
    ds.validate()
    return ds


def _profile_from_dict(name: str, raw: dict[str, Any]) -> Profile:
    """Build a Profile dataclass from a yaml dict (which the ruamel
    'rt' loader hands us as a ``CommentedMap`` — dict-like).
    """
    # The ``name`` argument comes from the outer ``load_all`` dict
    # comprehension's iteration over ``profiles_raw.items()`` where
    # the keys are ruamel CommentedMap keys. When the on-disk yaml
    # is the JSON-string form (which is what
    # ``eval/_skill_setup.write_profile_yaml`` writes — see the
    # 0.3.0a18 regression-test context), every quoted key is a
    # ``DoubleQuotedScalarString`` subclass of ``str``. Coerce to a
    # plain Python ``str`` here so the Profile dataclass's ``name``
    # field is the same plain-str type as every other string field
    # on the Profile (the ``_plain_str`` boundary applies to all
    # value-side fields elsewhere in this function). Without this
    # coercion the ruamel subclass leaks through to downstream
    # ``YAML(typ='safe')`` consumers and triggers the same
    # "cannot represent an object" RepresenterError the 0.3.0a18
    # fix addressed for the value side.
    name = _plain_str(name)
    try:
        auth_raw = raw["auth"]
        auth_type = _plain_str(auth_raw["type"])
        if auth_type == "process":
            auth: ProcessAuth | AkAuth = ProcessAuth(
                command=_plain_str(auth_raw["command"]),
                timeout=int(auth_raw.get("timeout", 60)),
            )
        elif auth_type == "ak":
            auth = AkAuth(
                access_key_id=_plain_str(auth_raw["access_key_id"]),
                access_key_secret=_plain_str(auth_raw["access_key_secret"]),
            )
        else:
            raise InvalidProfileError(
                f"profile {name!r}: unknown auth.type {auth_type!r}",
                remediation="auth.type must be 'process' or 'ak'",
            )
        ct_raw = raw.get("cost_thresholds") or {}
        confirm_cny = float(ct_raw.get("confirm_cny", 10.0))
        blocked_cny = float(ct_raw.get("blocked_cny", 100.0))
        enabled_raw = ct_raw.get("enabled")
        enabled = confirm_cny > 0 and blocked_cny > 0 if enabled_raw is None else bool(enabled_raw)
        cost = CostThresholds(
            confirm_cny=confirm_cny,
            blocked_cny=blocked_cny,
            enabled=enabled,
        )
        tags_raw = raw.get("tags") or []
        package_path_raw = raw.get("package_path")
        package_path = Path(_plain_str(package_path_raw)) if package_path_raw else None
        # Note: yaml files written by the d115314-era code may carry
        # an ``identity:`` top-level key (the briefly-existing
        # captured-identity field). The current loader silently
        # ignores it — the data was never authoritative anyway, and
        # the next ``upsert`` rewrites the file without the key
        # because ``_profile_to_dict`` no longer emits one.
        compute_project = _plain_str(raw.get("compute_project") or "")
        sources_raw = raw.get("sources") or ()
        sources = tuple(_datasource_from_yaml(s, profile_name=name) for s in sources_raw)
        # Fork-discriminator fields. ``.get(default)`` so legacy yaml
        # without the keys deserializes as a plain ``kind="main"``
        # profile with both back-pointers ``None``.
        kind_raw = raw.get("kind")
        kind = _plain_str(kind_raw) if kind_raw is not None else "main"
        if not kind:
            kind = "main"
        parent_profile_raw = raw.get("parent_profile")
        parent_profile = _plain_str(parent_profile_raw) if parent_profile_raw is not None else None
        git_sha_raw = raw.get("git_sha")
        git_sha = _plain_str(git_sha_raw) if git_sha_raw is not None else None
        description = _plain_str(raw.get("description") or "")
        return Profile(
            name=name,
            compute_project=compute_project,
            endpoint=_plain_str(raw["endpoint"]),
            auth=auth,
            sources=sources,
            cost_thresholds=cost,
            tags=tuple(_plain_str(t) for t in tags_raw),
            description=description,
            package_path=package_path,
            kind=kind,
            parent_profile=parent_profile,
            git_sha=git_sha,
        )
    except KeyError as e:
        raise InvalidProfileError(
            f"profile {name!r}: missing required yaml key {e}",
            remediation="per the spec §5 yaml grammar, a profile block "
            "requires at least: name (the key), compute_project, "
            "endpoint, and auth. The 'sources' list is optional at "
            "create time (the create wizard's source-picker loop "
            "appends entries interactively), but at least one source "
            "is required before `mcs build` / `mcs meta` can do "
            "anything useful.",
        ) from e


def _tablespec_to_yaml(ts: TableSpec) -> str | dict[str, Any]:
    """Inverse of ``_tablespec_from_yaml``. Bare-string output when
    there's no column scoping (the compact form); dict output when
    columns or columns_exclude is set."""
    if ts.columns is None and not ts.columns_exclude:
        return ts.name
    out: dict[str, Any] = {"name": ts.name}
    if ts.columns is not None:
        out["columns"] = list(ts.columns)
    if ts.columns_exclude:
        out["columns_exclude"] = list(ts.columns_exclude)
    return out


def _datasource_to_yaml(ds: DataSource) -> dict[str, Any]:
    out: dict[str, Any] = {"project": ds.project, "schema": ds.schema}
    if isinstance(ds.tables, str):
        # The wildcard.
        out["tables"] = ds.tables
    else:
        out["tables"] = [_tablespec_to_yaml(ts) for ts in ds.tables]
    return out


def _profile_to_dict(p: Profile) -> dict[str, Any]:
    """Serialize a Profile to the on-disk yaml shape (compute_project +
    sources list)."""
    auth_block: dict[str, Any]
    if isinstance(p.auth, ProcessAuth):
        auth_block = {
            "type": "process",
            "command": p.auth.command,
            "timeout": p.auth.timeout,
        }
    else:
        auth_block = {
            "type": "ak",
            "access_key_id": p.auth.access_key_id,
            "access_key_secret": p.auth.access_key_secret,
        }
    out: dict[str, Any] = {
        "compute_project": p.compute_project,
        "endpoint": p.endpoint,
        "auth": auth_block,
        "cost_thresholds": {
            "confirm_cny": p.cost_thresholds.confirm_cny,
            "blocked_cny": p.cost_thresholds.blocked_cny,
            "enabled": p.cost_thresholds.enabled,
        },
        "sources": [_datasource_to_yaml(s) for s in p.sources],
    }
    if p.tags:
        out["tags"] = list(p.tags)
    if p.description:
        out["description"] = p.description
    if p.package_path is not None:
        out["package_path"] = str(p.package_path)
    # Fork-discriminator fields emit only when non-default so existing
    # main-kind profiles don't grow stale ``kind: main`` boilerplate
    # on a re-write through ``upsert``.
    if p.kind != "main":
        out["kind"] = p.kind
    if p.parent_profile is not None:
        out["parent_profile"] = p.parent_profile
    if p.git_sha is not None:
        out["git_sha"] = p.git_sha
    return out


def load_all() -> dict[str, Profile]:
    """Return all profiles keyed by name. Empty dict if file missing/no profiles."""
    raw = _read_raw()
    profiles_raw = raw.get("profiles") or {}
    return {name: _profile_from_dict(name, data) for name, data in profiles_raw.items()}


def get(name: str) -> Profile:
    profiles = load_all()
    if name not in profiles:
        raise ProfileNotFoundError(
            f"profile {name!r} not found",
            remediation="run `mcs profile list` to see available profiles",
        )
    return profiles[name]


def upsert(profile: Profile) -> None:
    from maxcompute_semantic.versioning.lock import WriteLock

    profile.validate()
    path = profiles_yaml_path()
    with WriteLock(path.parent / ".profiles.lock"):
        raw = _read_raw()
        raw["profiles"][profile.name] = _profile_to_dict(profile)
        dump_yaml(raw, path)


def remove(name: str, *, delete_data_dir: bool = True) -> None:
    """Remove profile entry + per-profile data dir. Idempotent."""
    from maxcompute_semantic.versioning.lock import WriteLock

    path = profiles_yaml_path()
    with WriteLock(path.parent / ".profiles.lock"):
            raw = _read_raw()
            profiles_raw = raw.get("profiles") or {}
            if name in profiles_raw:
                del profiles_raw[name]
            dump_yaml(raw, path)
    if delete_data_dir:
        # The on-disk data dir at remove time is canonically the
        # ``data_root() / <name>`` slot since the profile entry
        # (and the optional custom ``package_path`` override that
        # would point elsewhere) is the only source of the
        # not-default location, and we just deleted that entry.
        # If the removed profile pointed at a custom package_path
        # on disk, the user has the directory and the rmtree call
        # below skips it (because ``profile_data_dir(name)``
        # always resolves to the default slot, not to whatever the
        # deleted-profile's ``package_path`` was).
        pdir = profile_data_dir(name)
        if pdir.exists():
            try:
                shutil.rmtree(pdir)
            except OSError:
                # Spec: warn but don't fail the remove — the
                # disk-level cleanup is best-effort, and a
                # half-removed data dir is recoverable manually
                # via ``rm -rf`` once the user notices the warning.
                import logging

                logging.getLogger("maxcompute_semantic").warning(
                    "failed to delete per-profile data dir at %s; remove manually",
                    pdir,
                )
