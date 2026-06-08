# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for auth/profile_store.py — yaml round-trip for the
multi-source Profile shape, missing-key validation, and the
ruamel-ScalarString-wrapper coercion regression test.

The on-disk per-profile-block shape is
``{compute_project, endpoint, auth, sources: [{project, schema,
tables}, ...], ...}``; the file-level envelope is
``{version: 1, profiles: {<name>: <block>, ...}}``.
A block missing ``compute_project`` is rejected by
``Profile.validate()`` with "compute_project is empty".

The ruamel-ScalarString coercion test exercises the
``_plain_str`` boundary: when a yaml file is written in JSON-string
form (a strict subset of YAML), the ruamel 'rt' loader returns
``DoubleQuotedScalarString`` for each quoted scalar — a ``str``
subclass that the ``YAML(typ='safe')`` representer used by
``build/markdown._yaml_dumps`` rejects with a surprising
"cannot represent an object" error. The deserializer coerces each
string field at the boundary so the Profile dataclass holds plain
Python ``str`` instances on every field: ``compute_project``, each
source's ``project`` and ``schema``, each ``TableSpec.name``, and
the entries of its ``columns`` / ``columns_exclude`` lists.

The eval harness's ``eval/_skill_setup.write_profile_yaml`` writes
the per-schema test profile in JSON-string form (it avoids
pulling ruamel into the eval surface), so the JSON-shape path
through the deserializer is the eval's critical-path. The
regression test pins this behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from maxcompute_semantic.auth.errors import (
    IncompatibleProfileVersionError,
    InvalidProfileError,
    InvalidProfileFileError,
    ProfileNotFoundError,
)
from maxcompute_semantic.auth.profile_store import (
    get,
    load_all,
    remove,
    upsert,
)
from maxcompute_semantic.auth.schema import (
    AkAuth,
    DataSource,
    ProcessAuth,
    Profile,
    TableSpec,
)


def _example_profile(name: str = "acme-corp") -> Profile:
    """The canonical example fixture for the round-trip
    tests. ProcessAuth for the auth side (the standard 'ncs'
    Aliyun credential helper command) and a single wildcard
    DataSource whose project equals the compute project (the
    common-case where the AK's home project is also one of the
    data sources, often the team's working namespace)."""
    return Profile(
        name=name,
        compute_project="acme_warehouse",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=ProcessAuth(
            command="my-credential-helper get --format json"
        ),
        sources=(DataSource(project="acme_warehouse", schema="default", tables="*"),),
    )


def test_load_all_returns_empty_when_file_missing(isolated_config: Path) -> None:
    assert load_all() == {}


def test_upsert_then_load(isolated_config: Path) -> None:
    p = _example_profile()
    upsert(p)
    profiles = load_all()
    assert "acme-corp" in profiles
    loaded = profiles["acme-corp"]
    assert loaded.compute_project == "acme_warehouse"
    # The sources list round-trips through the yaml.
    assert len(loaded.sources) == 1
    assert loaded.sources[0].project == "acme_warehouse"
    assert loaded.sources[0].schema == "default"
    assert loaded.sources[0].tables == "*"
    assert loaded.sources[0].is_wildcard()


def test_get_existing(isolated_config: Path) -> None:
    upsert(_example_profile())
    p = get("acme-corp")
    assert p.name == "acme-corp"
    assert p.compute_project == "acme_warehouse"


def test_get_missing_raises_profile_not_found(isolated_config: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        get("nope")


def test_remove_deletes_entry_and_data_dir(isolated_config: Path) -> None:
    upsert(_example_profile())
    from maxcompute_semantic._internal.paths import profile_data_dir

    pdir = profile_data_dir("acme-corp")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "package.db").write_text("dummy")
    remove("acme-corp")
    assert load_all() == {}
    assert not pdir.exists()


def test_remove_idempotent(isolated_config: Path) -> None:
    remove("nope")  # must not raise


def test_invalid_yaml_raises_invalid_profile_file(isolated_config: Path) -> None:
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("key: [unclosed\n")
    with pytest.raises(InvalidProfileFileError):
        load_all()


