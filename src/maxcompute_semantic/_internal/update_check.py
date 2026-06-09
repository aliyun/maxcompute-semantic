"""Update-check probe, cache, and banner formatting.

The module's job is the *passive* version-awareness layer of mcs:

- ``fetch_latest_metadata`` synchronously pulls
  ``MCS_UPDATE_BASE_URL/latest.json`` (default
  ``https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com``) with a
  short timeout. Returns ``None`` on any failure so callers don't need
  exception handling.
- ``read_cache`` / ``write_cache`` / ``should_check`` manage a
  per-user JSON cache at ``cache_dir()/update_check.json`` so the
  network probe is amortized across mcs invocations (default TTL 6 h).
- ``start_background_probe`` spawns a daemon thread that runs the
  fetch and writes the cache. The main process can exit without
  waiting; if the probe finishes in time the next invocation's banner
  is fresh.
- ``format_banner`` renders the cache contents into the stderr line
  the user actually sees.
- ``banner_suppressed`` is the single decision point for "should we
  show the banner on this invocation" — TTY check, output format
  check, opt-out env var, suppressed-subcommand list.
- ``is_disabled`` answers "is the currently running mcs blocked from
  use by the publisher" — below ``min_supported`` or listed in
  ``disabled`` of the latest metadata.

The cache schema lives at module top so the producer (the daemon
probe) and the consumer (``cli.py`` finally block, ``commands/doctor.py``)
agree.

For the publish-side companion (``latest.json`` schema, the OSS upload
order, the disabled-versions CSV input), see the spec's
"Release pipeline" section.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from maxcompute_semantic._internal.paths import cache_dir

DEFAULT_BASE_URL = "https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com"
"""Hardcoded fallback. Override via ``MCS_UPDATE_BASE_URL`` env var.

The forward-compat seam: when mcs moves to PyPI, the env var points
``https://pypi.org`` and ``fetch_latest_metadata`` switches the
envelope adapter to the PyPI JSON shape (``{"info": {"version": ...},
"urls": [{"url": ...}]}``). The cache, banner, and install-mode
detection don't change — they all consume the normalized
``LatestMetadata``."""

_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "maxcompute-semantic.oss-cn-beijing.aliyuncs.com",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


_SCHEMA_VERSION = 1
"""The only ``latest.json`` schema_version this client understands.

The spec says: bump on incompatible changes. The consumer falls
through to ``None`` if it sees a higher schema_version so an old
client gracefully ignores a future publisher."""


class UpdateCheckError(Exception):
    """Base class for the two specific error types below."""


class UnsupportedSchemaError(UpdateCheckError):
    """``schema_version`` in the fetched JSON exceeds what this client
    understands. The caller treats this as "no metadata available."""

    def __init__(self, got: int, supported: int) -> None:
        super().__init__(
            f"latest.json schema_version={got} > supported={supported}; "
            f"ignoring metadata (please upgrade mcs)"
        )
        self.got = got
        self.supported = supported


class MalformedMetadataError(UpdateCheckError):
    """A required field is missing or wrong-typed."""

    def __init__(self, message: str) -> None:
        super().__init__(f"malformed latest.json: {message}")


def _require_str(d: dict[str, Any], key: str) -> str:
    """Pull a required string field, raising ``MalformedMetadataError`` on
    miss or wrong type."""
    if key not in d:
        raise MalformedMetadataError(f"missing required field '{key}'")
    val = d[key]
    if not isinstance(val, str):
        raise MalformedMetadataError(f"field '{key}' must be a string, got {type(val).__name__}")
    return val


def _require_int(d: dict[str, Any], key: str) -> int:
    if key not in d:
        raise MalformedMetadataError(f"missing required field '{key}'")
    val = d[key]
    if not isinstance(val, int) or isinstance(val, bool):
        raise MalformedMetadataError(f"field '{key}' must be an integer, got {type(val).__name__}")
    return val


def _require_str_list(d: dict[str, Any], key: str) -> tuple[str, ...]:
    if key not in d:
        raise MalformedMetadataError(f"missing required field '{key}'")
    val = d[key]
    if not isinstance(val, list):
        raise MalformedMetadataError(f"field '{key}' must be a list, got {type(val).__name__}")
    out: list[str] = []
    for i, x in enumerate(val):
        if not isinstance(x, str):
            raise MalformedMetadataError(
                f"field '{key}[{i}]' must be a string, got {type(x).__name__}"
            )
        out.append(x)
    return tuple(out)


