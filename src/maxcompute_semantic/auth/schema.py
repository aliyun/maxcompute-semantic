# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Profile dataclasses with manual validation (no pydantic).

The model:

- A ``Profile`` is one AK identity. The AK lives in the
  ``compute_project`` (the MaxCompute project where SQL job
  instances run and where billing accrues). The data the AK reads
  cross-project lives in a list of ``DataSource`` entries, each a
  ``(project, schema, tables)`` triple. For 2-level data sources
  the ``schema`` is the literal string ``"default"`` (matching
  MaxCompute's 2→3 upgrade flat-table landing-zone naming). For
  the wildcard "every table currently in the schema" case the
  ``tables`` field is the literal string ``"*"``. For the
  enumerated case ``tables`` is a tuple of ``TableSpec`` entries.
- A ``TableSpec`` carries the bare table name and optional column
  scoping. ``columns`` is the whitelist (the agent sees only the
  listed columns); ``columns_exclude`` is the blacklist (the agent
  sees everything except the listed columns). The two are mutually
  exclusive. Partition columns are kept regardless of the
  blacklist, because the agent needs partition keys to write
  filter predicates that avoid MaxCompute's full-scan denial.
- The column lists are an *agent-visibility filter*, not an
  access-control mechanism. MaxCompute classic GRANT is table-
  level; column-level access control is LabelSecurity, configured
  server-side by the project owner independently of mcs.
- The ``package_path`` field on ``Profile`` is the optional
  override for where the PackageDB and per-table markdown live;
  default is ``data_root() / <profile-name>``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from maxcompute_semantic.auth.errors import InvalidProfileError

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-@:.]{2,63}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ProcessAuth:
    """Subprocess-style auth: ``command`` returns a JSON credential
    payload on stdout in the Alibaba Cloud STS AssumeRole format
    (``AccessKeyId``, ``AccessKeySecret``, ``SecurityToken``, and
    optional ``Expiration``).  The canonical command is a process auth helper (e.g. ``my-credential-helper get
    --format json``)
    which produces exactly this shape.  Any program emitting the same
    four-field JSON works — snake_case variants (``access_key_id``
    etc.) are also accepted.  ``timeout`` is the max wall-clock for
    the command in seconds (1..600)."""

    command: str
    timeout: int = 60


@dataclass(frozen=True)
class AkAuth:
    """Aliyun AccessKey pair. Both fields support the
    ``${env:NAME}`` reference form (the credential resolver expands
    it at use time) so the literal secret doesn't have to live on
    disk."""

    access_key_id: str
    access_key_secret: str


@dataclass(frozen=True)
class CostThresholds:
    """The verdict gate for ``mcs sql cost`` and the execution-time gate
    in ``MaxComputeClient.execute_sql``. ``confirm_cny`` is the
    "ask the user to confirm" boundary; ``blocked_cny`` is the
    "refuse without an override" ceiling. Defaults are 10 / 100
    CNY. Stored per-profile so different identities can have
    different risk budgets.

    Use ``enabled=False`` to disable the gate entirely — used by the
    eval harness so the agent never sees a confirm prompt and never
    gets blocked. ``is_enabled()`` is the single check callers use;
    the validator skips the strict-LT check when the gate is disabled.
    """

    confirm_cny: float = 10.0
    blocked_cny: float = 100.0
    enabled: bool = True

    def __post_init__(self) -> None:
        import warnings

        if self.enabled and (self.confirm_cny <= 0 or self.blocked_cny <= 0):
            warnings.warn(
                f"CostThresholds has enabled=True but confirm_cny={self.confirm_cny} "
                f"and blocked_cny={self.blocked_cny}; non-positive thresholds with "
                f"enabled=True are likely a misconfiguration — use "
                f"CostThresholds(enabled=False) to disable the gate explicitly",
                UserWarning,
                stacklevel=2,
            )

    def is_enabled(self) -> bool:
        return self.enabled


AuthSpec = ProcessAuth | AkAuth