def test_unsupported_file_level_version_raises_incompatible(isolated_config: Path) -> None:
    """The top-level ``version`` envelope is 1. A file with a
    different version number is treated as "from a future / unknown
    mcs format" and rejected with ``IncompatibleProfileVersionError``."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("version: 99\nprofiles: {}\n")
    with pytest.raises(IncompatibleProfileVersionError):
        load_all()


def test_upsert_overwrites_existing(isolated_config: Path) -> None:
    """Two upserts under the same profile name keep the latest;
    the AK-pair flavor of auth replaces the ProcessAuth one."""
    upsert(_example_profile())
    p2 = Profile(
        name="acme-corp",
        compute_project="acme_prod",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="LTAI_id", access_key_secret="secret_redacted"),
        sources=(DataSource(project="acme_prod", schema="warehouse", tables="*"),),
    )
    upsert(p2)
    loaded = get("acme-corp")
    assert loaded.compute_project == "acme_prod"
    assert isinstance(loaded.auth, AkAuth)


def test_upsert_canonicalizes_file_envelope(isolated_config: Path) -> None:
    """Any write emits only the supported top-level envelope keys."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\ndefault_profile: alpha\nprofiles: {}\n",
        encoding="utf-8",
    )

    upsert(_example_profile("alpha"))

    written = path.read_text(encoding="utf-8")
    assert "default_profile" not in written
    assert "profiles:" in written


def test_upsert_preserves_other_profile_comments(isolated_config: Path) -> None:
    """ruamel 'rt' mode preserves yaml comments through the
    round-trip. A user comment in the on-disk profiles.yaml
    survives a subsequent ``upsert`` of a different profile."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    upsert(_example_profile())
    path = profiles_yaml_path()
    content = path.read_text()
    content = content.replace("profiles:", "# my comment\nprofiles:")
    path.write_text(content)
    upsert(_example_profile("acme-bench"))
    assert "# my comment" in path.read_text()


def test_load_all_empty_yaml_file(isolated_config: Path) -> None:
    """An empty profiles.yaml file (which ruamel loads as None)
    yields an empty profiles dict, not an error."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    assert load_all() == {}


def test_load_all_missing_profiles_key(isolated_config: Path) -> None:
    """A yaml file with the top-level ``version`` envelope but no
    ``profiles`` key yields an empty profiles dict."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("version: 1\n")
    assert load_all() == {}


def test_yaml_missing_compute_project_fails_validate(isolated_config: Path) -> None:
    """A yaml profile block missing the required ``compute_project``
    field is loaded with an empty ``compute_project`` string and
    fails ``Profile.validate()`` with "compute_project is empty".

    ``load_all`` itself returns the dataclass *without* calling
    ``validate``; the empty value surfaces when a consumer (``upsert``,
    a CLI command that needs the value, the auth-test helper) goes
    through the validator. We exercise this here via an explicit
    iterate-and-validate pass."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\n"
        "profiles:\n"
        "  no-compute:\n"
        "    endpoint: https://x.example.com\n"
        "    auth:\n"
        "      type: ak\n"
        "      access_key_id: id\n"
        "      access_key_secret: sec\n"
        "    sources: []\n"
    )
    with pytest.raises(InvalidProfileError, match="compute_project is empty"):
        for p in load_all().values():
            p.validate()


def test_load_ignores_unknown_top_level_keys(isolated_config: Path) -> None:
    """Unknown top-level keys on a profile block (e.g. leftovers from
    a hand-edited yaml) are silently ignored — the deserializer only
    reads the keys it knows about. This is the contract that lets a
    user hand-add ad-hoc annotations to a profile block without
    breaking mcs's round-trip."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\n"
        "profiles:\n"
        "  prof_extras:\n"
        "    compute_project: my_compute\n"
        "    endpoint: https://x.example.com\n"
        "    auth:\n"
        "      type: ak\n"
        "      access_key_id: id\n"
        "      access_key_secret: sec\n"
        "    sources:\n"
        "      - project: data_proj\n"
        "        schema: data_schema\n"
        '        tables: "*"\n'
        "    custom_note: 'free-form annotation, not read by mcs'\n"
    )
    profiles = load_all()
    assert "prof_extras" in profiles
    p = profiles["prof_extras"]
    assert p.compute_project == "my_compute"
    assert len(p.sources) == 1
    assert p.sources[0].project == "data_proj"
    assert p.sources[0].schema == "data_schema"
    assert p.sources[0].is_wildcard()
    # The unknown ``custom_note`` key is dropped from the dataclass —
    # the Profile fields are the only attributes that exist.
    assert not hasattr(p, "custom_note")


def test_load_rejects_yaml_missing_endpoint(isolated_config: Path) -> None:
    """The ``endpoint`` field is read with the bracket-subscript
    form (``raw['endpoint']``), so a missing key raises a Python
    KeyError that the deserializer wraps in an
    ``InvalidProfileError`` with the "missing required yaml key"
    message."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\n"
        "profiles:\n"
        "  no-endpoint:\n"
        "    compute_project: cp\n"
        "    auth:\n"
        "      type: ak\n"
        "      access_key_id: id\n"
        "      access_key_secret: sec\n"
        "    sources: []\n"
    )
    with pytest.raises(InvalidProfileError, match="missing required yaml key"):
        load_all()