def _optional_str(d: dict[str, Any], key: str, default: str = "") -> str:
    val = d.get(key, default)
    if not isinstance(val, str):
        raise MalformedMetadataError(f"field '{key}' must be a string, got {type(val).__name__}")
    return val


def _require_sha256(d: dict[str, Any], key: str) -> str:
    val = _require_str(d, key)
    if not _SHA256_RE.fullmatch(val):
        raise MalformedMetadataError(f"field '{key}' must be a 64-character hex SHA256")
    return val.lower()


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclasses.dataclass(frozen=True)
class LatestMetadata:
    """Parsed ``latest.json`` body. Frozen so caches can't accidentally
    mutate shared instances."""

    schema_version: int
    latest_version: str
    released_at: str
    wheel_url: str
    min_supported: str
    disabled: tuple[str, ...]
    notice: str
    sha256: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LatestMetadata:
        """Validating constructor. Raises ``UnsupportedSchemaError`` if
        the publisher emitted a newer schema than we know, or
        ``MalformedMetadataError`` if a required field is missing or
        the wrong type. ``notice`` defaults to empty if absent."""
        sv = _require_int(d, "schema_version")
        if sv != _SCHEMA_VERSION:
            # The spec carves out forward-compatibility: an old client
            # that sees a newer schema must ignore the metadata
            # entirely. We treat lower schema_versions the same way
            # (the publisher is broken if it sends 0, but the client
            # is the wrong place to fix that).
            raise UnsupportedSchemaError(got=sv, supported=_SCHEMA_VERSION)
        return cls(
            schema_version=sv,
            latest_version=_require_str(d, "latest_version"),
            released_at=_require_str(d, "released_at"),
            wheel_url=_require_str(d, "wheel_url"),
            min_supported=_require_str(d, "min_supported"),
            disabled=_require_str_list(d, "disabled"),
            notice=_optional_str(d, "notice", ""),
            sha256=_require_sha256(d, "sha256"),
        )

    def to_json(self) -> str:
        """Round-trippable serialization for the cache file."""
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "latest_version": self.latest_version,
            "released_at": self.released_at,
            "wheel_url": self.wheel_url,
            "min_supported": self.min_supported,
            "disabled": list(self.disabled),
            "notice": self.notice,
        }
        d["sha256"] = self.sha256
        return json.dumps(d, ensure_ascii=False, sort_keys=True)


def _base_url() -> str:
    """Return the metadata base URL, env-overridable. Trailing slash is
    stripped so callers can do ``base + '/latest.json'`` uniformly.

    Validates scheme (must be https) and host (must be in allowlist).
    Raises ValueError if the URL is not trusted.
    """
    raw = os.environ.get("MCS_UPDATE_BASE_URL", DEFAULT_BASE_URL)
    url = raw.rstrip("/")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"MCS_UPDATE_BASE_URL must use https (got {parsed.scheme}://); "
            f"refusing to fetch metadata over insecure transport"
        )
    if parsed.hostname not in _ALLOWED_HOSTS and not _truthy_env(
        "MCS_ALLOW_UNTRUSTED_UPDATE_BASE_URL"
    ):
        raise ValueError(
            f"MCS_UPDATE_BASE_URL host {parsed.hostname!r} is not in the "
            f"trusted allowlist {sorted(_ALLOWED_HOSTS)}; unset it to use the default "
            f"or set MCS_ALLOW_UNTRUSTED_UPDATE_BASE_URL=1 for a deliberate HTTPS mirror"
        )
    return url