@dataclass(frozen=True)
class TableSpec:
    """One entry in a ``DataSource``'s enumerated table list.

    ``columns`` (whitelist) and ``columns_exclude`` (blacklist) are
    mutually exclusive — set one or neither, never both. Partition
    columns are surfaced regardless of either filter so the agent
    can write the partition-predicate that MaxCompute requires on
    partitioned tables. The filter is an agent-view filter (what
    the agent sees in the package metadata); it is **not** an
    access-control boundary on the data the agent's SQL returns
    (which is gated by MaxCompute server-side ACL — table-level
    GRANT plus optionally LabelSecurity for column-level — see
    spec §11).
    """

    name: str
    columns: tuple[str, ...] | None = None
    columns_exclude: tuple[str, ...] = ()

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidProfileError(
                f"TableSpec.name must be a non-empty string (got {self.name!r})",
                remediation="every entry in a source's tables list needs the "
                "bare table name within the source's schema",
            )
        if self.columns is not None and self.columns_exclude:
            raise InvalidProfileError(
                f"TableSpec({self.name!r}) sets both 'columns' (whitelist) "
                "and 'columns_exclude' (blacklist); they are mutually "
                "exclusive — pick one",
            )
        for label, lst in (
            ("columns", self.columns or ()),
            ("columns_exclude", self.columns_exclude),
        ):
            seen: set[str] = set()
            for c in lst:
                if not isinstance(c, str) or not c.strip():
                    raise InvalidProfileError(
                        f"TableSpec({self.name!r}).{label} contains a "
                        f"non-string or empty entry: {c!r}",
                    )
                if c in seen:
                    raise InvalidProfileError(
                        f"TableSpec({self.name!r}).{label} has a duplicate column name {c!r}",
                    )
                seen.add(c)


def _coerce_tablespec_iter(items: Iterable[object]) -> tuple[TableSpec, ...]:
    """Normalize a list-of-tablespecs-or-bare-strings into a
    homogeneous ``tuple[TableSpec, ...]``. Bare strings become
    ``TableSpec(name=string)`` with no column scoping; existing
    ``TableSpec`` objects pass through. Anything else is a type
    error. Used by ``DataSource.__post_init__`` to normalize what
    the yaml deserializer hands us (yaml gives us a mix of strings
    and dicts; the deserializer converts dicts to TableSpec but the
    bare-string form is the convenient one for the common
    "no column filter" case and we accept it here too)."""
    out: list[TableSpec] = []
    for it in items:
        if isinstance(it, TableSpec):
            out.append(it)
        elif isinstance(it, str):
            out.append(TableSpec(name=it))
        else:
            raise InvalidProfileError(
                f"DataSource.tables entry must be a string or a TableSpec "
                f"(got {type(it).__name__}: {it!r})",
            )
    return tuple(out)