def test_load_rejects_yaml_unknown_auth_type(isolated_config: Path) -> None:
    """The auth-type dispatch in ``_profile_from_dict`` accepts only
    'process' or 'ak'; anything else is rejected at deserialization
    time with the "unknown auth.type" message."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    path = profiles_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\n"
        "profiles:\n"
        "  bad-auth:\n"
        "    compute_project: cp\n"
        "    endpoint: https://x.example.com\n"
        "    auth:\n"
        "      type: oauth2\n"
        "    sources: []\n"
    )
    with pytest.raises(InvalidProfileError, match="unknown auth.type"):
        load_all()


def test_remove_rmtree_failure_warns_not_raises(isolated_config: Path) -> None:
    """When ``shutil.rmtree`` of the per-profile data directory
    fails (e.g. permission-denied), the remove operation logs a
    warning but does not raise — the profile entry is still
    removed from the yaml. The user is left with a stale data
    directory they have to clean up manually, with the warning
    log indicating which directory."""
    from unittest.mock import patch

    upsert(_example_profile())
    from maxcompute_semantic._internal.paths import profile_data_dir

    pdir = profile_data_dir("acme-corp")
    pdir.mkdir(parents=True, exist_ok=True)

    with patch("shutil.rmtree", side_effect=OSError("permission denied")):
        remove("acme-corp")

    assert load_all() == {}
    assert pdir.exists()


def test_remove_no_delete_data_dir(isolated_config: Path) -> None:
    """``delete_data_dir=False`` removes the yaml entry only,
    preserving the on-disk data directory. Useful when the
    operator wants to keep the data for the export-and-re-import
    workflow."""
    upsert(_example_profile())
    from maxcompute_semantic._internal.paths import profile_data_dir

    pdir = profile_data_dir("acme-corp")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "package.db").write_text("dummy")

    remove("acme-corp", delete_data_dir=False)
    assert load_all() == {}
    assert pdir.exists()


def test_load_strips_ruamel_scalar_string_wrappers_top_level(
    isolated_config: Path,
) -> None:
    """The 0.3.0a18 fix's regression test, ported to the v2 shape.

    When ``eval/_skill_setup.write_profile_yaml`` writes a profile
    block in JSON-string form (the eval harness's convention,
    avoiding the ruamel dependency in the eval surface), the
    ruamel 'rt' loader on the read side returns
    ``DoubleQuotedScalarString`` (a ``str`` subclass) for every
    quoted scalar. The downstream
    ``YAML(typ='safe')`` writer in ``build/markdown._yaml_dumps``
    rejects the subclass with a "cannot represent an object"
    error, which crashed every benchmark schema's build in
    an earlier smoke run. The fix coerces every string field at the
    deserializer boundary via ``_plain_str``. The regression test
    here pins the property that the v2 fields
    (``compute_project``, each source's ``project``/``schema``,
    the wildcard ``tables`` string) come out as plain ``str``."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    yaml_path = profiles_yaml_path()
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    # JSON-form yaml. Every string is double-quoted, so ruamel's 'rt'
    # loader wraps each value in DoubleQuotedScalarString.
    yaml_path.write_text(
        '{"version": 1, "profiles": {"alpha": {'
        '"compute_project": "compute_alpha", '
        '"endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api", '
        '"auth": {"type": "ak", "access_key_id": "ak_id_lit", '
        '"access_key_secret": "ak_secret_lit"}, '
        '"sources": ['
        '{"project": "data_proj_a", "schema": "sales", "tables": "*"}'
        "]"
        "}}}\n",
        encoding="utf-8",
    )
    p = get("alpha")
    # Every string-typed field on the Profile is a plain Python str.
    assert type(p.name) is str
    assert type(p.compute_project) is str
    assert type(p.endpoint) is str
    assert isinstance(p.auth, AkAuth)
    assert type(p.auth.access_key_id) is str
    assert type(p.auth.access_key_secret) is str
    # And on each DataSource.
    assert len(p.sources) == 1
    src = p.sources[0]
    assert type(src.project) is str
    assert type(src.schema) is str
    assert isinstance(src.tables, str)  # wildcard form
    assert src.tables == "*"
    assert type(src.tables) is str

    # The downstream smoke-test: the safe-mode YAML representer
    # used by ``build/markdown._yaml_dumps`` for the per-overview
    # frontmatter must accept these fields without the
    # "cannot represent an object" RepresenterError that motivated
    # the 0.3.0a18 fix.
    from maxcompute_semantic.build.markdown import _yaml_dumps

    out = _yaml_dumps(
        {
            "compute_project": p.compute_project,
            "endpoint": p.endpoint,
            "source_project": src.project,
            "source_schema": src.schema,
            "tables": src.tables,
        }
    )
    assert "https://service.cn-shanghai.maxcompute.aliyun.com/api" in out