def fetch_latest_metadata(timeout_s: float = 5.0) -> LatestMetadata | None:
    """Synchronously fetch and parse ``<base>/latest.json``.

    Returns the parsed ``LatestMetadata`` on success, or ``None`` on
    any failure: DNS resolution, connection refused, non-2xx HTTP
    status, JSON parse error, unsupported schema_version, missing or
    wrong-typed required fields. The single ``None`` return means the
    daemon-thread caller (``start_background_probe``) does not need
    exception handling — it just decides whether the cache gets a
    ``fetch_error`` or a real value.

    The ``timeout_s`` bounds the total wall-clock time. Default 5 s
    matches a1's update-check default and is well under any plausible
    interactive-prompt blink. The TLS handshake counts toward the
    timeout because urllib's timeout applies to the whole connection
    lifecycle once the request starts.

    The base URL comes from ``MCS_UPDATE_BASE_URL`` (default
    ``DEFAULT_BASE_URL``); see ``_base_url``. We intentionally don't
    use ``requests`` here — stdlib ``urllib.request`` is one less
    dependency in the wheel, and the surface is one GET with no auth.

    The fetch_error string isn't surfaced from this function — the
    caller (``_run_probe`` in the daemon thread, ``_check_update_*``
    in doctor) decides what string to record in the cache or report to
    the user. This function's contract is the strict "metadata or
    nothing" boundary.
    """
    import urllib.error
    import urllib.request

    try:
        url = f"{_base_url()}/latest.json"
        # urlopen's ``timeout`` argument covers both connect and read.
        # The default global socket timeout is None (block forever) on
        # Python, so this argument is the only thing standing between
        # mcs and an unbounded hang if OSS becomes unreachable mid-TCP.
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            # urlopen raises HTTPError for >=400, so by here status is
            # 2xx. We still defensively read up to a sane cap so a
            # misconfigured OSS object serving a multi-GB file can't
            # OOM us. 64 KiB is comfortably larger than any plausible
            # latest.json (~250 bytes).
            raw = resp.read(64 * 1024)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        # URLError covers DNS, connection refused, TLS handshake fail,
        # HTTP non-2xx (HTTPError is a URLError subclass), and read
        # timeout. OSError catches the socket layer. TimeoutError is
        # the explicit timeout case on 3.10+. ValueError catches malformed
        # URLs (unlikely given we control the base, but cheap to guard).
        return None

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None

    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    try:
        return LatestMetadata.from_dict(parsed)
    except UpdateCheckError:
        # Covers both UnsupportedSchemaError and MalformedMetadataError —
        # the daemon thread should not crash because the publisher
        # rolled out a bad latest.json. The doctor checks below
        # distinguish "no metadata" (network) from "bad metadata"
        # (publisher) by re-issuing the fetch and inspecting the
        # exception path. Banner stays silent in both cases.
        return None


def is_disabled(metadata: LatestMetadata, current: str) -> tuple[bool, str]:
    """Decide whether the running version ``current`` is blocked from
    use by ``metadata``.

    Returns ``(blocked, reason)``. When ``blocked`` is True, ``reason``
    is a short human-readable string used directly in the hard banner
    ("running disabled mcs version 0.4.0a35 — upgrade required" /
    "running mcs 0.4.0a29 < min_supported 0.4.0a30"). When ``blocked``
    is False, ``reason`` is the empty string.

    Block conditions:

      1. ``current`` parses as a PEP 440 ``Version`` and
         ``Version(current) < Version(metadata.min_supported)``.
         Failure to parse either string (publisher quirk, or the
         in-development fallback ``__version__ = "0+unknown"``)
         silently skips the comparison — we don't block users on
         metadata oddities.

      2. ``current`` is a string-equal member of
         ``metadata.disabled``. The disabled list is a hard exact-match
         denylist; the publisher writes it for specific bad releases.
         No version-string normalization is applied here (so the
         publisher must write the exact pyproject.toml string).

    The two checks are independent — a version below min_supported
    AND in the disabled list is still blocked once, with the
    min_supported message winning (it's the more specific cause).
    See spec §"Error handling" for the banner-rendering contract that
    consumes this output.
    """
    from packaging.version import InvalidVersion, Version

    # Disabled-list check is exact-string, so it tolerates a malformed
    # ``min_supported`` field (the publisher might ship a bad
    # ``min_supported`` while still publishing a valid ``disabled[]``,
    # and the disabled[] block should still take effect).
    in_disabled = current in metadata.disabled

    try:
        cur_v = Version(current)
        min_v = Version(metadata.min_supported)
        below_min = cur_v < min_v
    except InvalidVersion:
        # Either ``current`` is the unknown-version fallback
        # ("0+unknown" — see ``maxcompute_semantic/__init__.py``) or
        # the publisher's ``min_supported`` is malformed. In either
        # case the version-ordering signal is unreliable so we don't
        # block on it. The disabled-list signal still applies because
        # it's pure string equality.
        below_min = False

    # min_supported is reported in preference to disabled when both
    # match — the floor message is more actionable ("upgrade past
    # 0.4.0a30") than the per-version block ("0.4.0a35 is broken").
    if below_min:
        return True, (
            f"running mcs {current} is below min_supported "
            f"{metadata.min_supported}; upgrade required"
        )
    if in_disabled:
        return True, (
            f"mcs {current} is in the disabled list {list(metadata.disabled)!r}; upgrade required"
        )
    return False, ""


_CACHE_DEFAULT_TTL_S = 6 * 60 * 60  # 6 hours
"""Match a1's update-check cadence. The TTL is a budget on network
chatter, not a freshness requirement — banner is "best effort recent"."""


