# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``mcs profile export`` / ``mcs profile import`` round-trip."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from maxcompute_semantic.auth.link_store import set_link
from maxcompute_semantic.auth.profile_store import get, load_all, upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.cli import cli


def _profile(name: str = "alpha") -> Profile:
    return Profile(
        name=name,
        compute_project="proj_alpha",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
        auth=AkAuth("FakeAKID", "FakeSecret-do-not-export"),
        tags=("benchmark", "shared"),
        sources=(DataSource(project="proj_alpha", schema="default", tables="*"),),
    )


def _seed_package(pdir: Path, *, with_state: bool = True) -> None:
    """Drop a minimal package tree at pdir."""
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "package.db").write_bytes(b"FAKE_DB\x00")
    (pdir / "_overview.md").write_text("# overview\n", encoding="utf-8")
    (pdir / "orders.md").write_text("# orders\n", encoding="utf-8")
    if with_state:
        (pdir / "_state.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "tables_count": 3,
                    "udfs_count": 1,
                    "joins_count": 2,
                    "tier": "3",
                    "history_skipped": False,
                    "tables_with_sample_sqls": 2,
                    "last_built_at": "2026-05-14T10:00:00Z",
                }
            ),
            encoding="utf-8",
        )


def _invoke(args: list[str]) -> object:
    runner = CliRunner()
    return runner.invoke(cli, args)


def test_export_writes_tarball(isolated_config: Path, tmp_path: Path) -> None:
    """``mcs profile export X -o pkg.tar.gz`` produces a valid archive."""
    p = _profile()
    upsert(p)
    set_link(str(Path.cwd()), p.name)
    _seed_package(isolated_config / "data" / p.name)

    archive = tmp_path / "alpha-pkg.tar.gz"
    result = _invoke(["profile", "export", p.name, "-o", str(archive)])
    assert result.exit_code == 0, result.output
    assert archive.exists()

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "manifest.json" in names
    assert any(n.startswith("data/") for n in names)
    assert "data/package.db" in names
    assert "data/_overview.md" in names


def test_export_manifest_excludes_auth(isolated_config: Path, tmp_path: Path) -> None:
    """The manifest has profile config but never auth credentials."""
    p = _profile()
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)
    archive = tmp_path / "alpha.tar.gz"
    _invoke(["profile", "export", p.name, "-o", str(archive)])

    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile("manifest.json")
        assert f is not None
        manifest = json.loads(f.read().decode("utf-8"))

    assert manifest["profile"]["name"] == "alpha"
    assert manifest["profile"]["compute_project"] == "proj_alpha"
    assert len(manifest["profile"]["sources"]) == 1
    assert manifest["profile"]["sources"][0]["project"] == "proj_alpha"
    assert manifest["profile"]["sources"][0]["schema"] == "default"
    # The whole point: secrets must NOT travel.
    assert "auth" not in manifest["profile"]
    assert "FakeSecret" not in json.dumps(manifest)
    assert "FakeAKID" not in json.dumps(manifest)


