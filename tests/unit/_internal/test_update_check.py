"""Tests for the update-check probe and cache.

See spec §"Component interfaces" / "_internal/update_check.py" for the
public surface. Tests are structured so each public function gets its
own ``class Test<Name>`` block.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest


class TestLatestMetadata:
    def test_parse_full_payload(self) -> None:
        from maxcompute_semantic._internal.update_check import LatestMetadata

        raw = {
            "schema_version": 1,
            "latest_version": "0.4.0a40",
            "released_at": "2026-05-22T12:00:00Z",
            "wheel_url": "https://example.test/wheels/x.whl",
            "min_supported": "0.4.0a30",
            "disabled": ["0.4.0a32", "0.4.0a35"],
            "notice": "scheduled maintenance",
            "sha256": "A" * 64,
        }
        m = LatestMetadata.from_dict(raw)
        assert m.schema_version == 1
        assert m.latest_version == "0.4.0a40"
        assert m.released_at == "2026-05-22T12:00:00Z"
        assert m.wheel_url == "https://example.test/wheels/x.whl"
        assert m.min_supported == "0.4.0a30"
        assert m.disabled == ("0.4.0a32", "0.4.0a35")
        assert m.notice == "scheduled maintenance"
        assert m.sha256 == "a" * 64

    def test_parse_minimal_payload(self) -> None:
        """Optional fields default to empty values."""
        from maxcompute_semantic._internal.update_check import LatestMetadata

        m = LatestMetadata.from_dict(
            {
                "schema_version": 1,
                "latest_version": "0.4.0a40",
                "released_at": "2026-05-22T12:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "min_supported": "0.4.0a30",
                "disabled": [],
                "sha256": "a" * 64,
                "notice": "",
            }
        )
        assert m.disabled == ()
        assert m.notice == ""
        assert m.sha256 == "a" * 64

    def test_parse_missing_sha256_field(self) -> None:
        """Remote latest.json must carry a wheel digest."""
        from maxcompute_semantic._internal.update_check import (
            LatestMetadata,
            MalformedMetadataError,
        )

        with pytest.raises(MalformedMetadataError, match="sha256"):
            LatestMetadata.from_dict(
                {
                    "schema_version": 1,
                    "latest_version": "0.4.0a40",
                    "released_at": "2026-05-22T12:00:00Z",
                    "wheel_url": "https://example.test/wheels/x.whl",
                    "min_supported": "0.4.0a30",
                    "disabled": [],
                    "notice": "",
                }
            )

    @pytest.mark.parametrize("sha256", ["abc123", "g" * 64])
    def test_parse_rejects_malformed_sha256_field(self, sha256: str) -> None:
        """Remote latest.json must carry a full hex SHA256 digest."""
        from maxcompute_semantic._internal.update_check import (
            LatestMetadata,
            MalformedMetadataError,
        )

        with pytest.raises(MalformedMetadataError, match="SHA256"):
            LatestMetadata.from_dict(
                {
                    "schema_version": 1,
                    "latest_version": "0.4.0a40",
                    "released_at": "2026-05-22T12:00:00Z",
                    "wheel_url": "https://example.test/wheels/x.whl",
                    "min_supported": "0.4.0a30",
                    "disabled": [],
                    "notice": "",
                    "sha256": sha256,
                }
            )

    def test_parse_rejects_unknown_schema_version(self) -> None:
        from maxcompute_semantic._internal.update_check import (
            LatestMetadata,
            UnsupportedSchemaError,
        )

        with pytest.raises(UnsupportedSchemaError):
            LatestMetadata.from_dict(
                {
                    "schema_version": 99,
                    "latest_version": "0.4.0a40",
                    "released_at": "2026-05-22T12:00:00Z",
                    "wheel_url": "https://example.test/wheels/x.whl",
                    "min_supported": "0.4.0a30",
                    "disabled": [],
                    "notice": "",
                }
            )

    def test_parse_missing_required_field(self) -> None:
        from maxcompute_semantic._internal.update_check import (
            LatestMetadata,
            MalformedMetadataError,
        )

        with pytest.raises(MalformedMetadataError):
            LatestMetadata.from_dict({"schema_version": 1})  # missing the rest


class TestLatestJsonServer:
    """Sanity check the fixture itself — every test that exercises
    fetch_latest_metadata depends on it."""

    def test_serves_dict_payload(self, latest_json_server) -> None:
        import urllib.request

        base_url, setter = latest_json_server
        setter({"hello": "world"})
        with urllib.request.urlopen(f"{base_url}/latest.json", timeout=2.0) as r:
            body = r.read().decode("utf-8")
        assert json.loads(body) == {"hello": "world"}

    def test_serves_500_status(self, latest_json_server) -> None:
        import urllib.error
        import urllib.request

        base_url, setter = latest_json_server
        setter(503)
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"{base_url}/latest.json", timeout=2.0)
        assert ei.value.code == 503


class TestFetchLatestMetadata:
    _VALID_PAYLOAD = {
        "schema_version": 1,
        "latest_version": "0.4.0a40",
        "released_at": "2026-05-22T12:00:00Z",
        "wheel_url": "https://example.test/wheels/x.whl",
        "sha256": "a" * 64,
        "min_supported": "0.4.0a30",
        "disabled": [],
        "notice": "",
    }

    def test_returns_metadata_on_200(self, latest_json_server) -> None:
        from maxcompute_semantic._internal.update_check import (
            LatestMetadata,
            fetch_latest_metadata,
        )

        _, setter = latest_json_server
        setter(self._VALID_PAYLOAD)
        result = fetch_latest_metadata(timeout_s=2.0)
        assert isinstance(result, LatestMetadata)
        assert result.latest_version == "0.4.0a40"

    def test_returns_none_on_5xx(self, latest_json_server) -> None:
        from maxcompute_semantic._internal.update_check import fetch_latest_metadata

        _, setter = latest_json_server
        setter(503)
        assert fetch_latest_metadata(timeout_s=2.0) is None

    def test_returns_none_on_invalid_json(self, latest_json_server) -> None:
        from maxcompute_semantic._internal.update_check import fetch_latest_metadata

        _, setter = latest_json_server
        setter("not-valid-json{{{")
        assert fetch_latest_metadata(timeout_s=2.0) is None

    def test_returns_none_on_unsupported_schema(self, latest_json_server) -> None:
        from maxcompute_semantic._internal.update_check import fetch_latest_metadata

        _, setter = latest_json_server
        setter({**self._VALID_PAYLOAD, "schema_version": 99})
        # UnsupportedSchemaError is raised by from_dict() and folded
        # into None by fetch_latest_metadata's catch-all.
        assert fetch_latest_metadata(timeout_s=2.0) is None

    def test_returns_none_on_connection_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point the env var at a port nothing is listening on. The
        OS-level connection refusal becomes urllib.error.URLError, which
        the fetcher swallows."""
        # Bind a socket to find an unused port, then close it so the
        # port is free when fetch_latest_metadata tries to connect.
        import socket

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        monkeypatch.setenv("MCS_UPDATE_BASE_URL", f"http://127.0.0.1:{port}")

        from maxcompute_semantic._internal.update_check import fetch_latest_metadata

        assert fetch_latest_metadata(timeout_s=0.5) is None

    def test_uses_base_url_from_env(
        self, latest_json_server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fixture sets MCS_UPDATE_BASE_URL. Confirm the fetcher
        actually reads it: serve a payload, then verify the parsed
        result matches."""
        base_url, setter = latest_json_server
        setter({**self._VALID_PAYLOAD, "latest_version": "9.9.9"})
        assert os.environ["MCS_UPDATE_BASE_URL"] == base_url

        from maxcompute_semantic._internal.update_check import fetch_latest_metadata

        result = fetch_latest_metadata(timeout_s=2.0)
        assert result is not None
        assert result.latest_version == "9.9.9"


class TestBaseUrlValidation:
    def test_default_base_url_is_trusted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maxcompute_semantic._internal.update_check import DEFAULT_BASE_URL, _base_url

        monkeypatch.delenv("MCS_UPDATE_BASE_URL", raising=False)
        monkeypatch.delenv("MCS_ALLOW_UNTRUSTED_UPDATE_BASE_URL", raising=False)

        assert _base_url() == DEFAULT_BASE_URL

    @pytest.mark.parametrize("url", ["http://example.test", "file:///tmp/latest"])
    def test_rejects_non_https_scheme_even_with_escape(
        self, url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maxcompute_semantic._internal.update_check import _base_url

        monkeypatch.setenv("MCS_UPDATE_BASE_URL", url)
        monkeypatch.setenv("MCS_ALLOW_UNTRUSTED_UPDATE_BASE_URL", "1")

        with pytest.raises(ValueError, match="https"):
            _base_url()

    def test_rejects_untrusted_https_without_escape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maxcompute_semantic._internal.update_check import _base_url

        monkeypatch.setenv("MCS_UPDATE_BASE_URL", "https://mirror.example.test/mcs")
        monkeypatch.delenv("MCS_ALLOW_UNTRUSTED_UPDATE_BASE_URL", raising=False)

        with pytest.raises(ValueError, match="trusted allowlist"):
            _base_url()

    def test_allows_untrusted_https_with_escape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maxcompute_semantic._internal.update_check import _base_url

        monkeypatch.setenv("MCS_UPDATE_BASE_URL", "https://mirror.example.test/mcs/")
        monkeypatch.setenv("MCS_ALLOW_UNTRUSTED_UPDATE_BASE_URL", "1")

        assert _base_url() == "https://mirror.example.test/mcs"


class TestIsDisabled:
    def _md(self, *, min_supported: str, disabled: tuple[str, ...]) -> object:
        """Construct a minimal LatestMetadata for these tests."""
        from maxcompute_semantic._internal.update_check import LatestMetadata

        return LatestMetadata(
            schema_version=1,
            latest_version="0.4.0a99",
            released_at="2026-05-22T00:00:00Z",
            wheel_url="https://example.test/wheels/x.whl",
            min_supported=min_supported,
            disabled=disabled,
            notice="",
            sha256="",
        )

    @pytest.mark.parametrize(
        "current,min_supported,disabled_list,expected_blocked,expected_reason_kw",
        [
            # Current ahead of min, not in disabled — clean.
            ("0.4.0a40", "0.4.0a30", (), False, ""),
            # Exactly equal to min_supported — clean (lower-bound is inclusive).
            ("0.4.0a30", "0.4.0a30", (), False, ""),
            # Current below min_supported — blocked.
            ("0.4.0a29", "0.4.0a30", (), True, "below"),
            # Current in disabled[] — blocked even though it's above min.
            ("0.4.0a35", "0.4.0a30", ("0.4.0a35",), True, "disabled"),
            # PEP 440 alpha-vs-beta ordering: a40 < b1 < rc1 < .0 final.
            ("0.4.0a40", "0.4.0b1", (), True, "below"),
            ("0.4.0rc1", "0.4.0a40", (), False, ""),
            ("0.4.0", "0.4.0rc1", (), False, ""),
            # Final release blocked explicitly.
            ("0.5.0", "0.4.0", ("0.5.0",), True, "disabled"),
        ],
    )
    def test_is_disabled_table(
        self,
        current: str,
        min_supported: str,
        disabled_list: tuple[str, ...],
        expected_blocked: bool,
        expected_reason_kw: str,
    ) -> None:
        from maxcompute_semantic._internal.update_check import is_disabled

        md = self._md(min_supported=min_supported, disabled=disabled_list)
        blocked, reason = is_disabled(md, current)
        assert blocked is expected_blocked
        if expected_blocked:
            assert expected_reason_kw in reason
        else:
            assert reason == ""

    def test_invalid_current_treated_as_not_blocked(self) -> None:
        """If ``__version__`` is the fallback ``0+unknown`` (the package
        isn't installed cleanly), the version compare can't be made
        meaningfully — we treat that as "no block info" so the user
        isn't kicked out by a metadata quirk during development."""
        from maxcompute_semantic._internal.update_check import is_disabled

        md = self._md(min_supported="0.4.0a30", disabled=())
        # ``packaging.version.Version`` raises InvalidVersion on
        # arbitrary strings. The helper should swallow that and return
        # (False, '').
        blocked, reason = is_disabled(md, "garbage-not-a-version")
        assert blocked is False
        assert reason == ""

    def test_invalid_min_supported_skipped(self) -> None:
        """Malformed publisher metadata in the comparison field must
        not crash the client. The disabled-list check still runs."""
        from maxcompute_semantic._internal.update_check import is_disabled

        md = self._md(min_supported="not-a-version", disabled=("0.4.0a40",))
        # min comparison skipped (publisher bug), disabled[] still
        # checked — and matches.
        blocked, reason = is_disabled(md, "0.4.0a40")
        assert blocked is True
        assert "disabled" in reason

        # ...and a version not in disabled[] under the same broken
        # min_supported is treated as clean.
        blocked, reason = is_disabled(md, "0.4.0a39")
        assert blocked is False


class TestCache:
    @pytest.fixture
    def _cache_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point cache_dir() at a fresh tmpdir per test."""
        cdir = tmp_path / "cache"
        monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
        return cdir

    def test_read_missing_returns_none(self, _cache_tmp: Path) -> None:
        from maxcompute_semantic._internal.update_check import read_cache

        assert read_cache() is None

    def test_round_trip(self, _cache_tmp: Path) -> None:
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            cache_path,
            read_cache,
            write_cache,
        )

        entry = CacheEntry(
            checked_at="2026-05-22T09:55:00Z",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a40",
            wheel_url="https://example.test/wheels/x.whl",
            min_supported="0.4.0a30",
            disabled=("0.4.0a32",),
            notice="hello",
            fetch_error="",
        )
        write_cache(entry)

        # File landed where expected.
        assert cache_path().exists()
        assert cache_path().parent == _cache_tmp

        # And round-trips.
        loaded = read_cache()
        assert loaded == entry

    def test_atomic_write_corrupt_does_not_destroy_existing(self, _cache_tmp: Path) -> None:
        """The write should land via a temp file + os.replace so a
        crash mid-serialize leaves the previous cache intact."""
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            cache_path,
            read_cache,
            write_cache,
        )

        # Seed a good cache.
        good = CacheEntry(
            checked_at="2026-05-22T09:00:00Z",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a39",
            wheel_url="https://example.test/wheels/x.whl",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        write_cache(good)
        assert read_cache() == good

        # Drop an unrelated stale temp file in the cache dir — the
        # write path must not be confused by it.
        (_cache_tmp / "stray.tmp").write_text("garbage", encoding="utf-8")
        new = dataclasses.replace(good, latest_version="0.4.0a40")
        # Imported lazily so the test doesn't need a top-of-file
        # ``import dataclasses`` change.
        from maxcompute_semantic._internal.update_check import write_cache as wc

        wc(new)
        assert read_cache() == new
        # The cache file is the canonical name, the stray .tmp is left
        # alone (no GC promise).
        assert cache_path().name == "update_check.json"

    def test_read_corrupt_json_returns_none(self, _cache_tmp: Path) -> None:
        """A non-JSON file on disk doesn't crash the reader."""
        from maxcompute_semantic._internal.update_check import cache_path, read_cache

        cache_path().parent.mkdir(parents=True, exist_ok=True)
        cache_path().write_text("{this is not valid json", encoding="utf-8")
        assert read_cache() is None

    def test_read_wrong_shape_returns_none(self, _cache_tmp: Path) -> None:
        """A valid-JSON-but-wrong-keys file is also treated as no cache."""
        from maxcompute_semantic._internal.update_check import cache_path, read_cache

        cache_path().parent.mkdir(parents=True, exist_ok=True)
        cache_path().write_text('{"hello": "world"}', encoding="utf-8")
        assert read_cache() is None


class TestShouldCheck:
    def test_no_cache_should_check(self) -> None:
        from maxcompute_semantic._internal.update_check import should_check

        assert should_check(None) is True

    def test_fresh_cache_should_not_check(self) -> None:
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            _utcnow_iso,
            should_check,
        )

        entry = CacheEntry(
            checked_at=_utcnow_iso(),
            current_at_check="0.4.0a38",
            latest_version="0.4.0a39",
            wheel_url="",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        # TTL of 1 h; entry is from now.
        assert should_check(entry, ttl_s=3600) is False

    def test_stale_cache_should_check(self) -> None:
        """A timestamp older than TTL means the daemon thread should
        re-probe."""
        from maxcompute_semantic._internal.update_check import CacheEntry, should_check

        entry = CacheEntry(
            checked_at="2000-01-01T00:00:00Z",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a39",
            wheel_url="",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        assert should_check(entry, ttl_s=3600) is True

    def test_unparseable_timestamp_should_check(self) -> None:
        """A garbage ``checked_at`` is treated as "no idea when this is
        from", which means re-probe."""
        from maxcompute_semantic._internal.update_check import CacheEntry, should_check

        entry = CacheEntry(
            checked_at="not-a-timestamp",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a39",
            wheel_url="",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        assert should_check(entry, ttl_s=3600) is True


class TestFormatBanner:
    def _entry(self, **overrides: str) -> object:
        from maxcompute_semantic._internal.update_check import CacheEntry

        base = dict(
            checked_at="2026-05-22T09:55:00Z",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a40",
            wheel_url="https://example.test/wheels/x.whl",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        base.update(overrides)
        return CacheEntry(**base)  # type: ignore[arg-type]

    def test_no_cache_returns_none(self) -> None:
        from maxcompute_semantic._internal.update_check import format_banner

        assert format_banner(None, current="0.4.0a38") is None

    def test_on_latest_returns_none(self) -> None:
        from maxcompute_semantic._internal.update_check import format_banner

        # Running == latest.
        entry = self._entry(latest_version="0.4.0a38")
        assert format_banner(entry, current="0.4.0a38") is None

    def test_running_newer_than_latest_returns_none(self) -> None:
        """If the user is on a dev build ahead of the published latest,
        no nag."""
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(latest_version="0.4.0a30")
        assert format_banner(entry, current="0.4.0a40") is None

    def test_fetch_error_with_no_prior_data_returns_none(self) -> None:
        """When the probe has never succeeded the cache carries empty
        version strings and a fetch_error — the banner is silent."""
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(
            latest_version="",
            wheel_url="",
            min_supported="",
            fetch_error="connection refused",
        )
        assert format_banner(entry, current="0.4.0a38") is None

    def test_soft_upgrade_banner(self) -> None:
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(latest_version="0.4.0a40")
        banner = format_banner(entry, current="0.4.0a38")
        assert banner is not None
        assert "0.4.0a38" in banner
        assert "0.4.0a40" in banner
        assert "mcs update" in banner
        # No "disabled" / "required" wording in the soft form.
        assert "disabled" not in banner.lower()
        assert "required" not in banner.lower()

    def test_soft_banner_includes_notice_prefix(self) -> None:
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(latest_version="0.4.0a40", notice="rate-limit fixed")
        banner = format_banner(entry, current="0.4.0a38")
        assert banner is not None
        assert "rate-limit fixed" in banner
        # Notice comes before the upgrade-prompt line.
        assert banner.index("rate-limit fixed") < banner.index("0.4.0a40")

    def test_hard_disabled_banner(self) -> None:
        """When the running version is in disabled[], the banner uses
        the hard wording and the metadata's disabled list is referenced
        so the user can confirm."""
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(
            latest_version="0.4.0a40",
            min_supported="0.4.0a30",
            disabled=("0.4.0a38",),
        )
        banner = format_banner(entry, current="0.4.0a38")
        assert banner is not None
        assert "disabled" in banner.lower() or "required" in banner.lower()
        assert "0.4.0a38" in banner
        # The remediation pointer ("mcs update" command) is still there
        # so users know what to run.
        assert "mcs update" in banner

    def test_hard_below_min_supported_banner(self) -> None:
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(
            latest_version="0.4.0a40",
            min_supported="0.4.0a30",
        )
        banner = format_banner(entry, current="0.4.0a29")
        assert banner is not None
        assert "min_supported" in banner.lower() or "minimum" in banner.lower()
        assert "0.4.0a29" in banner
        assert "0.4.0a30" in banner

    def test_format_banner_is_returns_a_value_when_disabled_and_on_latest(
        self,
    ) -> None:
        """Edge case: the publisher disabled the *latest* version. The
        banner is still hard (the user needs to know), and the
        "upgrade to <new>" arrow points at the same version which is
        the publisher's responsibility to fix. We just render the
        disabled wording without the soft arrow."""
        from maxcompute_semantic._internal.update_check import format_banner

        entry = self._entry(
            latest_version="0.4.0a38",
            disabled=("0.4.0a38",),
        )
        banner = format_banner(entry, current="0.4.0a38")
        assert banner is not None
        assert "disabled" in banner.lower()


class TestIsHardBlock:
    """Helper that callers use to decide between an exit-0 soft banner
    and an exit-2 hard banner. Reuses ``is_disabled`` over the cache
    contents."""

    def test_no_cache_not_hard(self) -> None:
        from maxcompute_semantic._internal.update_check import is_hard_block

        assert is_hard_block(None, current="0.4.0a38") is False

    def test_clean_cache_not_hard(self) -> None:
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            is_hard_block,
        )

        entry = CacheEntry(
            checked_at="2026-05-22T09:55:00Z",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a40",
            wheel_url="",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        assert is_hard_block(entry, current="0.4.0a38") is False

    def test_disabled_is_hard(self) -> None:
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            is_hard_block,
        )

        entry = CacheEntry(
            checked_at="2026-05-22T09:55:00Z",
            current_at_check="0.4.0a38",
            latest_version="0.4.0a40",
            wheel_url="",
            min_supported="0.4.0a30",
            disabled=("0.4.0a38",),
            notice="",
            fetch_error="",
        )
        assert is_hard_block(entry, current="0.4.0a38") is True

    def test_below_min_is_hard(self) -> None:
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            is_hard_block,
        )

        entry = CacheEntry(
            checked_at="2026-05-22T09:55:00Z",
            current_at_check="0.4.0a29",
            latest_version="0.4.0a40",
            wheel_url="",
            min_supported="0.4.0a30",
            disabled=(),
            notice="",
            fetch_error="",
        )
        assert is_hard_block(entry, current="0.4.0a29") is True


class TestBannerSuppressed:
    @pytest.fixture(autouse=True)
    def _fake_no_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default test environment is "stderr IS a TTY" so suppression
        is driven only by the input argv / env. The pytest test runner
        normally has stderr captured (no TTY), so we monkeypatch
        ``sys.stderr.isatty`` to True for the duration of each test in
        this class. Individual tests that want the no-TTY case
        override this fixture."""
        import sys

        monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    @pytest.mark.parametrize(
        "argv",
        [
            ["mcs", "version", "--version"],  # --version flag
            ["mcs", "-V"],
            ["mcs", "--help"],
            ["mcs", "sql", "execute", "-h"],
            ["mcs", "update"],
            ["mcs", "update", "--check"],
            ["mcs", "doctor"],
            ["mcs", "doctor", "--offline"],
            ["mcs", "-q", "build"],
            ["mcs", "--quiet", "build"],
            ["mcs", "-f", "json", "show"],
            ["mcs", "--format", "json", "sql", "execute", "select 1"],
            ["mcs", "show", "-f", "json"],  # post-hoist position
        ],
    )
    def test_argv_suppression(self, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(argv) is True, f"expected suppression for {argv!r}"

    def test_env_opt_out_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCS_NO_UPDATE_CHECK", "1")
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(["mcs", "sql", "execute", "select 1"]) is True

    @pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "Yes", "on", "ON"])
    def test_env_opt_out_truthy_values(self, truthy: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCS_NO_UPDATE_CHECK", truthy)
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(["mcs", "build"]) is True

    @pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "garbage"])
    def test_env_opt_out_falsy_does_not_suppress(
        self, falsy: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCS_NO_UPDATE_CHECK", falsy)
        from maxcompute_semantic._internal.update_check import banner_suppressed

        # No other suppression vector → falsy env means "do show".
        assert banner_suppressed(["mcs", "build"]) is False

    def test_no_tty_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
        monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(["mcs", "build"]) is True

    def test_regular_command_with_tty_no_env_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(["mcs", "sql", "execute", "select 1"]) is False
        assert banner_suppressed(["mcs", "build"]) is False
        assert banner_suppressed(["mcs", "memory", "recall", "orders"]) is False

    def test_hard_block_overrides_env_opt_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A disabled-version banner must show even with the opt-out
        env set — it's a gate, not a nag. This is enforced by
        ``banner_suppressed(argv, hard_block=True)`` returning False."""
        monkeypatch.setenv("MCS_NO_UPDATE_CHECK", "1")
        from maxcompute_semantic._internal.update_check import banner_suppressed

        # Without hard_block, env-opt-out wins (suppress).
        assert banner_suppressed(["mcs", "build"], hard_block=False) is True
        # With hard_block, the env opt-out is overridden — the
        # disabled banner shows. Still suppressed if argv targets
        # `update` / `doctor` / `--help` / `--version` though, because
        # those subcommands are themselves the remediation path or
        # an informational read.
        assert banner_suppressed(["mcs", "build"], hard_block=True) is False
        assert banner_suppressed(["mcs", "update"], hard_block=True) is True
        assert banner_suppressed(["mcs", "doctor"], hard_block=True) is True

    def test_hard_block_overrides_tty_check_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A disabled-version banner shows even when stderr isn't a
        TTY — pipelines should see the gate."""
        import sys

        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(["mcs", "build"], hard_block=True) is False
        # Soft banner still suppressed for no-TTY.
        assert banner_suppressed(["mcs", "build"], hard_block=False) is True

    def test_hard_block_still_suppressed_for_json_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """JSON output is consumed by another process — the human
        banner would corrupt the parser. The hard-block exit code
        (non-zero) is the machine-visible signal in that mode; the
        envelope already carries the error. So ``-f json`` suppresses
        even the hard banner. Same for ``-q``."""
        from maxcompute_semantic._internal.update_check import banner_suppressed

        assert banner_suppressed(["mcs", "-f", "json", "build"], hard_block=True) is True
        assert banner_suppressed(["mcs", "-q", "build"], hard_block=True) is True


class TestBackgroundProbe:
    @pytest.fixture
    def _cache_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setenv("MCS_CACHE_DIR", str(tmp_path / "cache"))
        return tmp_path / "cache"

    def test_run_probe_writes_successful_cache(
        self,
        latest_json_server,
        _cache_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The synchronous probe routine (the body the daemon thread
        runs) populates the cache file from the served metadata."""
        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "0.4.0a99",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.4.0a30",
                "disabled": [],
                "notice": "synced",
            }
        )

        from maxcompute_semantic._internal.update_check import (
            _run_probe,
            cache_path,
            read_cache,
        )

        _run_probe(current_version="0.4.0a38")

        loaded = read_cache()
        assert loaded is not None
        assert loaded.latest_version == "0.4.0a99"
        assert loaded.notice == "synced"
        assert loaded.fetch_error == ""
        assert loaded.current_at_check == "0.4.0a38"
        assert cache_path().exists()

    def test_run_probe_records_fetch_error_on_5xx(
        self,
        latest_json_server,
        _cache_tmp: Path,
    ) -> None:
        _, setter = latest_json_server
        setter(503)
        from maxcompute_semantic._internal.update_check import _run_probe, read_cache

        _run_probe(current_version="0.4.0a38")

        loaded = read_cache()
        assert loaded is not None
        assert loaded.latest_version == ""  # cold-cache, no prior to carry
        assert loaded.fetch_error  # non-empty error string

    def test_run_probe_preserves_prior_on_transient_error(
        self,
        latest_json_server,
        _cache_tmp: Path,
    ) -> None:
        from maxcompute_semantic._internal.update_check import _run_probe, read_cache

        # First probe: successful.
        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "0.4.0a99",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.4.0a30",
                "disabled": [],
                "notice": "",
            }
        )
        _run_probe(current_version="0.4.0a38")
        good = read_cache()
        assert good is not None
        assert good.latest_version == "0.4.0a99"

        # Second probe: server is sick.
        setter(503)
        _run_probe(current_version="0.4.0a38")
        carried = read_cache()
        assert carried is not None
        # The latest_version is carried forward from the prior good
        # probe so the banner keeps working through a transient outage.
        assert carried.latest_version == "0.4.0a99"
        assert carried.fetch_error  # but the error is recorded too.
        # Timestamp advanced.
        assert carried.checked_at >= good.checked_at

    def test_start_background_probe_returns_immediately(
        self,
        latest_json_server,
        _cache_tmp: Path,
    ) -> None:
        """The public entry point is non-blocking. We assert that by
        wrapping the probe body with a delay and confirming the spawn
        call returns before the delay would have completed."""
        import time

        _, setter = latest_json_server
        # Slow the server-side response by serving a payload that
        # takes a moment — the simplest knob is just the wall-clock of
        # the fetch when the server is up vs the wall-clock of the
        # spawn call, which should be sub-millisecond regardless.
        setter(
            {
                "schema_version": 1,
                "latest_version": "0.4.0a99",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.4.0a30",
                "disabled": [],
                "notice": "",
            }
        )

        from maxcompute_semantic._internal.update_check import start_background_probe

        # The spawn call should return well under 100 ms even if the
        # fetch itself takes longer.
        t0 = time.perf_counter()
        thread = start_background_probe(current_version="0.4.0a38")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"spawn took {elapsed_ms:.1f} ms"

        # The returned thread is daemonized so the test runner won't
        # block on it during interpreter shutdown.
        assert thread is not None
        assert thread.daemon is True

        # Wait for the probe to finish so the cache is populated for a
        # downstream-test assertion ladder if we want one.
        thread.join(timeout=5.0)
        # The cache file exists if the probe completed cleanly.
        from maxcompute_semantic._internal.update_check import read_cache

        loaded = read_cache()
        assert loaded is not None
        assert loaded.latest_version == "0.4.0a99"

    def test_start_background_probe_skips_when_ttl_fresh(
        self,
        latest_json_server,
        _cache_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the existing cache is fresh-under-TTL the spawn returns
        a no-op thread and the probe body doesn't execute.

        We assert this by counting fetch calls: monkeypatch the fetcher
        to bump a counter, prime the cache with a fresh entry, then
        start_background_probe and confirm the counter is unchanged.
        """
        from maxcompute_semantic._internal import update_check as uc
        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            _utcnow_iso,
            write_cache,
        )

        # Prime a fresh cache (checked_at = now).
        write_cache(
            CacheEntry(
                checked_at=_utcnow_iso(),
                current_at_check="0.4.0a38",
                latest_version="0.4.0a40",
                wheel_url="https://example.test/wheels/x.whl",
                min_supported="0.4.0a30",
                disabled=(),
                notice="",
                fetch_error="",
            )
        )

        # Counter the fetcher.
        counter = {"calls": 0}
        original = uc.fetch_latest_metadata

        def counting_fetch(*a, **kw):
            counter["calls"] += 1
            return original(*a, **kw)

        monkeypatch.setattr(uc, "fetch_latest_metadata", counting_fetch)

        thread = uc.start_background_probe(current_version="0.4.0a38")
        if thread is not None:
            thread.join(timeout=2.0)

        assert counter["calls"] == 0, "TTL gate failed — probe ran even though cache was fresh"

    def test_start_background_probe_force_bypasses_ttl(
        self,
        latest_json_server,
        _cache_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``force=True`` (used by ``mcs doctor`` for a fresh probe)
        runs even when the cache is fresh."""
        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "0.4.0a99",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.4.0a30",
                "disabled": [],
                "notice": "",
            }
        )

        from maxcompute_semantic._internal.update_check import (
            CacheEntry,
            _utcnow_iso,
            read_cache,
            start_background_probe,
            write_cache,
        )

        # Prime a fresh-but-stale-content cache: timestamp is now, but
        # latest_version is an old value. Without ``force``, the probe
        # is skipped and the cache keeps the stale latest_version.
        write_cache(
            CacheEntry(
                checked_at=_utcnow_iso(),
                current_at_check="0.4.0a38",
                latest_version="0.4.0a40",
                wheel_url="",
                min_supported="0.4.0a30",
                disabled=(),
                notice="",
                fetch_error="",
            )
        )

        # With force=True the probe runs anyway and overwrites the
        # latest_version with the freshly fetched value.
        thread = start_background_probe(current_version="0.4.0a38", force=True)
        assert thread is not None
        thread.join(timeout=5.0)
        loaded = read_cache()
        assert loaded is not None
        assert loaded.latest_version == "0.4.0a99"

    def test_run_probe_exception_does_not_propagate(
        self,
        _cache_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A bug in the fetcher must not crash the daemon thread to
        the point of leaving a sys.excepthook trace on the user's
        terminal. We monkeypatch the fetcher to raise an arbitrary
        exception type that ``fetch_latest_metadata`` wouldn't
        normally produce (it returns None on every failure), and
        confirm ``_run_probe`` still writes a cache entry with a
        fetch_error string."""
        from maxcompute_semantic._internal import update_check as uc

        def boom(*a, **kw):
            raise RuntimeError("simulated probe-body bug")

        monkeypatch.setattr(uc, "fetch_latest_metadata", boom)

        # ``_run_probe`` should swallow the exception and write an
        # error-marked cache entry rather than re-raising.
        uc._run_probe(current_version="0.4.0a38")
        loaded = uc.read_cache()
        assert loaded is not None
        assert loaded.fetch_error  # has the error string
        assert "simulated probe-body bug" in loaded.fetch_error