def _utcnow_iso() -> str:
    """ISO-8601 UTC second-precision timestamp string.

    ``datetime.now(tz=UTC).isoformat()`` gives microsecond precision and
    a ``+00:00`` suffix; we trim to seconds and use the ``Z`` suffix to
    match the spec's ``released_at`` format. The format is just a
    convention — readers parse via ``datetime.fromisoformat`` and
    tolerate either spelling."""
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(s: str) -> _dt.datetime | None:
    """Parse the few ISO-8601 spellings we care about. Returns ``None``
    on anything that ``datetime.fromisoformat`` chokes on (so the
    "garbage timestamp" code path falls through to "we don't know how
    old this is, treat it as expired")."""
    # ``Z`` isn't accepted by fromisoformat before Python 3.11; swap it
    # for the equivalent ``+00:00`` to keep 3.10 happy. (We declare
    # ``requires-python = ">=3.10"``.)
    candidate = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        return _dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None


def cache_path() -> Path:
    """Absolute path to the JSON cache file. Parent dir may not exist
    yet — ``write_cache`` is responsible for creating it. ``read_cache``
    tolerates absence by returning ``None``."""
    return cache_dir() / "update_check.json"


@dataclasses.dataclass(frozen=True)
class CacheEntry:
    """The on-disk cache shape. Mirror of the wire ``LatestMetadata``
    plus diagnostic fields:

      * ``checked_at`` — timestamp of the probe that wrote this entry.
        Used by ``should_check`` to decide whether the entry is fresh.
      * ``current_at_check`` — the running mcs version at the time the
        probe wrote the cache. The banner renderer compares the
        *currently running* version against ``latest_version`` rather
        than against this field, so this is purely diagnostic
        ("the cache was written when mcs was X").
      * ``fetch_error`` — empty on success, otherwise a short error
        string for the doctor channel-reachable check. When non-empty,
        the version fields are still populated from the *previous*
        successful probe (so the banner can still show a stale
        "upgrade available" hint if the network blip is transient).
        When the prior probe also failed and there's nothing useful
        to cache, the version fields are the empty string and the
        banner renderer skips the announcement.

    Frozen so the daemon thread's hand-off to the cache file is
    side-effect-free for the reader.
    """

    checked_at: str
    current_at_check: str
    latest_version: str
    wheel_url: str
    min_supported: str
    disabled: tuple[str, ...]
    notice: str
    fetch_error: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "checked_at": self.checked_at,
                "current_at_check": self.current_at_check,
                "latest_version": self.latest_version,
                "wheel_url": self.wheel_url,
                "min_supported": self.min_supported,
                "disabled": list(self.disabled),
                "notice": self.notice,
                "fetch_error": self.fetch_error,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> CacheEntry | None:
        """Returns ``None`` if the JSON is malformed or shape-wrong."""
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(d, dict):
            return None
        required = (
            "checked_at",
            "current_at_check",
            "latest_version",
            "wheel_url",
            "min_supported",
            "disabled",
            "notice",
            "fetch_error",
        )
        for k in required:
            if k not in d:
                return None
        if not isinstance(d["disabled"], list):
            return None
        if not all(isinstance(x, str) for x in d["disabled"]):
            return None
        for k in required:
            if k == "disabled":
                continue
            if not isinstance(d[k], str):
                return None
        return cls(
            checked_at=d["checked_at"],
            current_at_check=d["current_at_check"],
            latest_version=d["latest_version"],
            wheel_url=d["wheel_url"],
            min_supported=d["min_supported"],
            disabled=tuple(d["disabled"]),
            notice=d["notice"],
            fetch_error=d["fetch_error"],
        )

    @classmethod
    def from_fetch(
        cls,
        metadata: LatestMetadata,
        current_version: str,
    ) -> CacheEntry:
        """Construct a successful-probe cache entry from a freshly
        fetched ``LatestMetadata`` and the running mcs version."""
        return cls(
            checked_at=_utcnow_iso(),
            current_at_check=current_version,
            latest_version=metadata.latest_version,
            wheel_url=metadata.wheel_url,
            min_supported=metadata.min_supported,
            disabled=metadata.disabled,
            notice=metadata.notice,
            fetch_error="",
        )

    @classmethod
    def from_error(
        cls,
        prior: CacheEntry | None,
        current_version: str,
        error_str: str,
    ) -> CacheEntry:
        """Construct a failed-probe cache entry. The previous successful
        probe's version fields are carried forward so a transient outage
        doesn't immediately erase the "upgrade available" signal. If
        there's no prior, the version fields are empty strings and the
        banner renderer treats it as "nothing known."""
        if prior is None or prior.fetch_error and not prior.latest_version:
            return cls(
                checked_at=_utcnow_iso(),
                current_at_check=current_version,
                latest_version="",
                wheel_url="",
                min_supported="",
                disabled=(),
                notice="",
                fetch_error=error_str,
            )
        # Carry forward the prior successful values; just update the
        # timestamp and error field.
        return cls(
            checked_at=_utcnow_iso(),
            current_at_check=current_version,
            latest_version=prior.latest_version,
            wheel_url=prior.wheel_url,
            min_supported=prior.min_supported,
            disabled=prior.disabled,
            notice=prior.notice,
            fetch_error=error_str,
        )