@dataclass(frozen=True)
class DataSource:
    """One ``(project, schema, tables)`` triple in a profile's
    sources list. The data ``project`` is reached via cross-project
    read from the profile's ``compute_project``. The ``schema``
    field is the namespace within the data project; for 2-level
    data projects (where there's no schema axis), use the literal
    string ``"default"`` — that's the name MaxCompute uses for the
    flat namespace after a 2→3 upgrade, and the 3-level compute
    parser accepts ``<2-level-proj>.default.<table>`` as the
    canonical 3-segment qualified form.

    The ``tables`` field is either the wildcard string ``"*"``
    ("every table currently in the schema, no column filter") or
    a tuple of ``TableSpec`` entries for the enumerated case. The
    wildcard expansion is lazy — it happens at ``mcs build`` time
    against the live MaxCompute catalog, so new tables added on the
    source side are picked up on the next build automatically
    without re-editing the source via ``mcs profile update``. The
    trade-off vs a snapshot-at-edit-time alternative is discussed
    in spec Open Question Q1.
    """

    project: str
    schema: str = "default"
    tables: tuple[TableSpec, ...] | str = "*"

    def __post_init__(self) -> None:
        # When the yaml deserializer or a programmatic caller hands
        # us a list (rather than a tuple) of TableSpec-or-strings,
        # normalize it via ``_coerce_tablespec_iter``. A bare string
        # (the wildcard "*" case) is left alone. The result is
        # frozen-dataclass-compatible — frozen=True disallows direct
        # attribute writes, so we use ``object.__setattr__`` for the
        # one in-place normalization.
        if (
            isinstance(self.tables, (list, tuple))
            and not isinstance(self.tables, str)
            and (
                not isinstance(self.tables, tuple)
                or any(not isinstance(t, TableSpec) for t in self.tables)
            )
        ):
            object.__setattr__(
                self,
                "tables",
                _coerce_tablespec_iter(self.tables),
            )

    def validate(self) -> None:
        if not isinstance(self.project, str) or not self.project.strip():
            raise InvalidProfileError(
                f"DataSource.project must be a non-empty string (got {self.project!r})",
                remediation="each source needs the MaxCompute project name where the data lives",
            )
        if not isinstance(self.schema, str) or not self.schema.strip():
            raise InvalidProfileError(
                f"DataSource({self.project!r}).schema is empty",
                remediation="use 'default' for 2-level data projects "
                "(matches MaxCompute's 2→3 upgrade naming where "
                "flat-namespace tables migrate into a 'default' schema)",
            )
        if isinstance(self.tables, str):
            if self.tables != "*":
                raise InvalidProfileError(
                    f"DataSource({self.project}.{self.schema}).tables is a "
                    f"string but not the wildcard '*' "
                    f"(got {self.tables!r}); use the wildcard for the "
                    f"whole schema or a list of TableSpec entries for an "
                    f"enumerated subset",
                )
        elif isinstance(self.tables, tuple):
            if len(self.tables) == 0:
                raise InvalidProfileError(
                    f"DataSource({self.project}.{self.schema}).tables is an "
                    f"empty list — a profile source must enumerate at least "
                    f"one table, or use the wildcard '*' to grab the whole "
                    f"schema",
                    remediation=(
                        "list candidate tables first via "
                        f"`mcs meta list-tables --project {self.project} "
                        f"--schema {self.schema}`, pick the ones you want, "
                        "and put them into the spec as "
                        "`tables: [{name: T1}, {name: T2}, ...]`. "
                        "Use `tables: '*'` only when you genuinely want every "
                        "table in the schema (expensive on large projects — "
                        "build samples + profiles each table)."
                    ),
                )
            seen: set[str] = set()
            for ts in self.tables:
                if not isinstance(ts, TableSpec):
                    raise InvalidProfileError(
                        f"DataSource({self.project}.{self.schema}).tables "
                        f"contains a non-TableSpec entry: {ts!r}",
                    )
                ts.validate()
                if ts.name in seen:
                    raise InvalidProfileError(
                        f"DataSource({self.project}.{self.schema}) has "
                        f"duplicate table name {ts.name!r}",
                    )
                seen.add(ts.name)
        else:
            raise InvalidProfileError(
                f"DataSource({self.project}.{self.schema}).tables is "
                f"neither the wildcard '*' nor a list/tuple of TableSpec "
                f"entries (got type {type(self.tables).__name__})",
            )

    def source_key(self) -> str:
        """Filesystem-portable disambiguator for this source.

        ``<project>__<schema>`` (double-underscore separator). Used
        as (a) the PackageDB's per-table-row's `(source_project,
        source_schema)` index column pair, (b) the per-source
        markdown filename prefix in ``profile_data_dir(profile)``,
        and (c) the per-source subdirectory name in the export
        tarball's ``data/`` layout. MaxCompute project and schema
        names don't contain ``__`` by convention, so the
        double-underscore separator never collides with a name
        substring; the form is also OS-portable (no path separators,
        no reserved filesystem characters).
        """
        return f"{self.project}__{self.schema}"

    def qualified(self, table: str) -> str:
        """Canonical 3-segment ``project.schema.table`` form for the
        named table within this source. The agent's SQL uses this
        form universally (the spec §10 rule)."""
        return f"{self.project}.{self.schema}.{table}"

    def qualified_for_tier(self, table: str, tier: str) -> str:
        """3-segment form on tier ``"3"``, bare name on tier ``"2"``.

        The build pipeline's sampling and profiling phases call this
        when constructing ``FROM`` clauses against the live project —
        2-level projects reject any qualifier, 3-level projects need
        the full ``project.schema.table`` form.
        """
        return self.qualified(table) if tier == "3" else table

    def qualified_for_connection(self, table: str, *, conn_tier: str, compute_project: str) -> str:
        """Return the SQL ``FROM``-clause form for ``table`` that the
        given connection will accept.

        The connection's tier governs which qualifier shapes the SQL
        parser recognizes; ``compute_project`` decides whether this
        source is same-project (no qualifier needed) or cross-project
        (must address the data project explicitly). The matrix the
        2026-05-22 cross-tier probes confirmed:

        =========  ==============  =====================  =================================
        conn_tier  same project?   schema                 form
        =========  ==============  =====================  =================================
        ``"3"``    yes / no        any                    ``<src.proj>.<src.schema>.<table>``
                                                          (3-segment universal)
        ``"2"``    yes             ``"default"``          bare ``<table>``
        ``"2"``    no              ``"default"``          ``<src.proj>.<table>`` (2-segment;
                                                          reaches the source project's
                                                          ``default`` schema)
        ``"2"``    no              non-``"default"``      ``<src.proj>.<src.schema>.<table>``
                                                          (3-segment; **paired with
                                                          ``connection_hints``** which
                                                          returns
                                                          ``odps.namespace.schema=true``)
        =========  ==============  =====================  =================================

        The fourth row is the escape-hatch for 2-level compute reading
        a non-``default`` schema of a 3-level source. The 2-level
        parser rejects 3-segment FQNs by default
        (``ODPS-0130161 Parse exception``), but
        ``odps.namespace.schema=true`` flips it open; pyodps
        ``execute_sql(..., hints=...)`` applies the hint
        session-locally without needing
        ``odps.sql.submit.mode=script`` (the script-mode requirement
        only kicks in when the SQL string itself contains multiple
        statements, e.g. inline ``SET`` followed by ``SELECT``).
        Empirically validated 2026-05-22 against a 2-level test
        project cross-reading the non-default schema of a paired
        3-level test project (``{src_project}.{src_schema}.account``).

        ``connection_hints`` returns the matching extra-hints dict
        for the same call site so callers can compose:

        .. code-block:: python

            fq = source.qualified_for_connection(t, conn_tier=ct,
                                                 compute_project=cp)
            extra = source.connection_hints(conn_tier=ct)
            client.execute_sql(f"SELECT ... FROM {fq}", hints=extra)
        """
        if conn_tier == "3":
            return self.qualified(table)
        if self.project == compute_project:
            return table
        # conn_tier == "2", cross-project: 2-segment for default schema
        # (works for both 2-level sources and 3-level sources whose
        # default schema is the target); 3-segment + namespace.schema
        # hint for non-default 3-level source schemas.
        if self.schema == "default":
            return f"{self.project}.{table}"
        return self.qualified(table)

    def connection_hints(self, *, conn_tier: str) -> dict[str, str]:
        """Extra pyodps execute hints needed for the connection to
        address this source. Pairs with ``qualified_for_connection``.

        Returns ``{"odps.namespace.schema": "true"}`` when this source
        forces a 3-segment FQN through a 2-level connection's parser
        (i.e. the source schema is non-``"default"`` and the
        connection is 2-level — see the fourth row of the matrix in
        ``qualified_for_connection``). Returns an empty dict
        otherwise. The 3-level connection branch needs no extra hint
        from this method because ``MaxComputeClient.execute_sql``
        already injects ``odps.namespace.schema=true`` via
        ``build_hints`` whenever ``schema=`` is set on the call.

        The dict is shallow-merged into ``execute_sql``'s
        ``user_hints`` argument; ``setdefault`` semantics in
        ``build_hints`` mean a caller-supplied hint of the same key
        wins.
        """
        if conn_tier == "2" and self.project and self.schema and self.schema != "default":
            return {"odps.namespace.schema": "true"}
        return {}

    def is_wildcard(self) -> bool:
        """True when ``tables == "*"`` (wildcard expansion at build
        time). The build pipeline checks this to decide whether to
        run ``list_tables(project, schema)`` against the live MC
        catalog or to read the enumerated list directly."""
        return isinstance(self.tables, str) and self.tables == "*"

    def table_names(self) -> tuple[str, ...]:
        """The enumerated table names if ``tables`` is a list, or
        the empty tuple if ``tables == "*"``. Callers that want the
        wildcard's expansion at build time use ``is_wildcard()`` and
        a live ``list_tables`` query."""
        if isinstance(self.tables, str):
            return ()
        return tuple(ts.name for ts in self.tables)