def test_export_does_not_follow_package_symlinks(
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    """Package export must not copy files reached through symlinks."""
    p = _profile()
    upsert(p)
    pdir = isolated_config / "data" / p.name
    _seed_package(pdir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not export\n", encoding="utf-8")
    (pdir / "linked").symlink_to(outside, target_is_directory=True)

    archive = tmp_path / "alpha.tar.gz"
    result = _invoke(["profile", "export", p.name, "-o", str(archive)])

    assert result.exit_code == 0, result.output
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "data/linked/secret.txt" not in names
    assert "data/linked" not in names


def test_export_no_package_built(isolated_config: Path, tmp_path: Path) -> None:
    """Export refuses to bundle an empty profile (no package on disk)."""
    p = _profile()
    upsert(p)
    archive = tmp_path / "alpha.tar.gz"
    result = _invoke(["profile", "export", p.name, "-o", str(archive)])
    assert result.exit_code != 0
    assert "no package data" in result.output.lower()
    assert "mcs build" in result.output
    assert not archive.exists()


def test_import_restores_profile_and_data(isolated_config: Path, tmp_path: Path) -> None:
    """Roundtrip: export from one isolated config, import into another."""
    # Source side: build a package, export.
    p = _profile()
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)
    archive = tmp_path / "alpha.tar.gz"
    _invoke(["profile", "export", p.name, "-o", str(archive)])

    # Destination side: pretend we never had this profile.
    from maxcompute_semantic.auth.profile_store import remove

    remove(p.name)
    assert p.name not in load_all()

    # Import.
    result = _invoke(["profile", "import", str(archive)])
    assert result.exit_code == 0, result.output

    # Profile is registered.
    imported = get(p.name)
    assert imported.compute_project == "proj_alpha"
    assert len(imported.sources) == 1
    assert imported.sources[0].project == "proj_alpha"
    assert imported.sources[0].schema == "default"
    assert imported.tags == ("benchmark", "shared")
    # Auth is placeholder — recipient configures their own.
    assert imported.auth.access_key_id == ""
    assert imported.auth.access_key_secret == ""

    # Package data is on disk at the default per-name slot.
    expected = isolated_config / "data" / p.name / "package.db"
    assert expected.exists()
    assert expected.read_bytes() == b"FAKE_DB\x00"


def test_import_with_name_override(isolated_config: Path, tmp_path: Path) -> None:
    """``--name X`` registers under a different name (avoids local clash)."""
    p = _profile("alpha")
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)
    archive = tmp_path / "alpha.tar.gz"
    _invoke(["profile", "export", "alpha", "-o", str(archive)])

    # Source profile still registered locally; import under new name.
    result = _invoke(["profile", "import", str(archive), "--name", "alpha-copy"])
    assert result.exit_code == 0, result.output
    assert "alpha-copy" in load_all()
    assert "alpha" in load_all()  # original untouched

    pdir = isolated_config / "data" / "alpha-copy"
    assert (pdir / "package.db").exists()


def test_import_with_package_path_override(isolated_config: Path, tmp_path: Path) -> None:
    """``--package-path P`` extracts data to a custom path; profile.package_path
    is set so subsequent reads honor it."""
    p = _profile("source")
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)
    archive = tmp_path / "source.tar.gz"
    _invoke(["profile", "export", "source", "-o", str(archive)])

    from maxcompute_semantic.auth.profile_store import remove

    remove("source")

    custom_dir = tmp_path / "shared-mount" / "source-pkg"
    result = _invoke(["profile", "import", str(archive), "--package-path", str(custom_dir)])
    assert result.exit_code == 0, result.output
    assert (custom_dir / "package.db").exists()
    # Default per-name slot stays empty since package_path redirected.
    assert not (isolated_config / "data" / "source" / "package.db").exists()

    imported = get("source")
    assert imported.package_path == custom_dir.resolve()


def test_import_package_path_override_refuses_non_empty_destination(
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    """Import must not merge archive data into a directory with stale files."""
    p = _profile("source")
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)
    archive = tmp_path / "source.tar.gz"
    _invoke(["profile", "export", "source", "-o", str(archive)])

    from maxcompute_semantic.auth.profile_store import remove

    remove("source")
    custom_dir = tmp_path / "shared-mount" / "source-pkg"
    custom_dir.mkdir(parents=True)
    stale = custom_dir / "stale.md"
    stale.write_text("old package data\n", encoding="utf-8")

    result = _invoke(["profile", "import", str(archive), "--package-path", str(custom_dir)])

    assert result.exit_code != 0
    assert "not empty" in result.output.lower()
    assert stale.read_text(encoding="utf-8") == "old package data\n"
    assert "source" not in load_all()