def read_cache() -> CacheEntry | None:
    """Read the cache JSON. ``None`` on missing file, unreadable, or
    malformed contents. Never raises."""
    path = cache_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    return CacheEntry.from_json(raw)


def write_cache(entry: CacheEntry) -> None:
    """Atomically replace the cache file with the serialized entry.

    Uses ``tempfile.NamedTemporaryFile`` in the same directory so
    ``os.replace`` is a same-filesystem atomic rename. Concurrent
    writers (two parallel mcs invocations with their daemon threads
    both writing) end up with last-writer-wins; the read side never
    sees a half-written file because the rename is atomic. The parent
    directory is created with ``parents=True, exist_ok=True``.

    Errors on the cache write are *silent* — losing the cache is
    strictly a banner-staleness issue, not a correctness issue, and the
    daemon thread that calls this function must not influence the
    foreground command's exit status. The single exception path that's
    re-raised is ``KeyboardInterrupt`` (covered by the bare
    ``except Exception``, since ``KeyboardInterrupt`` inherits from
    ``BaseException``, not ``Exception``).
    """
    target = cache_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # ``delete=False`` because we want to keep the file on disk and
        # then rename it. ``dir=target.parent`` ensures the temp file
        # is on the same filesystem as the final destination so
        # ``os.replace`` is a kernel-level rename instead of a copy.
        # The lifetime spans the inner try/finally + the os.replace
        # call, so the context-manager form isn't a clean fit here.
        fd = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".update_check.",
            suffix=".tmp",
            delete=False,
        )
        try:
            fd.write(entry.to_json())
            fd.write("\n")
            fd.flush()
            os.fsync(fd.fileno())
        finally:
            fd.close()
        os.replace(fd.name, target)
    except Exception:
        # Best-effort. The probe writes silently fail rather than
        # tripping the main process. Clean up the temp file if it's
        # still hanging around.
        try:
            tmp_path = Path(fd.name)
            if tmp_path.exists() and tmp_path != target:
                tmp_path.unlink()
        except Exception:
            pass


def should_check(cache: CacheEntry | None, ttl_s: float = _CACHE_DEFAULT_TTL_S) -> bool:
    """Decide whether the daemon thread should fire a fresh probe.

    True when:
      * the cache is missing (first run), or
      * the cache's ``checked_at`` doesn't parse as ISO-8601 (corrupt
        timestamp), or
      * the cache's ``checked_at`` is older than ``ttl_s`` seconds.

    False otherwise. The decision is advisory — even a False result
    doesn't *prevent* a probe; ``start_background_probe`` exposes its
    own bypass via the ``force=True`` kwarg used by the ``mcs doctor``
    cache-warmup. The TTL just amortizes the network cost in the
    steady state.
    """
    if cache is None:
        return True
    stamp = _parse_iso_utc(cache.checked_at)
    if stamp is None:
        return True
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    # Future-dated timestamps (clock skew between the writer and the
    # reader) collapse to "not stale" — the reader treats them as if
    # they were just written. That's the conservative direction: a
    # forward-jumping clock shouldn't trigger network noise.
    if stamp > now:
        return False
    age = (now - stamp).total_seconds()
    return age >= ttl_s


_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
"""Mirror the ``MCS_NO_HISTORY`` truthy set documented in CLAUDE.md so
the env-var contract across mcs feels uniform."""


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name, "")
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


_SUPPRESSED_SUBCOMMANDS = frozenset({"update", "doctor"})
"""Subcommands where the banner is silenced because the command IS
the remediation path (``update``) or the diagnostic surface that
reports the same information through its own check
(``doctor``'s ``update_version_current`` line). See spec
§"Component interfaces" / ``banner_suppressed``."""