@dataclass(frozen=True)
class Profile:
    """One AK identity + the data sources it reads.

    ``name`` is the local mcs-side identifier (the wizard's "Profile
    name" prompt fills it in, defaulting to the compute_project
    name with auto-disambiguation when colliding — see the
    0.3.0a26 fix). ``compute_project`` is the MaxCompute project
    the AK is registered in (where SQL instances spawn, where
    billing happens); ``endpoint`` is the MaxCompute REST endpoint
    for that project's region. ``auth`` is the AK pair (literal or
    env-var-referenced) or the subprocess-style credential helper.

    ``sources`` is the list of data projects-and-schemas the AK
    has cross-project read access to. ``mcs profile create``'s
    interactive source-picker populates this list one entry at a
    time during initial setup; later edits go through the
    ``mcs profile update`` interactive editor or the non-interactive
    ``mcs profile update --from-spec <json>`` rewrite. There are
    no per-source CRUD verbs (``add-source`` / ``remove-source`` /
    ``update-source``) — the editor and the spec-rewrite path
    cover both cases.

    ``package_path`` is the optional override for where the
    PackageDB and per-table markdown live on disk; default is the
    standard ``data_root() / <name>`` slot (from the vocabulary
    cleanup spec).
    """

    name: str
    compute_project: str
    endpoint: str
    auth: AuthSpec
    sources: tuple[DataSource, ...] = ()
    cost_thresholds: CostThresholds = field(default_factory=CostThresholds)
    tags: tuple[str, ...] = ()
    description: str = ""
    package_path: Path | None = None

    # --- fork-alias fields (added by the git-versioning feature) ----

    kind: str = "main"
    """``"main"`` for a normal mcs-writable profile; ``"fork"`` for a
    read-only alias backed by a detached ``git worktree`` in the
    parent profile's repo. The discriminator is the gate every write
    command checks at entry — a fork-kind profile raises
    ``ProfileReadOnly`` instead of mutating ``package.db``. The
    string-typed field (rather than an ``enum.Enum``) matches the
    yaml's natural ``kind: fork`` representation without a
    serialization adapter. Unknown values are rejected by
    ``validate()`` so a hand-edited yaml with ``kind: bogus`` fails
    the load."""

    parent_profile: str | None = None
    """The name of the parent profile this fork is an alias of, or
    ``None`` for a ``kind="main"`` profile. The parent's data
    directory is the parent git repository; this fork's
    ``package_path`` is a worktree under that repo. The cross-field
    invariant — ``kind="fork"`` requires this to be non-None — is
    enforced by ``validate()``."""

    git_sha: str | None = None
    """The 40-character hex commit SHA the fork's worktree is
    detached-HEAD'd at, or ``None`` for a ``kind="main"`` profile.
    The integration tests assert that the on-disk
    ``<worktree>/.git/HEAD`` text matches this string after a fresh
    fork creation. The hex-shape check is part of ``validate()``."""

    # Note: an ``identity: str | None`` field briefly existed here
    # in a feat/claude-code-plugin commit (d115314) that captured
    # the ODPS whoami principal at create / auth-test time and
    # persisted it. It was removed in the same branch when the
    # design was simplified to a one-shot ``mcs profile whoami``
    # verb (no persistence). Old yaml on disk that still carries
    # an ``identity:`` key is loaded fine — the loader ignores
    # unknown keys — and a subsequent ``upsert`` strips the key.

    def __post_init__(self) -> None:
        # Normalize ``sources`` to a tuple of DataSource objects if
        # the caller hands a list / iterable. The yaml deserializer
        # gives us a list of dicts that it converts to DataSource
        # via the helper in ``profile_store._datasource_from_dict``,
        # so by the time the constructor sees ``sources`` it's
        # already a sequence of DataSource. The check below is
        # defensive: if a programmatic caller passes a list, we
        # tuple-ify it; if it's a sequence of non-DataSource, the
        # validator raises.
        if isinstance(self.sources, list):
            object.__setattr__(self, "sources", tuple(self.sources))

    def validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise InvalidProfileError(
                f"profile name {self.name!r} invalid",
                remediation="name must match "
                "^[a-zA-Z0-9][a-zA-Z0-9_\\-@:.]{2,63}$ "
                "(3-64 characters, starting alphanumeric; ``@``, ``:``, "
                "and ``.`` admitted inside the body as fork-name "
                "delimiters)",
            )
        if not isinstance(self.compute_project, str) or not self.compute_project.strip():
            raise InvalidProfileError(
                f"profile {self.name!r}: compute_project is empty",
                remediation="set compute_project to the MaxCompute project "
                "where your AK is registered (the project SQL jobs run in "
                "and where billing accrues, often called the 'home project' "
                "or 'compute account project')",
            )
        if not self.endpoint.startswith(("http://", "https://")):
            raise InvalidProfileError(
                f"profile {self.name!r}: endpoint must start with http:// "
                f"or https:// (got {self.endpoint!r})",
                remediation="for public MaxCompute, the endpoint is of the "
                "form https://service.<region>.maxcompute.aliyun.com/api; "
                "internal endpoints vary",
            )
        if isinstance(self.auth, ProcessAuth):
            if not self.auth.command.strip():
                raise InvalidProfileError(
                    f"profile {self.name!r}: auth.command is empty",
                    remediation="ProcessAuth's command is the shell command "
                    "the credential resolver runs to get a JSON token; "
                    "the canonical form is 'my-credential-helper get "
                    "--format json'",
                )
            if not (1 <= self.auth.timeout <= 600):
                raise InvalidProfileError(
                    f"profile {self.name!r}: auth.timeout must be 1-600 (got {self.auth.timeout})",
                    remediation="default is 60 seconds; the upper bound "
                    "protects against a stuck subprocess hanging the CLI",
                )
        elif isinstance(self.auth, AkAuth):
            if not self.auth.access_key_id:
                raise InvalidProfileError(
                    f"profile {self.name!r}: auth.access_key_id is empty",
                    remediation="set a literal AccessKey ID, or use the "
                    "'${env:NAME}' env-var reference form so the secret "
                    "stays out of the on-disk yaml",
                )
            if not self.auth.access_key_secret:
                raise InvalidProfileError(
                    f"profile {self.name!r}: auth.access_key_secret is empty",
                    remediation="set the AccessKey Secret literal or the "
                    "'${env:NAME}' reference form",
                )
        else:
            # Defensive: AuthSpec is a Union of ProcessAuth | AkAuth.
            # Hit this only if a future auth-type is added and this
            # validator hasn't been extended.
            raise InvalidProfileError(
                f"profile {self.name!r}: auth has unknown type {type(self.auth).__name__}",
            )
        if (
            self.cost_thresholds.is_enabled()
            and self.cost_thresholds.confirm_cny >= self.cost_thresholds.blocked_cny
        ):
            raise InvalidProfileError(
                f"profile {self.name!r}: cost_thresholds.confirm_cny "
                f"({self.cost_thresholds.confirm_cny}) must be strictly less "
                f"than blocked_cny ({self.cost_thresholds.blocked_cny})",
                remediation="defaults are confirm=10.0, blocked=100.0 (CNY); "
                "set both to 0 to disable the gate entirely",
            )
        if len(self.description) > 4000:
            raise InvalidProfileError(
                f"profile {self.name!r}: description is "
                f"{len(self.description)} chars, exceeds the 4000-char cap",
                remediation="the description is a short scenario / purpose "
                "summary used to drive table recommendation during "
                "onboarding; keep it under 4000 characters",
            )
        # Source-list validation. The list itself can be empty (a
        # freshly-created profile with no sources added yet is a
        # legal mid-wizard state). Consumers that need at least one
        # source — ``mcs build``, ``mcs meta list-tables``,
        # ``mcs show`` — raise their own "no sources configured"
        # errors when they hit an empty list.
        seen_keys: set[str] = set()
        for src in self.sources:
            if not isinstance(src, DataSource):
                raise InvalidProfileError(
                    f"profile {self.name!r}: sources entry is not a "
                    f"DataSource (got {type(src).__name__}: {src!r})",
                )
            src.validate()
            key = src.source_key()
            if key in seen_keys:
                raise InvalidProfileError(
                    f"profile {self.name!r} has duplicate data source "
                    f"with source_key={key!r} (the same (project, schema) "
                    f"pair appears twice)",
                    remediation="if two sources point at the same "
                    "(project, schema) but with different table subsets, "
                    "merge them into one source with the union of the "
                    "table lists",
                )
            seen_keys.add(key)

        # --- fork-vs-main cross-field invariants ----------------------
        if self.kind not in ("main", "fork"):
            raise InvalidProfileError(
                f"profile {self.name!r}: ``kind`` must be ``main`` or "
                f"``fork`` (got {self.kind!r}).",
                remediation=(
                    "the ``kind`` discriminator labels whether a profile "
                    "is a writable main alias or a read-only fork alias. "
                    "If you hand-edited profiles.yaml and meant a real "
                    "profile, drop the ``kind`` line (default is ``main``); "
                    "if you meant a fork created via ``mcs profile fork``, "
                    "the value must be the literal string ``fork``."
                ),
            )
        if self.kind == "fork":
            if self.parent_profile is None:
                raise InvalidProfileError(
                    f"profile {self.name!r}: fork-kind profile must name its parent_profile.",
                    remediation=(
                        "a fork is an alias backed by a detached "
                        "``git worktree`` in the parent's data-directory "
                        "git repo; the ``parent_profile`` field names the "
                        "parent's profile name so the wrapper can find "
                        "the parent's ``.git/`` dir. Re-create the fork "
                        "via ``mcs profile fork <name> --from <sha> "
                        "--profile <parent>`` so the yaml is written by "
                        "the tool rather than by hand."
                    ),
                )
            if self.git_sha is None:
                raise InvalidProfileError(
                    f"profile {self.name!r}: fork-kind profile must "
                    f"name a git_sha (the anchor commit the worktree "
                    f"is detached at).",
                    remediation=(
                        "the ``git_sha`` is the 40-hex commit SHA this "
                        "fork's worktree is anchored at. ``mcs profile "
                        "fork`` writes this automatically. A missing "
                        "value usually means a hand-edited yaml — "
                        "re-create the fork through the CLI."
                    ),
                )
            if not _GIT_SHA_RE.fullmatch(self.git_sha):
                raise InvalidProfileError(
                    f"profile {self.name!r}: git_sha {self.git_sha!r} "
                    f"is not a 40-character hex SHA.",
                    remediation=(
                        "the ``git_sha`` must be the full 40-character "
                        "hexadecimal commit SHA, not a short SHA or a "
                        "ref name. The CLI writes the full SHA via "
                        "``GitRepo.rev_parse(ref)`` which resolves "
                        "abbreviated refs to the long form before "
                        "persisting."
                    ),
                )
            if self.package_path is None:
                raise InvalidProfileError(
                    f"profile {self.name!r}: fork-kind profile must "
                    f"have ``package_path`` pointing at the worktree "
                    f"directory.",
                    remediation=(
                        "by convention, the fork's worktree lives at "
                        "``<XDG_DATA_HOME>/maxcompute-semantic/data/"
                        "<fork-name>/`` and ``package_path`` carries "
                        "that absolute path. Re-create the fork via "
                        "the CLI."
                    ),
                )
        else:
            # kind == "main": the fork-only fields must be unset.
            if self.parent_profile is not None or self.git_sha is not None:
                raise InvalidProfileError(
                    f"profile {self.name!r}: ``parent_profile`` and "
                    f"``git_sha`` are fork-only fields and must be "
                    f"unset on a main-kind profile.",
                    remediation=(
                        "the yaml has stray fork-only fields on a "
                        "non-fork profile. Remove the ``parent_profile`` "
                        "and ``git_sha`` lines, or set ``kind: fork`` "
                        "if it's actually meant to be a fork (in which "
                        "case the worktree on disk has to also exist — "
                        "see ``mcs doctor``'s ``forks_healthy`` check)."
                    ),
                )

    def source_by_key(self, source_key: str) -> DataSource | None:
        """Look up a source by its ``<project>__<schema>`` key.

        Used by ``build/pipeline.py`` to resolve a stored
        ``source_key`` back to its ``DataSource`` mid-build (e.g.
        when re-using a checkpoint that already wrote its rows
        under a known key), and by the interactive
        ``mcs profile update`` editor when locating an existing
        source to modify. There is no CLI ``--source`` flag on the
        catalog / annotate / memory verbs — disambiguation in those
        groups goes through the FQN ``proj.schema.table`` form
        (e.g. ``--tables proj.schema.t1,proj.schema.t2`` on
        ``memory verify``), not a source-key filter.

        Accepts both the dot-form ``<project>.<schema>`` and the
        double-underscore ``<project>__<schema>`` form (the
        internal source-key used as the on-disk subdir name and
        the PackageDB row key); we normalize the dot-form to the
        double-underscore form here so callers don't have to.
        """
        normalized = source_key.replace(".", "__", 1)
        for src in self.sources:
            if src.source_key() == normalized:
                return src
        return None

    def source_keys(self) -> tuple[str, ...]:
        """Tuple of ``<project>__<schema>`` keys in the order the
        sources were added. Stable iteration order for the export
        tarball's subdirectory layout and the show command's
        per-source section ordering."""
        return tuple(s.source_key() for s in self.sources)


__all__ = [
    "AkAuth",
    "AuthSpec",
    "CostThresholds",
    "DataSource",
    "InvalidProfileError",
    "Profile",
    "ProcessAuth",
    "TableSpec",
]
