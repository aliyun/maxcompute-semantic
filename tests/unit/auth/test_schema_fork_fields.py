"""Profile schema — the new fork-discriminator fields and the yaml
round-trip for them."""

from __future__ import annotations

from pathlib import Path

import pytest
from maxcompute_semantic.auth.errors import InvalidProfileError
from maxcompute_semantic.auth.profile_store import _profile_from_dict, _profile_to_dict
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile


def _make_main(name: str = "acme") -> Profile:
    return Profile(
        name=name,
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="${env:AK_ID}", access_key_secret="${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )


def _make_fork(parent: Profile, fork_name: str, sha: str, worktree: Path) -> Profile:
    return Profile(
        name=fork_name,
        compute_project=parent.compute_project,
        endpoint=parent.endpoint,
        auth=parent.auth,
        sources=parent.sources,
        cost_thresholds=parent.cost_thresholds,
        tags=parent.tags,
        package_path=worktree,
        kind="fork",
        parent_profile=parent.name,
        git_sha=sha,
    )


def test_main_kind_profile_validates_without_fork_fields() -> None:
    """A profile with default ``kind="main"`` and ``parent_profile``
    / ``git_sha`` both ``None`` is the existing pre-versioning
    shape. ``validate()`` accepts it. This guards against the new
    cross-field check accidentally rejecting the pre-existing
    profile shape during the rollout — every existing user's
    profiles.yaml deserializes into this shape."""
    p = _make_main()
    p.validate()  # no raise


def test_fork_kind_requires_parent_profile() -> None:
    """A ``kind="fork"`` profile with ``parent_profile=None`` is
    invalid — the wrapper needs the parent's name to find the
    parent's git repo."""
    p = Profile(
        name="acme@v1",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="x", access_key_secret="y"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
        package_path=Path("/tmp/wt"),
        kind="fork",
        parent_profile=None,
        git_sha="0" * 40,
    )
    with pytest.raises(InvalidProfileError, match="parent_profile"):
        p.validate()


def test_fork_kind_requires_git_sha_in_hex_40() -> None:
    """The ``git_sha`` must be a 40-character lowercase-hex string —
    short SHAs and branch names are rejected at the schema layer so
    the on-disk yaml is unambiguous. The CLI's ``mcs profile fork``
    resolves any user-supplied short SHA via ``GitRepo.rev_parse``
    before persisting, so this restriction is invisible to the
    user."""
    base = _make_main()
    # Short SHA — rejected.
    bad_short = Profile(
        name="acme@v1",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        package_path=Path("/tmp/wt"),
        kind="fork",
        parent_profile=base.name,
        git_sha="abcdef1",  # 7 chars, not 40
    )
    with pytest.raises(InvalidProfileError, match="git_sha"):
        bad_short.validate()
    # Non-hex char — rejected.
    bad_chars = Profile(
        name="acme@v1",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        package_path=Path("/tmp/wt"),
        kind="fork",
        parent_profile=base.name,
        git_sha="g" * 40,  # 'g' isn't a hex digit
    )
    with pytest.raises(InvalidProfileError, match="hex"):
        bad_chars.validate()
    # Happy path — 40 lowercase hex chars.
    good = Profile(
        name="acme@v1",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        package_path=Path("/tmp/wt"),
        kind="fork",
        parent_profile=base.name,
        git_sha="0123456789abcdef" * 2 + "01234567",
    )
    good.validate()  # no raise


def test_fork_name_allows_at_and_colon_delimiters() -> None:
    """The name regex admits ``@`` and ``:`` as separators inside the
    body of the name so the canonical fork-name conventions
    (``parent@<7-hex>``, ``parent:baseline``, ``parent@v1.2``) are
    legal. The opening character is still alphanumeric, so a
    leading ``@v1`` is illegal — names start with a letter or
    digit."""
    base = _make_main()
    for legal_name in ("acme@abcdef0", "acme:baseline", "acme@v1.0", "acme-pre"):
        fork = _make_fork(base, legal_name, sha="0" * 40, worktree=Path("/tmp") / legal_name)
        try:
            fork.validate()
        except InvalidProfileError as exc:
            pytest.fail(f"the fork-name regex rejected the legal name {legal_name!r}: {exc}")
    # Names beginning with a non-alphanumeric character are still
    # rejected.
    illegal = Profile(
        name="@illegal",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
    )
    with pytest.raises(InvalidProfileError, match="name"):
        illegal.validate()


def test_yaml_roundtrip_for_main_profile_omits_fork_fields() -> None:
    """Serializing a main-kind profile produces a yaml dict
    *without* the three new keys (so existing yaml files don't grow
    a redundant ``kind: main`` line on every save). Deserializing
    the same dict gives back an equal profile."""
    p = _make_main()
    out = _profile_to_dict(p)
    assert "kind" not in out
    assert "parent_profile" not in out
    assert "git_sha" not in out
    # The round-trip equates.
    back = _profile_from_dict(p.name, out)
    # The round-trip is structural, not identity. ``Profile`` is a
    # frozen dataclass with __eq__ generated by @dataclass, so the
    # comparison is field-by-field.
    assert back == p


def test_yaml_roundtrip_for_fork_profile_emits_three_extra_keys(
    tmp_path: Path,
) -> None:
    """A fork-kind profile emits ``kind``, ``parent_profile``, and
    ``git_sha`` keys, in addition to the standard ``package_path``
    that's already in the existing surface."""
    parent = _make_main("acme")
    wt = tmp_path / "acme@v1"
    fork = _make_fork(parent, "acme@v1", sha="a" * 40, worktree=wt)
    out = _profile_to_dict(fork)
    assert out["kind"] == "fork"
    assert out["parent_profile"] == "acme"
    assert out["git_sha"] == "a" * 40
    assert out["package_path"] == str(wt)

    back = _profile_from_dict(fork.name, out)
    assert back == fork


def test_yaml_with_unknown_kind_value_fails_validate() -> None:
    """Hand-edited yaml with ``kind: maybe`` doesn't load — the
    discriminator's allowed set is closed."""
    bad_raw = {
        "compute_project": "proj",
        "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
        "auth": {"type": "ak", "access_key_id": "x", "access_key_secret": "y"},
        "sources": [{"project": "proj", "schema": "default", "tables": "*"}],
        "kind": "maybe",
    }
    p = _profile_from_dict("acme-weird", bad_raw)
    # ``_profile_from_dict`` itself doesn't run ``validate`` (the
    # existing contract is that loading is loose and validation is
    # explicit at the boundary where the profile is used — see the
    # existing ``upsert`` function which calls ``profile.validate()``
    # before persisting). The validate call raises on the bad kind.
    with pytest.raises(InvalidProfileError, match="kind"):
        p.validate()


def test_old_yaml_without_new_keys_deserializes_as_main() -> None:
    """Existing on-disk yaml files written by mcs versions before
    this feature don't carry the three new keys. The loader's
    ``.get(..., default)`` calls treat the absence as "main with
    no parent and no anchor", which is the same shape the brand-
    new ``_make_main()`` produces. This is the backwards-compat
    contract for the rollout — old profiles continue to load."""
    legacy_raw = {
        "compute_project": "proj",
        "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
        "auth": {"type": "ak", "access_key_id": "x", "access_key_secret": "y"},
        "sources": [{"project": "proj", "schema": "default", "tables": "*"}],
        # No "kind", no "parent_profile", no "git_sha" — the
        # pre-versioning shape.
    }
    p = _profile_from_dict("acme", legacy_raw)
    assert p.kind == "main"
    assert p.parent_profile is None
    assert p.git_sha is None
    p.validate()  # no raise — old yaml is forward-compatible.


def test_main_kind_rejects_stray_fork_fields() -> None:
    """A main-kind profile with a stray ``parent_profile`` (and no
    ``git_sha``) is rejected — the cross-field invariant says both
    back-pointers are None on a main profile. Catches hand-edited
    yaml that's lost the ``kind: fork`` line but kept the
    back-pointer."""
    base = _make_main()
    p = Profile(
        name="acme",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        kind="main",
        parent_profile="some-parent",
        git_sha=None,
    )
    with pytest.raises(InvalidProfileError, match="parent_profile"):
        p.validate()


def test_fork_kind_requires_non_none_package_path() -> None:
    """Real forks always have a worktree directory; ``package_path``
    is the on-disk pointer to it."""
    base = _make_main()
    p = Profile(
        name="acme@v1",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        package_path=None,
        kind="fork",
        parent_profile=base.name,
        git_sha="0" * 40,
    )
    with pytest.raises(InvalidProfileError, match="package_path"):
        p.validate()


def test_validate_runs_existing_checks_before_fork_checks() -> None:
    """A profile that is both name-regex-illegal AND fork-fields-broken
    raises the *name* error first — the existing per-field validation
    block runs before the appended fork cross-field invariants."""
    base = _make_main()
    p = Profile(
        name="@illegal-and-bad-fork",  # leading separator: name fails
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        package_path=None,  # would also fail fork invariant
        kind="fork",
        parent_profile=None,  # would also fail fork invariant
        git_sha=None,  # would also fail fork invariant
    )
    with pytest.raises(InvalidProfileError, match="name"):
        p.validate()


def test_profile_with_kind_main_and_only_one_fork_field_set_is_invalid() -> None:
    """Symmetric case: main-kind with only ``git_sha`` set (and
    ``parent_profile`` None) is also invalid. The cross-field
    invariant says both back-pointers must be None together on a
    main profile."""
    base = _make_main()
    p = Profile(
        name="acme",
        compute_project=base.compute_project,
        endpoint=base.endpoint,
        auth=base.auth,
        sources=base.sources,
        kind="main",
        parent_profile=None,
        git_sha="0" * 40,
    )
    with pytest.raises(InvalidProfileError, match="git_sha"):
        p.validate()


def test_profile_to_dict_does_not_quote_kind_value(tmp_path: Path) -> None:
    """The dict emitter writes a plain str for the ``kind`` value
    (not a ruamel ScalarString wrapper) so the downstream yaml
    dumper renders it as the bare unquoted identifier ``fork``.
    Pins the visual format against future emitter-library changes."""
    parent = _make_main("acme")
    fork = _make_fork(parent, "acme@v1", sha="a" * 40, worktree=tmp_path / "acme@v1")
    out = _profile_to_dict(fork)
    # The value is a plain ``str``, not a ruamel-wrapped subclass.
    assert type(out["kind"]) is str
    assert out["kind"] == "fork"