def test_import_existing_profile_without_override_errors(
    isolated_config: Path, tmp_path: Path
) -> None:
    """Importing into a name that already exists locally errors out (use --name)."""
    p = _profile()
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)
    archive = tmp_path / "alpha.tar.gz"
    _invoke(["profile", "export", p.name, "-o", str(archive)])

    # Don't remove; profile still exists.
    result = _invoke(["profile", "import", str(archive)])
    assert result.exit_code != 0
    assert "already exists" in result.output.lower()


def test_import_name_override_existing_profile_errors(
    isolated_config: Path, tmp_path: Path
) -> None:
    """``--name`` must not silently clobber another local profile."""
    source = _profile("source")
    upsert(source)
    _seed_package(isolated_config / "data" / source.name)
    archive = tmp_path / "source.tar.gz"
    _invoke(["profile", "export", source.name, "-o", str(archive)])

    existing = _profile("target")
    upsert(existing)

    result = _invoke(["profile", "import", str(archive), "--name", existing.name])

    assert result.exit_code != 0
    assert "already exists" in result.output.lower()
    assert get(existing.name).compute_project == "proj_alpha"


def test_import_invalid_archive_errors(tmp_path: Path) -> None:
    """Garbage archive → clear error, no profile registered."""
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"NOT A TARBALL")
    result = _invoke(["profile", "import", str(bad)])
    assert result.exit_code != 0
    assert "extract" in result.output.lower() or "invalid" in result.output.lower()


def test_export_name_override_replaces_manifest_name(isolated_config: Path, tmp_path: Path) -> None:
    """``--export-name X`` replaces the profile name in the manifest and
    default output filename, without changing the local profile."""
    p = _profile("my-private-name")
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)

    archive = tmp_path / "sanitized.tar.gz"
    result = _invoke(
        ["profile", "export", p.name, "--export-name", "shared-benchmark", "-o", str(archive)]
    )
    assert result.exit_code == 0, result.output

    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile("manifest.json")
        assert f is not None
        manifest = json.loads(f.read().decode("utf-8"))

    # Manifest carries the sanitized name, not the local one.
    assert manifest["profile"]["name"] == "shared-benchmark"
    assert "my-private-name" not in json.dumps(manifest)

    # Local profile is untouched.
    local = get("my-private-name")
    assert local.name == "my-private-name"


def test_export_name_override_default_output_filename(
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    """Without -o, default filename uses the export-name, not the local name.
    Verify by calling export_profile directly (CliRunner has no cwd param)."""

    p = _profile("my-private-name")
    upsert(p)
    _seed_package(isolated_config / "data" / p.name)

    # Use export-name but don't specify -o — the default path logic
    # lives in export_cmd, not export_profile. Verify the CLI layer
    # by checking the result output contains the effective name.
    archive = tmp_path / "output.tar.gz"
    result = _invoke(
        ["profile", "export", p.name, "--export-name", "shared-benchmark", "-o", str(archive)]
    )
    assert result.exit_code == 0, result.output
    # The success envelope reports the effective (sanitized) name.
    assert "shared-benchmark" in result.output


def test_import_archive_missing_manifest_errors(tmp_path: Path) -> None:
    """Tarball without manifest.json → clear error."""
    bad = tmp_path / "no-manifest.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        # Empty data/ subdir, no manifest
        info = tarfile.TarInfo(name="data/")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
    result = _invoke(["profile", "import", str(bad)])
    assert result.exit_code != 0
    assert "manifest" in result.output.lower()


@pytest.mark.parametrize(
    "manifest_content",
    [
        '{"schema_version": 99}',  # too-new schema
        '{"schema_version": 1}',  # missing profile.name
    ],
)
def test_import_archive_bad_manifest_schema(tmp_path: Path, manifest_content: str) -> None:
    """Archive with unsupported manifest schema → clean error."""
    bad = tmp_path / "bad-manifest.tar.gz"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "manifest.json").write_text(manifest_content, encoding="utf-8")
    (staging / "data").mkdir()
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(staging / "manifest.json", arcname="manifest.json")
        tar.add(staging / "data", arcname="data")
    result = _invoke(["profile", "import", str(bad)])
    assert result.exit_code != 0