_SUPPRESSED_FLAGS = frozenset({"--version", "-V", "--help", "-h"})
"""Information-only argv tokens. ``--help`` isn't in the spec verbatim
but matches the user expectation that ``mcs --help`` ends with the
command listing, not with a footer banner. ``--version`` IS in the
spec ("the invoked subcommand is in {update, version, doctor,
--version}")."""


def _first_subcommand(argv: list[str]) -> str | None:
    """Return the first non-option, non-script-path token of argv.

    ``argv[0]`` is the script name (``mcs`` or the absolute path
    inside the install dir); ``argv[1:]`` is the user input.
    Click's option-hoisting in ``cli._hoist_global_flags`` moves
    ``-f``/``-q``/``--format``/``--debug``/``--verbose``/``--config``
    to the front of argv, so the first non-flag token after those
    is the subcommand. We re-implement that scan here (instead of
    importing from cli.py) so update_check stays a leaf module with no
    cli.py dependency — the import direction is cli → update_check,
    not the other way."""
    # Skip argv[0] (the script path) and all leading dashes-prefixed
    # tokens and their values where applicable.
    flag_takes_value = frozenset({"--format", "-f", "--config"})
    i = 1
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-"):
            return tok
        # Skip the flag's value if it's a two-token form. The
        # ``--key=value`` form is a single token and falls through.
        base = tok.split("=", 1)[0] if "=" in tok else tok
        if base in flag_takes_value and "=" not in tok:
            i += 2
            continue
        i += 1
    return None


def banner_suppressed(argv: list[str], *, hard_block: bool = False) -> bool:
    """Decide whether to skip the update-banner footer on this
    invocation. Argv is the full ``sys.argv`` shape including the
    script name at index 0. The ``hard_block`` kwarg flips on the
    "disabled / below min_supported" override path: the banner is
    shown even if the user set ``MCS_NO_UPDATE_CHECK`` because the
    publisher has marked the running version unsafe. The TTY check is
    bypassed for hard_block too so CI pipelines see the gate. The
    machine-output suppressors (``-q`` / ``-f json``) and the
    informational subcommands (``update``, ``doctor``, ``--version``,
    ``--help``) are still honored, because:

      * In JSON mode the human banner would corrupt the consumer's
        parser; the non-zero exit + the JSON envelope's ``error``
        block carry the machine-readable signal.
      * In quiet mode the user has explicitly asked for minimal
        output; the per-command exit code is still non-zero so
        scripts can detect the disabled-version state.
      * On ``mcs update`` we're already running the remediation; a
        meta-banner saying "run mcs update" would be a redundant
        loop.
      * On ``mcs doctor`` the channel-reachable and version-current
        checks output the same information through their own lines.
      * On ``--version`` / ``--help`` the user is asking for a
        specific piece of information and a trailing nag is noise.

    The "first non-flag token equals one of the suppressed subcommands"
    check uses ``_first_subcommand`` which re-implements the same
    flag-skipping logic that ``cli._hoist_global_flags`` uses, so the
    decision is robust across pre-hoist and post-hoist argv shapes
    (the wrapper calls this function once before hoist for the probe
    decision, and once after dispatch for the render decision — both
    positions see argv with the global flags placed correctly).
    """
    import sys

    # Informational flags always suppress (information-only invocations).
    if any(tok in _SUPPRESSED_FLAGS for tok in argv[1:]):
        return True

    # Likewise the two carve-out subcommands.
    sub = _first_subcommand(argv)
    if sub in _SUPPRESSED_SUBCOMMANDS:
        return True

    # Machine output modes — the human banner would interleave with
    # structured stdout. We respect this even for the hard-block path
    # since the JSON envelope already exposes the failure.
    argv_tail = argv[1:]
    if "-q" in argv_tail or "--quiet" in argv_tail:
        return True
    json_format = False
    j = 0
    while j < len(argv_tail):
        t = argv_tail[j]
        if t == "-f" or t == "--format":
            if j + 1 < len(argv_tail) and argv_tail[j + 1] == "json":
                json_format = True
                break
            j += 2
            continue
        if t == "-f=json" or t == "--format=json":
            json_format = True
            break
        j += 1
    if json_format:
        return True

    # The remaining two suppression vectors — the env opt-out and the
    # no-TTY check — are bypassed when the cache says the running
    # version is hard-blocked, because the disabled banner is a gate,
    # not a nag. The hard banner runs on stderr regardless of whether
    # stderr is a TTY or not. (Hard-block-with-JSON is still
    # suppressed; see the json_format branch above.)
    if hard_block:
        return False

    if _env_truthy("MCS_NO_UPDATE_CHECK"):
        return True

    return not sys.stderr.isatty()