def test_load_strips_ruamel_wrappers_on_enumerated_tables_and_columns(
    isolated_config: Path,
) -> None:
    """The wrapper-coercion extends to the per-TableSpec.name field
    and the entries of TableSpec.columns / TableSpec.columns_exclude
    when the tables list is enumerated (not the wildcard ``"*"``
    string). The yaml grammar (spec §5) accepts each entry as
    either a bare string (no column scoping) or a ``{name, columns
    | columns_exclude}`` dict; both forms go through the
    ``_tablespec_from_yaml`` helper which calls ``_plain_str`` on
    every string field. Without this coercion, the BM25 retrieval
    over the package_doc memory entries — which uses the table
    name as the key — would compare ScalarString-wrapped names
    against plain-str queries and get type-confused matches."""
    from maxcompute_semantic._internal.paths import profiles_yaml_path

    yaml_path = profiles_yaml_path()
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        '{"version": 1, "profiles": {"beta": {'
        '"compute_project": "compute_beta", '
        '"endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api", '
        '"auth": {"type": "ak", "access_key_id": "id_b", "access_key_secret": "sec_b"}, '
        '"sources": ['
        # Bare-string-list form. Each entry becomes a TableSpec
        # with the bare name and no column scoping.
        '{"project": "dproj_one", "schema": "sales", "tables": ["orders", "customers"]}, '
        # Dict-entry form. Each entry already has a ``name`` field
        # and optionally a ``columns`` whitelist or
        # ``columns_exclude`` blacklist.
        '{"project": "dproj_two", "schema": "default", "tables": ['
        '{"name": "wide_table", "columns": ["col_a", "col_b", "col_c"]}, '
        '{"name": "hr_table", "columns_exclude": ["ssn", "salary"]}, '
        '"bare_audit_log"'
        "]"
        "}"
        "]"
        "}}}\n",
        encoding="utf-8",
    )
    p = get("beta")
    assert len(p.sources) == 2
    src1, src2 = p.sources

    # First source: bare-string-list lifted to TableSpecs.
    assert all(isinstance(t, TableSpec) for t in src1.tables)
    assert tuple(t.name for t in src1.tables) == ("orders", "customers")
    for t in src1.tables:
        assert type(t.name) is str
        assert t.columns is None
        assert t.columns_exclude == ()

    # Second source: mix of dict-form (whitelist, blacklist) and
    # bare-string entries. All names plain str. All column-list
    # entries plain str.
    by_name = {t.name: t for t in src2.tables}
    assert set(by_name.keys()) == {"wide_table", "hr_table", "bare_audit_log"}
    wide = by_name["wide_table"]
    assert wide.columns is not None
    assert tuple(wide.columns) == ("col_a", "col_b", "col_c")
    assert all(type(c) is str for c in wide.columns)
    assert wide.columns_exclude == ()

    hr = by_name["hr_table"]
    assert hr.columns is None
    assert tuple(hr.columns_exclude) == ("ssn", "salary")
    assert all(type(c) is str for c in hr.columns_exclude)

    audit = by_name["bare_audit_log"]
    assert audit.columns is None
    assert audit.columns_exclude == ()
    assert type(audit.name) is str


def test_description_round_trips():
    from maxcompute_semantic.auth.profile_store import (
        _profile_from_dict,
        _profile_to_dict,
    )
    from maxcompute_semantic.auth.schema import AkAuth, Profile

    p = Profile(
        name="p1",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth("LTAI_x", "secret"),
        description="orders + payments analysis",
    )
    body = _profile_to_dict(p)
    assert body["description"] == "orders + payments analysis"
    p2 = _profile_from_dict("p1", body)
    assert p2.description == "orders + payments analysis"


def test_description_omitted_when_empty():
    from maxcompute_semantic.auth.profile_store import (
        _profile_from_dict,
        _profile_to_dict,
    )
    from maxcompute_semantic.auth.schema import AkAuth, Profile

    p = Profile(
        name="p1",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth("LTAI_x", "secret"),
        description="",
    )
    body = _profile_to_dict(p)
    assert "description" not in body
    p2 = _profile_from_dict("p1", body)
    assert p2.description == ""


def test_legacy_zero_cost_thresholds_remain_disabled():
    """Profiles written before the enabled flag used 0/0 as the disabled sentinel."""
    from maxcompute_semantic.auth.profile_store import _profile_from_dict

    p = _profile_from_dict(
        "legacy-cost-disabled",
        {
            "compute_project": "proj",
            "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "LTAI_x",
                "access_key_secret": "secret",
            },
            "sources": [
                {
                    "project": "proj",
                    "schema": "default",
                    "tables": "*",
                }
            ],
            "cost_thresholds": {
                "confirm_cny": 0,
                "blocked_cny": 0,
            },
        },
    )

    assert p.cost_thresholds.is_enabled() is False