def is_hard_block(cache: CacheEntry | None, *, current: str) -> bool:
    """Convenience predicate over ``is_disabled`` against the cache's
    metadata-shaped fields. Returns False when the cache is missing
    or carries the empty-string "no metadata yet" placeholders that
    ``CacheEntry.from_error`` writes on a cold-cache fetch failure.

    The "carry forward prior on transient error" logic in
    ``CacheEntry.from_error`` means a single failed probe doesn't
    erase the prior disabled signal — if the prior probe saw the
    running version in ``disabled[]``, the post-failure cache still
    has the disabled-list value, and is_hard_block continues to
    return True. That's the intended behavior: the publisher's hard
    gate shouldn't flap on a network blip.
    """
    if cache is None:
        return False
    if not cache.latest_version:
        # The "no successful probe ever" state — the cache has nothing
        # to compare against and no disabled-list to consult. The
        # next successful probe will populate this.
        return False
    # Construct a transient LatestMetadata wrapper over the cache
    # fields so we can re-use ``is_disabled``'s logic. Schema_version
    # and released_at are diagnostic-only for is_disabled, so we fill
    # them with sentinels.
    md = LatestMetadata(
        schema_version=_SCHEMA_VERSION,
        latest_version=cache.latest_version,
        released_at=cache.checked_at,  # close enough; is_disabled
        # ignores this field.
        wheel_url=cache.wheel_url,
        min_supported=cache.min_supported,
        disabled=cache.disabled,
        notice=cache.notice,
        sha256="",
    )
    blocked, _reason = is_disabled(md, current)
    return blocked


_BANNER_PREFIX = "✨"
"""Identifies the line so it's distinguishable from the wrapped
command's own stderr output. A single sparkle is enough of a visual
marker without the bracketed-tag noise that pip's ``[notice]`` and
uv's ``warning:`` carry. The banner is also prefixed with a blank
line (see ``format_banner``) so it doesn't crowd whatever the
wrapped command printed last."""


def format_banner(cache: CacheEntry | None, *, current: str) -> str | None:
    """Render the human banner string, or None for "say nothing."

    Returns:
      * ``None`` when the cache is missing, has no successful probe
        on record (empty ``latest_version``), the running version is
        already at or ahead of ``latest_version``, or the cache only
        contains a fetch_error from the cold-cache state.
      * The hard form (no soft arrow, "disabled" / "below min" wording)
        when ``is_hard_block`` returns True.
      * The soft form (one-line "new release available: cur → new.
        Run mcs update to upgrade.") otherwise.

    A non-empty ``notice`` field from the publisher's ``latest.json``
    is prepended to the line, separated by ``" — "``, so a
    publisher-side announcement ("CVE patched in 0.4.0a40") rides
    the same channel as the version arrow. The notice can be the
    whole message in cases where there's no version delta (the
    publisher pushed a notice without a wheel bump); we render the
    notice alone in that case.

    The "exit non-zero" behavior for the hard form is the caller's
    responsibility (see ``cli._cli_main``'s finally block). This
    function is string-only — easy to unit-test, never raises.

    The cached ``current_at_check`` field is intentionally NOT used
    for the version comparison — the running mcs version (``current``
    argument) is the source of truth at render time. If the user
    upgraded between the cache write and now, the comparison reflects
    reality. ``current_at_check`` is diagnostic-only (lets ``mcs
    doctor`` say "cache was written when mcs was at version X").
    """
    from packaging.version import InvalidVersion, Version

    if cache is None:
        return None
    if not cache.latest_version:
        # Cold cache (the very first probe attempt failed and there's
        # nothing in the latest_version slot). Banner stays silent;
        # the next successful probe populates the cache.
        return None

    # Hard-block path takes priority — the publisher has said this
    # version must not be used. The wheel_url / min_supported strings
    # are passed through so the message can be precise.
    if is_hard_block(cache, current=current):
        md_for_reason = LatestMetadata(
            schema_version=_SCHEMA_VERSION,
            latest_version=cache.latest_version,
            released_at=cache.checked_at,
            wheel_url=cache.wheel_url,
            min_supported=cache.min_supported,
            disabled=cache.disabled,
            notice=cache.notice,
            sha256="",
        )
        _, reason = is_disabled(md_for_reason, current)
        notice_prefix = f"{cache.notice.strip()} — " if cache.notice.strip() else ""
        return (
            f"\n{_BANNER_PREFIX} {notice_prefix}{reason}. "
            f"Run 'mcs update' to install the latest "
            f"({cache.latest_version})."
        )

    # Soft path. Compare current vs latest under PEP 440 semantics so
    # a dev build ahead of the published latest doesn't get a "new
    # release available" nag suggesting a downgrade.
    try:
        cur_v = Version(current)
        new_v = Version(cache.latest_version)
    except InvalidVersion:
        # Current is the "0+unknown" fallback or the publisher shipped
        # a malformed latest_version — no comparison signal. The
        # cold-cache "no signal" semantics apply: we say nothing.
        return None
    if cur_v >= new_v:
        return None

    notice_prefix = f"{cache.notice.strip()} — " if cache.notice.strip() else ""
    return (
        f"\n{_BANNER_PREFIX} {notice_prefix}A new release of mcs is available: "
        f"{current} → {cache.latest_version}. "
        f"Run 'mcs update' to upgrade."
    )


def _run_probe(*, current_version: str) -> None:
    """Synchronous probe body. The daemon thread's target.

    Side effects: writes ``cache_path()`` with either a fresh
    ``CacheEntry`` from a successful fetch, an error-marked entry that
    carries the prior successful version forward (so a transient
    outage doesn't drop the upgrade hint), or a cold-cache error
    entry when there is no prior at all. Never raises — wraps the
    fetcher and the cache write in a bare ``except Exception`` so a
    bug in either layer doesn't propagate out of the daemon thread,
    which would otherwise print to stderr via the default
    ``threading.excepthook``.

    Reads the existing cache file once before the fetch so the
    transient-error carry-forward has the prior values available.
    """
    try:
        prior = read_cache()
        metadata = fetch_latest_metadata()
        if metadata is None:
            # The fetcher's contract is "None on any failure". We
            # don't get a specific error string out of it — record a
            # generic marker. The doctor channel-reachable check
            # surfaces the specific error class by re-issuing the
            # fetch when the user runs it.
            entry = CacheEntry.from_error(
                prior,
                current_version=current_version,
                error_str="fetch returned None (network or schema)",
            )
        else:
            entry = CacheEntry.from_fetch(metadata, current_version=current_version)
        write_cache(entry)
    except Exception as exc:  # noqa: BLE001 — daemon-thread isolation.
        # Last-ditch error path: the fetcher's "no exceptions" contract
        # was violated (defensive), or the cache write blew up. Record
        # the exception text in the cache so ``mcs doctor`` has
        # something to print on the next interactive run.
        try:
            prior = read_cache()
        except Exception:
            prior = None
        try:
            entry = CacheEntry.from_error(
                prior,
                current_version=current_version,
                error_str=f"{type(exc).__name__}: {exc}",
            )
            write_cache(entry)
        except Exception:
            # Cache write also failed — log to the daemon thread's
            # stderr and give up. There's nowhere else to put this.
            # Use ``warnings.warn`` so the user sees it on the *next*
            # foreground command instead of mid-stream of the current
            # one (Python's warning machinery dedups by source location
            # so the same write-fail doesn't spam each invocation).
            import warnings

            warnings.warn(
                f"mcs update-check daemon: cache write failed: {exc!r}",
                stacklevel=1,
            )


def start_background_probe(*, current_version: str, force: bool = False) -> threading.Thread | None:
    """Spawn a non-blocking daemon thread that runs ``_run_probe``.

    Returns the ``Thread`` object so the caller can ``thread.join()``
    in contexts where we DO want to wait (e.g. ``mcs doctor`` warmup),
    or ignore it for the fire-and-forget banner path. Returns ``None``
    when the TTL gate said "cache is fresh, no probe needed" — in that
    case no thread was spawned.

    The ``force=True`` kwarg bypasses the TTL gate. ``mcs doctor``
    passes ``force=True`` so the diagnostic always reflects the
    current publisher state instead of a 6-hour-old cache. The banner
    path passes ``force=False`` so the steady-state network cost is
    bounded.

    The thread is marked daemon so the main process can exit without
    waiting on it. The Python interpreter doesn't run ``atexit``
    handlers in daemon threads (which is what we want — no half-flushed
    output during process teardown), but it does interrupt the
    thread's syscalls cleanly. The kernel-level socket close on
    process exit is what aborts an in-flight HTTP read.
    """
    cache = read_cache()
    if not force and not should_check(cache):
        return None

    thread = threading.Thread(
        target=_run_probe,
        kwargs={"current_version": current_version},
        name="mcs-update-probe",
        daemon=True,
    )
    thread.start()
    return thread
