# Changelog

All notable changes to `maxcompute-semantic` will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **`mcs sql run` / `mcs sql result` crashed with
  `'CsvRecordReader' object has no attribute 'schema'` (code `Unknown`)
  whenever the instance tunnel is unavailable** (e.g. sandboxed networks
  where the tunnel endpoint is unreachable). pyodps then silently falls
  back to the REST result reader (`CsvRecordReader`), which has no public
  `.schema`. `_read_instance_rows` now detects the fallback: it uses the
  typed schema pyodps stashes on `_schema` when the service provides a
  result descriptor, otherwise derives string-typed columns from the CSV
  header, and tolerates an empty result body (pyodps itself raises
  `TypeError` there). A WARNING is logged on every fallback, and result
  metadata gains `fetch_path` (`instance_tunnel` / `rest_fallback`) so
  callers can tell the server may cap REST results (typically 10000 rows).

### Changed

- **CLI startup no longer imports sqlglot either**, and the pyodps
  dependency floor is raised to `>=0.13`. sqlglot (and the MaxCompute
  dialect package built on it) now loads only inside the commands that
  parse SQL (write guard / cost gate / review / build mining / metric
  validation), removing the remaining startup hotspot (~26ms local,
  ~0.3s on slow sandboxes). pyodps 0.13 imports pandas lazily, so the
  query path never pays the pandas import cost older pyodps forced
  (the ~1.8s `PandasRedirection` block seen in profiles of pre-0.13
  environments). The startup-import guard test now also covers
  `sqlglot`.

- **CLI startup no longer imports pyodps** (and its pandas / numpy /
  pyarrow import tail). `mc_client/client.py` and `mc_client/tier.py` now
  import `odps` lazily inside the methods that talk to MaxCompute, so
  local-only commands (`mcs profile list`, `mcs link`, `mcs doctor`,
  `mcs --help`) skip the multi-second pyodps import chain entirely.
  Profiled trigger: on one sandbox environment `import odps` accounted for
  3.56s of a 4.62s `mcs profile list` run (~77%), with pyodps's eager
  pandas import alone taking ~1.8s. A startup-import guard test keeps the
  CLI free of `odps` / `pandas` / `numpy` / `pyarrow` imports.

## [0.18.0] — 2026-06-24

### Added

- **`mcs sql` verbs extract `SET key=val` into pyodps hints before the write
  guard, cost gate, and submission**, so `SET k=v; SELECT ...` scripts run
  transparently (classified as the remaining statement — `read`, no
  `--allow-write` required) instead of being rejected as a write or blocked
  by the cost gate's `execute_sql_cost` (which, unlike `run_sql`, does not
  strip `SET` to hints). New `split_set_hints` helper (MaxCompute sqlglot
  tokenizer-based, string/comment-aware, verbatim-preserving — non-SET SQL
  is never AST-regenerated). `classify_sql` is unchanged, so non-`key=val`
  SETs (`SET LABEL`, `SETPROJECT`) stay gated as `write`/unparseable. A
  standalone `SET k=v` with no query is rejected with a clear "no query"
  message. Affects `execute` / `submit` / `cost` / `explain` / `review`.

### Changed

- Dropped the OSS install/update channel from `update_check` /
  `update` / `doctor` (+ tests).
- CI: moved `mypy` out of the `ci.yml` test matrix (it ran 3×, once per
  Python — version-independent) into a single `mypy-blocking` job in
  `lint.yml`; `mypy` is now also included in the `lint-diff` PR-comment
  report (new/resolved type errors alongside ruff/ty, codecov-style) and
  `.lint-reports/` is uploaded as an artifact. Parallelised pytest with
  `pytest-xdist` (`-n auto --dist loadscope`); test jobs are ~50% faster.

## [0.17.3] — 2026-06-22

### Fixed

- **Site version badge stuck on v0.16.** The six `<span class="ver">` markers
  had been hand-edited to the literal `v0.16`, so the pages.yml deploy-time
  `sed s/v__MINOR__/v${MINOR}/` matched nothing and every release showed
  `v0.16`. Restored the `v__MINOR__` placeholder; the deployed site now shows
  the current minor (`v0.17`).

### Changed

- **PyPI publish now goes through the `pypi` GitHub Actions environment**
  (matching the PyPI-side Trusted Publisher environment-protected setup),
  giving a proper deployment record per release and enabling required-reviewer
  / branch protection on publishes.

## [0.17.2] — 2026-06-22

### Added

- **Code coverage badge.** Codecov tokenless (OIDC) upload is now wired up:
  `ci.yml` grants `id-token: write` and sets `use_oidc: true` so coverage
  uploads from protected branches authenticate via the Codecov GitHub App
  without a token. README badge:
  `[![codecov](https://codecov.io/gh/aliyun/maxcompute-semantic/graph/badge.svg)]`.
- **Python classifiers** in `pyproject.toml` (`3.10` / `3.11` / `3.12` + Apache
  license) so the shields.io `pypi/pyversions` badge renders instead of
  "missing".

### Changed

- `report-issue` skill: internal Aone upstream project id `2155299` → `871418`.

## [0.17.1] — 2026-06-22

### Fixed

- **`mcs sql execute` no longer fails on sync timeout.** The instance keeps
  running and the command returns `sync_timed_out: true` with the real
  `instance_id`, `logview_url`, and a copy-pasteable `next_step` (including
  `--profile` / `--project`). The old remediation message referenced a
  nonexistent `--timeout` flag; it now carries real values.
- Added `--timeout` flag to `mcs sql execute` (default **30s**, down from the
  old hard-coded 120s). Build phases keep the original 120s timeout.
- Fixed the old timeout error remediation text that told users to "raise
  `--timeout`" on a command that had no such flag.
- Bumped `torch` 2.12.0 → 2.12.1 to clear CVE-2025-3000 (low; transitive via
  the optional `vec` extra).

### Changed

- **`report-issue` skill:** added sensitive-info redaction model (credentials
  stripped unconditionally; other items flagged for user review) and a hard
  confirmation gate before any submit or paste. Added a manual hand-off path
  for sandboxed / headless environments where neither `a1` nor `gh` can
  authenticate.
- GitHub Releases are now created automatically from `CHANGELOG.md` when a
  `v*` tag is pushed (new `github-release` job in `publish.yml`).

## [0.17.0] — 2026-06-22

### Added

- **MaxCompute SQLGlot dialect** with ANTLR grammar validation — the dialect is
  cross-checked against the ANTLR parser to keep parsing behaviour aligned
  (`tests/grammar/test_antlr_vs_sqlglot.py`, `tests/unit/dialect/`).
- `ALTER TABLE` parser support for 5 ODPS extensions.
- **Internal Aone destination restored in the `report-issue` skill.** The skill
  now auto-detects the environment: `a1` CLI present → internal Aone workitem
  tracker; otherwise → GitHub Issues. It confirms title/body/destination before
  submitting and lets the user override the auto-choice.
- GitHub Actions CI/CD: lint, dependency review, GitHub Pages, PR-agent review,
  uv lockfile check, zizmor security scan, and PyPI publish on tag; PR template;
  raised coverage gates and security checks.
- i18n documentation site with locale auto-detection and a language dropdown.

### Changed

- Rewrote `README.md` / `README.zh-cn.md`: new intro framing, explicit CLI quick
  start, a safety & privacy section, and an expanded feature table.

### Fixed

- Resolved mypy type errors across the dialect package and the wider codebase;
  cleared lint errors (unused imports, f-strings); added missing SPDX headers.
- Synced `uv.lock` with the `antlr4-python3-runtime` dev dependency.

## [0.16.2] — 2026-06-09

### Fixed

- Fixed `.gitignore` pattern so `_skill_data/build/` is included in the wheel.
- Fixed GitHub Actions CI to install dev dependencies (`uv sync --extra dev`).
- Added `tomli` fallback for Python 3.10 compatibility (`tomllib` is 3.11+).
- Removed mono-repo-only test assertions that referenced files absent from the
  standalone GitHub repo (CLAUDE.md, AGENTS.md, Makefile, site/).

### Changed

- Added SPDX license headers (`Apache-2.0`) to all Python source files.
- Updated `scripts/install.md` from OSS wheel distribution to PyPI.
- Updated README: split install into human / LLM-agent sections, added
  `curl` raw guide pattern for agent-assisted installation, fixed development
  commands to use repo-root paths.

## [0.16.1] — 2026-06-09

### Changed

- Open-source release on GitHub and PyPI.
- Replaced OSS-based distribution with PyPI Trusted Publisher (OIDC).
- Added Apache-2.0 license and SPDX headers.
- Added GitHub Actions CI/CD workflows.

## [0.14.35] — 2026-06-08

### Fixed

- Preserved permission, auth, syntax, and missing-resource errors raised during
  cost estimation instead of rewriting them as generic uncosted-query blocks.

## [0.14.34] — 2026-06-07

### Fixed

- Required explicit trust before adopting non-ncs `ProcessAuth` commands from
  external maxc / odpscmd credential configs; `mcs profile import-creds
  --trust-process-command` is the non-interactive opt-in.

## [0.14.33] — 2026-06-07

### Fixed

- Made `mcs meta list-schemas` return `default` for 2-level MaxCompute
  projects instead of surfacing the service-side "not 3-tier model project"
  error.

## [0.14.32] — 2026-06-07

### Fixed

- Preserved command-specific options such as `mcs link status --verbose`
  when global output flags are also present, while keeping misplaced global
  flags like `mcs profile list -f json` compatible.

## [0.14.31] — 2026-06-07

### Fixed

- Tightened cost-gate behavior when MaxCompute cost estimation fails: only
  explicit low-risk probes may proceed without an estimate; regular read SQL
  now fails closed instead of bypassing thresholds.
- Added `mcs profile create --ak-secret-stdin` as a safer literal-AK path,
  warned on `--ak-secret`, and validated malformed env-var fallback profiles
  while preserving anonymous fallback behavior.
- Added package-resource checks for runtime skill content and clearer search
  fallback diagnostics for unreadable ODPS table schemas.

## [0.14.30] — 2026-06-06

### Fixed

- Hardened the OSS install and update channels: latest installs now verify the
  wheel SHA256, update metadata must point at the canonical wheel URL, default
  trusted hosts are enforced, and pinned `MCS_VERSION` installs warn that they
  bypass digest verification.
- Enforced the SQL write guard at the MaxCompute client layer for both
  synchronous `execute` and async `submit`, keeping `--allow-write` explicit
  and preserving the cost gate for intentional writes.
- Made async SQL submit return the submitted `instance_id` even when the
  immediate status probe fails, and rejected invalid `wait` timeout/interval
  values before polling MaxCompute.
- Restricted `mcs udf test --args` to literal values so identifiers,
  expressions, semicolons, and malformed quoted strings cannot be spliced into
  the generated `SELECT`.
- Moved reusable profile/project resolution helpers out of `commands.profile`
  so production command modules no longer depend on private command helpers.

## [0.14.29] — 2026-06-06

### Fixed

- Preserved full-result semantics in Bird EX evaluation by disabling the
  reader-side row cap for predicted-SQL execution.
- Kept `MCS_SQL_RESULT_MAX_ROWS` out of isolated eval subprocesses so parent
  shells cannot change benchmark row-window behavior.
- Cleared stale SQL parse details between classifications and made
  unescaped-apostrophe rejections point agents at doubled single quotes.

## [0.14.28] — 2026-06-06

### Changed

- `mcs sql execute` and `mcs sql result` now cap returned result rows at
  10000 by default on the reader side, without rewriting submitted SQL. Use
  `--max-rows` to change the page size, `--max-rows 0` to disable the cap,
  and `--offset` with `data.next_offset` to fetch subsequent pages.

## [0.14.27] — 2026-06-06

### Fixed

- Required SHA256 digests in `latest.json` and published stable release metadata
  with the wheel digest so `mcs update` always verifies the downloaded wheel
  before installing it.
- Preserved compatibility for profiles that used `cost_thresholds: 0/0` as the
  disabled cost-gate sentinel, and exposed the explicit `enabled` flag in
  profile editing/template flows.
- Corrected cost-estimation failure guidance for write-shaped SQL, tightened UDF
  remove-name validation, and made async cancel report `cancelled=true` only
  for actual cancelled states.

## [0.14.26] — 2026-06-04

### Changed

- Clarified the query runtime skill's SQL execution-mode decision so agents
  choose between synchronous `mcs sql execute` and async
  `submit` / `wait` / `result` from the `mcs sql cost` verdict and query
  shape, not from the vague "short query" label. Probes and small-result
  lookups stay synchronous; confirmed-cost, final analytical, unbounded,
  large scan/JOIN/aggregation, prior-timeout queries, and queries the user says
  can run in the background use the async lifecycle.

## [0.14.25] — 2026-06-04

### Added

- Added an async `mcs sql` lifecycle for long-running MaxCompute queries:
  `mcs sql submit` returns a MaxCompute `instance_id` immediately, while
  `mcs sql status`, `mcs sql wait`, `mcs sql result`, and `mcs sql cancel`
  let users and agents poll, fetch rows, or stop the instance later. Status
  payloads include normalized `status_name`, `lifecycle_state`, `terminal`,
  `successful`, and `task_statuses` fields so agents can distinguish
  successful, failed, cancelled, running, and suspended work instead of
  relying on the raw MaxCompute `Terminated` instance string. The synchronous
  `mcs sql execute` result envelope is unchanged.

## [0.14.24] — 2026-06-02

### Fixed

- Kept `mcs package propose --from-stdin` fail-fast on ambiguous or
  unresolvable table references, with regression coverage proving no partial
  proposal rows or versioning commits are written.

## [0.14.23] — 2026-06-02

### Fixed

- `mcs -f json status --tables` now exposes per-table annotation coverage
  fields (`has_ai_context`, `columns_total`, `columns_annotated`, and
  `columns_with_description`) so build/enrich runtime skills can follow their
  documented coverage checks.
- Corrected build and UDF runtime-skill command examples to match the actual
  CLI surface: `mcs build` is profile-scoped, and UDF create/test examples now
  include their required arguments.

## [0.14.22] — 2026-06-01

### Fixed

- Restored atomic failure behavior for `mcs package propose --from-stdin`
  table-resolution errors: ambiguous or unresolvable table entries now fail
  the command before any proposal rows or versioning commits are written.
- Corrected proposal-workflow documentation to use the real
  `mcs package propose --from-stdin` → `mcs package apply <id>` CLI shape and
  clarified that `MCS_NO_VERSIONING` only skips git versioning, not SQLite
  proposal writes.

## [0.14.21] — 2026-06-01

### Fixed

- Added typo-key normalization (`description` → `ai_context`, `col` →
  `columns`, etc.) to `create_proposals_from_yaml`.
- Added list-of-dicts column format coercion (`[{name: col, ...}]` →
  `{col: {...}}`).
- Collapsed redundant except clauses in `apply_semantic_proposal`.
- Wrapped bare `sqlite3.IntegrityError` in `add_metric` as `McsError`.

## [0.14.20] — 2026-06-01

### Removed

- Removed `mcs annotate` command group (table, column, batch, list).
  All annotation writes now go through the proposal queue
  (`mcs package propose` → `mcs package apply`).
- Removed `MCS_NO_ANNOTATE` env var and the `annotate_locked`
  build-time stamping mechanism.
- Removed `last-annotate` keyword from `mcs profile log-show` /
  `mcs profile reset` / `mcs profile fork`.

### Added

- `mcs package propose --from-stdin` now accepts a top-level `metrics:`
  list (creates `metric` proposals that write through `db.add_metric`
  on apply).

## [0.14.19] — 2026-06-01

### Changed

- Removed `annotate.md` from the skill bundle — all annotation guidance
  now routes through the proposal queue (`mcs package propose` →
  `mcs package apply`).
- `mcs build` output now shows a post-build hint when annotation
  suggestions exist (`💡 N annotation suggestions generated — run
  mcs package propose --from-suggestions to review`).
- `mcs build` JSON output now includes `annotation_suggestions_count`.
- `mcs sql review` hint `if_misleading` text now directs to the proposal
  workflow instead of direct `mcs annotate` commands.

## [0.14.18] — 2026-06-01

### Fixed

- `mcs package propose --from-stdin` now reopens a previously rejected
  agent-authored proposal for the same target as a new `suggested` proposal
  instead of reporting it as created while leaving no pending proposal to
  review. This keeps build/enrich agents inside the proposal workflow when
  they correct an earlier reject decision.

## [0.14.17] — 2026-05-31

### Added

- `mcs package propose --from-stdin` can now create reviewed
  `column_semantics` proposals from YAML fields like `role`, `dim_type`,
  `agg`, `id_type`, and `references`, so agents can correct column-role
  suggestions without falling back to direct `mcs annotate` writes.
- `mcs status --tables` JSON output includes `columns_with_description`
  per table, giving build/enrich agents a checkable column-description
  coverage signal.

### Changed

- The build and enrich runtime workflows now route agent-written
  `ai_context`, column descriptions, and column-role corrections through
  `mcs package propose -> show/apply/reject`, keeping semantic maintenance
  in the proposal review queue.
- The served `annotate` runtime workflow has been removed; `mcs annotate`
  remains available as the low-level CLI escape hatch and apply backend.

## [0.14.16] — 2026-05-31

### Fixed

- The runtime build skill now explicitly instructs agents to write table-level
  `ai_context` for every table missing it, so builds do not leave table purpose
  coverage at 0% when columns have annotations.

## [0.14.15] — 2026-05-31

### Fixed

- The runtime onboarding skill now shows valid `mcs profile log/log-show/diff/reset/fork`
  command shapes with `--profile` and explicit refs.

## [0.14.14] — 2026-05-31

### Fixed

- `mcs -f json/yaml profile log`, `profile log-show`, `profile diff`,
  and `profile fork-list` now use the standard structured success envelope
  instead of emitting ad hoc JSON and ignoring YAML format.

## [0.14.13] — 2026-05-31

### Fixed

- `mcs -f json skill catalog` and `mcs -f json skill get ...` now honor
  the global structured-output flag instead of falling back to plain text.

## [0.14.12] — 2026-05-31

### Fixed

- `mcs sql execute`, `mcs sql cost`, and `mcs sql explain` now keep
  profile/client/tier setup failures on the standard SQL JSON stdout
  envelope instead of falling through to the root stderr exception handler.

## [0.14.11] — 2026-05-31

### Fixed

- Public docs now use the current `mcs link status` / `mcs link unlink`
  commands, show `mcs annotate batch --stdin`, and mention `mcs sql review`,
  `mcs package`, and `mcs metric` in the quick references.
- The package README now lists the current `gemini-cli` and `qwen-code`
  platform names instead of only the deprecated aliases.

## [0.14.10] — 2026-05-31

### Fixed

- `mcs metric list/show/edit/remove` no longer create an empty `package.db`
  when the profile has not been built; this preserves `mcs sql review`'s
  syntax-only cold-start mode.
- `mcs --help` now lists `metric` with the semantic-package commands instead
  of after `doctor`.

## [0.14.9] — 2026-05-31

### Fixed

- `mcs profile export` now skips symlinks inside package data instead of
  following them into files outside the profile package directory.

## [0.14.8] — 2026-05-31

### Fixed

- `mcs skill install` and `mcs skill update` now refuse to overwrite
  non-empty real directories, matching `uninstall` ownership semantics and
  limiting replacement to managed links or empty target directories.

## [0.14.7] — 2026-05-31

### Fixed

- `mcs sql review` now returns its standard JSON error envelope when profile
  resolution fails instead of leaking through the generic Click error path.

## [0.14.6] — 2026-05-31

### Fixed

- `mcs skill uninstall` now refuses to delete real directories, limiting
  uninstall ownership to managed symlinks and Windows junctions.
- `mcs profile import --package-path` now refuses non-empty destinations
  instead of merging archive data with stale package files.
- Eval sandbox skill-install verification now expects Codex at its shared
  `.agents/skills` discovery path.

## [0.14.5] — 2026-05-31

### Fixed

- `mcs profile import --name` now refuses names that already exist locally
  instead of overwriting the existing profile.

## [0.14.4] — 2026-05-31

### Fixed

- Bird dataset zip extraction now rejects archive members that would escape
  the target directory.
- `mcs annotate batch` now accepts top-level `name:` as the documented
  single-table alias for `table:`.
- `mcs udf create` and `mcs udf test --args` now quote generated SQL string
  literals with SQL-standard doubled single quotes.
- The Unix installer now skips post-install skill linking cleanly when the
  freshly installed `mcs` executable is still not on `PATH`.

## [0.14.3] — 2026-05-31

### Fixed

- `mcs build` now discovers UDF catalog entries through pyodps instead of
  silently returning an empty placeholder list.
- Benchmark CI now clears the current `tier_cache/<project>` sentinels before
  a run, so stale tier probes cannot mask discovery regressions.
- UDF and SQL command help now reference the current `mcs build` and
  `tier_cache/<project>` surfaces instead of retired command/path names.

## [0.14.2] — 2026-05-31

### Fixed

- `mcs annotate batch` now accepts the documented singular `table:` payload
  with a sibling top-level `metrics:` list.
- `mcs build --refresh` now re-runs column profiling for every table it
  rebuilds before advancing that table's freshness baseline, keeping null
  ratios and distinct-count stats in sync with refreshed samples.
- `mcs build --refresh` now describes newly discovered tables before history
  mining, so mined workload SQL for those tables can influence first-run
  profiling instead of being dropped from attribution.
- `mcs build --refresh` now applies the same default view/object-table
  sampling skip as full builds; `--include-views` still opts back in.
- `eval download-bird --variant dev --json-only` no longer leaves a later
  full dev download stuck without `dev_databases`; the downloader restores
  the database tree from the cached zip, or re-downloads the zip if the
  cache was removed, and treats empty database directories as incomplete.
- Public docs and the modular memory skill no longer advertise the retired
  `mcs feedback record` command or the invalid `mcs memory note --text` form.
- `PackageDB` shutdown cleanup no longer emits ignored `ImportError` noise
  when Python is already tearing down imports.
- The Aone Open Platform cli-hub registration pipeline now documents itself
  as metadata-only and publishes OSS-install shims instead of opaque
  placeholder binaries.

## [0.14.1] — 2026-05-30

### Changed

- **`mcs doctor` now streams each check as it completes** (plain mode).
  Previously every check — including the slow network probes (auth,
  connectivity, tier, update channel) — ran before any output appeared,
  so the command looked frozen for several seconds. The local checks now
  paint immediately and the slow ones land one line at a time. JSON/YAML
  output is unchanged (still a single batched envelope).

## [0.14.0] — 2026-05-30

### Added

- **Semantic package proposal review workflow.** `mcs package propose
  --from-suggestions` promotes build-evidence semantic suggestions into
  reviewable package proposals; `mcs package list-proposals`,
  `show-proposal`, `apply`, and `reject` let agents and humans inspect,
  accept, or discard those suggestions before they change the semantic
  package.
- **`enrich` runtime skill workflow for post-build maintenance.** Agents can
  load `mcs skill get enrich` after a build or refresh to review proposal
  evidence and apply only accepted semantic-package changes.
- **Generated suggestions now go through proposals by default.** Build and
  annotation runtime guidance routes generated suggestions through
  `mcs package` review/apply/reject instead of writing them directly with
  `mcs annotate batch`.

### Fixed

- **Legacy v9 annotation suggestions now migrate to `measure`.** Package
  migration rewrites historical `annotation_suggestions.suggested_role =
  metric` rows to `measure`, so proposal promotion does not generate
  column-level `metric` patches that the annotation validator rejects.
- **Installed skill references now match the proposal-first workflow.** The
  symlinked `_skill/references` build and annotate docs now route generated
  suggestions through `mcs skill get enrich` and the package proposal queue,
  matching the runtime skill guidance.

## [0.13.3] — 2026-05-30

### Fixed

- **`mcs sql review` no longer warns about bare table names in single-source
  3-level profiles.** `mcs sql execute` auto-injects `odps.default.schema`
  for single-source profiles, so bare names resolve correctly; the previous
  `tier.bare-table-in-3level` warning made agents waste turns on unnecessary
  FQN qualification. Multi-source profiles still get the warning, now with
  the available schemas named and a 3-segment FQN suggestion.
- **Restored query-workflow rules dropped in the CLI-served skills refactor,
  and inlined the highest-leverage ones into the workflow body.** The
  monolithic-SKILL.md split had lost several SQL-correctness rules into
  `references/` files that agents rarely load (they run `mcs skill get query`
  without `--full`). Recovered: FROM-table selection + join-cardinality /
  `COUNT(DISTINCT)` discipline (new `references/from-table.md`), the
  projection-trap worked examples + GROUP-BY / one-statement / which-vs-what
  rules (`references/projection.md`), the tier-aware table-reference form
  (`references/rules.md`), the read-only `mcs sql execute` default and the
  check-for-an-existing-metric step (query `SKILL.md`), metric naming
  guidance (`annotate`), dev/prod profile-design guidance (`onboarding`), and
  the `report-issue` workflow (now `mcs skill get report-issue`). The
  projection / FROM-table / cardinality essentials now live in the query
  `SKILL.md` body as an inline checklist so they reach the agent on the plain
  `mcs skill get query` path.

### Added

- **`mcs show --table` / `--tables` JSON now carries a `sql_name` field** —
  the shortest table reference an agent can paste into SQL: the bare name
  for single-source profiles, the 3-segment `project.schema.table` FQN for
  multi-source profiles. Computed at runtime from the current profile, so a
  source add/remove is reflected immediately.

## [0.13.2] — 2026-05-30

### Fixed

- Migrated pre-v12 packages now re-sample schema-unchanged tables once when
  their freshness baseline is missing, so `mcs build` / `mcs build --refresh`
  truthfully establishes `data_modified_at` instead of skipping those rows
  forever.
- Failed sampling/profiling no longer advances `last_sampled_at` /
  `data_modified_at`; failed tables remain incomplete so the next build can
  retry instead of treating stale stats as fresh.
- `mcs build --refresh-min-age-hours` now rejects negative values. Use `0`
  to explicitly disable the data-change re-sample throttle.

## [0.13.1] — 2026-05-29

### Added

- **`mcs build --refresh` is now data-aware.** Previously refresh only
  re-sampled tables whose *schema* changed; a table with new/updated rows
  but an unchanged schema was skipped, so its sample values, null ratios
  and distinct counts went stale. Refresh now also re-samples tables whose
  data changed since the last build (tracked via the table's
  `last_data_modified_time`). Re-sampling is throttled by
  `--refresh-min-age-hours` (default 24) so a constantly-changing hot table
  isn't re-sampled on every refresh; pass `0` to disable the throttle.
- **`mcs status --tables` surfaces per-table freshness** — each table now
  reports `data_modified_at` (the source modification time captured at the
  last sample) and `last_sampled_at`, so you can tell which tables are
  behind the source without a live probe. Compare against a live
  `mcs meta freshness <table>` to confirm.

  Package schema bumped to v12 (adds `data_modified_at` / `last_sampled_at`
  to the tables store); existing rows migrate with NULL baselines instead of
  guessing a data version for samples captured by older package schemas.

## [0.13.0] — 2026-05-29

### Added

- **Scenario-driven table recommendation during profile create/update.**
  Profiles now carry an optional `description` field capturing the scenario
  the package is for (the business questions, domain, metrics). During the
  agent onboarding flow the agent asks for this scenario up front, then
  recommends candidate tables from `mcs meta list-tables` ranked against it —
  you adjust the recommendation instead of picking from hundreds of tables
  blind. The description shows in `mcs profile show`, seeds the
  `mcs profile spec-template`, and `mcs build` records it at the top of the
  package `_overview.md`.

## [0.12.6] — 2026-05-29

### Added

- **`mcs build` now resumes an interrupted build.** If a prior build was
  interrupted partway, re-running `mcs build` skips tables that already
  finished (sampled/profiled) with an unchanged schema and only builds the
  remaining ones — no need to redo the whole package. `mcs build --fresh`
  forces a full rebuild from scratch. `mcs build --refresh` likewise resumes
  incomplete tables during its incremental diff. A per-table
  `build_complete` flag (package schema v11) tracks this; pre-v11 packages
  are backfilled as complete so the first upgrade does not re-sample
  everything.

### Changed

- **`--parallel` default is now `auto`** — scales worker threads to
  `min(table_count, 32)` instead of the fixed default of 4. Profiles with
  many tables build significantly faster. Pass `--parallel <N>` to override.
- **Build progress now shows elapsed time and ETA.** The sampling/profiling
  phase emits `(done/total, ~Xm remaining)` progress. The completion line
  includes total elapsed time. JSON envelope output adds `elapsed_seconds`
  and `parallel_workers` fields.

## [0.12.5] — 2026-05-28

### Added

- **`mcs skill catalog` and `mcs skill get <name>` now serve task-specific
  runtime skill workflows from the installed package.** Available workflows
  are `query`, `build`, `annotate`, `onboarding`, `memory`, and `udf`;
  `--full` includes referenced guidance files for agents that need the full
  workflow context.

### Changed

- **The installed `maxcompute-semantic` skill is now a thin discovery
  stub.** Query agents load only `mcs skill get query`, while explicit
  build/refresh, annotation, onboarding, memory, and UDF tasks load their
  own focused workflows. The query workflow keeps the hard `mcs build` /
  `mcs annotate` ban and preserves syntax-only `mcs sql review` guidance for
  unbuilt profiles.
- **Benchmark prompts now request the runtime workflow explicitly.** Per-case
  query prompts load `mcs skill get query`; profile-build prompts load
  `mcs skill get build`.

## [0.12.4] — 2026-05-28

### Changed

- **Skill query flow now hardens the no-package path against accidental
  rebuilds.** The top-level skill index adds a query-flow build ban:
  agents must not run `mcs build` / `mcs annotate` while answering data
  questions, and `mcs show` / `mcs status` no-package signals route to
  cold-start metadata instead. `mcs sql review` no longer refuses unbuilt
  profiles with `MCS_PACKAGE_NOT_FOUND`: it returns a success envelope
  with `review_mode: syntax_only`, still runs package-independent syntax /
  dialect / tier checks, and marks semantic hints / coverage skipped.
  `references/query.md` and `references/cold-start.md` now spell out that
  this is not a reason to build the profile mid-query.

## [0.12.3] — 2026-05-27

### Added

- **`mcs sql review '<SQL>'` — stateless pre-execution conformance check.** Returns a JSON envelope `{issues, hints, model_coverage}` without touching MaxCompute: it reads the profile's `package.db` and BM25-indexed memory only, so the round-trip is sub-second and can run on every non-trivial query before the cost gate. Eight deterministic issue rules cover the failure classes we kept seeing in benchmark replay — four dialect (`sqlite-iif`, `sqlite-strftime`, `sqlite-julianday`, `substr-negative-start`), two schema (`table-not-found`, `column-not-found` with available-column suggestions), one tier (`bare-table-in-3level`), one type (`string-date-compare`, lex-vs-chrono guard). Four inferential hint kinds add advisory signal: two join hints (`join.not-declared`, `join.bridge-suggested`) read from the profile's `joins` table, one aggregation hint (`aggregation.dimension-aggregated`) reads `column_semantics.role`, one pattern hint (`pattern.verified-match`) reads `mcs memory verify` entries by SQL shape. Every hint carries an `if_misleading` text showing the exact `mcs annotate` / `mcs memory remove` command that would correct the underlying data — a wrong hint is data, not noise. Refuses non-read SQL with `MCS_REVIEW_UNSUPPORTED`; refuses unbuilt profiles with `MCS_PACKAGE_NOT_FOUND`.
- **`mcs sql execute` success envelope now carries an optional `next_step` field** with a JIT one-liner — for read SQL referencing real tables, the field suggests `mcs memory verify --tables ... --question '<NL>' --sql '<this SQL>'` so the agent can teach the verified result back into the profile's memory store after the user confirms. CTE names are excluded from the `--tables` list and FQN qualifiers (`project.schema.table`) are preserved. The field is omitted entirely for writes / table-less SELECTs / unparseable SQL — existing callers see no shape change.
- **SKILL.md and `references/query.md` updated** with the new step. The Decision Matrix gains a "Review SQL before run" row, and the numbered query workflow inserts review as the new step 5 (between Compose and Cost-gate); subsequent steps renumber.

### Changed

- **`_build_success_envelope` in `mc_client/client.py` now accepts a `sql=` kwarg** so it can compute the `next_step` hint. Existing call sites pass through unchanged; the field is conditional, so envelopes for writes / table-less SELECTs / unparseable SQL keep the prior shape.
- **`site/docs.html` documents `mcs sql review`.** The §3.3 table now
  lists `sql review` alongside `execute` / `cost` / `explain`, with a
  short example and a card-note explaining the `issues` / `hints`
  two-layer envelope.

### Fixed

- **Two-segment routing now prefers the active SQL-session project
  when multiple sources share the same schema.** `schema.table`
  references resolve against `profile.compute_project` / the routed
  target project first, so a profile with both `proj_a.schema_x.orders`
  and `proj_b.schema_x.orders` no longer routes
  `schema_x.orders` to whichever source appears first.
- **Two-segment review resolution no longer falls back to another
  project after a target-project miss.** If `--project proj_b` (or the
  routed project) has `schema_x` but not `schema_x.orders`, review now
  emits `schema.table-not-found` instead of validating columns against
  `proj_a.schema_x.orders`.
- **`project.table` two-segment fallback is restricted to the default
  schema.** The 2-level form `proj.table` no longer resolves to
  `proj.some_schema.table`, which would make an invalid 3-level
  reference look valid.

- **`mcs sql execute/cost/explain` routing now honors two-segment
  FQNs.** `SELECT * FROM schema_b.orders` no longer silently routes
  to the first source that contains bare `orders` — it matches the
  `schema_b` segment against profile sources first.
- **Two-segment review resolution prefers the target project.** When
  `--project proj_b` is set and multiple sources share the same schema
  name, `resolve_source_for_table` now validates columns against the
  target project's table rather than whichever source appears first.
- **`PackageDB.get_table` is now case-insensitive.** `SELECT FROM
  ORDERS` correctly resolves to the `orders` row so `column-not-found`,
  type checks, and coverage calculations no longer silently suppress
  findings on case-mismatched SQL.

- **`schema.column-not-found` no longer false-positives on projection
  aliases referenced from `ORDER BY` / `GROUP BY` / `HAVING` /
  `QUALIFY`.** MaxCompute (like standard SQL) lets these clauses
  reference SELECT-list aliases by name: `SELECT COUNT(*) AS cnt
  FROM orders ORDER BY cnt` is valid because `cnt` is the projection
  alias, not a column on `orders`. Pre-fix the rule walked every
  `exp.Column` without recognizing the alias, resolved `cnt` against
  the single real table, and emitted an `error`-severity
  `schema.column-not-found`. Since the skill says to fix every error,
  the false positive could block or rewrite valid SQL. The fix scopes
  alias suppression to the clauses that may reference SELECT-list
  aliases (WHERE is excluded — it evaluates before the projection so
  alias refs there are genuine errors) and only within the same Select
  that declared the alias, so a nested subquery inside an outer ORDER
  BY still resolves its own columns normally.
- **Two-segment `schema.table` / `project.table` FQNs no longer
  silently fall through to a same-named table in an unrelated schema.**
  Pre-fix `SELECT amount FROM schema_b.orders` could resolve to
  `default.orders` (any source whose bare `orders` table happened to
  carry an `amount` column), silently hiding that `schema_b.orders` is
  missing the reference. `check_table_not_found` and
  `resolve_source_for_table` now honor the explicit segment: try
  schema-form first (the standard 3-level read where the project is
  implicit), then project-form (the 2-level shape), and return `None`
  / emit `schema.table-not-found` when neither matches.
- **Alias lookup is now case-insensitive.** Pre-fix the alias map
  stored the raw alias key but lookups lower-cased the table
  qualifier, so `SELECT o.bogus FROM orders O` silently dropped the
  column reference: the map held `"O"` but the lookup was for `"o"`.
  Affected `schema.column-not-found`, `type.string-date-compare`,
  `aggregation.dimension-aggregated`, and the coverage calculator —
  hints / issues silently dropped on mixed-case aliases. The
  `alias_to_table_in_select` helper now lower-cases keys at
  insertion time, matching MaxCompute's case-insensitive identifier
  semantics.
- **`join.not-declared` and `join.bridge-suggested` now pair joins
  by the ON-clause column references, not source order.** Pre-fix
  `orders JOIN users ON ... JOIN payments ON orders.id =
  payments.order_id` was paired as `[(orders, users), (users,
  payments)]`, firing a wrong `join.not-declared` hint for `users
  <-> payments` even when `orders <-> payments` is declared. The
  pairing logic now inspects each JOIN's `ON` condition and pairs the
  joined-in table against the tables its ON references resolve to;
  it falls back to the source-order chain only when the ON clause is
  absent (CROSS JOIN / NATURAL JOIN / USING) or none of its columns
  map to a known FROM/JOIN table.
- **`_classify_sql` fails closed on incomplete SQL.** Pre-fix
  `error_level=ErrorLevel.IGNORE` silently returned a half-constructed
  `Select` for input like `SELECT * FROM orders WHERE`, so the
  classifier called it `"read"` and the write-guard let the broken
  SQL through to the server. The helper now uses `ErrorLevel.RAISE`
  and catches the resulting `ParseError`, bucketing incomplete /
  syntactically broken input as `"unparseable"` so `mcs sql execute`
  rejects with the `WriteOpRejected` envelope and `mcs sql review`
  refuses to lint it.

- **sql_review alias resolution is now scoped per `SELECT` instead of
  statement-wide.** The schema / type / coverage / aggregation passes
  built their alias map by walking `stmt.find_all(exp.Table)`, so a
  nested subquery that reused an outer alias for a different table
  (e.g. `SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM other
  o WHERE o.x = 1)`) silently overwrote the outer entry. The outer
  `o.id` then resolved against `other` and the schema rule fired a
  false `error`-severity `schema.column-not-found` against valid SQL;
  the type / aggregation passes mis-routed against the wrong table's
  type / annotation; coverage under-counted annotated columns. The
  fix walks `col.find_ancestor(exp.Select)` and builds the alias map
  from that Select's direct `args["from_"]` + `args["joins"]`, so
  outer and inner scopes never cross-resolve. The unqualified-column
  single-table fallback is similarly scoped to the enclosing Select.
- **`join.not-declared` and `join.bridge-suggested` no longer pair a
  subquery's JOIN against the outer FROM table.** The earlier
  `_sql_join_pairs` seeded `left` from a top-level `stmt.find(exp.From)`
  and then walked `stmt.find_all(exp.Join)` across every nested
  subquery, so `SELECT * FROM orders WHERE EXISTS (SELECT 1 FROM
  events JOIN users ON events.user_id = users.id)` fabricated a
  phantom `orders <-> users` pair and missed the real `events <->
  users` one. The loop now iterates `stmt.find_all(exp.Select)` and
  pulls each Select's own `args["from_"]` + `args["joins"]`, so each
  scope contributes only its own pairs.

- **`schema.column-not-found` no longer mis-resolves outer CTE column
  refs to a base table nested inside the CTE body.** Pre-fix, the
  unqualified-column branch scanned `stmt.find_all(exp.Table)` over
  the whole statement and treated the lone non-CTE table it found
  (e.g. `events` in `WITH ev AS (SELECT id AS user_id FROM events)
  SELECT user_id FROM ev`) as the implicit FROM target — flagging the
  outer `user_id` CTE-output alias as missing on `events` even though
  the outer SELECT never touched `events`. Since this rule is
  `error`-severity and the skill says to fix every error, the false
  positive could block or rewrite valid CTE-heavy SQL. The lookup now
  scopes to the column's enclosing Select's *direct* FROM/JOIN
  tables, matching the actual visibility rules.
- **`join.not-declared` and `join.bridge-suggested` are FQN-aware.**
  Previously the helpers keyed pairs and the bridge graph on bare
  table names, so in a multi-source profile a declared join for
  `proj_a.default.orders` ↔ `proj_a.default.users` silently
  suppressed the undeclared-join hint for `proj_b.default.orders` ↔
  `proj_b.default.users`, and the bridge BFS could synthesize phantom
  paths across sources via look-alike intermediate table names.
  Endpoints are now identity-keyed on `(source_key, table_name)`;
  messages are qualified as `source_key.table` so the agent can tell
  which source the hint refers to, and the hint evidence carries
  per-side `left_source_key` / `right_source_key` fields.

- **FQN-aware dedup in `aggregation.dimension-aggregated` hint.** The
  previous round swept `check_column_not_found` and
  `compute_model_coverage` to FQN-aware dedup keys but missed the
  aggregation hint, which still keyed on the bare `(func, table, col)`
  triple. Same-bare-name tables from different sources
  (`proj_a.default.orders.amount` vs `proj_b.default.orders.amount`)
  were collapsed by the first aggregate, silently dropping a real
  wrong-role hint against the second source's column. Dedup now uses
  the full `(func, catalog, db, name, col)` tuple so each FQN
  reference is checked independently.
- **Focused-mypy cleanup on the touched review surface.** Three
  type-soundness gaps in newly-introduced / Round-2-touched files now
  hold: `ReviewContext.to_source_key` narrows the
  `lookup_source_key` return to `str | None` before returning (the
  bare assignment leaked `Any`); `parse_statements` filters
  `sqlglot.parse()` results via `isinstance(exp.Expression)` so the
  list-comprehension's element type matches the annotation; and the
  `odps.errors` import in `mc_client/tier.py` carries a dual
  `[import-untyped, unused-ignore]` silence so the ignore is valid
  both under package-local mypy (which sees the untyped import) and
  workspace-root mypy (whose `odps.*` override pre-silences the
  import, otherwise flagging the ignore as unused).

- **`mcs sql review` tier check reads the routed data project, not
  `compute_project`.** Standard dev/prod profiles configure
  `compute_project` (the billing project, often tier 2) distinct from
  each `DataSource.project` (the actual data home, often tier 3). The
  prior fix routed tier lookup through `compute_project` unconditionally
  to dodge a None crash on cross-source SQL; the side effect was that
  bare-table SQL against a tier-3 data project saw `compute_project`'s
  tier-2 sentinel and silently skipped `tier.bare-table-in-3level`. The
  CLI now prefers the routed `target_project`, falling back to
  `compute_project` only when cross-source SQL leaves the route None.
- **`dialect.sqlite-iif` no longer flags `IIF(...)` inside string
  literals or comments.** The 0.11.1 source-text regex matched `IIF(`
  anywhere in `ctx.sql`, including `SELECT 'IIF(x,1,2)'` and
  `-- IIF(x,1,2)` — since the rule is `error`-severity and the skill
  says "fix every error", a literal/comment hit blocked valid SQL. The
  rule now walks sqlglot's tokenizer (which naturally strips quoted
  strings and comments) and flags only `IIF` `VAR` tokens followed by
  `L_PAREN`. Side benefit: a bare `IIF` identifier (column name in
  `SELECT IIF FROM t`) is no longer flagged.
- **FQN-aware dedup keys in `check_column_not_found` and
  `compute_model_coverage`.** Two same-bare-name tables from different
  sources in one SQL (e.g. `proj_a.default.orders a JOIN proj_b.default.orders b`)
  were collapsed by bare `(table, col)` / `table.name` keys: the
  column rule skipped checking `b.amount` once `a.amount` was seen,
  and coverage reported one referenced table even when two FQN-distinct
  ones were touched. Both surfaces now key on the full
  `(catalog, db, name)` triple so each FQN reference contributes
  independently.

- **`mcs sql review` no longer makes a live MaxCompute round-trip.** The
  previous build fell through to `MaxComputeClient` + `list_schemas` on
  tier-cache miss, breaking the spec's "no MC round-trip" promise that
  makes review safe to run before every query. Tier resolution now
  pins `allow_live_probe=False`, returning the operationally-safe `"3"`
  default on cache miss. Sibling tier callsites (execute / cost /
  explain) keep the live probe.
- **`mcs sql review` no longer crashes on cross-source SQL.** A JOIN
  whose tables span two `DataSource` projects in a multi-source profile
  produced `tier_cache_path(profile, None)` — `_route_project` correctly
  returns None for cross-source SQL but `get_tier` rejected it before
  the JSON envelope could wrap. The CLI now routes tier resolution
  through `profile.compute_project` (same shape as execute / cost /
  explain).
- **`dialect.sqlite-iif` no longer false-positives on valid MaxCompute
  `IF(cond, a, b)`.** sqlglot collapses both `IIF(c, a, b)` and
  `IF(c, a, b)` into the same three-arg `exp.If` node, so the prior
  AST-based detection flagged every valid IF call. The rule now uses
  a word-bounded source-text regex on `ctx.sql` (also a side benefit:
  IIF in otherwise-unparseable SQL still gets flagged).
- **FQN-aware bare-name resolution across the review surface.** In
  multi-source profiles where two `DataSource`s expose the same table
  name, `ctx.to_source_key(table_name)` returned whichever source was
  listed first — silently ignoring the 3-segment FQN the user wrote
  (`proj_b.default.orders`). Type rule reported wrong column types,
  aggregation hint fired against the wrong role, and coverage credited
  the wrong source's annotations. A new shared
  `resolve_source_for_table` helper prefers the explicit
  `origin.catalog` / `origin.db` from the AST when present; bare-name
  resolution stays as the fallback. Applies to `rules/type_check`,
  `rules/schema`, `commands/sql_review/coverage`, and
  `hints/aggregation`.

## [0.12.2] — 2026-05-27

### Fixed
- `mcs -f json show --table T` now includes a compatibility
  `data.tables[0]` alias with `columns_index` so agent scripts that
  accidentally use the batch JSON shape no longer fail with
  `KeyError: 'tables'`; the canonical single-table shape remains
  `data.columns`.
- Skill query docs now spell out the distinct JSON shapes for
  overview, single-table, and batch `mcs show` output.

## [0.12.1] — 2026-05-26

### Fixed
- OSI export now writes metric `ai_context` to the native
  `semantic_model.metrics[].ai_context` field instead of hiding it in
  `custom_extensions[]`, matching OSI core schema v0.2.0.dev0.
- OSI dataset `source` now identifies the physical table (`project.schema.table`
  for normal mcs source keys) instead of reusing the profile-level
  `source_key` for every table in a source.
- `mcs profile export --osi --output <nested/path.yaml>` now creates the
  output parent directory and wraps write failures in the normal mcs error
  envelope.

## [0.12.0] — 2026-05-26

### Breaking
- Column-level `semantic_role = "metric"` renamed to `"measure"`. The
  PackageDB schema is bumped to v10 (auto-migrates on open). The
  `--role metric` flag on `mcs annotate column` is rejected with a
  pointer error directing the user to either `--role measure` (for
  the column-property tag) or `mcs metric add` (for the new top-level
  entity). Batch YAML files that used `role: metric` inside `columns:`
  need updating to `role: measure`.

### Added
- New top-level metric entity: `mcs metric add/list/show/edit/remove`
  for profile-global named SQL expressions (e.g.
  `total_revenue = SUM(orders.amount)`). Metrics live in the new
  `metrics` table with `UNIQUE(name)` across the profile (no source
  binding — see ADR-0002).
- `mcs annotate batch` accepts a top-level `metrics:` list alongside
  the existing `tables:` / `table:` shape.
- `mcs status` and `_overview.md` surface the per-profile metric count
  and metric definitions.
- OSI export emits `semantic_model.metrics[]` for each top-level
  metric, with the expression in the `dialects[]` slot and `ai_context`
  carried through `custom_extensions[]`.
- OSI export now uses `<source_key>__<table>` qualified dataset names
  for both single-source and multi-source profiles, fixing
  `validate_unique_names` collisions in multi-source profiles. Existing
  golden fixture (`expected_export_small.yaml`) updates accordingly.
- OSI export emits column-level measure metadata as a nested
  `measure: {agg: ...}` sub-object under `custom_extensions[].data`,
  alongside the existing flat `agg` key (back-compat; flat key will
  drop in v0.13).

### Documentation
- New SKILL.md sections: "Using metrics" (consume existing metrics
  before re-deriving SQL) and "Sedimenting metrics" (only after the
  user confirms both correctness AND reuse intent).
- New `references/metrics.md` covering the metric verb group.
- ADR-0001 (measure/metric rename) and ADR-0002 (profile-global
  namespace) committed under `packages/maxcompute-semantic/docs/adr/`.
- CONTEXT.md glossary established for the package.

## [0.11.1] — 2026-05-26

### Added

- `mcs skill install -p qoderwork` — Qoder Work (Qoder enterprise edition) added to the platform registry. Local: `.qoderwork/skills/`; global: `~/.qoderwork/skills/`. Covered by `--all` and `--detect`.

### Fixed

- `mcs skill install --target <dir>` no longer wipes co-located skills when the supplied path's basename is not `maxcompute-semantic`. Previously, passing e.g. `--target ~/.qoderwork/skills/` deleted the entire `skills/` directory (every peer skill alongside ours) via the install-time overwrite branch. The CLI now auto-appends `maxcompute-semantic` to the target when missing and prints a one-line note so the install lands at `<dir>/maxcompute-semantic/`. As defense-in-depth, the internal removal routine refuses to recursively delete any non-empty directory whose basename is not `maxcompute-semantic` (or empty), surfacing the unexpected entries instead.

## [0.11.0] — 2026-05-26

### Added

- `mcs profile export --osi [--out FILE]` — export the active profile's package as [OSI](https://open-semantic-interchange.org/) (Open Semantic Interchange) YAML for interop with external semantic-model tooling. Output conforms to OSI core schema v0.2.0.dev0. Default tar.gz behaviour is unchanged.
- New internal package `maxcompute_semantic.osi/` containing the OSI translator. mcs internal vocabulary (`tables` / `columns` / `joins` / `semantic_role` / `dim_type`) is unchanged; OSI vocabulary appears only at the export boundary.

## [0.10.21] — 2026-05-25

### Changed

- **Internal: `ProfileContext` value object + `@profile_command`
  decorator.** Introduces `maxcompute_semantic.auth.context.ProfileContext`
  (frozen dataclass bundling `profile`, `project_override`,
  `schema_override`, `renderer`) and `maxcompute_semantic.commands.
  _profile_command.profile_command` (click subcommand decorator that
  injects the context, registers the `--project` / `--profile` /
  optional `--schema` flag triple, runs `reject_if_fork` for write
  verbs, wraps the body in the `McsError → renderer.error → sys.exit`
  ladder, and fires `commit_after_command` on the success-path tail).
  Pilot conversions: `mcs memory note` (write verb with commit hook)
  and `mcs sql cost` (read verb with `--schema`). The remaining ~36
  click verbs follow in a later release. No user-visible behavior
  change — flag triples, exit codes, envelope shape, and commit
  semantics all match the previous output verbatim.

## [0.10.20] — 2026-05-25

### Changed

- **Errors live in one tree.** Every `McsError` subclass now lives in
  `maxcompute_semantic.errors.*` (auth, build, mc, memory, versioning,
  annotate). The wire codes are an `ErrorCode` `(str, Enum)` so the
  contract used by the JSON envelope, eval harness, and CI summaries
  is a single enumerable list — and `MEMORY_NOT_FOUND` →
  `MemoryNotFound` is normalized to match the PascalCase used by every
  other code. The old import paths (`mc_client.errors`, `auth.errors`,
  `build.errors`, `memory.errors`, `versioning.errors`) keep working
  via deprecation shims for one minor cycle. New `to_envelope()`
  method on `McsError` is now the single rendering site for the
  `{"status": "error", "error": {...}}` shape, and a
  `@maps_pyodps_errors` decorator replaces the copy-pasted
  `except odps_errors.ODPSError` blocks in `mc_client/client.py`. No
  user-visible behavior change — exit codes, wire codes, and envelope
  shape all match the previous output verbatim.

- **SKILL.md: forbid `cd` before invoking `mcs`.** Profile resolution
  via cwd-link is keyed on the directory the shell currently sits in,
  so any `cd` (into the installed skills directory, a `/tmp` working
  area, anywhere) silently drops the link and the next `mcs` call
  falls through to the env-var fallback — which may resolve to a
  different MaxCompute project than the bound profile. The new
  guidance is "stay in your starting cwd; pass absolute paths inside
  arguments instead." Empirically observed on qwen3.7-max which
  prefixes `mcs` invocations with `cd <skills-dir> &&` for tool
  discovery — the resulting `mcs meta list-tables` returns the
  fallback project's flat table list and the agent picks a wrong
  table.

## [0.10.19] — 2026-05-24

### Fixed

- **Windows `mcs skill install` now succeeds without Developer Mode or admin rights.** The 0.10.18 install path called `os.symlink`, which on a stock Windows install requires elevated privileges — the user saw `error: symlink failed — On Windows, creating symlinks requires Developer Mode or administrator rights` and the install aborted. The installer now tries `os.symlink` first and falls back to a directory junction (`mklink /J`) on the resulting `OSError`. Junctions don't need elevation and the agent reads them transparently. Both forms are recognized throughout `skill install / update / uninstall / list / diff / path` and by `mcs doctor`'s skill-install check. The fallback message names what happened so the user knows they got a junction rather than a symlink.

- **Missing `git` no longer breaks `mcs build` / `annotate` / `memory` / `udf` / `profile create`.** The auto-commit hook now treats an absent `git` binary as a soft opt-out: the user's write to `package.db` and markdown still happens, the snapshot is silently skipped, and a one-shot warning surfaces naming the install paths and the `MCS_NO_VERSIONING=1` opt-out. Explicit versioning verbs (`mcs profile log` / `diff` / `reset` / `fork` / `enable-versioning`) still raise `GitNotAvailable` upfront with the install hint — those verbs are by-definition asking for history, so a hard fail is the right behavior. `mcs doctor`'s `git_available` check downgraded from `fail` to `warn` to match: with the hook tolerant, missing git is degraded-but-functional rather than broken.

### Changed

- **Cross-platform git install hints** in `mcs doctor` and the `GitNotAvailable` remediation. The previous text only named `brew install git` (macOS-only); the new text covers macOS (`brew install git` / `xcode-select --install`), Debian/Ubuntu (`apt-get install git`), RHEL/CentOS/Fedora (`yum install git`), and Windows (`winget install --id Git.Git` or download from <https://git-scm.com/download/win>).

- **`install.sh` / `install.ps1` now default to `mcs skill install --detect -g`** (auto-detect installed agents) rather than forcing `claude-code`. Detected platforms (Cursor, Codex, Gemini CLI, Qwen Code, OpenCode, Windsurf, …) all get the symlink in one shot. The script falls back to `mcs skill install -p claude-code -g` when `--detect` reports no installed agents, so a fresh box still ends up with a working skill link. Override with `MCS_SKILL_PLATFORMS=claude-code` (or any comma-separated list / `all`) to pin specific targets.

- **`install.ps1` now bounds the `astral.sh/uv/install.ps1` bootstrap fetch with `-TimeoutSec 10`** and exits 127 on a fetch failure with a clear "install uv manually" hint. The previous unbounded `Invoke-RestMethod` would hang for 100 s on offline / DNS-blocked machines before the script aborted.

## [0.10.18] — 2026-05-24

### Fixed

- **`mcs --version` (and every other `mcs` verb) now imports and runs on Windows.** The 0.10.17 wheel landed `import fcntl` at module load time in `versioning/lock.py`; `fcntl` is POSIX-only, so any `mcs` invocation on Windows raised `ModuleNotFoundError` before the CLI even reached argparse. The per-profile write lock now uses the cross-platform `filelock` library (POSIX `flock` on Linux/macOS, `msvcrt.locking` on Windows) with a hand-rolled liveness probe — POSIX uses `os.kill(pid, 0)`, Windows uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` via `ctypes` (deliberately NOT `os.kill`, which on Windows calls `TerminateProcess` and would actually kill the prior holder). On-disk layout adds a hidden `.mcs-lock.flock` sibling next to the existing `.mcs-lock` so the PID body file (used for the "another mcs is running (PID NNN)" error message) survives a contender's failed acquire — both files are gitignored and excluded from `mcs profile export` archives.

## [0.10.17] — 2026-05-24

### Added

- **`mcs build --parallel N` fans out per-table sampling and profiling across N worker threads (default 4).** The previous serial loop kept ODPS RPCs back-to-back even though each table's sampling and profiling phase is pure I/O wait — a 5-table × ~110-column build that idled most of its wall clock blocking on `tunnel.open_reader` / `execute_sql` round-trips. The fan-out hits the same MaxCompute endpoint concurrently from one process, cutting wall time roughly proportionally to N up to the per-tenant RPC ceiling. Pass `--parallel 1` to force the old serial path for debug or reproducibility. SQLite-backed `PackageDB` was made thread-safe (`check_same_thread=False` + an `RLock` on every public method) so the workers can flush profile rows concurrently. Per-table failures still land in `summary.errors` via the existing hard_error envelope — one worker raising does not abort the build.

## [0.10.16] — 2026-05-24

### Changed

- **`mcs build` skips `VIRTUAL_VIEW` and `OBJECT_TABLE` objects in sampling and profiling by default.** Views re-execute their underlying SQL on every sample fetch, which routinely turned a single profiling pass into a multi-minute per-object timeout against any non-trivial view; `OBJECT_TABLE` has no row structure to sample in the first place. `MATERIALIZED_VIEW` and `EXTERNAL_TABLE` continue to be profiled normally since their reads are O(stored rows) rather than O(re-run query). Pass `--include-views` to opt back into the legacy "sample everything" behavior. Schema bumps to v9 (adds `tables.table_type` column, populated on next build); pre-v9 profiles read `NULL` for `table_type` and are treated as regular tables for backward compatibility — existing builds keep their old behavior until rebuilt.

### Added

- **`mcs status --tables` JSON envelope now carries a per-entry `table_type` field.** Values are `MANAGED_TABLE`, `VIRTUAL_VIEW`, `MATERIALIZED_VIEW`, `EXTERNAL_TABLE`, `OBJECT_TABLE`, or `—` for legacy NULL on pre-v9 profiles. Lets agents and users see which entries were skipped by the new view-skip default at a glance, instead of having to re-derive object kinds from MaxCompute meta.

## [0.10.15] — 2026-05-24

### Changed

- **`columns_index` date-format hints now ship the wrap recipe
  inline.** The bare marker codes (`[str-date]`, `[str-datetime]`,
  `[str-time]`, `[date]`) compressed away the actionable fix —
  empirically the agent saw `[str-datetime]` on a column and still
  wrote `col > '2014-09-01'` (lex-compare boundary trap) because
  the recipe lived in `references/rules.md` and not in the entry
  the agent was reading. The marker now expands inline to
  `[<code>: <recipe>]` (e.g.
  `[str-datetime: compare via SUBSTR(c,1,10) > 'YYYY-MM-DD']`,
  `[str-date: wrap with TO_DATE(c,'yyyy-MM-dd'); date fns return NULL on STRING]`,
  `[str-time: extract via SUBSTR/REGEXP_EXTRACT; HOUR(STRING) is NULL]`,
  `[date: wrap with FROM_UNIXTIME(c) when stored as BIGINT seconds]`).
  Naming the wrap function explicitly is the load-bearing piece —
  paraphrasing to "use a date function" undoes the lift. JSON
  exporter (`compact_column_entry`) keeps emitting the bare hint
  code in `format_hint`; the recipe expansion is markdown-only.

## [0.10.14] — 2026-05-24

### Added

- **ncs install guidance.** The profile-create wizard preflight,
  `mcs doctor`, and the runtime `AuthBinaryMissingError`
  remediation now link to the Akless CLI install docs
  (<[internal])
  when `ncs` is missing for an internal-endpoint profile. The
  hint wording lives in a single helper (`auth/ncs.install_hint`)
  so the three surfaces stay in sync.
- **`_classify_endpoint` recognises `*.aliyun-inc.com` hosts as
  `internal`.** User-typed intranet endpoint variants (e.g.
  `service.cn-shanghai-corp.odps.aliyun-inc.com`) now default to
  `ncs` auth in Step 3 of the wizard, and benefit from the new
  install-guidance gates above. Preset and public-template
  matches still take precedence.

## [0.10.13] — 2026-05-24

### Added

- **`[str-time]` column marker.** STRING-typed pure-time / duration
  columns whose `format_examples` look like `H:MM[:SS][.fff]` or
  `HH:MM[:SS][.fff]` — without a leading date — now surface as
  `name [str-time]` in `mcs show`'s `columns_index`. Covers lap
  times (`1:34.188`), wall-clock times (`12:34:56`), and response
  durations (`2:30.500`). Marker teaches the agent two traps:
  (a) date / time functions return NULL on STRING (no
  `HOUR(STRING_COL)` recovery), and (b) lexical `ORDER BY` / `MIN`
  / `MAX` on a mixed-width time string is wrong (`'1:34.188'`
  byte-sorts before `'12:34.188'`). The corresponding SKILL.md +
  `references/rules.md` rows nudge the agent to use a sibling
  `*_ms` / `milliseconds` BIGINT column whenever one exists rather
  than parsing the string. Date / datetime detection wins on
  mixed-pattern samples; pure non-time strings (currency codes,
  colon-bearing notes) stay bare.

### Fixed

- **Test-suite stops polluting the user's update-check cache.** The
  `latest_json_server` pytest fixture stubs the `latest.json` HTTP
  endpoint via `MCS_UPDATE_BASE_URL` so update-check tests can inject
  synthetic `latest_version: 9.9.9` payloads. It previously did not
  redirect `MCS_CACHE_DIR`, so any test that triggered the daemon
  probe (`mcs doctor`, `mcs update`) wrote `9.9.9` straight into
  `~/Library/Caches/maxcompute-semantic/update_check.json` on the
  developer's host — surfacing as `✨ A new release of mcs is
  available: 0.10.12 → 9.9.9. Run 'mcs update' to upgrade.` on the
  next mcs invocation. The fixture now sandboxes
  `MCS_CACHE_DIR` to a per-test tmpdir.

## [0.10.12] — 2026-05-24

### Fixed

- **`mcs annotate` accepts natural-English aggregator names.** Agents
  reaching for `agg: average` / `mean` / `total` / `minimum` /
  `maximum` / `count_distinct` / `distinct_count` / `unique_count`
  / `nunique` / `row_count` / `cnt` / `n` previously tripped rule-3
  validation (`agg not in {SUM, COUNT, AVG, MAX, MIN, COUNT_DISTINCT}`)
  and lost the column annotation. These map to the canonical SQL
  verb (e.g. `average` → `AVG`, `total` → `SUM`, `nunique` →
  `COUNT_DISTINCT`) before rule-3 fires. Also reaches the
  `subtype: average` routing path used by the discriminated-union
  YAML shape the agent often emits (`{role: metric, subtype:
  average}`) — both forms now land on the canonical agg on the
  first try.
- **`mcs annotate` accepts ML / data-science role vocabulary.**
  `role: target` / `label` / `outcome` / `response` / `feature` /
  `predictor` / `dependent` / `independent` now resolve to
  `attribute` (none of these carry an analytic role in the
  dimension/metric/identifier OSI taxonomy; they're payload
  columns whose value is what the row "is about"). Agents that
  annotate a training-dataset-shaped table no longer drop the
  outcome-column annotation entirely just because the role token
  was ML vocabulary rather than canonical.

## [0.10.11] — 2026-05-24

### Fixed

- **`mcs annotate batch` human-format output now echoes per-table
  errors.** Previously the summary line `batch: 0/3 tables OK, 0
  columns written, 0 failed` was the only signal when every table
  hit a resolution error (table-not-found, ambiguous bare name,
  empty FQN segment) — the agent had to either re-run with `-f
  json` to see `results[].error` or re-read `references/annotate.md`
  to guess at the cause. Each failing entry now gets a
  `  <table>: <code>: <message> — <remediation>` line, symmetric
  with the existing per-column failure echo. JSON-mode output is
  unchanged.
- **`resolve_table_to_source` accepts the `source_key.table` form**
  that `mcs show --tables` displays (e.g.,
  `acme__warehouse.orders`). Agents that copy a reference back out
  of the rendered package into `mcs annotate batch` /
  `mcs memory verify` payloads now land on the right row directly,
  instead of routing through a bare-name lookup that never matched
  the dotted literal. The 3-segment FQN (`proj.schema.table`) and
  bare-name (`orders`) paths are unchanged; when the LHS isn't a
  registered source_key the dotted form falls through to the
  bare-name path so user typos still surface the standard "not
  found" remediation.

## [0.10.10] — 2026-05-24

### Fixed

- **SKILL.md and `references/profile-history.md` named flags that
  don't exist; both worked examples failed on first copy-paste.**
  `mcs annotate batch --from-file <path>` was Click-rejected with
  "no such option" — the actual flag is `--input`. `mcs profile show
  --format text` errored with "invalid choice: text" — the enum is
  `Choice(["plain","json","yaml"])`. Both surfaces have been
  corrected in `_skill/SKILL.md`, `_skill/references/onboarding.md`,
  and `_skill/references/profile-history.md`.

### Documented

- **`mcs skill {update, uninstall, path, diff}` are now in the
  onboarding reference.** Previously only `install` and `list` were
  documented, leaving agents with no handle on post-upgrade
  maintenance, install verification, or symlink removal. The section
  was renamed `Manage Skill Installation` to match the broader
  scope.
- **`mcs profile log --grep <regex>`** is the only filter for
  searching the per-profile commit log beyond the `-n` window;
  surfaced in `references/profile-history.md`.
- **`mcs sql explain --timeout <secs>`** caps how long mcs waits for
  the plan (default 120s); surfaced in `references/query.md`.
- **`mcs profile remove` is idempotent** — removing a nonexistent
  name exits 0; surfaced in `references/onboarding.md`.

### Project

- **`CLAUDE.md` now requires every CLI-changing PR to declare
  `skill bundle: [updated|n/a]`** in its description, treating the
  installed skill as an executable contract rather than drift-prone
  documentation.

### Fixed

- **`mcs build --refresh` no longer wipes column sample stats
  (`sample_values_json` / `is_enum` / `null_ratio` /
  `distinct_count`).** The schema-hash probe `_run_refresh` runs
  against every live table during classification re-emits each
  column row through `phase_describe_table` → `db.upsert_columns`,
  but the dict carries only schema fields (`name` / `type` /
  `comment` / `is_partition`). Pre-fix, the unconditional
  `ON CONFLICT DO UPDATE SET sample_values_json = excluded.…` clause
  bound `col.get("sample_values_json")` etc. to NULL and clobbered
  whatever `phase_column_sampling` had written. The data loss was
  invisible under normal refresh (markdown re-renders only the
  changed-table set, so unchanged tables kept their stale-but-correct
  .md text); an inference-logic version bump that triggers
  `render_all` surfaces the missing fields in every per-table .md
  as `format_examples` / `sample_values` / `null_ratio` /
  `[const]` markers all disappearing. `upsert_columns` now updates
  sample fields only when the caller's col dict explicitly contains
  the matching key — so `phase_column_sampling` can still set or
  clear samples (including the deliberate `sample_values_json=None`
  path that drops stale enum values when cardinality outgrows the
  gate), while `phase_describe_table` becomes a true schema-only
  round-trip. Recovery: re-run a full `mcs build` (without
  `--refresh`) to re-populate sample stats; older profiles that
  refreshed through the buggy path will keep their NULLs until the
  next full build.

## [0.10.8] — 2026-05-24

### Changed

- **`mcs annotate batch` accepts more general-vocabulary role
  synonyms: `code` / `type` / `enum` / `flag` / `boolean` / `bool`.**
  Build-session telemetry across the no-history smoke arm showed the
  agent reaching for `role: code` as a catch-all for short
  categorical identifier columns (country code, currency code,
  enum-style typeid, post/comment/link type IDs) and `role: type` /
  `role: enum` / `role: flag` / `role: boolean` for the obvious
  categorical-looking cousins — all universal data-modeling
  shorthand that wasn't in the alias map. Each unaliased role
  failed rule-1 and dropped the whole column annotation; one
  observed build session lost 7 columns this way on a single batch.
  All six aliases resolve to `role=dimension` with an auto-fill of
  `dim_type=categorical` so the rule-2 dim_type check passes
  without the caller specifying it explicitly. Generic alias
  expansion — works for any project; no Bird-specific terms.

## [0.10.7] — 2026-05-24

### Changed

- **`mcs annotate` "table/column not found" errors now suggest the
  closest existing names.** A representative build session showed
  the agent looping seven batch attempts on a single off-by-one
  typo because the previous remediation only said "run `mcs build
  --refresh` or check spelling" — no candidate names to consider.
  The new shape prepends up to three closest matches via stdlib
  `difflib.get_close_matches` (cutoff 0.6, case-insensitive) so the
  agent gets explicit candidates to pick from on the first retry:

  ```
  AnnotateNotFound: column 'totl_amount' not found on table 'orders'
    remediation: did you mean 'total_amount'? Or run `mcs build
      --refresh` or check spelling
  ```

  Same wiring covers table-name typos. When no candidate clears
  the similarity threshold (or the source is empty), the
  remediation falls back to the plain "check spelling" form
  unchanged. Generic forgiveness layer — works for any project.

- **`references/annotate.md` now tells the agent to read
  `error.code_subkey` and `error.remediation` before retrying.**
  The example failure envelope was missing `remediation` entirely,
  so the agent had no explicit cue that those fields existed. A
  short paragraph after the envelope spells out the priority: read
  the rule subkey + remediation first, fall back to `--refresh` /
  re-reading the schema only after those have been tried.

## [0.10.6] — 2026-05-24

### Changed

- **`mcs annotate batch` plain-text summary now surfaces the rule
  subkey and remediation hint per failure group.** Previous form
  collapsed failures to `(AnnotateValidation: <colname>)`, leaving
  the caller unable to tell which §1 rule fired or what to do.
  Agents took 4-6 batch retries on a representative build session
  (smoke 42444078) because the terse surface didn't tell them that
  `role: identifier, id_type: name` violates rule-4 and needs to
  switch to `role: attribute`. The new format groups failures by
  `(code, code_subkey, remediation)` tuple so same-rule failures
  collapse to one actionable line:

  ```
  proj__default.sets: 3 failed
    AnnotateValidation/rule-4: code,name,mcmname — set --id-type
      to primary, foreign, or unique
  ```

  The JSON envelope (per-column `error.code_subkey` /
  `error.remediation`) is unchanged — this only enriches the
  human-readable surface that batch-builders actually parse.

## [0.10.5] — 2026-05-24

### Changed

- **SKILL.md "Pick the FROM table" — deprioritize `COUNT(DISTINCT
  pk)` on `[1:n]` joins.** Previous wording listed `COUNT(DISTINCT
  this_table.pk)` as the first remediation for fan-out after a 1:n
  JOIN; agents picked the first option presented and applied it
  defensively, changing the answer when a parent row legitimately
  matched several partner rows. Rewritten to put `COUNT(*)` /
  `COUNT(this_table.id)` (the standard NL-SQL reading of "how many
  X meet condition C") first, with explicit two-clause carve-out
  for when `DISTINCT` is genuinely needed (question explicitly says
  "distinct / unique / different X", or a partner column appears in
  SELECT). Per benchmark-full 42441573 audit, defensive `COUNT(DISTINCT
  c.id)` on a banned-cards × legalities JOIN was the dominant
  regression shape across 3 card_games / european_football_2
  / debit_card_specializing cases — gold uses `COUNT(T.id)` =
  `COUNT(*)` after the same JOIN.
- **SKILL.md "SELECT only what the question asks for" — added
  "respect evidence column-mapping VERBATIM" bullet.** When evidence
  says "eyes refers to eye_colour_id" or "publisher refers to
  publisher_id", project that exact column; do NOT add a JOIN to a
  lookup table to dereference an ID to a `name` / `label` /
  `description` field for "human readability". The semantic profile
  surfaces the lookup JOIN edge; resist the pull. Per benchmark-full
  42441573 audit, defensive dereferencing of color/publisher IDs on
  the superhero cases was the second-largest regression shape after
  COUNT(DISTINCT) defensiveness.

## [0.10.4] — 2026-05-24

### Fixed

- **`PRE_AGGREGATED_NAME_RE` no longer falsely flags identifier-shaped
  columns whose names start with `num` (`number`, `numerator`,
  `numerical`, `numeric`, `numbers`).** The previous regex matched
  any prefix from the aggregation-word list followed by `_` *or* any
  letter; `num` + `b` (from `number`) qualified, so a plain racing-
  number identifier column like `qualifying.number` was tagged with
  a confidence-0.65 `annotation_suggestions` row carrying the "value
  is already an aggregate; SELECT directly, do NOT re-aggregate"
  evidence. Downstream the agent read the per-table markdown,
  treated `qualifying.number` as the canonical driver-number column,
  and skipped the JOIN to `drivers` that would have returned the
  correct racing number — observed across 3 formula_1 regressions
  (cases 0114, 0117, 0136) in `benchmark-full 42441573`. The fix
  splits the prefix list into two tiers: unambiguous aggregation
  words (`avg`, `mean`, `median`, `stddev`, `variance`, `count`)
  still match with `_` or a following letter; short / ambiguous
  abbreviations (`num`, `cnt`) now require a trailing `_` separator.
  `num_takers` / `cnt_orders` (the canonical snake_case aggregation-
  prefix shapes that the existing tests cover) continue to match;
  the previously-false-positive `number` / `numerator` / `numerical`
  / `numeric` no longer surface as pre-aggregated.

## [0.10.3] — 2026-05-24

### Fixed

- **`mcs annotate batch` silently promotes table-entry typo keys to
  their canonical names instead of raising.** Per-build event-stream
  analysis of `benchmark-smoke 42440573` showed the with-history arm
  losing every table's `ai_context` and every column's
  `semantic_description`: the agent's first batch attempt wrote
  `description:` at the table level (intending per-table NL context,
  but the canonical name is `ai_context:`), the validator raised a
  "did you mean 'ai_context'?" UsageError, and the agent's retry
  over-corrected by stripping ALL description-shaped fields
  (including the valid per-column ones) — leaving the profile fully
  annotated structurally but with bare column names and no NL
  context for downstream SQL generation. Post-fix, the existing
  typo map (`description` / `desc` / `comment` / `context` /
  `ai_description` / `ai_desc` / `table_description` → `ai_context`;
  `col` / `column` / `fields` → `columns`) is now applied as a
  silent rename before validation, so the agent's first-attempt
  payload lands intact. Mirrors the rule-3 / rule-4 / rule-5
  soft-drop forgiveness precedent in `build/storage.py`. Truly
  unknown keys (e.g., `role:` at table level — a structural mistake,
  not a vocabulary synonym) still raise so the agent rethinks
  rather than silently losing the field.

## [0.10.2] — 2026-05-24

### Fixed

- **`mcs annotate batch` falls back to the build-phase
  `annotation_suggestions` row when the agent's payload fails §1
  validation.** Previously, a column whose `role` / `agg` / `id_type`
  / `dim_type` combination violated cross-field rules was dropped
  entirely — the agent's intent to annotate was lost, and the
  high-confidence suggestion already produced by Phase 7c sat
  unused in the suggestions table. The fallback now promotes the
  highest-confidence suggestion for that column (confidence ≥ 0.7,
  matching the per-table .md `[pk]` marker floor) to a confirmed
  annotation, preserving the agent's free-form `description` field
  on the way through. Defense-in-depth that mirrors the existing
  rule-3 / rule-4 / rule-5 soft-drop pattern in `build/storage.py` —
  no payload reaches `package.db` empty when a known-good fallback
  is one lookup away. Surfaces in the result envelope per-column
  as `fallback.from_suggestion: true` with the applied role /
  subtype / confidence and the original validation error for
  observability.

## [0.10.1] — 2026-05-24

### Added

- **`mcs profile suggest-creds`** — discover existing-mcs and external
  (maxc / odpscmd) credential candidates as a JSON envelope without
  importing them. Read-only; secrets are stripped. Mirrors the
  CLI wizard's Step 1.5 picker so agents can present the same options
  without committing to import.
- **`mcs profile endpoint-presets`** — list endpoint presets for agent
  use: the public-region URL template, common public regions, and
  the intranet endpoints from `_INTERNAL_ENDPOINTS`. Mirrors the
  wizard's Environment picker.
- **`mcs profile list-ncs-identities`** — enumerate ncs ODPS
  authorizations as a JSON envelope. Gracefully degrades to
  `{"available": false, "reason": ..., "identities": []}` when the
  ncs binary is missing, returns no authorizations, or the probe
  fails. Mirrors the wizard's Step 4 ncs identity picker; agents
  fall back to collecting `employee_id` when `available=false`.

### Changed

- Skill onboarding doc rewritten to mirror the CLI wizard's 8-step
  flow. New "Agent Wizard Flow" section maps each wizard step
  (alias → credential discovery → endpoint → auth → credentials →
  compute project → advanced → sources → submit & test → identity
  confirm) to the agent-callable verb that supplies the data the
  TTY wizard's `iterfzf` picker would. Removes the redundant
  "Onboarding Questions" section, now subsumed into the flow's
  Step 1, Step 2, Step 4.5, and Step 6.

### Fixed

- **`mcs annotate batch` recognizes more column-name-as-role
  shorthand.** Per-case analysis of `benchmark-smoke 42439728` and
  `42439819` identified ~170 columns lost across smoke runs to
  agents reaching for the column's literal name as its role tag
  (the agent writes `role: name` / `role: url` / `role: description`
  / `role: status` for columns of those names, treating the column
  name as a semantic-kind hint). Added these role aliases:
  - Attribute-shaped (no analytic role, just payload):
    `name`, `url`, `description`, `location`, `const`, `constant`,
    `value`.
  - Dimension-shaped, auto-fill `dim_type=categorical`:
    `category` (sibling of existing `categorical` alias), `status`
    (status columns are essentially always categorical filters).
  - Metric-shaped (sibling of existing `numeric`): `numerical`.
- **`mcs annotate batch` accepts the `entity_id` identifier alias.**
  Universal entity-attribute-value-model vocabulary for "this is the
  entity's key column." Maps to `role=identifier` with no
  `id_type` auto-fill — an `entity_id` column could be a local PK or
  a polymorphic FK depending on schema, so the choice is left to the
  build's join_candidates inference layer rather than guessed at
  annotation time.
- **Soft-drop `id_type` when `role=identifier` is given without
  one.** Mirroring the rule-3 soft-demote (metric without agg) and
  the rule-5 soft-drop (id_type=foreign without references), an
  identifier role without an explicit id_type no longer hard-fails
  rule-4. The column lands with `id_type=None` instead of losing the
  entire annotation — SQL generation still benefits from the
  identifier signal, and the join_candidates layer infers
  primary-vs-foreign-vs-unique from data co-occurrence independently.
  Unblocks the new `entity_id` alias path.

## [0.9.3] — 2026-05-24

### Fixed

- **`mcs annotate batch` softens two more agent-vocabulary failure
  shapes.** Investigation of `benchmark-smoke 42439215` found a
  single 2-level European-football build losing 152 column
  annotations to a single batch call. Two new tolerances close that
  gap:
  - Added Kimball `role: context` → `attribute` alias. `context` is
    universal star-schema vocabulary for descriptive payload columns
    and parallels the existing `descriptive` alias. (Recovered 74 of
    the 152 lost columns in the observed failure.)
  - Soft-demoted `role: metric` (or any of its aliases:
    `measure` / `fact` / `numeric` / `measurable` / `quantitative`)
    without an `agg` field to `role: attribute`. Agents frequently
    pick a metric role and forget the aggregation; rather than
    losing the entire column annotation (description,
    semantic_description, etc.), the column lands as an attribute
    so SQL generation still has the descriptive text + column-type
    signal to work with. Mirrors the rule-5 soft-drop of
    `id_type=foreign` without a `references` target. (Recovered the
    other 78 of 152.)

## [0.9.2] — 2026-05-24

### Fixed

- **`mcs annotate batch` tolerates duplicate column keys.** When the
  agent declares the same column twice in one table's `columns:`
  block (typical pattern: once early as one `id_type`, then again
  late with the FK reference), default `ruamel.yaml` raises
  `DuplicateKeyError` and the entire batch is rejected — dropping
  every other column in that table. The loader now sets
  `allow_duplicate_keys = True` so Python-dict last-wins semantics
  apply and the surrounding annotations land. Mirrors the rule-3
  / rule-5 soft-drop philosophy already in `storage.py`.

## [0.9.1] — 2026-05-24

### Fixed

- **`mcs annotate batch` accepts more general-vocabulary role
  synonyms.** Added `categorical` / `numeric` / `numeric_measurable`
  / `measurable` / `quantitative` / `free_text` / `text` / `string`
  to the role-alias map so agents that reach for SQL or
  data-modeling vocabulary — instead of the OSI canonical
  `dimension` / `metric` / `identifier` / `attribute` — get their
  first attempt accepted. `role: categorical` also auto-fills
  `dim_type=categorical` so rule-2 passes without an explicit
  `dim_type`. Investigation of recent `benchmark-smoke` builds
  showed batches losing whole columns to `AnnotateValidationError`
  rule-1 when the agent annotated columns with these natural
  vocabulary names.
- **Soft-drop `agg` when the role isn't `metric`.** Mirroring the
  existing rule-5 soft-drop of `id_type=foreign` without a
  `references:` target, `mcs annotate batch` now silently demotes a
  non-metric column's `agg` to `None` (preserving the rest of the
  annotation) instead of raising `AnnotateValidationError` rule-3
  and dropping the entire column. The strict canonical metric path
  (`role: metric, agg: SUM`) is unchanged; only the cross-role
  combination (`role: identifier, agg: COUNT` /
  `role: attribute, agg: COUNT_DISTINCT`) becomes a no-op on the
  agg field.

## [0.9.0] — 2026-05-24

### Changed (BREAKING)

- **`mcs profile show --format text|json|yaml` removed.** Format
  is now the single global `-f plain|json|yaml` flag — invoking
  the subcommand option exits with a Click "no such option"
  error. Update agent scripts that used the local flag (e.g.
  `mcs profile show foo --format json` → `mcs -f json profile show foo`).
- **`-f yaml` is envelope-wrapped, mirroring `-f json`.** The
  previously-bare yaml output (only emitted by the now-removed
  `mcs profile show --format yaml`) is gone; `-f yaml` on every
  subcommand serializes the same `{status, data}` / `{status, error}`
  envelope `-f json` does, so format swap is purely a serializer
  swap. Callers consuming the bare spec for
  `mcs profile update --from-file` extract `.data`
  (`mcs -f yaml profile show foo | yq .data`).

### Added

- **Global `-f yaml` output format.** Every CLI verb now accepts
  `-f yaml` and emits the envelope shape as YAML on stdout. The
  Renderer's new `is_envelope` property is the canonical way for
  command code to branch "structured envelope vs human prose"
  without enumerating each format name.

### Changed

- **Description scope discipline added to the annotate skill
  reference.** New "description scope discipline" section forbids
  enum listings / numeric ranges / sample values in column
  descriptions; the annotate batch payload must carry business
  semantics + unit/format only. Value enumeration is the lazy
  value-discovery layer's job at query time.

## [0.8.0] — 2026-05-24

### Changed

- **`-f json` mode silences `StaleLockClearedWarning`.** The
  recovery-class warning emitted by `WriteLock` when it finds and
  clears a crashed-prior-mcs's lockfile used to surface on stderr
  alongside the JSON error envelope, breaking tools that scrape
  stderr for the structured payload. In `-f json` mode the warning
  is now filtered (`--debug` / `--verbose` keep it visible for
  diagnosis); `-f plain` is unchanged.

### Added

- **`mcs profile create --from-spec` / `--from-file` reject empty
  `sources[].tables` lists with an actionable error.** An empty
  table list used to save silently and then produce a no-op build,
  which is the worst-of-both failure mode. The validator now
  raises `InvalidProfile` pointing back at
  `mcs meta list-tables --project P --schema S` so the agent /
  user enumerates first and puts the resulting names into the
  spec (or uses the `"*"` wildcard for whole-schema builds).
- **`mcs sql execute` is read-only by default.** The verb now
  refuses DML/DDL (INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/ALTER/
  TRUNCATE/GRANT/REVOKE) and any statement sqlglot cannot classify
  as a known read shape (e.g. `ADD JAR`, `SET LABEL`, vendor DDL)
  *before* the cost gate, returning a `WriteOpRejected` envelope
  (exit code 2). Pass `--allow-write` to submit a write
  intentionally. Internal callers that go through
  `MaxComputeClient.execute_sql` directly (`mcs udf *`,
  `mcs build`'s catalog probes, etc.) are unaffected — the
  guard sits at the CLI layer, not the client.

## [0.7.1] — 2026-05-24

### Added

- **`INFERENCE_LOGIC_VERSION` stamp + offline re-derivation path.**
  A CLI upgrade that ships new semantic-suggestion logic (Phase 7c
  classification, naming-regex heuristics, markdown rendering, or
  workload aggregation thresholds) used to leave existing on-disk
  profiles silently stale until the user ran a full `mcs build` —
  which costs real MaxCompute round-trips (LIMIT 20 sampling +
  APPROX_DISTINCT per table). The build pipeline now stamps the
  current `INFERENCE_LOGIC_VERSION` into each profile's
  `package_settings` table after every full build. On the next
  `mcs build --refresh`, if the stored stamp is behind the CLI's
  current version, the refresh path re-runs Phase 7c against
  already-cached columns and re-renders the full per-table markdown
  bundle using only the on-disk data — zero MaxCompute calls. The
  stamp updates only after a complete success, so a mid-rederive
  failure leaves the old stamp in place and the next refresh
  retries.
- **`mcs doctor` audits all built profiles for stale inference
  layer.** The new `inference_logic_current` check walks every
  registered profile's `package.db`, compares the stored stamp
  against the running CLI's `INFERENCE_LOGIC_VERSION`, and warns
  (with the per-profile `mcs build --refresh --profile <name>`
  remediation) when any profile is behind. The check skips
  profiles that have never been built and never trips exit 1
  (warn semantics).
- **`mcs update` post-install hint points users at the offline
  refresh.** After a successful self-upgrade, the command prints a
  single stderr line telling the user that their built profiles
  may now have a stale inference layer and that `mcs build
  --refresh` reconciles it without MaxCompute round-trips.

## [0.7.0] — 2026-05-24

### Changed

- **`mcs sql {execute,cost,explain}` now auto-routes the client's
  default project by inspecting the SQL.** Previously, a standard-
  mode profile with `compute_project = acme_dev` (the write
  sandbox) and `sources[0].project = acme` (the prod data store)
  would refuse a bare-name query like `mcs sql execute 'SELECT *
  FROM orders LIMIT 10'` with `Table not found` unless the user
  remembered to pass `--project acme`. The verb now parses the
  SQL via sqlglot, looks each referenced bare name up against the
  per-profile `package.db` source-key index, and picks the unique
  owning project: single-source SQL routes to that source,
  multi-source SQL keeps `compute_project` and lets the engine
  route via each FQN, and zero-match SQL (`SELECT 1`, DDL, parse
  failure) falls back to `sources[0].project`. Explicit
  `--project X` still wins, so existing scripts that pin the
  project are unaffected. Closes the dev/prod onboarding cliff
  in standard-mode DataWorks workspaces — agents querying a
  configured source no longer need to thread `--project` on every
  invocation. The matching SKILL.md "Profile design — dev vs prod"
  section is rewritten to reflect the new default.
  ([#82380548]([internal]))

## [0.6.2] — 2026-05-24

### Changed

- **`mcs annotate batch` plain-text output now surfaces the failed
  column names per table when any column fails to write.** Previously
  the summary line (`batch: N/M tables OK, X written, Y failed`) was
  the only signal in plain mode — the per-failure error envelope
  was only available via `-f json`, so an agent that hit a partial
  failure had to either re-issue the entire call with `-f json` to
  discover which columns went wrong, or proceed without retrying
  (leaving those columns unannotated). Each table with failures now
  emits a follow-up line of the shape
  `  <source>.<table>: <N> failed (<error_code>: col1,col2,col3,...)`,
  capped at five names with a trailing `,+K more` count if there
  are more. The singular form (`tables: [single]`) gains the same
  breadcrumb. JSON-mode output is unchanged.

## [0.6.1] — 2026-05-24

### Fixed

- **Annotation lookups (`mcs annotate batch` / `mcs annotate column`)
  now match table and column names case-insensitively** against the
  package's canonical case, mirroring MaxCompute's identifier
  semantics. MaxCompute treats identifiers as case-insensitive (the
  catalog canonicalizes to lowercase), but the storage layer's
  annotation-write path previously used exact-case SQL `WHERE name=?`
  comparisons. Agents that passed an upper- or mixed-case form for a
  column or table name (a common pattern when external schema docs,
  CSV import conventions, or training-data priors disagree with
  MaxCompute's canonical case) silently lost the annotation. A
  real-world build session showed 36/183 columns of a single wide
  table going unannotated on every run because the agent emitted
  upper-case names while storage held lower-case. The fix adds
  `COLLATE NOCASE` to the relevant SELECT comparators in
  `PackageDB._resolve_table_id`, `table_exists`,
  `find_table_by_name`, `lookup_source_key`,
  `set_column_semantics`, and `get_column_semantics`, and rebinds
  the canonical column name from storage into
  `set_column_semantics`'s subsequent UPDATE WHERE clause. Storage
  shape, schema migrations, and the agent-facing CLI surface are
  unchanged; the change strictly expands the set of accepted inputs.

## [0.6.0] — 2026-05-24

This is the user-facing release rollup for the per-profile git-
versioning feature delivered across 0.5.0a39–0.5.0a67 (see the
per-alpha sections below for the step-by-step audit trail). The
canonical design spec is at
[`docs/superpowers/specs/2026-05-23-mcs-profile-git-versioning-design.md`](../../docs/superpowers/specs/2026-05-23-mcs-profile-git-versioning-design.md).

### Added

- **Eight new commands under `mcs profile` for git-backed version
  management of the per-profile semantic catalog**: `log`,
  `log-show <sha>`, `diff <a> <b>`, `reset --to <ref>`,
  `fork <name> --from <sha>`, `fork-list`, `fork-remove <name>`, and
  `enable-versioning` (the explicit upgrade verb for pre-versioning
  profiles).
- **Auto-commit hook on every successful write command** —
  `mcs build`, `mcs annotate {table, column, batch}`, `mcs memory
  {verify, fail, note, recall, list, show, remove, clear, reindex}`,
  `mcs udf {create, test, remove}`, and `mcs profile import` each
  land a commit in the per-profile git repo with an action-prefixed
  subject (`build:`, `annotate:`, `annotate-batch:`, `memory:`,
  `udf:`, `import:`). The commit-message convention is documented in
  the spec.
- **Read-only fork-write guard** at the entry of every write command —
  attempts to write against a `kind="fork"` profile raise
  `ProfileReadOnly` with a two-option remediation naming
  `mcs profile reset --to <anchor>` (advance the parent to the
  fork's anchor) and `mcs profile fork <new-name> --from <anchor>`
  (branch a fresh writable line from the same anchor).
- **`MCS_NO_VERSIONING=1`** opt-out env knob (case-insensitive truthy:
  `1` / `true` / `yes` / `on`) that disables both the per-profile git
  auto-init at `mcs profile create` time and the per-write
  auto-commit hook for the duration of the matching invocation. The
  Bird eval harness's `eval._skill_setup.build_minimal_env` force-sets
  this so the per-case sandbox profiles don't grow throwaway git
  histories that would jitter EX.
- **Five new `mcs doctor` checks**: `git_available`,
  `profile_versioned`, `working_tree_clean`, `forks_healthy`,
  `package_sql_parses` — surfaces the versioning layer's health in
  the same pre-flight pass as auth, connectivity, tier, and build
  data. A new `warn` status complements the existing
  `pass`/`fail`/`skip` taxonomy.
- **`mcs profile show <name>` version trailer**: on a `kind=main`
  profile, the output ends with a `📜 Version` line naming
  HEAD's short-sha + subject and a `🌿 Forks` line listing live
  fork names (when any). On a `kind=fork` profile, the trailer
  reads `🌿 Parent  <parent> @ <short-sha> (<subject>)`. The JSON
  envelope grows the matching `version` / `forks` / `parent` /
  `anchor` keys.
- **End-to-end integration test** at
  `tests/integration/test_versioning_lifecycle.py` walking the
  spec's worked example top-to-bottom: profile create → build →
  annotate-batch → memory verify → fork at anchor → annotation
  drift on parent → fork-write guard → diff → reset → reflog
  recovery → fork-remove → doctor check.

### Changed

- **`Profile` dataclass in `auth/schema.py`** gained three optional
  fields: `kind` (`"main"` or `"fork"`, default `"main"`),
  `parent_profile` (default `None`), and `git_sha` (40-hex anchor,
  default `None`). `validate()` enforces the cross-field invariants
  (all three must be either all-default or all-fork-set together).
  YAML round-trip emits the new fields only when non-default, so
  existing `profiles.yaml` files don't grow stale boilerplate.
- **Profile-name regex** loosened from
  `^[a-zA-Z0-9][a-zA-Z0-9_-]{2,31}$` to
  `^[a-zA-Z0-9][a-zA-Z0-9_\-@:]{2,63}$` so canonical fork-name
  conventions (`<parent>@<short-sha>` for anchor-named forks,
  `<parent>:<label>` for human-named forks like `acme:baseline`) are
  legal without quoting. Existing alphanumeric/underscore/dash names
  remain legal.
- **`mcs profile create` success path** now ends with the auto-commit
  hook landing the inaugural `init: import existing data` commit
  (skipped when `MCS_NO_VERSIONING=1`).
- **`mcs profile remove <main>`** on a main-kind profile with live
  forks is now rejected with a message naming each live fork and
  pointing at `mcs profile fork-remove`. Removing a `kind="fork"`
  profile delegates to the same git-worktree-then-yaml teardown that
  `mcs profile fork-remove` uses, so the parent's
  `.git/worktrees/<short>/` admin entry stays in sync.

## [0.5.0a67] — 2026-05-24

### Added

- **End-to-end integration coverage for the per-profile git-versioning
  worked example.** A new `tests/integration/test_versioning_lifecycle.py`
  walks the spec's "Data flow / Worked examples" sequence top-to-bottom
  against a fake-MaxCompute fixture: profile create → build →
  annotate-batch → memory verify → log inspection → fork at the
  annotate-batch commit → annotation drift on the parent → fork-write
  guard fires against the fork → diff between anchor and HEAD names
  the drift → reset on the parent rolls back to the captured anchor
  → discarded commit is in the reflog but not in the log → parent
  DB content matches the pre-drift state → fork-remove cleans the
  yaml entry, worktree directory, and the parent's
  `.git/worktrees/<short>/` admin entry → final `mcs doctor --offline`
  confirms all five versioning checks pass. The file's auxiliary
  tests pin the cross-cut contracts the worked example doesn't
  naturally exercise: `MCS_NO_VERSIONING=1` short-circuits every
  per-write hook (the same knob the eval-harness force-sets in
  `0.5.0a66`); a legacy pre-versioning profile auto-inits its data
  dir on the first write, landing both the `init: import existing
  data` and the build commit. A sibling `tests/integration/conftest.py`
  hosts the `versioned_profile` + `fake_maxcompute` fixtures and
  applies a `git`-binary-required skip mark to the lifecycle file.

## [0.5.0a66] — 2026-05-24

### Changed

- **Bird eval-harness force-sets `MCS_NO_VERSIONING=1` for every per-case
  subprocess** so the agent's inner `mcs build` / `mcs annotate` /
  `mcs memory ...` calls skip the per-profile git auto-init and
  the per-write auto-commit hook. The per-case sandbox lives only
  as long as the `claude --print` subprocess that wrote it, so a
  fresh `.git/` init per case × hundreds of cases × multiple arms
  would be pure dead weight (non-trivial disk I/O for state nobody
  reads). The opt-out lives in `eval._skill_setup.build_minimal_env`
  — the single source of truth for both `eval/profiler.py` and
  `eval/adapters/claude_code.py` — and is invariant to whatever the
  parent shell happens to export for `MCS_NO_VERSIONING` (the
  helper force-sets after the env copy so a stale `=0` in the
  parent doesn't re-enable the hook).

## [0.5.0a65] — 2026-05-24

### Added

- **`mcs doctor` learns five versioning-aware checks** so the
  per-profile git layer is in the same pre-flight pass as auth,
  connectivity, tier, and build data. The five checks are: (1)
  **`git_available`** — probes the system `git` binary and fails
  with an `MCS_NO_VERSIONING=1` opt-out hint if missing; (2)
  **`profile_versioned`** — inspects whether the resolved profile's
  data dir is a git repo (passes with HEAD `<short-sha> — <subject>`
  on a healthy main, warns with an `enable-versioning` remediation
  on a legacy pre-versioning profile, fails with "double-orphan" on
  a fork whose parent is missing from `profiles.yaml`); (3)
  **`working_tree_clean`** — `git status --porcelain` truthy check
  that warns with the pending-change count when dirty (the next
  write command will roll the changes into a `recover: pre-existing
  changes` commit, so this is informational about pending recovery
  rather than blocking); (4) **`forks_healthy`** — system-level walk
  of every fork row in `profiles.yaml` that tallies healthy vs
  orphan (parent has no `.git/`) vs ghost (worktree dir
  hand-deleted) vs double-orphan (fail), pointing at
  `mcs profile fork-list` for self-heal; (5) **`package_sql_parses`**
  — opens an in-memory sqlite3 connection and replays
  `package.sql` to confirm it parses, with a `mcs profile reset
  --to <prior-short-sha>` rollback hint sourced from
  `git log -- package.sql` on failure.

### Changed

- **`CheckResult` grows a fourth status, `warn`**, for informational
  signals that are neither a clean pass nor a blocking failure. The
  doctor's exit-code logic treats `warn` like `skip` — present in
  the summary tally but does not trip exit 1. Both text mode (yellow
  ⚠️) and JSON mode (status string `"warn"`, summary precedence
  `fail > warn > skip > pass`) carry the new status through. The
  five new checks above are the first emitters; all pre-T19 checks
  keep their original `pass`/`fail`/`skip` taxonomy.

## [0.5.0a64] — 2026-05-24

### Changed

- **`mcs profile show <name>` grows a per-profile version trailer
  that names the profile's place in the per-profile git graph.** On a
  `kind=main` profile the human-formatted output ends with a
  `📜 Version  <short-sha> (<subject>)` line that names HEAD as the
  inaugural-or-latest commit on the parent repo, plus — when the
  profile has registered forks — a `🌿 Forks  <name1>, <name2>, …`
  line listing the fork names alphabetically. On a `kind=fork`
  profile the trailer instead reads `🌿 Parent  <parent-name> @
  <short-sha> (<subject>)` (the fork's identity is its anchor commit,
  so no separate `📜 Version` row), and there is no `🌿 Forks` row.
  A legacy main-kind profile that has never been versioned (no
  `.git/` directory under its data dir) emits a dimmed `📜 Version
  not versioned; run \`mcs profile enable-versioning\` to create the
  inaugural commit` hint where the version row would have been; a
  versioned main-kind profile whose repo is on an unborn HEAD (init
  with no commits) emits `📜 Version  (repo initialized, no commits
  yet)`. The JSON envelope grows the matching keys: on main, a
  `version` dict (`{short_sha, full_sha, subject}` or `null`) and a
  `forks` list (alphabetical fork names, possibly empty); on fork, a
  `parent` string and an `anchor` dict (same shape as `version`),
  and neither `version` nor `forks` is present. The yaml output
  (`-f yaml`) is intentionally left bare-round-trippable so the
  config can be fed straight back into `mcs profile update
  --from-file` without git-state leakage.

- **`mcs profile remove <name>` is now fork-aware.** Running
  `mcs profile remove <main>` on a `kind=main` profile that has
  live `kind=fork` rows pointing at it is rejected with a
  `McsError` that names every live fork and points the user at
  `mcs profile fork-remove <fork>` (the per-fork teardown verb
  from 0.5.0a63) and `mcs profile fork-list --profile <main>`
  (to enumerate them) — the main row stays in place to preserve
  the parent repo until every dependent worktree is gone first.
  Running `mcs profile remove <fork>` on a `kind=fork` profile
  no longer falls through to the legacy `rmtree`-based main
  remover; it delegates to the same `parent_repo(fork)
  .worktree_remove(<path>)` + `unregister_fork(<name>)` flow that
  `mcs profile fork-remove` uses, which keeps the parent's
  `.git/worktrees/<short>/` admin entry in sync. The same two
  self-heal arms ride along: a **ghost-fork** (worktree dir
  hand-deleted) falls through to `git worktree prune` + yaml drop,
  and a **double-orphan** (parent yaml gone too) leaves the
  on-disk worktree in place and just drops the yaml row.

## [0.5.0a63] — 2026-05-24

### Added

- **`mcs profile fork-remove <name> [--force] [--yes]` — dual of
  `mcs profile fork`.** Tears down a `kind=fork` profile in a
  git-then-yaml order: `git worktree remove <path>` first (which
  also sweeps the parent's `.git/worktrees/<short>/` admin entry),
  then `unregister_fork(name)` to drop the yaml row. The yaml-side
  cleanup goes through `versioning.forks.unregister_fork`, which
  calls `profile_store.remove(name, delete_data_dir=False)` — the
  worktree directory is git's responsibility, never the yaml store's
  `rmtree`. Without `--yes` the verb shows a one-line banner with
  the parent name + short anchor SHA and prompts `[y/N]`; default
  is no. `--force` passes through to `git worktree remove --force`
  for the dirty-worktree case (uncommitted markdown edits inside the
  fork). Two recovery paths land on the same exit-zero rails as the
  fork-list self-heal: the **ghost-fork** path (worktree dir was
  hand-deleted) falls through to `git worktree prune` + yaml drop,
  and the **double-orphan** path (parent yaml already gone too — the
  `parent_repo(fork)` lookup raises `ProfileNotFoundError`) emits a
  "parent gone — yaml-only cleanup" warning, drops the yaml row, and
  leaves the on-disk worktree in place since there's no parent repo
  to `worktree remove` against. Running against a `kind=main`
  profile is rejected with a remediation pointing at
  `mcs profile remove`; unknown names point at `mcs profile list`.
  The parent's HEAD is invariant across the operation (the fork
  was an auxiliary view, never a commit on the parent's graph).

## [0.5.0a62] — 2026-05-24

### Added

- **`mcs profile fork-list [--profile <parent>] [--no-self-heal]` —
  audit + self-heal verb for the per-profile worktree-fork
  bookkeeping.** Lists every `kind=fork` profile in `profiles.yaml`
  with one of three states: `healthy` (the fork's anchor SHA is
  reachable from the parent's current HEAD via `git merge-base
  --is-ancestor`), `ORPHAN` (the anchor isn't an ancestor of HEAD
  anymore — covers the parent-was-reset-backward case, the
  parent-profile-was-removed sub-case where the parent yaml entry
  is gone, and the parent-data-dir-isn't-git-initialized
  sub-case), and `GHOST` (the yaml row exists but the worktree
  directory has been hand-deleted). The default-on self-heal
  sweeps every GHOST row on the same invocation: it runs
  `git worktree prune` once per parent (de-duplicated across
  multiple ghost forks of the same parent) and drops the yaml
  entry via `unregister_fork`. `--no-self-heal` reports the
  GHOST row without the side effect for read-only audits. Output
  is an aligned `NAME PARENT ANCHOR STATE DETAIL` table on stdout
  plus a one-line summary on stderr; `-f json` swaps to a
  top-level `{forks: [...], totals: {total, healthy, orphan,
  ghost, self_healed}}` envelope with each fork row carrying
  `name` / `parent` / `anchor` / `state` / `detail`. The
  `--profile <parent>` filter restricts the listing to forks of
  one parent profile.

## [0.5.0a61] — 2026-05-24

### Added

- **`mcs profile fork <name> --from <ref>` — create a read-only fork
  of a profile anchored at a specific commit.** The fork is added
  to `profiles.yaml` as a `kind=fork` entry (inheriting the parent's
  `compute_project` / `endpoint` / `auth` / `sources` /
  `cost_thresholds` / `tags`) and a matching detached `git worktree`
  is created at the default `<data_root>/<fork-name>/` slot (override
  with `--worktree-path`). Forks share the parent's git object
  database, so disk overhead is the checked-out working-tree
  content (markdown / json files at the anchor) plus the freshly
  materialized `package.db`. The anchor commit's `package.sql` (when
  present) is restored into a fresh `package.db` via the T3
  `restore_sql_to_db` path so the fork is queryable via
  `mcs sql execute --profile <fork-name>` immediately; FTS5 / vec0
  virtual tables are recreated empty via `run_reindex(vectors=False)`.
  Pre-build anchors (no `package.sql` in tree) emit a warn banner
  but the verb still succeeds. The `--from <ref>` resolver accepts
  the same vocabulary as `mcs profile reset / log / diff`: short or
  full SHA, `HEAD` / `HEAD~N`, or the `last-build` / `last-refresh`
  / `last-annotate` keywords. Forks-of-forks are rejected with a
  remediation pointing at the underlying parent.
  `MCS_NO_VERSIONING=1` hard-errors. Fork name validation reuses the
  schema's `_NAME_RE` (allowing the `@` and `:` delimiters for the
  canonical `<parent>@<short-sha>` and `<parent>:<label>` naming
  conventions). Existing worktree-path directories cause the verb
  to refuse rather than overwrite. Write verbs against a fork
  continue to raise `ProfileReadOnly` via the T9 `reject_if_fork`
  guard wired in at each write-verb entry point.
- **`versioning/forks.py`** lifts the yaml-side bookkeeping into
  three reusable helpers: `register_fork(parent, fork_name, sha,
  worktree_path)`, `unregister_fork(fork_name)`, and
  `parent_repo(fork)`. The first normalizes a short SHA to full
  40-hex via the wrapper's `rev_parse` (idempotent on same-anchor
  re-register; rejects name collisions with a remediation pointing
  at `mcs profile remove` vs `mcs profile fork-remove` depending on
  the existing entry's `kind`). The second is the inverse and
  refuses to remove a main-kind profile via the fork-removal path.
  The third returns a `GitRepo` rooted at the fork's parent's
  data-dir (every fork-related git operation flows through the
  parent's repo so the wrapper's `log` / `rev-parse` / `merge-base`
  commands run against the canonical object store).

## [0.5.0a60] — 2026-05-24

### Added

- **`mcs profile reset --to <ref>` — the destructive rollback verb on
  the per-profile git layer.** Resolves `<ref>` via the same
  short / full SHA + `HEAD` / `HEAD~N` + `last-build` /
  `last-annotate` / `last-refresh` keyword vocabulary as `profile
  log` and `profile diff`. Prints a stderr banner naming the
  source and target SHAs and the first 10 commits that would be
  discarded (`... and N more.` when more), warns if the target
  isn't a HEAD-ancestor, and prompts `[y/N]` unless `--yes` is
  passed. The rebuild sequence is wrapped in the per-profile
  `WriteLock`: any uncommitted dirty tracked files are first
  captured as a `recover:` commit (so the pre-reset state is
  reachable in the reflog), the current `package.sql` is copied
  to a sidecar `.mcs-reset-backup/<sha>.sql` for the bounce-back
  path, `git reset --hard` moves HEAD, the target tree's
  `package.sql` is restored into `package.db` via the T3
  `restore_sql_to_db` path, and the FTS5 + vec0 virtual tables
  are rebuilt via the T13.1 `run_reindex` helper. If any rebuild
  step raises, HEAD is bounced back to the pre-reset SHA, the
  sidecar SQL is restored, the index is rebuilt, and the original
  error is surfaced with exit 3. Fork-kind profiles error with a
  two-option remediation naming the parent. `MCS_NO_VERSIONING=1`
  hard-errors (the verb can't rebuild from a history the env opts
  out of). `--to HEAD` is a no-op with a `nothing to do` stderr
  hint. Target commits without a `package.sql` in their tree (the
  bare inaugural is the canonical case) emit a warn banner and
  leave `package.db` untouched rather than failing.
- **`run_reindex(db_path, *, vectors=True) -> (fts_count,
  vec_count)`** is now a standalone helper in `commands.memory`,
  lifted out of the `mcs memory reindex` verb so the reset
  verb's rebuild sequence can re-materialize the virtual tables
  without going through Click. The existing `mcs memory reindex`
  CLI surface is unchanged.

## [0.5.0a59] — 2026-05-24

### Added

- **Three new history-inspection verbs on the per-profile git layer**:
  `mcs profile log`, `mcs profile log-show <ref>`, and
  `mcs profile diff <ref_a> <ref_b>`. `log` lists commits with a
  default `^memory:` noise filter (drop with `--all`), a 20-row
  cap (`-n 0` for unlimited), and a `--grep <regex>` filter that
  supersedes the default. `log-show` dumps a single commit's
  metadata + diff over the four tracked-file globs (`*.md`,
  `*.json`, `package.sql`, `.gitignore`); refs accept short / full
  SHAs, `HEAD` / `HEAD~N`, and the keywords `last-build` /
  `last-annotate` / `last-refresh` (most-recent commit whose
  subject starts with the matching prefix). `diff` shows the
  unified diff between two commits' trees over the same path
  whitelist. All three verbs transparently redirect a
  `kind="fork"` profile to its parent's repo (a fork is a detached
  worktree sharing the parent's `.git/`) with a stderr banner
  naming the parent + anchor SHA. JSON output (`-f json`) emits
  the canonical envelopes documented in the design. `mcs profile
  log-show` (rather than `mcs profile show`) avoids colliding with
  the existing `mcs profile show <name>` config-dump verb.

## [0.5.0a57] — 2026-05-23

### Changed

- `mcs profile build` column-sampling phase now persists up to 5 format-example
  values for non-enum STRING / VARCHAR / CHAR columns (distinct ≥ 2,
  max value length ≤ 80). Previously only enum columns (distinct ≤ 30,
  max length ≤ 80) carried any sample-value payload, so high-NDV STRING
  columns storing dates, urls, codes, or identifiers landed in the
  per-table markdown with name + type only. The downstream
  `_date_format_hint` already keyed off `sample_values_json` to emit
  the `str-date` / `str-datetime` marker — it just never fired for
  high-cardinality date columns because the producer left the field
  empty. Net effect on a single representative table profile: a STRING
  `signup_date` column with ~10k distinct ISO dates now renders with
  `format_examples: ["1990-03-15", "1991-07-04", ...]` and is suggested
  as `dimension/time`, instead of landing at `attribute/fallback` with
  the agent then writing `YEAR(signup_date)` (silently NULLs out on
  STRING) instead of `SUBSTR(signup_date, 1, 4)`. The `is_enum` flag
  still gates the rendered key — `sample_values` for enums (full
  distinct set, authoritative), `format_examples` for non-enum STRINGs
  (shape hints only). Numeric / temporal / text-blob columns get no
  new payload — the gate is type + length only, not NDV.

## [0.5.0a56] — 2026-05-23

### Fixed

- **``mcs meta list-schemas`` no longer crashes with NameError.**
  A prior refactor of the meta commands to use
  ``resolve_project_for_profile`` (so the explicit ``--project``
  flag, profile sources, and compute_project fall through in the
  documented order) silently omitted the resolver call in
  ``list_schemas_cmd``. The function then referenced an undefined
  ``target_project`` local variable. Restored the resolver call so
  the auto-fill chain works identically to the other meta verbs.
- **``mcs meta`` verbs honour the profile's data-source project
  when ``--project`` is omitted.** All six meta verbs
  (``list-schemas`` / ``list-tables`` / ``describe-table`` /
  ``search-tables`` / ``search-columns`` / ``list-partitions`` /
  ``freshness``) previously fell back to
  ``client.profile.compute_project`` when the user did not pass
  ``--project``, which broke profiles whose data source lives in
  a different project than the compute project (the common case
  for multi-source profiles). They now route through a single
  ``resolve_project_for_profile`` helper that takes the first
  source's project when ``--project`` is omitted, falls back to
  the empty string when there are no sources, and always lets the
  explicit ``--project`` flag win.

### Changed

- **Semantic suggestions: NDV-tier dimension classifier gains a
  ``large`` band for clinical-style categorical codes.** Columns
  with NDV ≤ 500 and ratio ``approx_ndv / row_count`` < 0.20 now
  receive the dimension boost when they're STRING-typed —
  previously they fell through to the attribute bucket because
  every existing NDV tier capped at 100. The motivating profile
  was a clinical-history-style ``patient`` table with a
  ``diagnosis`` STRING column carrying ~219 distinct labels across
  ~1.2k rows (ratio ≈ 0.18): each value applies to ≥ 5 rows on
  average, so GROUP BY / WHERE-equality is meaningful, but the
  silent-promotion path never surfaced it as a dimension and the
  downstream agent generated ``LIKE '%pattern%'`` instead of
  ``=``. The tier is STRING-only (numeric columns in the same NDV
  band — ``avg_pages_read`` BIGINT ndv≈400 — are pre-aggregated
  measures or counters, not dimensions) and the 0.20 ratio gate
  filters out free-text fields whose NDV happens to land in the
  same band. ``_dimension_subtype`` also broadens its STRING
  categorical ceiling from 30 to 500 (the large-tier ceiling) so
  the new path lands as ``dimension/categorical`` rather than
  ``dimension/ordinal``. ``_NDV_DIMENSION_TIERS`` entries now
  carry a ``string_only`` flag for future per-tier type gating.

### Internal

- **Sanitization: removed benchmark-set-specific identifiers from
  source and tests.** The ``_signal_priority`` docstring in
  ``build/markdown.py`` previously cited specific Bird-dataset
  column names (``artist``, ``asciiname``, …, ``power``, ``text``,
  ``type``) as the motivating example for the wide-table
  ``columns_index`` reorder; replaced with a generic
  "catalog-style entity imports" wording so the source tree no
  longer contains test-set-specific references that could read as
  per-benchmark tuning. ``test_profile_create_versioning.py``
  similarly used ``compute_project="bird_0001"`` as a fixture
  value, replaced with ``compute_project="evalproj_0001"``. CI's
  live-matrix tests against real MaxCompute table names are
  unaffected — sanitization is limited to source/test docstrings
  and fixture values.

## [0.5.0a55] — 2026-05-23

### Fixed

- **History mining no longer pollutes the workload summary with
  cross-source tables.** ``phase_mine_history`` pulls SQL from the
  compute project's ``INFORMATION_SCHEMA.TASKS_HISTORY`` — which
  returns queries against any table in the project, not just the
  ones a profile's data sources actually select. The legacy
  attribution path used a case-insensitive word-boundary regex over
  the user-selected table names, which (a) false-positively matched
  table-name tokens inside string literals (e.g. ``WHERE description
  = 'Post Cards, Posters'`` matched the ``cards`` source table), and
  (b) kept the full SQL text — including JOINs to out-of-source
  tables — as a per-table sample, which then fed
  ``aggregate_workload_evidence`` and pushed cross-source joins,
  WHERE columns, and aggregates into the per-source workload summary
  that downstream ``join_candidates`` and ``semantic_suggestions``
  rank against. On real catalog-style entity data the legacy code
  invented a cross-source JOIN candidate with frequency 31 against
  a profile whose source did not include the join's left table at
  all. Attribution now parses each SQL with
  ``analyze_sql_pattern`` (sqlglot) and only counts true
  ``exp.Table`` references; on parse error it falls back to the
  legacy regex so MaxCompute-specific syntax doesn't shrink mining
  coverage. ``aggregate_workload_evidence`` gains an
  ``allowed_tables`` parameter (passed by the build pipeline as the
  per-source table set) that scopes the resulting
  ``table_counts`` / ``join_counts`` / ``where_counts`` /
  ``group_by_counts`` / ``aggregate_counts`` to qualified refs whose
  table side is in-source, while leaving unqualified column refs
  (single-FROM ``WHERE col``) and the persisted sample SQL itself
  alone. Multi-clause ON expressions
  (``t1.a = t2.b AND t1.c = ?``) and OR-disjunctions are also
  correctly handled by walking every table-qualified column ref in
  the edge string rather than naively splitting on ``=``.

### Changed

- **Full build (non-refresh) now cleans up tables removed from the
  source.** Previously only ``--refresh`` ran the orphan-removal
  pass; a profile that ran ``mcs build`` (without ``--refresh``)
  after a table was dropped from the source would silently retain
  the stale row in ``package.db`` and the per-table ``.md`` file
  on disk, both feeding into ``mcs status --tables`` /
  ``mcs annotate batch`` as if the table still existed. The
  cleanup pass now runs at the start of each source's per-source
  iteration in the main pipeline, mirroring the refresh path's
  behavior and incrementing ``summary.tables_removed``.

## [0.5.0a54] — 2026-05-23

### Changed

- **Wide-table `columns_index` reorder lifts annotated columns into
  the cap.** When a table has more than 20 user columns, the always-
  loaded `_overview.md` previously truncated to the first 20 DDL-order
  columns and dropped any annotated identifier / dimension / metric
  defined past the cap. On wide tables (e.g. 70+ column tables seen
  in the wild) this hid the semantic-layer's highest-signal columns
  from the agent's first overview read, forcing per-table
  `mcs show --table T` round-trips just to discover the column
  exists — and on wrong-table picks where the question's answer
  column lives on a partner table, the agent never realized the
  partner was a better fit because the partner's overview entry
  itself hid the relevant column. `render_overview` now reorders
  wide tables by `_signal_priority` (confirmed `semantic_role` →
  identifier marker from suggestions / join graph → carries
  `semantic_description` → default) using a stable sort so DDL
  order is preserved within each tier. Tables at or under the
  20-column cap skip the reorder entirely so narrow-table
  projections render in the DDL order they did before this change.

## [0.5.0a53] — 2026-05-23

### Changed

- **Universal 3-segment table refs on 3-level compute projects.**
  `build_hints` now injects `odps.namespace.schema=true` whenever the
  compute tier is `"3"`, independent of whether a session schema was
  supplied — previously the namespace flag was added only when both
  conditions held. The change makes 3-segment `project.schema.table`
  references parse correctly on every `mcs sql execute` /
  `mcs sql cost` / `mcs sql explain` call against a 3-level project,
  even when the agent didn't pass `--schema`. `odps.default.schema`
  remains schema-conditional so bare names don't silently resolve to
  an unintended default.

- **`mcs status` surfaces the compute project's tier.** The summary's
  `tier:` line now reads `tier_cache/<compute_project>` directly, so
  multi-source profiles where the compute project differs from the
  first DataSource's project see the correct value — the agent's SQL
  emission decision (3-segment FQN on 3-level vs bare names on 2-level)
  hinges on the compute project's tier specifically, which is what
  governs the SQL session's `odps.namespace.schema` setting. Builds
  pre-dating the per-(profile, project) `tier_cache` layout fall back
  to `_state.json`'s recorded tier so existing packages still render.

- **`SKILL.md` teaches tier-aware table-ref form.** New section guides
  the agent to read `tier:` from `mcs status` at session start and to
  prefer 3-segment `project.schema.table` for every reference on
  3-level compute, falling back to bare names on 2-level (where the
  parser rejects 3-segment refs for connection-owned tables).

## [0.5.0a52] — 2026-05-23

### Changed

- **Annotation suggester: detect pre-aggregated column names and
  classify as attribute, not metric.** Numeric columns whose name
  starts with `avg` / `mean` / `median` / `stddev` / `variance` /
  `num` / `cnt` / `count` (followed by `_` or another letter) store
  values that are *already aggregates* — per-group average scores,
  per-row counts — and should be projected directly rather than
  wrapped in `SUM` / `AVG` / `COUNT` at query time. The
  type-heuristic metric default (added in 0.5.0a48) was promoting
  such columns to `metric/AVG` with confidence 0.40; the agent's
  `mcs annotate batch` then wrote `metrics: - {name: X, agg: AVG}`
  into the annotation, and the downstream SQL generator
  mechanically emitted e.g. `AVG(avg_write_score)` over a column
  whose gold answer was the column selected raw.

  Now: pre-aggregated names suppress the type-heuristic metric
  default and surface an explicit `attribute` suggestion at
  confidence 0.65 (above the markdown render floor) carrying
  evidence `{pattern: pre_aggregated, note: "name prefix indicates
  value is already an aggregate; SELECT directly, do NOT
  re-aggregate with SUM/AVG/COUNT"}`. Workload-driven metric
  signals (`SUM(col)` / `AVG(col)` in `history_sql`) and the
  `METRIC_NAME_RE` substring-match path (e.g. `avg_score` matches
  both prefixes — but the `score` token signals it is still a
  metric) are unaffected; only the weakest name-blind
  type-heuristic fallback is gated. Skill-side
  (`references/annotate.md`) teaches the agent the matching
  `role: attribute` annotation convention.

## [0.5.0a51] — 2026-05-23

### Fixed

- **INFORMATION_SCHEMA detection now keys on `compute_project`, not
  `sources[0].project`.** For multi-source profiles where the compute
  project differs from the first DataSource's project, the tenant
  probe's `task_catalog = '<project>'` filter previously used the
  first source's project name. An AK with tenant-level access to the
  compute project but no rows recorded for that catalog filter
  silently fell through to project-form (or to "none"), losing
  history mining that should have caught the tenant path. The probe
  now uses `compute_project` directly, matching where both
  INFORMATION_SCHEMA forms actually resolve. Per-source mining
  attribution is unaffected — `phase_mine_history` still uses
  `source.project` in the mining SQL's filter, so per-catalog
  attribution remains correct for multi-source profiles.

## [0.5.0a50] — 2026-05-23

### Changed

- **Unify tier-3 + missing `--schema` failure across all 10 CLI verbs.**
  Previously, `mcs sql execute` / `mcs sql cost` / `mcs build` hard-failed
  with a plain-text Click-style error (exit 2), while `mcs sql explain`
  and the six `mcs meta` verbs (`list-tables`, `describe-table`,
  `search-tables`, `search-columns`, `list-partitions`, `freshness`)
  silently coerced a missing `--schema` to `"default"` — masking
  misconfigured profiles by hitting the upgrade-synthetic `default`
  slot. All ten now route through a shared
  `commands/_schema_resolve.resolve_schema_for_tier` helper that
  raises the new classified `SchemaRequiredError` (code
  `SchemaRequired`, exit 2, structured JSON envelope) when no schema
  is supplied for a 3-level project, *unless* the active profile has
  exactly one source, in which case that source's schema is
  auto-filled. Multi-source profiles get a remediation listing the
  available schema names; the agent doesn't have to round-trip
  through `mcs meta list-schemas` to discover its choices.



### Changed

- **Suppress NDV-tier dimension boost for constant-value columns.**
  The annotation suggester's NDV-tier boost (`tiny`/`small`/`medium`
  bands at `_NDV_DIMENSION_TIERS`) previously fired whenever a column's
  approximate distinct-value count fell under the band's ceiling. This
  silently promoted single-valued columns — a profiling artefact where
  every sampled row carries the same placeholder literal (e.g. `"-"`,
  `"N/A"`, schema-version constants from a one-shot table) — to
  `dimension/categorical` at confidence 0.35, polluting the
  `dimensions:` list in the resulting batch annotation. The boost loop
  now gates on `approx_ndv > 1`, so columns with no variance fall
  through to attribute fallback. Explicit workload evidence
  (`group_by_counts`, `where_counts`) and name-pattern signals still
  apply, so a user who deliberately groups by a constant column will
  still see the suggestion — only the silent NDV-only promotion path is
  closed. Generic semantic-layer improvement applicable to any dataset
  with placeholder-saturated columns.

## [0.5.0a48] — 2026-05-23

### Changed

- **Default numeric measurement columns to `metric/AVG` from type
  alone.** The annotation suggester previously only promoted a column
  to `metric` when the name matched a small regex of generic English
  tokens (`amount`/`price`/`cost`/`count`/`qty`/`quantity`/
  `score`/`total`/`sum`) or when history-SQL aggregate evidence was
  present. Domain-specific schemas with abbreviation column names
  (clinical labs with `hgb`/`hct`/`ldh`/`alt`; instrumentation with
  `rpm`/`psi`/`mwh`; finance with `bps`/`pnl`) never matched the
  regex, and a freshly-built profile with no `mcs memory verify`
  entries has no aggregate evidence either — so every numeric
  measurement column fell through to `attribute/fallback` and the
  agent had to manually override 20+ columns per table in
  `mcs annotate batch`. The new type-heuristic boost classifies
  `DOUBLE`/`FLOAT`/`DECIMAL`/`NUMERIC` columns (unconditionally on
  NDV — concentrations and ratios legitimately have low cardinality
  in small samples) and `BIGINT`/`INT` columns with `approx_ndv >= 10`
  (the floor protects against tiny enums like 0/1/2 status flags) as
  `metric/AVG` when (a) the column isn't already promoted via the
  name regex or workload, (b) the name doesn't end in id/uuid/key/code,
  and (c) `uniqueness_ratio < 0.95` (or unset) — row-unique numerics
  like geographic coordinates are attributes, not measurements. The
  subtype defaults to `AVG` (the safest aggregate for an unknown
  continuous quantity — `SUM` assumes additivity that ratios lack);
  name-driven promotions still default to `SUM` since name tokens
  like `amount`/`total` imply additivity. Evidence carries a
  `type_heuristic` source with `tier=continuous_numeric` or
  `tier=integer_numeric_ndv10+` so the agent reading suggestions
  sees the promotion came from type alone, not from name or workload.
- **Promote `dimension/time` from content-detected `format_hint`.**
  The build phase's `_date_format_hint` already inspects
  `sample_values_json` and tags a STRING column `str-date` /
  `str-datetime` when ≥ 50% of sampled values match an ISO date /
  datetime shape. The annotation suggester previously never consulted
  this hint, so a STRING column whose stored values are ISO dates but
  whose name carries no temporal signal (e.g. a clinical-history
  `description` column literally storing `"1995-04-13"`) fell through
  to `attribute/fallback`. The new dim-block branch reads
  `format_hint` and boosts `dim_conf` by 0.40 when set to `str-date`
  or `str-datetime` — content evidence is stronger than name regex,
  and 0.40 alone clears the 0.3 gate so the column lands as
  `dimension/time` even with no other signal. `_dimension_subtype`
  also reads `format_hint` to return `time` regardless of name
  pattern. The `pipeline.py` semantic-suggestion call site now feeds
  `format_hint` into the column dict so the classifier can see it.

## [0.5.0a47] — 2026-05-23

### Fixed

- **Strip filter-bias `where_count` evidence from annotation suggestions
  on columns already confirmed by the annotation pass.** When a column
  carries a `semantic_role` (dimension / metric / identifier), the
  per-table `.md` previously surfaced it twice: once in the confirmed
  block (`dimensions:` / `metrics:` / `identifiers:`) carrying the
  role + description, and again in `annotation_suggestions:` with raw
  evidence that often included a `history_sql / where_count: N` row.
  The role assignment in the confirmed block already carries the
  load-bearing signal; the `where_count` on top of it functions as a
  filter-bias signal that nudges the agent toward gratuitous WHERE
  clauses on questions that don't need filtering — the regression that
  motivated 0.5.0a42's wholesale suggestion suppression (later
  reverted as too broad in 0.5.0a43). The narrow fix strips only the
  `where_count` key from `history_sql` entries on already-annotated
  columns, preserving the other productive evidence (`id_suffix`,
  `uniqueness_ratio`, `aggregate`, `group_by_count`) that the 0.5.0a43
  revert restored. If the strip leaves an evidence row containing only
  `source` (i.e. the row's payload was just `where_count`), the row is
  dropped; if that leaves the entire evidence list empty, the
  suggestion is dropped from the rendered .md altogether. Both the
  `build/markdown.py` per-table renderer and the `commands/show.py`
  `mcs show --table` JSON envelope apply the strip — both surfaces
  feed the same agent.

## [0.5.0a46] — 2026-05-23

### Fixed

- **Route `ODPS-0130131` / `NoSuchTable` codes to `TableNotFoundError`.**
  The 0.5.0a45 refactor that dropped substring classification missed
  the structured codes pyodps's ``parse_instance_error`` stamps onto
  ``NoSuchTable`` instances (the parser-side "table cannot be
  resolved" path, distinct from the meta-REST ``NoSuchObject`` path).
  The live-matrix parser arm started folding those errors into
  ``UnknownError``; the build-phase soft-failure path that catches
  ``TableNotFoundError`` lost coverage. Both ``"NoSuchTable"`` (class
  name) and ``"ODPS-0130131"`` (wire-level code) now route to
  ``TableNotFoundError``.
- **Update live `test_identity_not_authorized` to expect
  `PermissionDeniedError`.** Mirrors the 0.5.0a45 collapse: the
  "User doesn't exist in the project" wording no longer types as
  ``IdentityNotAuthorizedError`` because MC emits both that wording
  and plain ``no permission`` wording for the same underlying ACL
  state.

## [0.5.0a45] — 2026-05-23

### Changed

- **Drop ODPS error-message substring classification; route exclusively
  on structured `exc.code`.** `map_pyodps_exception` previously ran a
  two-layer scheme: Layer 1 routed on pyodps's structured `exc.code`,
  Layer 2 fell back to `msg.lower()` substring tests (`"access denied"`,
  `"table not found"`, `"doesn't exist in the project"`, …) when the
  code was empty or unrecognized. Layer 2 is gone. MaxCompute's
  server-side wording is not stable enough to bucket on — the same
  underlying condition surfaced with different wording across cache
  states, and the live-matrix arms flipped between
  `PermissionDeniedError` and `IdentityNotAuthorizedError` for identical
  ACL state. Any exception arriving without a recognized `exc.code` now
  folds into `UnknownError` carrying the raw pyodps message verbatim;
  the message already names the privilege / object / SQL fragment, which
  is what the user and agent need to remediate. The `build/phases.py`
  per-table soft-failure path still works because pyodps emits the
  relevant structured codes (`NoSuchObject` / `NoPermission` /
  `AccessDenied` / `ODPS-0130013`) reliably for the conditions it cares
  about.
- **Collapse the `"User doesn't exist in the project"` substring branch
  inside `_permission_denied`.** Earlier this wording was routed to
  `IdentityNotAuthorizedError` (exit 4, auth-axis) to distinguish it
  from a missing object-level grant (exit 5). MC emits both wordings
  for the same underlying ACL state depending on cache warmth, so the
  classifier flapped between the two exception types on identical
  inputs. All permission errors now collapse to
  `PermissionDeniedError`; the raw message still names the principal
  and object for the user / agent to read.
  `IdentityNotAuthorizedError` is still reachable via the structured
  `exc.code == "IdentityNotAuthorized"` branch for the unmistakable
  case pyodps types directly.

## [0.5.0a44] — 2026-05-23

### Fixed

- **Recover distinctive-name PK↔PK same_name joins as 1:1 entity-split
  edges.** Two PK-like columns sharing a name were being dropped
  unconditionally — the gate could not distinguish coincidental
  generic-name collisions (multiple entity tables each carrying their
  own `id` PK) from legitimate entity-split joins (the same entity
  decomposed across tables, both keyed by the same distinctive PK name
  like `cdscode`, `account_number`, `ssn`). The previous behavior left
  multi-table schemas where the canonical join is PK↔PK with empty
  `relationships:` in the generated joins markdown, forcing the agent
  to guess JOIN structure across whole case classes. The new gate
  splits the two cases: generic identifier names (≤3 chars, or in a
  small lexicon `{id, uuid, pk, key, code, rid, gid, uid, num, no}`)
  remain dropped; distinctive PK names emit a `same_name` edge with
  `1:1` cardinality at reduced confidence (0.4) so the agent has a
  join hint to anchor on. Type-based eliminations
  (temporal/url/numeric/label) still run first so a near-unique
  TIMESTAMP column shared between tables doesn't slip through.
- **Test fixes for the structured-code error mapping path
  (0.5.0a42).** Two `mc_client` tests
  (`test_execute_sql_odps_error_mapped`,
  `test_cost_estimate_pyodps_error_classified`) constructed pyodps
  errors without setting `.code`, which silently classified them as
  `UnknownError` under the new structured-code path. Set the
  matching `.code` explicitly in both tests so they exercise the
  intended classifier branch.

## [0.5.0a43] — 2026-05-23

### Reverted

- **Revert 0.5.0a42 annotation-suggestion suppression.** The
  suppression cut smoke EX from 22→19 on the with-history arm and
  20→19 on the no-history arm (commit 5bb0884 vs prior 8ccf790).
  Per-case inspection showed the suppressed suggestions carried
  cardinality / sample_values / role-confirmation evidence the
  agent was using productively — e.g. `cards.id` regressed because
  the suggestion's `identifier` role hint, when removed, led the
  agent to add a defensive `COUNT(DISTINCT id)` instead of the
  `COUNT(id)` the join structure already deduplicates. The
  `where_count` over-filter symptom (the original motivation) needs
  a narrower fix — drop only the `history_sql` evidence entries for
  already-annotated columns, not the whole suggestion row — once
  the right surface for it is identified.

## [0.5.0a42] — 2026-05-23

### Fixed

- **Suppress redundant annotation suggestions in agent-facing
  markdown.** When a column already carries a confirmed
  `semantic_role` (so it appears under the table's `dimensions:` /
  `metrics:` / `identifiers:` block), drop the matching
  `annotation_suggestions` row from the rendered table markdown
  instead of emitting it alongside the confirmed entry. The
  unconfirmed row was re-surfacing internal scoring evidence — most
  damagingly the `evidence: [{source: history_sql, where_count: N}]`
  tuple from workload mining — which the SQL-generation agent reads
  as "this column is always filtered to value X" and adds the filter
  to queries that don't ask for it. The miner's per-column workload
  counts remain in the `annotation_suggestions` table for ranking
  and re-suggestion when annotations are dropped; only the
  agent-facing per-table `.md` drops the now-superseded row.

## [0.5.0a41] — 2026-05-23

### Changed

- **BREAKING** (internal API): collapse the 6 permission-denied error
  subclasses (`PermissionDeniedTableError`,
  `PermissionDeniedColumnError`, `PermissionDeniedMetaError`,
  `PermissionDeniedFunctionError`,
  `PermissionDeniedInfoSchemaTenantError`,
  `PermissionDeniedInfoSchemaProjectError`) into a single
  `PermissionDeniedError` with `code="PermissionDenied"` and
  `exit_code=5`. The classifier no longer guesses which "flavour" of
  permission deny it received from ODPS — the raw pyodps message
  passes through verbatim, since the message itself names the
  privilege and the object (`odps:Select` / `CheckLabelSecurity` /
  `odps:Describe` / `information_schema.*` / etc.) more reliably than
  any keyword heuristic. The classifier helpers
  `_classify_no_permission` and `_classify_info_schema_permission`
  are removed. The one disambiguation that survives is the "principal
  doesn't exist in the project" branch, which still routes to
  `IdentityNotAuthorizedError` (exit 4, auth-axis) rather than to the
  resource-axis `PermissionDenied` bucket (exit 5). Callers that did
  `except PermissionDeniedColumnError` (etc.) must now `except
  PermissionDeniedError`; the exit-code envelope shape is unchanged
  for the CLI surface.

## [0.5.0a39] — 2026-05-23

### Changed

- **BREAKING** (pre-1.0): drop the `--source SOURCE_KEY` CLI flag from
  `mcs annotate table` / `column` / `list` / `batch`,
  `mcs memory verify`, and `mcs meta list-tables`. The flag duplicated
  what the 3-segment FQN form `project.schema.table` already expresses
  and what the `--project P --schema S` pair already scopes for
  catalog verbs, so two surfaces existed for one disambiguation
  question. To migrate:
  - `mcs annotate table users --source acme__warehouse` →
    `mcs annotate table acme.warehouse.users` (pass FQN as the table
    argument; the resolver splits `proj.schema.table` into
    `(proj__schema, table)` deterministically without a DB lookup)
  - `mcs memory verify --tables users --source acme__warehouse` →
    `mcs memory verify --tables acme.warehouse.users` (FQN works
    inside `--tables` per-entry)
  - `mcs meta list-tables --source acme__warehouse` →
    `mcs meta list-tables --project acme --schema warehouse` (use
    the same `--project P --schema S` pair the other `mcs meta`
    verbs already accept)
  - `mcs annotate batch` top-level `--source` flag also goes away;
    set `source: <source_key>` per entry in the YAML (or write the
    table as an FQN in the entry's `table:` field). The per-entry
    YAML `source:` field is retained — only the CLI flag is removed.
  Error-message remediation hints from `TableResolutionError` no
  longer mention `--source SOURCE_KEY`; they point at the FQN form
  exclusively.
- Skill bundle (`_skill/SKILL.md` + `references/`) closes documented
  gaps against the actual `mcs` CLI surface:
  - `query.md`: cost-gate verdicts no longer state `< 10 / 10–100 /
    ≥ 100 CNY` as fixed boundaries. The thresholds are now described as
    the active profile's `cost_thresholds.confirm_cny` / `blocked_cny`
    (10 / 100 are defaults; configurable via `mcs profile create
    --confirm-cny / --blocked-cny` or `mcs profile update`). Also calls
    out explicitly that `mcs sql cost` exits 0 even on `blocked` (read
    the JSON `verdict`, not the exit code) and that agents should pass
    `mcs sql execute -y / --yes` to bypass the interactive confirm
    prompt in non-TTY callers.
  - `build.md`: adds `mcs build --schema S` to the flag list and a new
    "Build status" section covering `mcs status --tables` /
    `--by-source`.
  - `onboarding.md`: documents `mcs profile create` per-prompt flags
    (`--alias / --project / --endpoint / --region / --auth-type /
    --ak-id-env / --ak-secret-env / --ak-literal / --employee-id /
    --ncs-command / --tag / --confirm-cny / --blocked-cny / --no-test
    / --show-advanced`), `mcs profile show --format yaml`,
    `mcs -q profile whoami` for shell pipelining, `mcs link status -v`,
    and `mcs profile import-creds` flag set
    (`--source / --config-path / --alias / --no-test`).
  - `SKILL.md` decision matrix: adds rows for `mcs annotate list`
    (check annotation coverage), `mcs profile list` / `remove`,
    `mcs udf search` / `mcs udf resource`, and the quiet-mode form
    of `mcs profile whoami`. The `mcs status` row now mentions
    `--by-source`. The `mcs profile show` row now lists all three
    output formats.
  No CLI behavior changes — every command and flag added to the docs
  already existed; the skill was simply silent about them.

### Added

- Build's date-format heuristic (`_date_format_hint` in
  `build/markdown.py`) now distinguishes ``[str-datetime]`` from
  ``[str-date]``. STRING-typed columns whose ``format_examples``
  carry a time component (``YYYY-MM-DD HH:MM[:SS[.f]]`` /
  ``YYYY-MM-DDTHH:MM…``) render with ``[str-datetime]`` in
  `columns_index`; pure-date columns keep ``[str-date]``. The
  marker variant carries a real correctness signal the agent did
  not previously have: lexical comparison against a date-only
  literal (``col > '2014-09-01'``) silently *includes* boundary-day
  rows for datetime values, because the longer string sorts after
  the shorter prefix — a query meant to exclude that date includes
  it instead. `references/rules.md` and `SKILL.md` document the
  ``SUBSTR(col, 1, 10) > 'YYYY-MM-DD'`` /
  `TO_DATE(SUBSTR(col, 1, 10), 'yyyy-MM-dd')` wrap patterns for the
  new marker. Even a single time-bearing sample upgrades the marker
  — one boundary-sensitive row is enough to make naive string compare
  unsafe. The fallback path (no samples, confirmed
  ``dim_type='time'`` annotation) still emits ``str-date`` because
  the annotation carries no sub-day precision info.
- Build now detects **cross-env duplicate sources** — pairs of
  `(project, schema)` sources within a single profile whose table-name
  sets overlap heavily (default thresholds: ≥70% of the smaller
  source's tables shared, ≥3 shared tables). The detector
  (`build/cross_env.py::detect_cross_env_duplicate_sources`) runs
  after the per-source phases complete in both the full and refresh
  build paths, and the resulting source-pair set is passed into
  `phase_infer_joins_heuristic` as the new keyword-only
  `suppressed_source_pairs` argument. Any candidate JOIN edge whose
  left and right tables fall under a suppressed pair is dropped
  before reaching the joins table — dev↔prod and staging↔prod
  copies of the same schema no longer fabricate cross-source FK
  edges across environments. Each flagged pair emits a per-pair
  progress line and a capped build warning of the form
  `cross_env/<src_a>+<src_b>: ... <N> of <M> tables (<pct>% overlap)
  — likely dev/prod or staging/prod copies of the same schema; JOIN
  inference between them suppressed`, so the build summary makes
  the suppression visible without burying it. Single-source profiles
  and profiles whose sources don't share table names see no behavior
  change. The detection is name-based only (no column comparison or
  row sampling) — conservative thresholds avoid false positives
  against legitimately overlapping but distinct schemas. The earlier
  `CROSS_SOURCE_CONFIDENCE_PENALTY=0.8` soft penalty still applies to
  surviving cross-source edges that don't fall under a suppressed
  pair, so the two layers compose cleanly.

### Fixed

- Build's join-inference (`phase_infer_joins_heuristic`) now prefers
  a same-named column on the right table over a bare ``id`` PK when
  resolving the FK target. Pre-fix, when both existed the resolver
  picked ``id``, which mis-emitted external-id FKs whose parent
  table also carries a bare surrogate PK — e.g. a
  ``team_attributes.team_api_id`` FK with a parent table carrying
  both ``id`` and ``team_api_id`` resolved to ``team.id`` instead
  of ``team.team_api_id``. The wrong target then appeared verbatim
  in the per-table ``join_candidates`` JSON surfaced by
  `mcs show --table`, which led the agent to write the matching
  wrong join (``JOIN team t ON ta.team_api_id = t.id``) even
  though the schema's own naming convention (same-name on both
  sides) telegraphs the right join. The new resolution chain is
  **same-name → bare ``id`` → natural PK**; bare-``id`` still wins
  whenever no same-name column exists on the right side, so the
  change is a no-op for the canonical
  ``orders.user_id → users.id`` shape. Behavior only differs in
  the both-exist case, where the schema author's same-name signal
  is now load-bearing instead of ignored.
- Build's workload-evidence aggregation
  (`aggregate_workload_evidence`) drops single-occurrence mined SQL
  shapes by default when consumed by the semantic-suggestion phase.
  A new keyword-only `min_shape_frequency=1` parameter (default
  preserves legacy behavior) lets callers raise the threshold; the
  build pipeline now passes `min_shape_frequency=2` when feeding
  mined `INFORMATION_SCHEMA.TASKS_HISTORY` rows so a one-shot ad-hoc
  query can no longer single-handedly drive a column's
  `where_counts` / `group_by_counts` past the dim/metric
  classification gates in `suggest_column_semantics` (a lone
  `GROUP BY foo` formerly contributed +0.45 dimension confidence —
  enough to flip a column to `dimension` suggestion on the back of
  one mined query). Verified queries persisted via `mcs memory
  verify` already short-circuit the noise floor; this change
  protects the classification path that derives signal from the
  unverified mined shapes themselves. Repeated mined shapes
  (≥2 occurrences across the lookback) still carry their full
  weight, so genuine reporting workloads are unaffected. Smoking
  gun: the post-fix smoke run's with-history arm regressed by 1
  case vs the no-history arm because singleton mined patterns
  surfaced spurious dim suggestions through the annotation pipeline
  even after `show.py` / `markdown.py` filtered them out of the
  raw `sample_sqls` block.
- Build's join-inference (`phase_infer_joins_heuristic`) no longer
  emits duplicate or phantom-column edges that confused the agent's
  join planning:
  - **Duplicate elimination.** When pattern 1 (``link_to``) /
    pattern 2 (``xxx_id``) already emit an edge for
    ``(left.col) → (right.col)``, pattern 3 (``same_name``) now
    dedups against it instead of re-emitting the same edge with a
    different cardinality label. Drop-target: a recent smoke
    profile rendered a child→parent FK edge as both
    ``n:m via link_to`` AND ``n:1 via same_name`` — two
    contradictory cardinalities for the same edge.
  - **Phantom right-column elimination.** Patterns 0/1/2 no longer
    fall back to ``col["name"]`` when the right table has neither
    ``id`` nor a verbatim same-named column. The new resolver
    chains: bare ``id`` → same-name column → right-table's
    natural-PK pattern (``<table>id`` / ``<table>_id`` with a
    singular variant). When none resolve, the edge is skipped
    rather than fabricated. Drop-target: same smoke profile
    emitted ``child.childdetailid → parent.childdetailid`` where
    ``parent`` had no such column (the pattern-2 reverse-substring
    matcher fired on a name prefix). Side benefit: schemas like
    ``posts.owneruserid`` (StackExchange-style author FK) and
    ``attendance.link_to_event`` (Airtable-style) now resolve to
    the right-side natural PK (``users.userid``,
    ``event.event_id``) instead of a non-existent same-name column.
  - **Left-PK guard.** Pattern 2's substring matcher now skips when
    the FK-shaped column is actually the left table's own PK
    (``id`` / ``<table>id`` / ``<table>_id`` with singular variant).
    Closes the parent's-PK reverse-substring bug where a parent's
    natural PK (``parents.parentid``) would mis-emit a bogus
    parent→child ``xxx_id`` edge after forward-substring matching
    a child whose name embeds the parent's.
- `mcs annotate batch` accepts `aggregation:` as an alias for the
  canonical `agg:` payload key on metric columns. `aggregation` is the
  natural English word smaller models reach for first; pre-fix the
  build layer dropped the field on the floor and the column then
  tripped the rule-3 "agg is only valid with role=metric" check
  (rule-3 inversely: role=metric without agg). Explicit `agg:`
  still wins when both are present. Drop-target: with-history smoke
  artifact 42405233 — forum_db emitted `aggregation: ...`
  on every metric column across 8 tables, contributing to 45/71
  column annotations dropped in that DB's batch.
- `mcs annotate batch` resolves ambiguous combinator agg values like
  `agg: sum_or_avg` / `count_or_sum` by splitting on `_or_` and
  taking the first canonical token. Smaller models reach for this
  shape when they aren't confident which canonical aggregator
  applies; pre-fix the value flunked the canonical-set check
  (rule-3) and the whole annotation was rejected. Now the column
  still gets a usable `role=metric` annotation with a defensible
  best-guess agg. Drop-target: same smoke artifact — half of
  forum_db's metric columns used the combinator form.
- `temporal` joins `date` / `time` / `timestamp` / `datetime` as a
  recognized **role alias** for `dimension` with implicit
  `dim_type=time`. Pre-fix the agent's natural reach for
  `role: temporal` on STRING-date columns hit rule-1 rejection.
  Drop-target: same artifact — forum_db emitted
  `role: temporal` on 3 columns that all dropped to the floor.
- `mcs annotate` no longer hard-fails columns where `id_type=foreign`
  (or its `role: foreign_key` alias) is set without an explicit
  `references:` target — it now demotes the `id_type` to `NULL` and
  keeps the `role=identifier` marker. The build's `join_candidates`
  layer already infers FK relationships from data co-occurrence
  independently of the annotation, so keeping the partial annotation
  is strictly better than dropping the whole row over a missing
  optional field. The explicit canonical shape
  (`role: identifier, id_type: foreign, references: T.col`) is
  unchanged. Drop-target: same smoke artifact — forum_db
  emitted `role: foreign_key` without `references:` on 38 columns
  across its 8 tables, all of which pre-fix were silently dropped.
- `mcs annotate batch` accepts `name:` as an alias for `table:` at
  the table-entry level — symmetric with the column-level `name` /
  `column` / `col` aliases. The agent's natural list-of-tables
  shape (`tables: [- name: T, columns: ...]`) now works without
  rewrite. Pre-fix it raised `tables[0] missing required 'table'
  key` and the whole batch failed. The canonical `table:` key still
  wins when both are present. Drop-target: 0.5.0a29 smoke artifact
  42404704 — catalog_db hit this exact shape.
- `mcs annotate batch` treats `subtype:` as a role-aware alias that
  routes to `dim_type` (when `role: dimension`), `id_type` (when
  `role: identifier`), or `agg` (when `role: metric`). For
  `role: attribute` the subtype is dropped silently (attribute
  carries no per-role substructure). An explicit `dim_type` /
  `id_type` / `agg` field wins when both are present. Mirrors the
  discriminated-union JSON-schema shape agents reach for naturally
  (`{role: identifier, subtype: primary}` → `id_type=primary`).
  Drop-target: same smoke artifact — the catalog_db agent's
  YAML uses `subtype: primary/foreign/unique/categorical/ordinal/
  time/SUM` throughout, and pre-fix every one of those columns
  silently dropped its sub-classifier.
- `mcs annotate batch` accepts the **list-of-dicts** shape for
  `columns:` (each column gets its own ordered block with `name:` /
  `column:` / `col:` as the column-name key) in addition to the
  canonical dict-keyed-by-name shape. Agents reach for the list form
  naturally because it reads more clearly in YAML; pre-fix the parser
  called `.items()` on the list and the resulting `'list' object has
  no attribute 'items'` AttributeError bubbled up to the top-level CLI
  classifier, which wrapped it as `code:"Unknown"` with the
  MaxCompute-specific `remediation:"see logview URL for raw MaxCompute
  error"` (actively misleading — this is a local parse problem). The
  coercion is in `_coerce_columns_spec`; it also rejects missing-name
  entries and duplicates with a clear `click.UsageError` instead of
  letting them surface as cryptic AttributeErrors downstream. Drop-
  target: 0.5.0a28 with-history smoke artifact 42403739 showed three
  Multi-tenant DBs (dba, dbb, dbc) lost all their
  annotations to this single error pattern.
- `mcs annotate batch` catches ruamel.yaml's `DuplicateKeyError` and
  the rest of the `YAMLError` hierarchy at load time and re-raises as
  a `click.UsageError` with the prefix `batch YAML parse error:`. The
  message keeps the parser's line/column context so the agent can
  fix the duplicate inline. Pre-fix the duplicate-key rejection
  bubbled up to the top-level classifier and emerged with the
  misleading `remediation:"see logview URL ..."` even though it
  was a local YAML problem (no server contact was made). Drop-target:
  same 0.5.0a28 smoke artifact — catalog_db hit this for a
  `uuid:` column accidentally annotated twice.
- `mcs annotate batch` treats `agg: none` / `agg: null` / `agg: ""`
  as **no aggregation** (coerced to Python `None` before validation)
  rather than tripping the rule-3 "agg is only valid with role=metric"
  check. Agents pass this marker on every column of a list-form batch
  to signal explicitly that non-metric columns don't aggregate; the
  intent is unambiguous and matches the human convention. Real `agg`
  values (`SUM`, `COUNT`, `AVG`, `MAX`, `MIN`, `COUNT_DISTINCT`) are
  unaffected.
- `map_pyodps_exception` no longer emits the
  `remediation:"see logview URL for raw MaxCompute error"` hint for
  non-pyodps exceptions. The top-level CLI dispatcher calls this on
  every uncaught exception, so before this change any local Python
  error that leaked past a command's own handler (YAML parse errors,
  AttributeError from a malformed input, etc.) was wrapped as if it
  were a server failure with a logview URL — an explicit invitation
  to look in the wrong place. Now: ODPSError subclasses keep the
  logview hint (the server did produce a logview); everything else
  gets `remediation:"local CLI error (not a MaxCompute server
  error); re-run with --debug for a Python traceback"`.
- `mcs annotate batch` and `mcs annotate column` accept common
  shorthand for the `role`, `id_type`, and `dim_type` enums.
  `role` aliases: `pk` / `primary_key`, `fk` / `foreign_key`,
  `unique_key`, `reference`, `id`, `dim`, `measure` / `fact`,
  `attr`, `descriptive`, `date` / `time` / `timestamp` / `datetime`.
  `id_type` aliases: `pk` / `primary_key`, `fk` / `foreign_key`,
  `unique_key`. `dim_type` aliases: `cat` / `category`, `date` /
  `datetime` / `timestamp`. Uppercase variants of canonical values
  (`DIMENSION`, `METRIC`) are also normalized. Role shorthand that
  pins a sub-flag auto-fills the missing slot when the caller didn't
  pass an explicit value: `pk` → `id_type=primary`,
  `fk` / `reference` → `id_type=foreign`,
  `unique_key` → `id_type=unique`, and the temporal aliases
  (`date` / `time` / `timestamp` / `datetime`) → `dim_type=time`.
  Explicit caller input always wins over implicit auto-fill.
  Concrete drop-target: smaller models (qwen3.6-plus on the
  with-history smoke arm) reach for SQL vocabulary on the first
  annotate attempt, hit `rule-1` rejection, and retry within a
  tight turn budget by dropping the `description:` field to fit.
  Two observed regression patterns: the 0.5.0a26 with-history smoke
  profile snapshot had 0/9 columns with `semantic_description` vs
  9/9 in the no-history arm; the follow-up 0.5.0a27 run (first
  alias batch) still showed 35 rule-1 rejections in the with-history
  arm across `descriptive` (25), `reference` (7), and `date` (3) —
  all three now covered.
- `phase_infer_joins_heuristic` no longer emits redundant fact↔fact
  `same_name` edges when both sides already carry a high-confidence FK
  to the same dimension column. Concrete drop-target: a schema
  where seven fact tables each carry `topicid` FKed to `topics.topicid`
  via pattern 1 (`link_to` 0.9); pre-fix pattern 3 then emitted
  `n*(n-1)/2 = 21` noise `fact_a.topicid <-> fact_b.topicid` n:m@0.5
  edges that all collapse to "join via topics" semantically. Across the
  smoke profile snapshots the guard removes ~37 noise edges from one
  schema and ~12 from another while preserving every fact-dimension
  edge. Schemas where the shared FK column has no parent table in the
  profile (the `team.team_api_id = team_attributes.team_api_id` case)
  keep their `same_name` edge — the guard only fires when both sides
  have a recorded high-confidence FK target.
- `phase_infer_joins_heuristic` no longer emits `same_name` edges between
  two temporal columns (native `DATE` / `DATETIME` / `TIMESTAMP` /
  `TIMESTAMP_NTZ` type, `dim_type='time'` annotation, or STRING column
  whose sample values mostly match `YYYY-MM-DD` shape). Sub-second
  precision pushes columns like `creationdate` to
  `uniqueness_ratio ≈ 1.0`, which satisfied the `one_side_pk_like`
  gate in the pattern's PK-like check and produced phantom
  `T1.creationdate = T2.creationdate` joins between unrelated tables.
  Concrete drop-target: schemas where many tables (e.g. `articles`,
  `comments`, `posts`, `users`, `votes`) all carry a `creationdate`
  column and the pre-fix run wrote many spurious `via creationdate`
  edges into
  `_overview.md` / `_joins.md`. Legitimate FK→PK same_name shapes
  (non-temporal columns, e.g. a natural-key `uuid` PK ↔ `uuid` FK)
  are unaffected — only edges where both sides are temporal are
  dropped.
- `phase_infer_joins_heuristic` no longer emits `same_name` edges between
  two URL columns (STRING column whose sample values mostly start with
  `http://` or `https://`). Each entity's Wikipedia/profile URL is
  unique within its own table (`uniqueness_ratio` ≈ 1.0), satisfying the
  `one_side_pk_like` gate; the pre-fix run wrote many spurious
  `via url` edges between unrelated entity tables that each carried
  their own per-row Wikipedia/profile URLs. Symmetric guard: only
  edges where BOTH sides are URL-shaped are dropped; identifier-
  shaped STRING FK joins (e.g. `country.code = shipment.code`) are
  unaffected.
- `phase_infer_joins_heuristic` no longer emits `same_name` edges between
  two NUMERIC columns whose names lack an identity-suggesting suffix
  (no `_id` / `id` / `_key` / `_code` / `_num` / `_no` / `_ref` ending).
  Monetary amounts, scores, weights, durations, and similar metric
  columns frequently have one side with high uniqueness (continuous
  value distribution); the high-uniqueness side tripped the
  `one_side_pk_like` gate and emitted phantom `T1.amount = T2.amount`
  edges between unrelated tables. Concrete drop-target: a
  multi-entity schema where three transactional tables (e.g. `loan` /
  `order` / `trans`) each carry an `amount` numeric column and the high-
  uniqueness side (uniqueness ≈ 0.97 when each row has a distinct
  dollar value) tripped the PK-like gate; the pre-fix run wrote
  spurious `via amount` edges across every pair that also propagated
  as misleading `amount:int [fk]` markers in the columns_index.
  Symmetric guard: only edges where BOTH sides are numeric value
  columns are dropped; legitimate FK joins like
  `team.team_api_id = team_attributes.team_api_id` survive via the
  identity-suffix exemption.
- `phase_infer_joins_heuristic` no longer emits `same_name` edges between
  two label-shaped STRING columns (column type STRING/VARCHAR/CHAR/TEXT
  AND column name in `{name, title, label, caption, description,
  comment, notes, summary, subject}`). Each entity's display label is
  unique per row (`uniqueness_ratio ≈ 1.0` when the table has distinct
  names), satisfying the `one_side_pk_like` gate; the pre-fix run
  wrote spurious `T1.name = T2.name` edges between unrelated entity
  tables whose display-name value spaces don't overlap. Symmetric
  guard: only edges where BOTH sides are label-shaped are dropped;
  legitimate FK joins on suffixed label columns (e.g.
  `users.user_name = login_attempt.user_name`) flow through the
  regular FK-shape branch because the name isn't an exact label
  keyword.

### Added

- SKILL.md `SELECT only what the question asks for` section gains two
  bullets covering failure modes seen in the latest with-history smoke:
  (a) **GROUP BY does not pull columns into SELECT** — engines require
  non-aggregated SELECT columns to be in GROUP BY but not the reverse,
  so filter/join columns dragged into SELECT "to keep the GROUP BY
  tidy" pollute the result tuple (concrete shape: a query projects
  `segment` because it was in GROUP BY, even though `segment` was
  already pinned to a single value in WHERE); (b) **Anchor SQL to
  the QUESTION, not the EVIDENCE** — evidence may carry reference
  snippets for entities the question doesn't name (concrete shape:
  evidence includes an unrelated attribute line for a question that
  only asked about other attributes), and the agent must drop
  unmoored evidence lines instead of materializing them as filters. Two new
  worked examples (one per trap) and a self-check addition that points
  back at the question text for every WHERE / JOIN filter.
- `_overview.md` `joins_to` entries now carry the join cardinality
  from the owning table's perspective as a trailing `[1:n]` / `[n:1]`
  / `[1:1]` / `[n:m]` marker (e.g. `joins_to: [orders via customer_id
  [1:n]]` on `customers`, flipped to `joins_to: [customers via id
  [n:1]]` on `orders`). The cardinality has always been computed and
  stored by `phase_join_inference`; before this change it was only
  visible in `_joins.md`, which the agent rarely loads. Surfacing it
  on the always-loaded overview lets the agent see at a glance
  whether a JOIN fans out (1:n → `COUNT(*)` after the join inflates
  this table's entity count by the average fan-out, `DISTINCT` needed
  or count from this side directly) or fans in (n:1 → partner filter
  columns are safe to pull into WHERE without changing this-table row
  count) or is a bridge (n:m → every count needs explicit `DISTINCT`).
  SKILL.md hint extended with the four-cardinality decision table.
- `_overview.md` `joins_to` entries now include the own-side join column
  as `partner_table via own_col` (was `partner_table` alone). The agent
  composing a JOIN saw `joins_to: [orders]` on `customers` and had to
  fan out one `mcs show --table orders` per partner just to discover
  which column on the current table was the join key — a round-trip
  cost that drove pick-the-wrong-FROM-table failures in the with-history
  smoke arm. Now `joins_to: [orders via customer_id]` on `customers`
  says outright that `customers.<id> = orders.customer_id`; the agent
  still consults the partner's `[pk]` / `[fk]` markers in
  `columns_index` for the matching key on the partner side, but no
  longer needs a partner round-trip to learn the own side. Cross-source
  joins keep their `source_key.partner_table via own_col` shape. SKILL.md
  hint updated to describe the new format.
- SKILL.md gains a `Pick the FROM table from the subject of the question`
  section. Result-set comparison is sensitive to the FROM table, not
  just the filter logic — counting from a child table on the N side of
  a 1:N join inflates the denominator by the average fan-out, so
  "% of X" / "how many X" questions now have explicit guidance to use
  X's table as FROM with `COUNT(x_table.id)` as denominator, pulling
  join partners in for filter columns rather than swapping the FROM.
  Also covers INNER vs LEFT JOIN selection from question phrasing
  ("with their Y" → INNER, "with optional Y / Y if any" → LEFT).
  Generic SQL pattern guidance — no schema-specific examples.

### Fixed

- Identifier markers (`[pk]` / `[fk]` / `[unique]`) in `mcs show` now
  override the `[const]` warning when the warning is the artifact of
  a 20-row column sample. `phase_column_sampling` reads
  `SELECT * FROM table LIMIT 20` and computes `distinct_count` from
  what it sees — for foreign-key columns in 1:n parent→child
  relationships, those 20 rows often all point at the same parent ID,
  giving `distinct_count == 1` even though the column carries
  hundreds of distinct IDs in the full table. The agent then read
  `column [const]` and avoided the join. Real-world trigger: a
  clinical-domain `observation.subject_id` (the subject FK on a fact
  table where each subject has many observation rows) surfaced as
  `id [const]` in the with-history smoke arm, dropping cohort-by-
  observation questions.
  Now: when `id_markers` carries structural counter-evidence
  (`[pk]` / `[fk]` / `[unique]` from a confirmed annotation, ≥0.7
  suggestion, or the join graph), the structural marker wins and
  `[const]` is suppressed. `[null]` is NOT subject to this override
  — a 99%-null sample is far more representative than distinct=1
  from a single 20-row batch.
- Join-graph-derived `[fk]` / `[pk]` markers in `mcs show` now override
  suggestion-level `[unique]` markers on the same column. In 1:1
  relationships (e.g. a billing-domain `account_link.client_id` — every
  account-link row maps to exactly one client) the uniqueness signal was firing a
  high-confidence `[unique]` suggestion that masked the more actionable
  `[fk]` edge from the join graph. The agent saw `client_id [unique]`
  and treated the column as merely distinctive instead of as the join
  key into `client`. `[unique]` says "this column is distinctive";
  `[fk]` says "this column joins to *that* table" — strictly more
  useful for SQL generation. Confirmed annotations and suggestion-level
  `[pk]` / `[fk]` still beat the join graph (operator-confirmed or
  higher-confidence than the join-edge floor); only the weaker
  `[unique]` is now upgradeable.

### Changed

- SKILL.md projection-discipline rules now separate **categorical** from
  **scalar** question shapes. The earlier wording lumped "what" together
  with "which / who / list" and a single worked example ("Which month had
  the largest consumption" → project the month) pulled the agent into
  projecting the *group* even when the question asked for a *value*. The
  benchmark-full at commit 12c3a4b regressed a finance-domain case
  ("What is the highest monthly consumption in the year 2012?") from PASS
  to FAIL because the agent projected the month string instead of
  `SUM(consumption)`. The new rules split:
  - "Which X / who X / list X / name the X" → categorical (project the
    column that names X).
  - "What is the highest / largest / maximum / total / average X" →
    scalar (project the aggregate of X itself).
  ORDER BY-as-filter guidance is now conditional on which form the
  question takes. A second worked example pairs the two month-consumption
  forms side-by-side so the agent sees the distinction explicitly.



- The `[date]` marker in `mcs show` columns_index now splits into two
  distinct markers based on the underlying type, eliminating the
  ambiguity that prior docs had to disambiguate verbally:
  - **`[str-date]`** — STRING-typed date column. Date functions
    (`YEAR`, `MONTH`, `TO_CHAR(col, fmt)`, `DATEDIFF`, `DATEADD`)
    return NULL silently on STRING; the agent must use
    `SUBSTR(col, 1, 4)` or `TO_DATE(col, 'yyyy-MM-dd')` wrap.
  - **`[date]`** — non-STRING non-native-temporal column annotated as
    `dim_type='time'` (typically a BIGINT unix timestamp). The agent
    should wrap with `FROM_UNIXTIME(col)`.
  Native temporal types (`DATE` / `DATETIME` / `TIMESTAMP`) continue
  to carry only their `:date` / `:datetime` / `:timestamp` type tag
  and no marker.

  Empirical motivation: smoke 42389092's clinical-domain age-from-dates
  case saw `birthday [date]` and `first_date [date]` on STRING-typed
  columns and still wrote `YEAR(first_date) - YEAR(birthday)`,
  returning NULL. The conflated marker required the agent to
  cross-reference the absence of a type tag with a disambiguator in
  rules.md — too much inference per query. `[str-date]` is
  self-documenting at point of use.

### Added

- `references/rules.md` now documents the columns_index marker legend
  (`[pk]` / `[fk]` / `[unique]` / `[null]` / `[const]` / `[str-date]` /
  `[date]`) and a dedicated "STRING-typed dates" section. The agent
  previously saw bracketed markers in `mcs show` output with no
  documented meaning, and routinely called `TO_CHAR(STRING, 'yyyy')` /
  `YEAR(STRING)` / `MONTH(STRING)` on STRING-typed date columns —
  MaxCompute returns NULL silently in that path, so the SQL parses and
  runs but yields an empty result set (EX=0 with no error signal).
  Benchmark-smoke 42387793 surfaced two failures from this exact
  trap (one on a sports schema's `team_attributes.date` and
  one on a clinical schema's `patient.birthday`, both
  STRING-typed); other STRING-date columns across the workload
  (`Match.date`, `Player.birthday`, `Player_Attributes.date`,
  `frpm.first_date`, …) carry the same risk. The new section prescribes
  the two safe patterns (`SUBSTR(col, 1, 4)` for year, or
  `TO_DATE(col, 'yyyy-MM-dd')` cast first) and the trap row in the
  function table cross-references the longer explanation.

- `SKILL.md` now carries a "Column markers in `mcs show`" callout that
  surfaces `[str-date]` and `[null]`/`[const]` (the result-corrupting
  markers) directly in the always-loaded skill index. The agent no
  longer has to load `references/rules.md` before composing SQL to know
  these gotchas — they're at the same scope as the projection-discipline
  rule. rules.md remains the full reference for the lower-stakes
  markers.

### Fixed

- High uniqueness alone no longer promotes STRING / DOUBLE columns to
  `identifier` when the column shape has no other identifier signal.
  Benchmark-smoke 42386218 surfaced the regression at scale: 4/9
  columns in a motorsport schema's `circuits` table were tagged
  `identifier` via the uniqueness-only path — `circuits.name` (STRING,
  0.986), `circuits.url` (STRING, 0.986), `circuits.lat` and
  `circuits.lng` (DOUBLE, 1.0) — none of which are identifiers; they're
  descriptive attributes that happen to be unique per row in the sample.
  Similar false positives across the smoke matrix: `drivers.driverref`,
  `member.{first_name, last_name, phone}`, `posts.body`, `catalogs.name`.
  Guard: the +0.35 uniqueness boost (uniqueness ≥ 0.98) now requires
  either an id-shape name (ID_NAME_RE matches OR bare suffix `id` /
  `uuid` / `key` / `code`) OR integer type (BIGINT / INT). Genuine
  identifiers still pass: `uuid` and `setcode` clear via the suffix
  path; `circuitid`, `driverid`, bare `id` clear via integer type.
  JC-derived identifier promotions flow through the separate JC-boost
  block unchanged, so STRING foreign keys that join to other tables
  (with `link_to` or `xxx_id` evidence) still get surfaced.

- `same_name`-only join candidates no longer over-promote generic-text
  columns to `identifier/foreign`. The join engine emits a `same_name`
  edge whenever two tables happen to have an identically-named column,
  regardless of content semantics — this is its weakest signal, used
  as a last-resort tie-breaker after `link_to` / `xxx_id` patterns
  fail. Without this guard the suggester took that as identifier
  evidence and tagged STRING content columns like `items.name` /
  `catalogs.name` / `attr_data.name` (entity-catalog schema) as
  `identifier/foreign`, polluting the suggestion stream. Guard: a
  `same_name`-ONLY JC (no `link_to` / `xxx_id` evidence) skips the
  +0.45 identifier boost unless the column shape independently looks
  like an ID — name ends in `id` / `uuid` / `key` / `code`, OR type
  is BIGINT / INT. Mixed-kind JCs (e.g. `same_name + xxx_id`) and
  pure `link_to` JCs always pass through; integer-typed and
  id-suffixed columns continue to receive the boost. Net effect on
  the suggester: identifier suggestions become noticeably less noisy
  on tables with shared category-name columns.

- METRIC_NAME_RE substring matching no longer mis-promotes
  identifier-suffix columns whose name happens to contain a metric
  token. Concrete regression caught in benchmark-smoke 42385590
  (forum schema): `users.accountid` (BIGINT, uniqueness 0.92)
  was suggested `metric/SUM` because the `count` substring inside
  `accountid` matched the metric-name regex, but the agent confirmed
  it as `identifier/unique`. The guard suppresses the metric boost
  when the column name ends in `id` / `uuid` / `key` / `code` —
  those endings are overwhelmingly identifier semantics regardless
  of substring collisions. Legitimate metric names ending in `count`
  / `amount` (e.g. `viewcount`, `bountyamount`) continue to match.

- Columns named with strict temporal suffix (`date` / `_date` /
  `releasedate` / `datetime` / `timestamp` / `created` / `updated` /
  `_at` — e.g. `events.date`, `catalogs.releasedate`,
  `items.originalreleasedate`, `created_at`, `updated_at`) now get a
  +0.35 dimension boost that lands them as `dimension/time`
  suggestions regardless of NDV cardinality. Time dimensions
  inherently have unbounded distinct values (one per day for the
  recorded history), so they routinely blow past the NDV-tier dim
  boost ceiling of 100 distinct values and previously fell through to
  `attribute`. Bare `time$` suffix is deliberately excluded from the
  new `TIME_DIM_NAME_RE` so duration columns (`laptime`,
  `fastestlaptime`, `responsetime`) stay classified as `attribute` —
  the broader `TIME_NAME_RE` continues to handle the identifier
  carve-out where over-matching is the safer error.

- Bare `id` / `uuid` columns with sampled uniqueness 0.95–0.989 are
  now classified `identifier/primary` (previously: `identifier/foreign`).
  On large tables the dump's bootstrap sample doesn't always exhaust
  the value space, so a true surrogate PK's measured uniqueness ratio
  can drift down to ~0.98 — strict `≥ 0.99` threshold then mis-tagged
  the PK as `foreign`, and the agent's `mcs annotate batch` pass
  hallucinated a plausible-looking `references: <other-table>.id` to
  satisfy the foreign subtype. Concrete regression caught in
  benchmark-full 42383796: `catalog_translations.id` (true PK, sampled
  uniqueness 0.98) was tagged foreign with phantom
  `references: catalogs.id`, driving the agent to write
  `JOIN ON ct.id = c.id` instead of the correct
  `JOIN ON ct.catalog_code = c.code` on two entity-catalog cases.
  Non-bare suffix names (`raceid`, `user_id`, `catalog_code`) keep the
  strict `≥ 0.99` threshold so genuine FK columns with incidentally-
  high uniqueness still land as foreign.

### Changed

- SKILL.md main body gains the "don't apply display formatting unless
  asked" bullet plus a worked percentage-query example. The same rule
  has been in `references/query.md` since 0.5.0a5 but benchmark-full
  showed agent ROUND usage was unchanged (14 cases both runs, 11 vs
  10 failures) — the rule was buried under an on-demand load. Promoting
  it to SKILL.md (which the agent reads every turn) brings the
  guidance where the model sees it before composing SQL.
- SKILL.md `references/query.md` projection-discipline section gains a
  "don't apply display formatting unless asked" rule and a matching
  pre-execution self-check. `ROUND(..., 2)`, `CAST(... AS INT)` on a
  true real value, `CONCAT(x, '%')`, etc. discard precision or change
  the type — programmatic callers (EX-style result comparisons, any
  downstream pipeline) compare values, not display strings. Eleven
  benchmark-full failures used `ROUND(... * 100 / ...)` where gold
  emitted the raw ratio; all eleven failed. The rule is neutral SQL
  hygiene, not a benchmark-specific tweak — formatting belongs in the
  presentation layer, not the SQL.

### Added

- `[pk]` / `[fk]` markers in `_overview.md` columns_index now derive
  from the inferred join graph as a third-tier signal when no
  confirmed annotation and no ≥0.7 suggestion exists. A column that
  appears as the FK side of a `link_to` / `xxx_id` edge, or either
  side of a `same_name` edge (≥0.5 confidence after the join
  engine's PK↔PK and attr↔attr filters), surfaces as `[fk]`; an
  `id` column on the PK side of a `link_to` / `xxx_id` edge
  surfaces as `[pk]`. Phantom `loose_id` rows are skipped so the
  agent never sees an `[fk]` marker pointing at a non-existent
  table. Closes the "two name-plausible join-key
  candidates on one table" failure (e.g. `cdscode` vs `school_code`
  on an education-domain `frpm` table) by nudging the agent toward
  the canonical join key without per-DB annotation passes.

- `[date]` marker in `_overview.md` columns_index now also honors a
  confirmed `dim_type='time'` annotation on non-native-temporal columns
  (e.g. a `BIGINT` epoch column). The annotation is a stronger signal
  than the STRING format_examples heuristic and tells the agent to
  reach for `from_unixtime(...)` / `to_date(...)` wraps without
  drilling into per-table detail.

- `_overview.md` columns_index entries now carry a `[date]` marker for
  STRING columns whose `format_examples` are mostly date-like
  (`YYYY-MM-DD` / `YYYY-MM` / timestamp form, 50%-majority threshold).
  Profiles with `semantic_description` set on a column now inline that
  description as a trailing `  # description` comment in the index. The
  agent sees both signals at overview load time without round-tripping
  `mcs show --table T`, so SQL composition reaches for `to_date(...)` /
  `year(to_date(...))` on STRING-typed dates and picks the right
  projection target when descriptions clarify column meaning.

### Fixed

- `mcs skill install -p codex -g` now lands in `~/.agents/skills/`
  (matching CLAUDE.md's documented Codex global path and the
  multi-agent-install CI yaml) instead of `~/.codex/skills/`. The
  registry entry was incorrectly routed to a `.codex/` namespace
  during the 55-platform expansion.

- `mcs skill list --detect` now reports a platform as detected when
  *either* its global home config dir or its local project dot-dir
  exists. Previously it only checked the local cwd dot-dir, so global
  installs (e.g. `~/.claude/`) were invisible to `list --detect`.

- `install.ps1` now correctly exits with code 127 when neither `uv`
  nor `python` is on PATH. Under `$ErrorActionPreference = 'Stop'`
  the previous `Write-Error` calls raised a terminating exception
  that exited with 1 before the explicit `exit 127` ran; swapped to
  `[Console]::Error.WriteLine` for the emit-and-control-exit paths.

- `mcs update` self-upgrade command — fetches the latest wheel from
  `https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com` (or
  `MCS_UPDATE_BASE_URL`), auto-detects the install method (uv tool /
  pipx / pip --user / system pip), and reinstalls in place. The
  `--version <pin>` flag allows downgrading to a specific release.
  Post-install, `mcs skill update --all` re-links the agent-side
  SKILL.md symlinks.

- Update-check banner on every `mcs` invocation — a daemon thread
  probes `latest.json` in the background (6-hour TTL cache at
  `~/.cache/maxcompute-semantic/`). When a new version is available, a
  soft non-blocking banner appears on stderr. When the running version
  is on the publisher's `disabled[]` list or below `min_supported`,
  the banner hardens and the command exits code 2 (gate, not nag).
  Suppress the soft banner with `MCS_NO_UPDATE_CHECK=1`.

- `mcs doctor` now includes two update-channel checks (`update_channel`
  for `latest.json` reachability and `update_version` for the version
  comparison), sharing a single HTTP fetch. The doctor run also warms
  the banner cache so the next foreground command reflects the freshly
  fetched state.

- OSS-hosted bootstrap scripts (`install.sh` for macOS/Linux,
  `install.ps1` for Windows) — `curl | bash` installers that detect
  uv vs pip, pull the latest wheel, and run `mcs skill install`.

- `_internal/update_check.py` module — PEP 440 version comparison,
  on-disk cache with atomic writes, suppression rules per subcommand /
  TTY state / env var, and a daemon-thread probe for non-blocking
  metadata refresh.

- Column profiling now measures a STRING column's `cast_rate` — the
  fraction of non-null values that survive `CAST(... AS DOUBLE)`. The
  profile aggregate emits one extra `COUNT(CAST(c AS DOUBLE))` per
  STRING column (same single full-table scan, no separate query), and
  the result is persisted on `columns.cast_rate` (new schema v8, with
  a v7→v8 ALTER migration that backfills NULL for pre-existing rows).
  Numeric-typed columns are unaffected. The semantic-suggester uses
  this to **suppress the `metric` suggestion** for STRING columns that
  history SQL happens to aggregate over but whose values are
  predominantly non-numeric (e.g. clinical measurement columns mixing
  "12.3" / "0.5" with "negative" / "trace") — MaxCompute's permissive
  CAST would otherwise return NULL on dirty rows and the agent would
  silently `AVG(...)` over an unrepresentative partial slice. When
  demoted, the per-column `annotation_suggestions.evidence` carries a
  `demoted_from=metric, tier=dirty_string_numeric, cast_rate=…` entry
  so the agent reading the suggestions still sees why metric was
  considered and rejected. The 0.99 threshold (≥99% castable) matches
  the uniqueness gate used for `identifier/primary`.
- `phase_column_profiling`'s default column cap is raised from 12 to
  200 (`DEFAULT_PROFILE_LIMIT`). The aggregate scans the table once
  regardless of column count, so the per-column marginal cost is
  effectively zero; the old cap was leaving 30+ columns out of the
  profile aggregate on typical wide warehouse tables, starving the
  semantic-suggester of the per-column stats it needs.

- `mcs profile create` wizard's Step 1.5 credential picker now lists
  existing mcs profiles as templates alongside maxc / odpscmd configs.
  Selecting an mcs profile prompts per-field (auth / endpoint /
  compute_project / data sources) for what to clone; defaults clone
  auth+endpoint but prompt fresh for project+sources, matching the
  typical "new scenario" intent.

- `mcs annotate batch` now accepts a plural `tables: [...]` payload
  shape in addition to the legacy singular `table:` shape — one
  invocation annotates every table in the profile, the markdown
  projections re-render once at the end, and per-entry failures
  (table not found, ambiguous bare name) land in the result envelope
  instead of aborting the batch. The build-Step-2 sweep is the
  primary caller: 8+ table DBs were running out of turn budget when
  the agent serialized per-table batch calls; the plural shape
  collapses N round-trips into one. Top-level `--source` applies as
  the default `source_key` for entries lacking their own; per-entry
  `source:` overrides; disagreement between an entry's `source:` and
  the CLI `--source` flag errors before any write. The singular
  envelope and exit-code semantics are unchanged for backward
  compatibility; the plural envelope adds `tables_total` /
  `tables_succeeded` / `tables_failed` / `columns_written` /
  `columns_failed` / `results: [...]` and exits 4 only when **every**
  entry hit a table-level error (mixed success → exit 0).
  `references/annotate.md` and `references/build.md` both updated to
  recommend the plural shape as the default for the build sweep.

### Fixed

- `_state.json.joins_count` and the `mcs show` JSON envelope's
  `joins_count` now exclude phantom-table edges — joins whose left or
  right table isn't present in the package. Previously both surfaces
  reported the raw `joins` table count while `_joins.md` already
  filtered the same edges via `render_joins`, so the agent saw
  inconsistent numbers between the markdown projection and the
  build-state / overview JSON. The new test
  `test_state_joins_count_excludes_phantom_table_endpoints` in
  `tests/unit/build/test_markdown.py` and
  `test_show_overview_json_joins_count_excludes_phantom_endpoints`
  in `tests/unit/commands/test_show_cmd.py` lock the agreement.
- `build/phases.py` `phase_infer_joins_heuristic` recognizes two more
  FK naming conventions so the inferred `_joins.md` no longer silently
  diverges from the per-table `identifiers[].references` the LLM
  annotator extracts:
  - **Airtable/Notion `link_to_<table>`** (new pattern 0): columns like
    `attendance.link_to_event` / `attendance.link_to_member` now emit
    a `link_to` edge to `event.id` / `member.id` at confidence 0.9
    (0.8 via trailing-word split on compound bases). The literal
    `link_to_` prefix is treated as a strong FK signal even though
    the column doesn't end in `_id`.
  - **StackExchange/SQLite no-underscore `<X>id`**: pattern 1 now
    accepts `userid` / `postid` / `creatoruserid` and resolves them
    against `users` / `posts` via the existing exact + `+s`-plural
    lookup; pattern 2 adds a reverse-substring direction so
    qualifier-prefixed FKs like `owneruserid` / `lasteditoruserid`
    resolve to `users` (singular `user` is a substring of
    `owneruser`). A `len(col_name) >= 5` guard in the new
    `_fk_suffix_form` helper rules out FK-shaped false friends
    (`bid`, `aid`, `paid`, `uuid`, `void`), and pattern 4
    (`loose_id`) stays restricted to the strict `_id` form so the
    no-underscore relaxation never produces phantom markers against
    nonexistent tables.

  Observed pre-fix gap: a forum-domain build had 15 annotator-only
  FKs the miner missed; an events-domain build had 8. Same-project builds
  with FK columns ending in `_id` are unaffected — the new patterns
  are strictly additive.
- `build/phases.py` `phase_list_tables` now honors a source's
  `tables: [...]` allowlist. Previously the schema parsed and
  validated the list, but the build pipeline ignored it and
  enumerated every table in the live `(project, schema)` — a
  profile that listed 5 tables would still describe / sample /
  profile all N tables in the schema. The phase now intersects
  the live catalog with `source.table_names()` when
  `is_wildcard()` is false, so placeholder rows in `package.db`
  also track the allowlist (no orphan rows for non-allowlisted
  tables). Names listed in the profile but missing from the live
  catalog surface as a build warning (typo / dropped-on-source
  signal) without aborting the build. The CLI `--tables` flag is
  still applied as a further narrowing filter on top — profile
  defines the universe, `--tables` picks a per-run subset.
- `build/phases.py` `phase_column_sampling` and
  `phase_column_profiling` now formulate the `FROM` clause using
  the **connection's** tier (the `compute_project`'s) instead of
  the **source project's** tier. Earlier: a 3-level compute
  project reading a cross-project 2-level source got a bare
  `FROM <table>` (because the source-project tier was 2), which
  the 3-level connection's parser then resolved under the
  compute_project — producing
  `Table not found - table <compute_project>.\`default\`.<table>`
  on every sampling/profiling SQL. Both phases now call
  `get_tier(profile, profile.compute_project, ...)` so the SQL
  form matches the parser's expectations; the
  `odps.namespace.schema=true` hint that `client.execute_sql`
  injects via `build_hints` already keys on `client._tier`
  (= compute_project's tier), so cross-project reads to a 2-level
  source under a 3-level compute project resolve via the canonical
  `<src.project>.default.<table>` 3-segment form per MaxCompute's
  2→3 upgrade naming convention. Same-project builds (the common
  case) are unaffected — connection tier == source tier, same SQL
  form as before.
- `auth/schema.py` `DataSource.qualified_for_connection` now
  emits the right SQL `FROM`-clause shape for the **fourth**
  cross-tier topology — a 2-level connection cross-reading a
  3-level source's non-`default` schema. The 2-level parser
  rejects 3-segment FQNs by default
  (`ODPS-0130161 Parse exception - full qualified name ... is not
  supported`), but `odps.namespace.schema=true` flips it open.
  Live-validated 2026-05-22 that the pyodps `execute_sql(...,
  hints={...})` path applies the hint session-locally **without**
  needing `odps.sql.submit.mode=script` (which only matters for
  multi-statement SQL strings). New
  `DataSource.connection_hints(conn_tier=...)` returns
  `{"odps.namespace.schema": "true"}` for exactly this row of the
  matrix and an empty dict otherwise; `phase_column_sampling` and
  `phase_column_profiling` now pass that dict to `client.execute_sql`
  so the build pipeline reaches every legal `(conn_tier, source)`
  combination. Topologies covered:

  | conn_tier | xproj? | src.schema    | form                        | extra hint               |
  |-----------|--------|---------------|-----------------------------|--------------------------|
  | 3         | any    | any           | `<src.proj>.<src.sch>.<t>`  | (none — client injects)  |
  | 2         | no     | `default`     | bare `<t>`                  | (none)                   |
  | 2         | yes    | `default`     | `<src.proj>.<t>`            | (none)                   |
  | 2         | yes    | non-`default` | `<src.proj>.<src.sch>.<t>`  | `namespace.schema=true`  |
- `build/markdown.py` `_trim_evidence` now also rounds
  `left_uniqueness_ratio` / `right_uniqueness_ratio` inside
  evidence entries — the join-candidate miner emits the prefixed
  pair (not the bare `uniqueness_ratio`), so the earlier
  round-extension fix in `0.4.0a48` missed them and per-table .md
  kept surfacing FP-residue values like
  `left_uniqueness_ratio: 0.5875444289908824` /
  `right_uniqueness_ratio: 8.520145410481673e-06` inside the
  `evidence[].join_shape` block of every `join_candidates:` entry.
  Now both prefixed keys flow through `_round_confidence` (2
  decimals); the in-DB raw floats are preserved.
- `build/markdown.py` extends the confidence-rounding fix from
  `0.4.0a47` to two more agent-facing leak sites:
  - `render_joins` now rounds `_joins.md` `relationships[].confidence`
    via the same `_round_confidence` helper (previously emitted raw
    FP-residue values like `0.6972222222222222`).
  - `compact_column_entry` and the per-table `fm_columns` build path
    now round per-column `null_ratio` via a new `_round_null_ratio`
    helper at 4-decimal precision (0.01%) — coarse enough to kill
    `0.18999999999999997`-style residue, fine enough that 0.5%-null
    columns don't collapse to `0.0` and read as "no nulls at all".
  In-DB raw floats are preserved for downstream ranking and threshold
  gates; rounding is at the agent-boundary only.

### Added

- `build/markdown.py` `render_overview` now embeds three first-look
  hints in each per-table entry of `_overview.md`'s frontmatter:
  - `ai_context` — the table's one-line `ai_context` from
    `set_table_ai_context` (when annotated), so the agent learns
    the table's purpose without round-tripping `mcs show --table T`.
  - `columns_index` — first 20 non-partition column names with a
    trailing `"..."` sentinel when truncated; lets the agent see
    candidate answer columns directly in the overview and avoids
    picking the wrong table when the desired column only lives on
    a join partner. Matches the projection-discipline guidance in
    SKILL.md.
  - `joins_to` — first-hop join partners sourced from
    `db.list_joins()`. Bare table names for same-source partners;
    `source_key.table` form for cross-source partners so multi-
    source profiles stay unambiguous. Surfaces the join graph at
    the overview level instead of requiring a per-table probe.

### Changed

- `mcs profile create` wizard now nudges users toward the conventional profile shape: the `compute_project` picker shows a tip about the SQL-execution / dev convention (and that the AK needs job-execute permission) and surfaces `*_dev` projects first; the data source picker shows a tip about querying production data (and that the source is the read-only real data layer) and surfaces the prod counterpart (`compute_project` minus `_dev`) first, with dev as the second-row alternative. Naming convention is `_dev` suffix only; non-`_dev` defaults are passed through unchanged. The tip is rendered inside fzf via its native `--header` flag so it stays visible during the full-screen interactive selection (an earlier form printed it to stderr before opening fzf, where the full-screen UI immediately scrolled it off-screen). The fzf prompt label is now role-specific (`Compute project (where SQL executes — usually a *_dev project):` vs `Data source (the project whose tables you'll query — usually production):`) so the compute step no longer shows the data-source wording.
- `_skill/SKILL.md` gains a "Profile design — dev vs prod" subsection under `## Multi-source profiles` documenting the rule and the rationale for not adding dev as a second source (join inference would emit cross-environment phantom joins).
- Skill `SKILL.md` "SELECT only what the question asks for" section
  rewritten with three worked examples — `event.name`-vs-
  `(event.name, ratio)` for "which X has highest Y", month-only-vs-
  `(month, sum)` for "which month had largest Y", and single-scalar-
  vs-broken-out aggregates for "difference between A and B" — plus
  an explicit "self-check before executing" paragraph listing
  invalid justifications (context, the value ordered by, intermediate
  computation step). Mirrors into `references/query.md`'s
  projection-discipline section. The strict-tuple-comparison failure
  mode is general SQL question-answering practice, not benchmark-specific.

### Fixed

- `build/markdown.py` `trim_annotation_suggestion` and `trim_join_candidate`
  now round agent-facing `confidence` (plus per-evidence `confidence`,
  `uniqueness_ratio`, and `coverage_ratio`) to 2 decimal places. The
  classifier sums per-signal floats whose arithmetic leaves FP residue
  (`0.55 + 0.10 → 0.6500000000000001`, `0.5 + 0.1 → 0.6000000000000001`).
  The verbatim long-form values landed in per-table `.md` frontmatter
  and the `mcs show --table T` JSON envelope, where they ate agent
  tokens and read as garbage data — a small model may also misjudge
  threshold reasoning ("is `0.6499999999999999` below my 0.65 cutoff?").
  In-DB values are unchanged so ranking and threshold gates still see
  the raw float; rounding happens at the agent boundary only.
- `build/phases.py` `phase_column_sampling` no longer marks 100%-NULL
  columns as `is_enum`. The prior `distinct_count <= 30` test evaluated
  True when `distinct_count == 0` (no observed values), producing rows
  with `is_enum=1` and an empty `sample_values_json`. Downstream
  surfaces (`_overview.md`, per-table `.md` frontmatter, `mcs show`
  JSON envelope) then advertised the column as an enum without any
  values — an internally inconsistent semantic-layer output. Tightened
  to `1 <= distinct_count <= 30`. Most visible on wide tables with
  systematically-NULL columns (e.g. a sports-domain `match.md`
  shipped 44 player-position columns that were entirely NULL in the
  loaded slice). Additionally, `phase_column_sampling` now clears
  `sample_values_json` when a column flips from `is_enum=True` to
  `False` on a re-sample (cardinality grew past 30 / new sample is
  all-NULL), preventing stale enum values from lingering in surfaces
  the current data no longer supports.
- `build/phases.py` `phase_infer_joins_heuristic` pattern 1 (link_to)
  now also tries progressively shorter trailing-word splits of the
  base name when the exact match fails. Common
  ``{qualifier}_{table}_id`` FK convention — e.g.
  ``entity.eye_colour_id`` resolves to ``colour.id``,
  ``customer_account_id`` to ``account.id``,
  ``payment_method_id`` to ``method.id``. Trailing-word matches
  land at confidence 0.8 (strictly below exact 0.9 so the agent
  still prefers the unambiguous form when both ``eye_colour`` and
  ``colour`` exist as tables, strictly above pattern 2's broader
  substring form at 0.7). Concrete failure mode this restores: a
  roster-domain case needs
  ``entity.eye_colour_id = colour.id`` /
  ``entity.hair_colour_id = colour.id`` joins — they were
  emitted as ``loose_id`` at confidence 0.3 pointing to nonexistent
  ``eye_colour`` / ``hair_colour`` tables, drowning the legitimate
  ``colour.id`` target.
- `build/phases.py` `_looks_like_primary_key` now consults
  `uniqueness_ratio` FIRST when the profiler has populated it,
  falling back to the name-only check (column named `id`) only
  when stats are missing. The earlier name-first form classified
  any column called `id` as a PK regardless of measured uniqueness,
  which dropped legitimate FK↔PK same_name edges where the FK side
  happened to also be named `id`. Concrete failure mode caught by
  benchmark-smoke 42194787 with-history: a clinical schema's
  `observation.id` (uniqueness=0.02, repeats ~50× per subject — the
  FK to `subject.id`) was treated as a PK, the
  `subject.id = observation.id` same_name edge got suppressed as a
  coincidental PK↔PK collision, and the affected case predicted
  the wrong join.
- `build/phases.py` `phase_infer_joins_heuristic` now suppresses
  `same_name` joins where both sides look like primary keys (column
  named `id`, or `uniqueness_ratio >= 0.95`). Coincidental
  cross-table PK-name collisions almost never represent real FK
  relationships — they were the dominant noise source in `_joins.md`.
  Concrete failure mode: an entity-catalog schema's `_joins.md` in
  benchmark-full 42193930 with-history listed ten 1:1 same_name
  edges joining the independent PKs `items.id`, `attr_data.id`,
  `rules.id`, `events.id`, `catalogs.id`, `catalog_translations.id`
  to each other, drowning the legitimate
  `items.uuid = attr_data.uuid` FK signal and steering the agent
  to wrong joins on the affected case (`catalogs.id =
  catalog_translations.id` instead of `catalogs.code =
  catalog_translations.catalogCode`). Real PK↔FK joins (one side PK-like,
  the other not) still surface unchanged, and pattern 1 (`link_to`)
  still captures the explicit `{X}_id` shape.

### Changed

- Skill `references/annotate.md` now explicitly tells the annotation
  agent to honor the `dedupe.primary_winner` evidence entry when
  picking which `identifier/primary` to write in
  `mcs annotate batch`. The classifier already runs a deterministic
  DDL-order tie-break across every column that cleared the
  uniqueness ≥ 0.99 gate and stamps the winner into each demoted
  column's evidence, but the downstream agent was second-guessing
  the choice based on column descriptions or naming aesthetics —
  flipping an entity-catalog `items` table between `id=primary,
  uuid=unique` and `uuid=primary, id=unique` from one build to the next despite
  identical suggestions. The new section spells out the rule with a
  worked YAML example so the agent always writes the dedupe winner
  as primary and demoted columns as unique.

### Fixed

- `build/markdown.py` and `commands/show.py` drop mined sample SQL
  patterns entirely from the per-table markdown — only
  `confidence=user_verified` entries land in `sample_sqls` /
  `sample_sql_patterns`. Earlier iterations stripped progressively
  more from mined patterns (literal `sql`, projection-redacted
  `canonical_sql`, suppressed `join_edges`) so the agent could still
  read workload-frequency stats. Each defensive layer still leaked:
  smoke 42189586's with-history arm regressed three cases by
  structurally copying singleton mined patterns from the same
  table's markdown (a roster case over-joining to
  `entity_skill`+`skill_def`, a motorsport case self-joining
  `results r853`+`results r854`, a finance case lifting
  a `district_id = (SELECT ... LIMIT 1)` subquery that misses the
  gold's `GROUP BY a4` semantics). The placeholder-bearing
  `canonical_sql` shape and the cross-table `where_predicates` are
  themselves template attractors regardless of redaction depth.
  The agent retains workload signal from the `joins` block and
  column annotations — both are per-relationship / per-column facts,
  not query templates. `mcs memory recall` / `mcs memory show` are
  unchanged: they still surface mined entries with projection
  redaction for the user-driven "what queries have run on table X"
  use case.
- `build/markdown.py` and `commands/show.py` no longer emit the
  literal-bearing `sql` field for non-`user_verified` sample SQL
  patterns; only the placeholder-bearing `canonical_sql` survives.
  The mined `sql` carried real filter values (e.g.
  `WHERE c.segment = 'LAM' AND y.date BETWEEN '201201' AND '201212'`)
  that frequently matched the current question's filters by accident,
  letting the agent lift the whole mined SQL — including
  question-specific projections like
  `SELECT customerid, SUM(consumption) AS total_consumption` — as a
  ready-made answer template. Witnessed in smoke 42188104's
  with-history arm regressing 6 cases vs no-history across finance /
  sports / motorsport schemas despite the JOIN/projection AST
  redaction landing correctly: the `<col>` placeholders in
  `canonical_sql` hid join keys, but the parallel `sql` field shipped
  the literal filter values that made the mined SQL look like the
  answer. Dropping `sql` for mined entries preserves the shape signal
  (`canonical_sql`, `where_predicates`, `frequency`, `verified_count`)
  while removing the copy-paste hazard. `user_verified` patterns keep
  both fields, since those literals were confirmed correct by the user.
- `build/markdown.py` and `commands/show.py` now redact mined SQL
  patterns via a new single-pass `redact_for_display` helper instead
  of chaining `redact_join_keys(redact_projection_columns(sql))`.
  The chained form silently failed: the first call emitted `<col>`
  placeholders, sqlglot then refused to re-parse the result because
  `<col>` tokenizes as a `<`/`col`/`>` triple instead of an
  identifier, the second call caught the `SqlglotError` and returned
  its input unchanged, and the JOIN keys survived un-redacted into
  the agent-visible markdown. Witnessed in the smoke 42186387
  with-history snapshot where a related-rules table surfaced
  `ON c.id = l.id` despite the markdown renderer claiming to redact
  it; the agent then copied that wrong join into two entity-catalog
  cases, both of which dropped EX in the with-history arm.
  The unified function parses once, applies both
  AST transforms in-place, and serializes once — no intermediate
  `<col>` round-trip through the parser.
- `apply_profile_result` clamps `uniqueness_ratio` to `[0, 1]`.
  `APPROX_DISTINCT` (HyperLogLog) can overshoot the true row count by
  the estimator's standard error (~1.6% at MaxCompute's default
  precision) when a column has near-zero duplicates, surfacing in the
  per-column annotation evidence as e.g.
  `uniqueness_ratio: 1.0493` — mathematically invalid as a probability
  and a direct violation of the project's "no erroneous information
  in the semantic layer" contract. Witnessed in an entity-catalog
  `items` table where `id` and `uuid` came back as `1.045`
  and `1.049` respectively. The raw `approx_ndv` is preserved on the
  entry so the agent can still see the estimator's output; only the
  derived ratio is bounded.
- Identifier-branch time-type carve-out now also catches STRING
  columns whose name matches `TIME_NAME_RE` (`date` / `time` /
  `created` / `updated` / `ds` / `pt` suffix). The previous form only
  checked the column type for `DATE` / `DATETIME` / `TIMESTAMP`
  substrings, which missed a forum-domain `users.LastAccessDate` and
  similar columns where the importer keeps source DATETIME values as
  STRING text for safe round-tripping. Without the carve-out per-event
  timestamps clear the uniqueness ≥ 0.98 gate, the column lands at
  `identifier/primary`, and the existing dedupe pass then demotes
  the real numeric primary key to `unique` — exactly the
  misclassification observed in the forum-domain `users` table
  build at 0.4.0a30. The dimension branch still picks the column up
  via the same name pattern, so it lands as `dimension/time` as
  intended.
- Annotation-suggestion phase exempts `DATE` / `DATETIME` / `TIMESTAMP`
  columns from the identifier branch. Event timestamps with sub-second
  precision routinely clear the uniqueness ≥ 0.98 gate and previously
  ended up tagged as `identifier/primary` (or `identifier/foreign` when
  a coincidental join candidate boost stacked on top), which both
  polluted the join picker (the agent might propose joining tables on
  event timestamps) and hid the column from the time-dimension role
  its name already implies. The dimension branch still picks up the
  `date`/`time`/`created`/`updated` name suffix via `TIME_NAME_RE`,
  so these columns now land as `dimension/time` instead.
- Annotation-suggestion phase now emits at most one
  `identifier/primary` per table. The classifier in
  `build/semantic_suggestions.py` previously labelled every column at
  uniqueness ≥ 0.99 as `primary`, so tables with a surrogate int PK
  plus a UUID natural key (e.g. an entity-catalog `items` table with
  `id` and `uuid` both 100% unique) surfaced two `primary`
  suggestions and the
  annotation agent had to guess which is THE primary. The new dedupe
  pass keeps the strongest candidate (tie-break: DDL ordinal →
  non-STRING type → name length → confidence → alphabetical — all
  non-benchmark-specific, codifying the universal convention that schema
  designers declare the primary key as column #1 across every
  relational dialect) and demotes the rest to `unique` (already a
  valid `id_type` per `storage.VALID_ID_TYPES`). Demoted columns
  retain their original evidence and gain a `dedupe` entry naming
  the winner so the agent has full context when reviewing.
  Confidence is deliberately the second-to-last tie-break (above
  alphabetical only): the join-candidate boost stacks for any column
  referenced by other tables, which in star schemas hits BOTH the
  surrogate PK and the natural key, so confidence doesn't reliably
  distinguish the true primary among already-qualified candidates.

### Changed

- `mcs show --table T` (plain / YAML output) reorders the per-table
  `<source_key>/<table>.md` frontmatter so the annotation-derived keys
  (`ai_context`, `dimensions`, `metrics`, `identifiers`,
  `partition_columns`, `join_candidates`, `annotation_suggestions`,
  `sample_sqls`, `sample_sql_patterns`) come **before** the bulk
  `columns` list. Mirrors the JSON envelope key-order fix and stops
  Claude Code's ~5 KB persisted-output preview from being filled by 74
  columns × 6 fields of bulk metadata before the agent can read
  `identifiers[].type: primary`. Empty lists (`verified_queries`,
  `sample_sqls`, `sample_sql_patterns`) are omitted entirely rather
  than emitted as `[]` placeholders.
- The Click root pre-processes argv to hoist global flags
  (`-f / --format`, `-q / --quiet`, `--debug`, `--verbose`, `--config`)
  that the user placed after the subcommand to before it. Both
  `mcs show -f json --table T` and `mcs -f json show --table T` now
  produce the JSON envelope; previously the former errored with
  `No such option: -f` and the agent fell back to the much larger
  YAML form. POSIX `--` still terminates the scan, so positional
  arguments containing flag-like tokens are not disturbed.

### Added

- SKILL.md hoists the load-bearing SELECT-projection rules into the
  index itself (a new "SELECT only what the question asks for"
  section between the Decision Matrix and "Build is two halves").
  Empirically the agent loads SKILL.md once via the Skill tool, then
  jumps straight to `mcs show` + `mcs sql execute` without ever
  `Read`-ing the per-feature reference files — so the projection
  discipline section sitting in `references/query.md` was invisible
  on every query. The new in-SKILL block carries the four
  highest-impact rules (project identifier alone for "which / who /
  list" questions, one scalar for "how many / percentage" questions,
  WHERE/JOIN columns are filter signal not output, one statement per
  answer); the full ruleset and edge cases stay in
  `references/query.md` under the same heading the link points to.
- `mcs show --tables T1,T2,T3` — batch view that fetches column hints,
  partition info, enum samples, sample SQL, join candidates, and
  annotation suggestions for several tables in one call. Mutually
  exclusive with `--table T`. JSON mode returns
  `{"profile": ..., "tables": [{...status: "ok" | "error"...}]}`; plain
  mode concatenates per-table markdown with `## sk.table` headers
  separated by `---`. Missing / ambiguous tables produce inline error
  entries — the command still exits 0, so callers must check each
  entry's `status` field. The skill bundle is updated to prefer
  `--tables` whenever the question touches more than one table.
- `mcs annotate {table,column,list,batch}` accept `--source SOURCE_KEY` for
  disambiguation in multi-source profiles. Bare table names auto-resolve
  when unique, error with candidate list when ambiguous. FQN form
  `proj.schema.table` is also accepted.
- `annotate batch` YAML gains an optional top-level `source:` key.

### Changed

- BREAKING (internal): `PackageDB.{set,get}_table_ai_context`,
  `set_column_semantics`, `get_column_semantics`, and `table_exists` now
  take `source_key` as the leading required argument. The single
  external caller (`commands/annotate.py`) has been updated.
- BREAKING (internal): `PackageDB.annotation_coverage(per_table=True)`
  returns `per_table` nested as `{source_key: {table_name: {...}}}`
  instead of the flat `{table_name: {...}}` shape that silently
  collapsed same-named tables under different sources. Consumers
  (`commands/status.py`, `commands/annotate.py:list`, `build/markdown.py`)
  updated.

### Changed

- `mcs show --table T` and the generated `<table>.md` no longer surface
  raw SELECT lists from mined SQL patterns. Only `user_verified` SQL
  lands in the literal `sample_sqls` list; mined patterns (low /
  medium / high confidence) are demoted to `sample_sql_patterns` with
  their SELECT projection redacted to `<col>` placeholders. WHERE /
  JOIN / GROUP / aggregate-function clauses stay intact — those carry
  the reusable access-pattern signal. Why this matters: in smoke
  runs the agent regurgitated a mined `SELECT name, accountid,
  scorecardid FROM items WHERE NOT scorecardid IS NULL`
  pattern verbatim for a question whose gold projection was just
  `SELECT ID` — copying the mined projection turns the right-tables /
  right-predicates path into a wrong-answer one. The new policy
  forces the agent to commit to its own projection while still
  benefiting from the mined access-pattern shape.
- `mcs memory recall` and `mcs memory show` apply the same
  SELECT-projection redaction for non-`user_verified` `sample_sql`
  entries (both JSON and plain output paths). Keeps the agent from
  tripping the copy-paste wire from a different verb when it falls
  back to hybrid search instead of the per-table `mcs show` flow.
- `mcs show --table T` (JSON output and the on-disk `<table>.md`)
  now also redacts JOIN ``ON`` column references in non-`user_verified`
  `sample_sql_patterns` to `<col>` placeholders, and suppresses the
  structured `join_edges` field on the same patterns (it carries the
  same ON-clause text we just redacted out of the SQL). The
  relationship signal (`cards JOIN legalities`, join type, cardinality)
  stays intact while the agent is forced to consult `join_candidates`
  — built from data-profiling evidence (uniqueness ratios +
  value-overlap) — for the actual join columns. Why this matters:
  smoke runs caught the agent copying mined `ON c.id = r.id`
  patterns verbatim even when the authoritative join_candidates said
  the real FK was `c.uuid = r.uuid`. Wrong-key joins in MaxCompute
  don't raise an error — they just produce 0-row results that the
  miner still counts as "executed", so the historical pool is
  systematically poisoned with wrong-FK queries that ran successfully
  but answered nothing. `USING (col1, col2)` clauses get rewritten to
  `USING (<col>)` for the same reason.
- `mcs show --table T` (both JSON output and the on-disk `<table>.md`)
  now trims each `annotation_suggestions` / `join_candidates` row to
  agent-relevant keys only. Drops `id`, `updated_at`, `status`,
  `source_key`, `table_name`, `evidence_json` from
  `annotation_suggestions`; drops `left_source_key`, `left_table`
  (always implied — left side **is** the current table),
  `right_source_key` on same-source pairs, `id`, `status`,
  `updated_at`, `evidence_json`, `right_uniqueness_ratio` (duplicate
  of `evidence[].right_uniqueness_ratio`) from `join_candidates`,
  and omits null-valued tail keys (`coverage_ratio`,
  `conflict_group`, `suggested_subtype`). Roughly halves per-table
  payload size for wide tables (74-column `items` case:
  `annotation_suggestions` ~980 → ~500 lines of YAML). Both shapes
  are still valid input to the existing skill guidance — the kept
  fields (`column_name`, `suggested_role`, `confidence`,
  `evidence`, `left_col`, `right_table`, `right_col`, `cardinality`)
  are exactly what `references/query.md`'s "annotation suggestions"
  and "join candidates" paragraphs name. Same-source join example:
  `{left_col, right_table, right_col, confidence, evidence}`.
- `mcs -f json show --table T` envelope rewritten so the agent sees
  the load-bearing semantic-layer signal in the preview window even
  on wide tables. Three changes: (1) annotation evidence
  (`ai_context`, `dimensions`, `metrics`, `identifiers`,
  `join_candidates`, `annotation_suggestions`, `sample_sql_patterns`)
  is emitted BEFORE the bulk `columns` array, not after; (2) each
  column's `sample_values_json` is parsed into a python list, capped
  at 5 entries, and per-value strings over 80 chars are truncated —
  the raw JSON-encoded string previously double-escaped every quote
  and blew the per-column budget; enum columns get the parsed list
  under `sample_values`, non-enum columns under `format_examples`
  (those are stored shapes, not the full domain — different
  semantics, different key); (3) the duplicate `markdown` body
  field is dropped when the structured PackageDB is present, since
  it carried the same signal in a fraction of the bytes. Empty
  buckets are omitted. Why this matters: Claude Code persists tool
  outputs above ~5 KB and shows the agent only a small preview
  before linking to the saved file (which the agent doesn't read
  back), so a wide-column-table 79 KB envelope previously had the
  agent guessing joins and projections without ever seeing the
  data-profiling evidence. The DB-absent fallback path still emits
  `{markdown: ...}` as the only signal source. The role-extraction
  and column-compaction logic is lifted into
  `build.markdown.build_role_groups` / `compact_column_entry` so
  the on-disk `<table>.md` frontmatter and the JSON envelope share
  a single source of truth.

### Fixed

- `mcs annotate {table,column,batch}` previously re-rendered against the
  first source's subdir regardless of which source actually owned the
  table; now uses the resolved source_key.
- `mcs status --tables` annotated column previously showed whichever
  source's tristate iterated last when same-named tables collided;
  now per-source-correct.
- `_overview.md` `sources[].tables[].annotated` tristate previously
  collapsed same-named tables under different sources; now per-source.
- `mcs status --by-source` previously crashed with "Cannot operate on a
  closed database" because `_emit_by_source` was called after the
  context manager closed `db`; moved inside the try block.

### Docs

- `_skill/references/query.md` adds a **SELECT projection discipline**
  section codifying the minimum-projection rules that NL2SQL agents
  routinely violate: "which / who / what" questions project the
  primary identifier only, single-quantity questions return one
  scalar, WHERE/JOIN columns are filter signal not output, and
  columns make it into the SELECT only when the question names them.
  Benchmark smoke runs caught the agent repeatedly turning correct
  table+WHERE answers into wrong-EX answers by adding "helpful"
  extra columns (uuid, name, intermediate inputs) the caller didn't
  ask for; the policy is a general NL2SQL convention, not
  benchmark-specific.
- `_skill/references/annotate.md` gains a multi-source section
  documenting `--source` flag, FQN form, and batch YAML `source:` key.
- `_skill/references/memory.md` no longer advertises `--project P` as
  the disambiguation flag — uses `--source SOURCE_KEY` per the actual
  CLI surface (only `verify` carries `--source`).
- `_skill/references/query.md` multi-source paragraph escapes the
  malformed code fence that previously rendered it as code.
- `_skill/references/cold-start.md` extends `--source` guidance to all
  `mcs meta` verbs instead of just `list-tables`.

### Added

- Evidence-driven column profiling: `mcs build --profile-level light|deep|none` runs APPROX_DISTINCT + null-ratio + uniqueness profiling per table, producing annotation suggestions and ranked join candidates from workload + uniqueness + name heuristic evidence.
- `--profile-level light` (default) generates `annotation_suggestions` (identifier/dimension/metric/attribute) and `join_candidates` (with confidence, evidence, conflict resolution).
- `--profile-level deep` adds cost-gated value-overlap validation (coverage_ratio) for top join candidates.
- `--profile-level none` skips profiling entirely (ablation arm).
- `--join-candidate-limit N` caps join candidates per table (default 5).
- `--profile-budget-cny X` caps deep profiling cost (default 3.0 CNY).
- `annotation_suggestions` table stores machine-generated column role hints — never written to confirmed `columns.semantic_role`.
- `join_candidates` table stores evidence-ranked join suggestions with conflict detection and status tracking.
- `mcs show --table T` JSON output now includes `join_candidates` and `annotation_suggestions`.
- Per-table markdown frontmatter now includes `join_candidates` and `annotation_suggestions` when present (omitted when empty).
- `mcs build` build summary includes `memory_count` (package_doc + sample_sql) and `vector_count` fields.
- SQL workload evidence extraction via sqlglot: group-by columns, aggregates (`agg.key`), and where-predicate columns.
- Annotation suggestion trust hierarchy: confirmed annotations > suggestions > naming heuristics.
- `eval build-profiles --profile-level none|light|deep` ablation hook for benchmark matrix.
- Skill reference docs (`query.md`, `build.md`, `annotate.md`) updated with suggestion trust model and profiling flags.

### Changed

- Schema version bumped from 5 to 6 (adds profile columns, join_candidates, annotation_suggestions tables).
- BM25Tokenizer now splits camelCase (`isstoryspotlight` → `is story spotlight`) and snake_case (`entity_catalog` → `entity catalog`) before tokenization.
- `mcs memory recall` switched from BM25Searcher to HybridSearcher (FTS5 + sqlite-vec RRF merge).

### Fixed

- Literal-insensitive `sample_sql` pattern grouping with frequency, confidence, and user-verification counts.
- `mcs show --table` now exposes ranked `sample_sql_patterns` while preserving the existing `sample_sqls` list.

### Fixed

- Table-scoped sample SQL reads now apply the per-table limit after source/table filtering, so other tables can no longer crowd the requested entries out of the window.

### Fixed

- Multi-source history mining now rebuilds `sample_sql` memories per source instead of the last source overwriting earlier sources.
- `mcs memory clear` now preserves generated `package_doc` / `sample_sql` entries by default; use `--include-generated` to clear them too.
- Refresh builds now always re-render overview / joins / UDFs / state markdown even when no tables changed.
- Refresh builds re-render table markdown when only mined sample SQL changed for that table.
- Memory reference docs no longer claim unimplemented verified-query dedup/FIFO/date-normalization behavior.

### Added

- `mcs show --table` now exposes mined `sample_sqls` in table projections and JSON output.
- `mcs build --with-vectors` flag for explicit vector embedding rebuilds (replaces hidden synchronous reindex).
- `MCS_AUTO_VECTOR` env var gates synchronous vector indexing on memory writes (opt-in; default off).
- HuggingFace endpoint auto-fallback: probes `huggingface.co`, switches to `hf-mirror.com` if unreachable or slow (>5s).
- `HybridSearcher` with Reciprocal Rank Fusion (FTS5 + sqlite-vec) and `--no-vector` flag for `mcs memory recall`.
- `mcs memory reindex --vectors` for explicit vector reindex.

### Added

- `_state.json` v5 carries `annotation_coverage` rollup (tables_total /
  tables_with_ai_context / tables_with_any_column_role / columns_total /
  columns_with_role) — same projection that `_overview.md` frontmatter
  already exposes, surfaced as structured JSON so eval / CI verifiers
  can read annotation-arm polarity without parsing markdown.
- `mcs annotate {table, column, batch, list}` command group for OSI-aligned semantic annotations
- `ai_context`, `dimensions`, `metrics`, `identifiers`, per-column `semantic_description` annotation fields
- `AnnotateValidationError` (exit_code=2) and `AnnotateNotFoundError` (exit_code=4) error classes
- `annotation_coverage(per_table=True)` rollup with tristate (yes/no/partial)
- `Annotated` column in `mcs status --tables` output
- `MCS_NO_ANNOTATE` env-var for eval dry-run mode
- `references/annotate.md` skill reference with 8-cell classification taxonomy
- `mcs profile create` / `update` interactive flow: echo line after each fzf
  pick (project / schema / endpoint / env / credential / auth type) showing
  the selected value with section emoji.
- "Include all listed tables" quick action in the source editor — snapshot
  current `list_tables` result without committing to wildcard semantics.
- `<other: type ...>` manual-entry escape on the project picker is now
  always offered (previously only when the suggested project was missing
  from the catalog list).
- fzf-multi column visibility picker (`<other:>` sentinel row for manual-entry columns, describe-denied fallback with McsError banner)
- `mcs profile export <name> --export-name <new-name>` rewrites the
  profile-name field embedded in the bundle manifest, so the receiver's
  `mcs profile import` registers the bundle under `<new-name>` rather
  than the source-side `<name>`. Without the flag the source-side name
  is preserved verbatim. Skill-side documentation: the matching one-line
  entry in `_skill/references/onboarding.md`'s "Profile management"
  verb table.

### Changed

- SKILL.md / `references/build.md` / `references/annotate.md` now
  document build as a single two-step workflow: `mcs build`
  (deterministic data dump) → per-table `mcs annotate batch` (LLM-
  inferred semantic layer). Dropped the standalone "Add semantic
  context" decision-matrix row and the post-build zero-coverage
  detection trigger; an unannotated profile is now explicitly
  documented as incomplete.
- `_state.json.version` bumped from 4 to 5 to surface the
  `annotation_coverage` rollup.
- Column visibility picker now uses fzf-multi with **mark-to-hide**
  semantics when fzf is available (Tab to mark cols you want hidden;
  Enter without marking keeps all visible). The questionary fallback
  retains the "all pre-checked, uncheck to hide" model.
- Esc at the top-level editor menu no longer discards changes — it is a
  silent no-op. Discarding requires the explicit `❌ Cancel` row, which
  now confirms first. Ctrl+C still exits the whole flow at any depth.
- All 4 rendered file-kinds (`<table>.md`, `_overview.md`, `_joins.md`, `_udfs.md`) are now frontmatter-only (markdown body dropped)
- `_joins.md` top-level key renamed from body-only pipe-table to `relationships:` in YAML frontmatter
- `_state.json.version` bumped from 3 to 4
- SKILL.md Decision-Matrix: added "Add semantic context to profile" row
- SKILL.md frontmatter `description:` rewritten from a workflow-summary
  capsule ("MaxCompute (ODPS) SQL skill — query data, build semantic
  profiles, manage UDFs, record feedback") into the canonical
  agentskills.io "Use when …" triggering-conditions form, so platforms
  that scan the description for skill-selection see only when-to-load
  cues rather than a what-the-skill-does paragraph. The bilingual
  trigger-phrase list is preserved and lightly expanded with CLI-verb
  vocabulary ("list tables", "describe table", "explain plan", "cost
  estimate", "view schema", "describe table structure") and the brand spelling "Alibaba
  Cloud data warehouse". No skill-behavior change; the rewrite is a
  discovery-time metadata fix per the `superpowers:writing-skills` CSO
  guidance ("description = when to use, not what the skill does").
- Rendered package files (`<table>.md`, `_overview.md`, `_joins.md`,
  `_udfs.md`) no longer carry a redundant `profile_name:` field in
  their YAML frontmatter. The file's parent directory `<package_path>`
  is the profile's data dir and already identifies the profile, so the
  field was redundant and made cross-machine bundle moves (via
  `mcs profile export ... --export-name`) racy by carrying two
  potentially-different names for the same profile. Tooling that read
  `profile_name:` out of the frontmatter should derive the profile
  identity from the data-dir's basename or from the resolver state
  (`MCS_PROFILE` / the cwd-link binding) instead.
- `_skill/references/onboarding.md`'s "Profile management" verb table
  now also documents the two receiver-side override flags of
  `mcs profile import` that have been part of the CLI since commit
  `4eeff03e` (2026-05-14, the Phase-C landing of the export/import
  pair) but were never surfaced in the skill bundle's verb listing.
  `--name <local-name>` registers the imported profile under a name
  other than the one in the archive's `manifest.profile.name` field
  and is the CLI's documented escape hatch for the local-name-
  collision case (the failing-import error envelope itself returns
  the literal hint `"profile X already exists locally; pass --name to
  import under a different name"`). `--package-path <dir>` extracts
  the per-profile SQLite + markdown package tree under `<dir>/`
  instead of the default `<data-root>/<local-name>/` slot — the
  per-import analogue of the process-wide `MCS_DATA_DIR` env-var that
  the same reference's "Profile Data Location" section near the
  bottom of the file already covers. Together with the sender-side
  `--export-name <new-name>` flag listed under **Added** above (which
  is the post-Phase-C follow-up commit `4836758d` from 2026-05-20
  that made the bundle-rename symmetric on both ends), the bundle
  round-trip's rename-and-relocate story is now end-to-end documented
  across `mcs profile export` and `mcs profile import` on the skill
  side. This is a doc-sync entry — no underlying CLI change in this
  bullet, just the skill bundle's verb table catching up with the
  CLI surface as it has stood for the past week.
- The export row's wrap-comment in the same verb table gains a single
  closing sentence that forward-links to the new import-row entry, so
  a reader scanning the table from either direction sees the two
  flags as a paired write-doublet on the archive manifest's
  `profile.name` field rather than as two unrelated knobs.

### Fixed

- `mcs annotate batch` now respects `MCS_NO_ANNOTATE=1` for per-column
  writes. Before this fix the dry-run hook gated the ai_context write
  and the markdown re-render but the per-column `set_column_semantics`
  call still landed, leaving SQLite half-written. Eval ablation arms
  using the env-var to suppress annotations now leave `package.db`
  genuinely untouched.
- Ctrl+C inside a section-editor's `click.prompt` now exits cleanly
  instead of silently returning the unchanged draft.
- Questionary fallback uses `.unsafe_ask()` so Ctrl+C propagates as
  `KeyboardInterrupt` (matching iterfzf behavior).
- Error-branch JSON envelopes now carry a uniform `status: "error"`
  string paired with the numeric `exit_code`, across every `mcs`
  subcommand. Previously the literal status spelling on the failure
  path drifted between commands, which broke agents that pattern-
  matched on the string. The success-branch `status: "ok"` was already
  uniform and is unchanged.
- Live whoami-probe code path in `mcs profile create` and
  `mcs profile update --auth-changed` accepts a `TableNotFoundError`
  from the schema-existence sub-probe as a non-fatal "schema does not
  exist yet" signal rather than promoting it to an auth failure (it
  was triggering false-positive auth-failed reports on freshly-created
  3-level projects whose default schema hadn't been provisioned).
- CLAUDE.md "Skill runtime surface" cheat-sheet's memory-store bullet
  has been resynchronized with the live CLI: the dead `mcs feedback
  record` verb-name (which the `feedback`→`memory` group rename had
  retired) is removed and the surviving `mcs memory …` bullet now
  enumerates the full nine-verb surface (`verify / fail / note /
  recall / list / show / remove / clear / reindex`) matching
  `_skill/references/memory.md`. Doc-only; no CLI change.
- Skill-bundle and top-level-doc audit sweep: the two surviving
  `mcs feedback record --tables …` invocations in `_skill/SKILL.md`'s
  "Multi-source profiles" section and the one in the top-level
  `README.md`'s "Usage" example list (the agent-loadable instruction
  surface) are switched to `mcs memory verify --tables …` to match
  the `feedback`→`memory` group rename. The CLAUDE.md "Skill runtime
  surface" cheat-sheet gains the two top-level verbs that had been
  missing — `mcs doctor` (the unified setup/auth/package/skill-install
  diagnostic with `--offline` and `-f json` modes) and
  `mcs annotate {table, column, batch, list}` (the OSI-aligned
  semantic-annotation surface from 0.4.0a10). The `_skill/SKILL.md`
  decision-matrix's verify cell now shows the `--tables T1,T2` flag
  inline so the agent doesn't have to load `references/memory.md` to
  see that table attribution is required. The `_skill/references/build.md`
  status block gains a `mcs status --tables` line (was already
  documented in CLAUDE.md but missing from the build reference's
  inline-bash block). The `_skill/references/onboarding.md` "Profile
  Data Location" block is corrected: the default `MCS_DATA_DIR` is
  the XDG-data-home path (`~/.local/share/maxcompute-semantic/data`
  on Linux, `~/Library/Application Support/maxcompute-semantic/data`
  on macOS), not `~/.config/maxcompute-semantic/data` (which is the
  config-home path, used only for `profiles.yaml` + `link.json`).
  The `_skill/references/cold-start.md` "full set of `mcs meta`
  subcommands" listing gains the two missing tier-discovery verbs
  `list-projects` and `list-schemas` (the CLI registers eight verbs;
  the listing was showing six), and the trailing closure clause
  "These are the *only* subcommands" is reworded to the
  factually-correct "Eight verbs across the four catalog tiers
  (projects → schemas → tables → columns / partitions / freshness)".
  The top-level `README.md` quick-reference block gains a
  `mcs link bind <profile>` row and a `mcs doctor` row so the two
  cwd-binding and diagnostic verbs the agent uses on every onboarding
  flow are surfaced alongside the data verbs. Doc-only across all
  the above; no CLI change.
- `mcs annotate batch`'s per-column exception handler now propagates
  the `McsError.remediation` field into both the
  `columns[].error.remediation` JSON field and the matching per-column
  entry of the top-level `warnings[]` array, for both the typed
  `AnnotateNotFoundError` path and the generic `Exception` path (the
  latter via `getattr(e, "remediation", None)`). Previously the
  batch report only surfaced `code` + `message`, so agents parsing
  the report had to fall back to printing the raw message without
  the actionable next-step string that `mcs annotate column` already
  shows on the single-write path. The JSON shape gains one field per
  failed column on each of the two arrays; no removal or rename.
- Test-side coverage backfill, three additions, no behavior change:
  (1) `tests/unit/mc_client/test_client.py` gains
  `test_run_sql_async_primes_tier_for_3level_project` and
  `test_explain_primes_tier_for_3level_project` — the existing
  `test_can_access_table_primes_tier` exercised the `if self._tier is
  None: self._tier = get_tier(...)` init-phase code path on one method,
  but the same three-line block exists on both `run_sql_async` (at
  `mc_client/client.py:881`) and `explain` (at `mc_client/client.py:1087`)
  and had no equivalent regression test. The new tests mirror the
  existing 3-level-namespace assertions (`odps.namespace.schema=true`
  + `odps.default.schema=<name>`) so a tier-init regression on either
  entry point can't slip the gate.
  (2) `tests/fixtures/pyodps_errors/no_permission_meta_describe_structured.json`
  and `no_permission_meta_list_structured.json` exercise the
  classifier's structured-wire-form check at
  `mc_client/errors.py:338` (`"odps:describe" in low or "odps:list" in
  low`) — the four pre-existing `no_permission_meta_*` fixtures hit
  only the bottom-of-function message-keyword fallback at L390
  (`"describe" in low or "list table" in low`), leaving the
  structured path uncovered. Both new fixtures are wired into the
  `test_fixture_maps_to_expected_class` parametrize list with a
  preserve-disambiguation comment.
  (3) `tests/fixtures/pyodps_errors/label_security_your_label_only.json`
  and `acl_deny_as_default_only.json` exercise each disjunct of the
  classifier's two OR-discriminators (`"checklabelsecurity" in low or
  "your label" in low` at L325; `"acl check failed" in low or "deny as
  default" in low` at L370) in isolation. Pre-existing fixtures
  satisfied both disjuncts of each OR simultaneously, so a typo in
  one half could not trip a test. The new fixtures contain only one
  side of each OR.
- `mc_client/catalog.py` bare-except sites narrowed and instrumented:
  the `odps.catalog_rest` accessor's `except Exception` is narrowed
  to `except AttributeError` (the only exception that arm legitimately
  swallows — older pyodps builds that lack the attribute), and the
  `_resolve_tenant_id` helper's `except Exception` is kept (it is the
  documented "Catalog API unavailable for this project, fall back to
  client-side iteration" signal — its caller `_search_tables` in
  `mc_client/client.py` checks `if catalog_results is not None` and
  falls through to `_search_tables_client_side` where the real
  pyodps exception resurfaces with proper `map_pyodps_exception`
  classification) but now carries an explanatory docstring stating
  the fallback contract, and both swallow sites emit a `logger.debug`
  with `exc_info=True` so operators can grep for the silent-fallback
  cases in trace logs without changing the caller-visible behavior.
  No CLI change.
- `mcs memory clear` now requires explicit confirmation before
  deleting every memory row in the package database. Add `--yes` /
  `-y` for non-interactive callers; interactive callers get a
  `click.confirm("This will delete all N memory entries...")`
  prompt that aborts on `n`. Pre-fix `mcs memory clear` deleted
  silently on the first invocation — a fat-finger or shell-history
  recall (the verb name is two letters off `mcs memory recall`)
  irreversibly wiped every verified-SQL / fail-note / domain-note
  the user had accumulated. The other destructive verb
  (`mcs memory remove ID`) targets a single row and so doesn't
  need the same guard.
- `PackageDB.upsert_columns` now uses `ON CONFLICT(table_id, name)
  DO UPDATE` instead of `DELETE FROM columns WHERE table_id=? ;
  INSERT …`. The previous shape blew away annotation columns
  (`semantic_role`, `dim_type`, `agg`, `id_type`,
  `references_target`, `semantic_description`) and profile
  statistics on every rebuild — even when the schema-side fields
  hadn't changed — so a `mcs build --refresh` silently dropped
  every annotation the user had attached via `mcs annotate`.
  The new path only updates the schema/sample fields
  (`type` / `comment` / `is_partition` / `format_examples` /
  `sample_values`) and explicitly preserves the annotation +
  profile-stats columns; rows whose names disappear from the
  incoming list are still removed in the same transaction.
- `_join_edges` (in `memory/sql_pattern.py`) now resolves table
  aliases inside JOIN ON expressions to their real table names
  before normalizing. Pre-fix a query like
  `SELECT … FROM users u JOIN orders o ON u.id = o.user_id`
  produced an edge_key of `u.id = o.user_id` — keyed on the local
  alias instead of the table — so two queries that joined the same
  two tables with different aliases (`u` vs `users`, `c` vs
  `cards`) would frequency-merge as separate "shapes" and the
  downstream `join_candidates` consumer would treat them as
  unrelated edges. The resolver builds the `{alias: real_name}`
  map from `tree.find_all(exp.Table)`, then rewrites
  `column.set("table", …)` on a copied ON expression. Knock-on
  fix in `build/join_candidates.py`: the edge_key parser now
  defensively `.strip()`s whitespace around `=` and the table/col
  splits on `.`, so any whitespace introduced by upstream
  normalization changes can't slip through.
- `phase_mine_history` errors and warnings now flow into the
  build pipeline's `BuildSummary` via `_absorb_phase_result(…,
  phase="history")`. Pre-fix the miner phase ran inside both the
  full-build and incremental-rebuild orchestrators but its
  `PhaseResult` was discarded — a permission failure or pyodps
  network error on `INFORMATION_SCHEMA.TASKS_HISTORY` left the
  build summary clean, the user saw a green build, and the
  mined-SQL count silently fell to zero. Now the miner's
  `errors` / `warnings` surface in the same per-source / per-
  phase shape as every other phase.
- `catalog_search_tables` (Catalog API server-side full-text
  search) now walks `nextPageToken` pagination instead of
  silently truncating at the first page. Pre-fix any project
  with > `pageSize` (50) matching tables returned a partial list
  and the agent had no signal that the result was incomplete —
  the caller saw an empty `nextPageToken` field that was actually
  populated and ignored it. Capped at 100 pages as a runaway
  guard.
- `mc_client._search_tables_client_side` and
  `mc_client.search_columns` now wrap `odps.list_tables(**kw)`
  in `try/except` and re-raise via `map_pyodps_exception(exc,
  source_key=…)`. Pre-fix raw `pyodps.errors.NoSuchObject` /
  `ODPSError` exceptions from `list_tables` leaked past the
  client-side iterators with no source-key context and no
  classification — the top-level CLI dispatcher then wrapped
  them as a generic `code:"Unknown"` envelope with the
  misleading "see logview URL" remediation hint. Now they
  classify as `PermissionDeniedMeta` /
  `ProjectNotFoundError` / etc. with `[source=…]` prefixed.
- `_resolve_profile_for_project` now respects an explicit
  `--project P` flag that differs from the resolved profile's
  `compute_project`. Pre-fix the resolver returned the saved
  profile verbatim and the explicit project flag was silently
  ignored when also routing through profile auto-resolution —
  so `mcs … --profile A --project B` ran against
  `A.compute_project`, not `B`. The resolver now clones the
  resolved Profile via `dataclasses.replace(resolved,
  compute_project=project)` when the two disagree.
- `mc_client/hints.build_hints` docstring no longer claims to
  raise `ValueError` on a `tier=3` + `schema=None` call. The
  function is a no-op in that case (returns `{}`) — the
  docstring drift came from a since-reverted experimental change.
- Eval harness: `eval/runner.py` now reads
  `config.extra.get("variant", "minidev")` so `bird_schema_for`
  routes to the right Bird schema namespace
  (`bird_<id>` for minidev, `bird_dev_<id>` for dev). Pre-fix
  the dev variant always missed and resolved against the
  minidev schema. `eval/__main__.py` plumbs `--variant` through
  `BenchmarkConfig.extra` so the runner sees it.
- Eval isolation: `build_minimal_env` denylist gains
  `MCS_CONFIG_DIR` and `XDG_CONFIG_HOME`. Pre-fix a host
  shell that exported either of these (developer convenience
  on a workstation) leaked into the per-case `claude --print`
  subprocess and the agent's `mcs` calls resolved
  `profiles.yaml` from the host's config dir instead of the
  isolated tmphome — a profile-data leak that bypassed the
  three-layer isolation contract.

## [0.4.0a9] — 2026-05-19

### Added

- `map_pyodps_exception(source_key=...)` prepends `[source=...]` to TableNotFoundError and all PermissionDenied* error messages for cross-source debugging.
- `PackageDB.lookup_source_key(project, schema, table)` resolves a table triple to its canonical source key.
- `mcs sql execute/cost/explain` error envelopes now carry resolved source tags from the failing SQL (using sqlglot for reliable parsing).
- `sqlglot>=25.0` added as a hard dependency.

### Changed

- `build_hints()` accepts optional `source_key` parameter for future attribution.

## [0.4.0a8] — 2026-05-19

### Added

- `mcs meta list-tables --source {proj}__{schema}`: parse source key into project/schema.
- `mcs meta describe-table TABLE` enriches output with a `source` field for multi-source profiles.

## [0.4.0a7] — 2026-05-19

### Added

- `mcs status --by-source`: group table/column counts by data source.
- `cross_source` boolean field in `_joins.md` relationship entries.

### Changed

- `_CROSS_SOURCE_PENALTY` renamed to `CROSS_SOURCE_CONFIDENCE_PENALTY` and exported from `build/__init__.py`.

## [0.4.0a6] — 2026-05-19

### Added

- `mcs profile remove` now includes `data_dir_preserved` in output; `--purge` flag deletes data dir.

### Changed

- `--project` help text canonicalized to "MaxCompute project name" across all 7 command groups (memory, meta, profile, show, sql, status, udf).
- `mcs link status --verbose` / `-v` shows bound profile's source count and per-source list.

## [0.4.0a4] — 2026-05-19

### Added — multi-source build pipeline

- ``mcs build`` now iterates **all** sources in a multi-source
  profile (was: silently only ``sources[0]``). A profile carrying
  multiple ``(project, schema)`` pairs gets every source built end-
  to-end into PackageDB, with per-table markdown landing under
  per-source subdirs and ``_state.json`` partitioned per source.
  Cross-(project, schema) is now a first-class workflow.
- **Cross-source join inference**. ``phase_infer_joins_heuristic``
  walks all sources in the profile; same FK-named columns across
  different sources surface in ``_joins.md`` with the source_key
  qualifying both endpoints (``acme__warehouse.users.id ->
  acme__staging.events.user_id``). Cross-source pairs get their
  pattern confidence multiplied by 0.8 so within-source joins
  surface above their cross-source equivalents in the ranked
  output.
- ``mcs feedback record --source SK`` and
  ``mcs memory verify --source SK`` flags for explicit
  disambiguation when a table name appears in multiple sources.
  Both commands also accept the 3-segment FQN form
  ``proj.schema.table`` in their ``--tables`` argument; bare names
  auto-resolve when unique across sources, hard-error with a
  candidate-listing remediation when ambiguous.
- ``BuildPipeline`` now hard-errors when a profile has no data
  sources, with a remediation pointing at
  ``mcs profile update <name>`` to add at least one source.
  Previously the pipeline silently synthesized a
  ``(compute_project, "default", "*")`` stand-in source — a
  footgun for users who hit Done in the editor before adding any
  source.

### Changed (breaking)

- **PackageDB on-disk format bumped to v3**. ``tables`` and
  ``joins`` are now source-keyed (composite ``UNIQUE(source_key,
  name)`` on tables; ``left_source_key`` / ``right_source_key``
  columns on joins so cross-source pairs are addressable). Old v2
  packages are rejected at open time with a new
  ``RebuildRequiredError`` and a remediation pointing at
  ``mcs build`` — no in-place migration, alpha line policy.
- **Per-table markdown layout** moves from
  ``<package_path>/<table>.md`` to
  ``<package_path>/<source_key>/<table>.md``. Same-named tables
  under different sources land in distinct subdirs and don't
  collide on disk.
- **``_state.json`` schema bumped to v3**. Per-source counts
  (``tables_count`` / ``project`` / ``schema`` / ``tier``) move
  under a top-level ``sources`` map keyed by ``source_key``.
  Profile-level fields (``last_built_at``, ``udfs_count``,
  ``joins_count``, ``history_skipped``,
  ``tables_with_sample_sqls``, ``info_schema_source``,
  ``errors``) stay at the root.
- **``_overview.md``** rendered with per-source sections
  (``## Source: <project>.<schema> (N-level)``) under the
  profile-level frontmatter; multi-source profiles surface every
  source's table list separately, single-source profiles still
  render with one section.
- **``mcs memory verify``** now stores ``payload.table_refs`` as
  ``{"source_key": ..., "table": ...}`` dicts (was: bare strings)
  so ``mcs memory recall`` can return source-qualified
  references. ``retrieval_text`` table tokens are prefixed with
  ``source_key:`` so BM25 retrieval is source-aware.
- **``mcs status --tables``** rows now include the ``source_key``
  field, enabling per-source breakdown in consumers.

### Fixed

- **``mcs show --table T`` regression.** The multi-source layout change
  moved per-table markdown to ``<source_key>/<table>.md``, but
  ``mcs show --table`` still constructed the old flat path. Every
  ``mcs show --table T`` call returned "table not found" regardless
  of whether the table existed. Single-source profiles now resolve
  the source_key directly; multi-source profiles look up the table
  in PackageDB to find the owning source.
- **ODPS-0130013 error classification overmatched.** The MaxCompute
  error code ODPS-0130013 is multi-purpose — it can carry "project
  not found", "table not found", or "no permission" messages. The
  permission-matrix classifier routed all ODPS-0130013 occurrences
  to ``PermissionDeniedTableError``, swallowing ``ProjectNotFoundError``
  for messages containing "Project not found". The code check now
  inspects the message text for known patterns before dispatching.
- **Build-tree tests are now actually collected.**
  ``tests/unit/build/`` had been silently dropped from pytest
  collection for the entire history of the package because
  pytest's default ``norecursedirs`` includes ``build`` and
  duplicate test-file basenames across subdirs
  (``test_acl_filter.py`` / ``test_errors.py`` exist in both
  ``_lib/`` and ``build/``) broke pytest's default ``prepend``
  import mode. The package's ``pyproject.toml`` now overrides
  ``norecursedirs`` to drop the ``build`` entry and pins
  ``--import-mode=importlib`` so duplicate-named files coexist.
  Test count went from 902 to 1046 with no new tests beyond the
  multi-source coverage; the rest were already there but never
  running.
- **PackageDB conn cleanup on GC**. Added ``__del__`` to close
  the underlying ``sqlite3`` connection when a ``PackageDB``
  instance is garbage-collected. Previously unclosed conns
  emitted ``ResourceWarning: unclosed database`` during
  interpreter teardown, and that warning got captured into
  ``CliRunner.result.output`` for tests that exercised the build
  command, breaking JSON-parsing assertions when a prior test
  had left a DB open.

### Removed (breaking) — CLI cleanup wave 2

- ``mcs profile use NAME`` and the on-disk machine-global
  default-profile mechanism it backed are gone. The
  ``profiles.yaml`` top-level ``default_profile:`` key is no
  longer written or read by any code path; the
  ``auth.profile_store.set_default`` and ``get_default`` helper
  pair is deleted; the corresponding slot in the
  ``auth.resolver.resolve_profile`` chain — the one between the
  ``link.json`` cwd binding and the env-vars-anonymous fallback
  — is excised. The ``commands.profile._default_name`` wrapper
  helper and its two call sites are gone with it: the
  ``(default)`` trailing column in the ``mcs profile list``
  table, and the green ``(default)`` tag that ``mcs profile
  show``'s title banner carried when the rendered profile name
  matched the on-disk default. The ``profile_store.remove``
  function's two-line clear-the-default-pointer-when-the-
  pointed-at-profile-is-removed branch is also gone.

  Legacy ``profiles.yaml`` files on disk that still carry the
  pre-cleanup ``default_profile:`` top-level key load cleanly
  on the new code path — the ruamel safe loader silently
  ignores unknown top-level keys — and the field disappears
  from disk on the next write of any kind (the writer side
  emits only the ``version`` and ``profiles`` keys, so any
  ``upsert`` / ``remove`` rewrites the file without it).

  Rationale: the on-disk ``default_profile`` slot and the
  ``MCS_PROFILE`` env-var slot were two answers to the same
  "active profile outside the command line" question, but the
  env-var form has the right lifetime: shell-scoped for local
  work and job-scoped for CI. Directory-scoped context is
  already covered by ``mcs link bind <NAME>``. Dropping the
  config-file default removes a class of "the on-disk default
  points at a profile that's been renamed since" wedge states
  without removing either shell-scoped or cwd-scoped selection.

  The post-cleanup resolution chain that every ``mcs`` verb
  consults — the canonical references are the
  ``commands.profile._resolve_profile_for_project`` docstring
  and the ``auth.resolver`` module docstring — is the four-
  slot priority "explicit ``--profile NAME`` flag →
  ``MCS_PROFILE`` env-var-named profile in ``profiles.yaml`` →
  cwd-link binding from ``mcs link bind`` (stored in
  ``link.json`` in the config dir) → ``ALIBABA_CLOUD_*``
  standard ODPS env-vars-anonymous in-memory Profile".
  ``--project P`` names the target MaxCompute project; it no
  longer selects a saved profile whose alias is also ``P``.
  The CLAUDE.md verb-
  table preamble and the ``_skill/references/onboarding.md``
  "Profile management" section have been rewritten to spell
  out the new chain; the ``docs/yuque-public-usage.md``
  command-catalogue table drops the ``mcs profile use``
  row, the chain-order paragraph drops the middle "default
  profile" clause, and the troubleshooting FAQ entry on
  "switch the active profile across shells" lists the
  three-way ``mcs link bind`` / ``export MCS_PROFILE`` /
  ``--profile <name>`` enumeration instead of the
  two-way-and-mcs-profile-use form. The test-side fallout
  drops the three ``test_use_*`` cases from
  ``tests/unit/commands/test_profile_cmd.py`` (the two
  top-level functions ``test_use_sets_default`` and
  ``test_use_missing_exits_3`` plus the
  ``TestProfileQuiet.test_use_quiet_outputs_profile_name``
  method), drops the ``test_set_and_get_default`` /
  ``test_set_default_missing_profile_raises`` /
  ``test_remove_clears_default_when_default_removed``
  cases in ``tests/unit/auth/test_profile_store.py`` (the
  unit-level coverage for the deleted helper pair), and
  reworks the resolver tests in
  ``tests/unit/auth/test_resolver.py`` plus the integration
  tests in ``tests/integration/test_resolve_chain.py`` to
  the post-cut three-slot priority order: the
  ``test_link_wins_over_default`` /
  ``test_default_used_when_no_env_no_link`` cases (whose
  whole point was the deleted slot) are gone, the
  ``test_stale_link_fallback_to_default`` case is reworked
  into ``test_stale_link_warns_and_falls_through`` (the
  warning-side assertion stays; the fall-through-to-default
  half becomes the chain-exhausted ``NoProfilesConfiguredError``
  assertion), the
  ``test_profiles_exist_no_default_no_env_no_link_raises``
  case is renamed
  ``test_profiles_exist_no_resolution_hint_raises`` and
  matches the new "no active-profile chain hit" wording in
  the resolver's error remediation, and the integration-side
  ``test_full_chain_link_beats_default`` /
  ``test_full_chain_default_only`` cases are deleted. The
  test-bare-invocation-uses-default-profile test in
  ``test_profile_cmd.py``'s ``TestProfileWhoamiCmd`` class
  is renamed ``test_bare_invocation_uses_cwd_link`` and its
  setup (which used the now-deleted ``set_default`` Python-
  API helper) becomes a direct
  ``auth.link_store.set_link(os.getcwd(), "meta-dev")`` call
  matching the new chain shape. The wizard-end-to-end test
  in ``tests/integration/test_wizard_e2e.py`` had its
  step-4 ``["profile", "use", NAME]`` and step-N
  ``["profile", "use", NAME]`` invocations both swapped to
  the ``["link", "bind", NAME]`` form via the same sed pass.

### Changed (breaking) — meta-group promotion

- The eight catalog-metadata-discovery verbs that the v0.4
  CLI surface scattered across two parent groups (the bottom
  six — ``list-tables`` / ``describe-table`` /
  ``search-tables`` / ``search-columns`` / ``list-partitions``
  / ``freshness`` — sat as a click sub-group ``meta`` of the
  ``sql`` execution group, addressed as ``mcs sql meta
  <verb>``; the top two — ``list-projects`` and
  ``list-schemas`` — sat as flat verbs in the ``profile``
  group because the source-picker wizard called the same
  underlying ``MaxComputeClient`` enumeration methods) are
  unified under a new top-level ``meta`` group at
  ``commands/meta.py``. Catalog metadata isn't a SQL concept
  (an agent enumerating tables before deciding what SQL to
  write hasn't issued any SQL yet) and isn't a profile-
  lifecycle concept either (the project / schema list is
  about what the AK can see in the catalog, not about the
  profile's own configuration); pulling the eight verbs
  together under a name that matches what they actually do
  is the simplification.

  Wire: ``commands/meta.py`` carries the new
  ``@click.group(name="meta")`` declaration and the eight
  ``@meta_group.command(<verb>)`` decorators. The function
  bodies are byte-equivalent moves from the two old homes;
  only the parent click-group binding changes. The
  ``_validate_schema_for_tier`` policy helper that gates
  ``--schema`` against the 2-tier-vs-3-tier project
  distinction stays in ``commands/sql.py`` (the
  ``execute`` / ``cost`` / ``explain`` verbs there still use
  it) and is imported by ``commands/meta.py`` so both groups
  share the same gate. The top-level CLI dispatch in
  ``cli.py`` gains a ``cli.add_command(meta_group)``
  registration alongside ``cli.add_command(sql_group)``. The
  ``mcs sql --help`` output no longer lists a ``meta`` sub-
  command; the ``mcs --help`` top-level output gains a
  ``meta`` entry between ``link`` and ``sql``. The
  ``commands/sql.py`` file shrinks to just its three
  execution verbs plus the schema-tier policy helper, and
  its file docstring is rewritten to describe the new
  narrower scope and to forward the catalog-metadata reader
  at ``commands/meta.py``. The ``commands/profile.py``
  module's former-home of the ``list_projects_cmd`` and
  ``list_schemas_cmd`` decorator blocks gets a placeholder
  comment in the same spot pointing at the new home.

  Source-side string fallout: the user-facing remediation
  text in ``commands/show.py`` (the "fall back to live
  catalog walking when there's no semantic package found"
  hint), the table-not-found / schema-not-found /
  permission-denied error texts in ``mc_client/errors.py``
  (their ``remediation`` argument refers the user at the
  appropriate ``mcs meta <verb>`` for re-discovery), the
  feedback-write-side error texts in
  ``commands/feedback.py``, and the internal docstring
  comments in ``auth/profile_store.py`` / ``auth/schema.py``
  / ``_lib/schema_hash.py`` / ``commands/_source_picker.py``
  all dropped the ``sql`` segment from the verb name. The
  ``commands/_source_picker.py`` docstring's pointer at
  the home-module of the list-projects / list-schemas verbs
  changed from ``commands/profile.py`` to
  ``commands/meta.py``.

  Doc fallout: the agent-facing skill bundle's
  decision-matrix row in ``_skill/SKILL.md`` (the
  "no-semantic-package fallback" row that names the live-
  catalog verbs) is updated. The catalog-discovery reference
  doc at ``_skill/references/cold-start.md`` (the whole
  "agent walks the catalog when the package isn't built"
  reference, where every verb-line spells out ``mcs sql
  meta <verb>``) has the ``sql`` segment dropped from every
  appearance — the file is the bulk of the doc-side change
  in this commit. The smaller in-passing mentions in
  ``_skill/references/query.md`` (the error-troubleshooting
  pointer at ``describe-table`` for "is the table name
  right?"), ``_skill/references/sql.md`` (the "for CREATE-
  TABLE templates, look at ``describe-table`` of an
  existing one"), ``_skill/references/profile-editor.md``
  (the interactive editor's mention of the picker's
  enumeration verbs), and the
  ``_skill/references/onboarding.md`` "agent should walk
  ``mcs meta list-projects`` → ``list-schemas`` → ``list-
  tables`` → ``describe-table``" four-step instruction all
  use the new spelling. The user-facing
  ``docs/yuque-public-usage.md`` command catalogue's verb
  rows have the ``sql`` segment dropped from the ``mcs sql
  meta <verb>`` entries and the two
  ``mcs profile list-projects`` / ``list-schemas`` rows
  rewritten as ``mcs meta list-projects`` /
  ``mcs meta list-schemas``. The top-level project
  ``CLAUDE.md`` 's "Skill runtime surface" verb table
  (which used to list ``mcs sql meta list-tables`` and
  ``mcs sql meta describe-table``) and the auto-resolution-
  chain description paragraph that follows it are both
  updated.

  Test fallout: the click-runner argv lists in
  ``tests/unit/commands/test_sql_cmd.py`` have the leading
  ``"sql"`` element dropped from every ``["sql", "meta",
  "<verb>", ...]``-shaped invocation, becoming ``["meta",
  "<verb>", ...]``. The class-docstring strings in the
  same file ("Tests for ``mcs sql meta <verb>``") have the
  ``sql`` segment dropped. The same change applies to the
  ``TestListProjectsCmd`` and ``TestListSchemasCmd``
  classes in ``tests/unit/commands/test_profile_cmd.py``,
  which switch their argv lists from ``["profile",
  "list-projects"]`` / ``["profile", "list-schemas"]`` to
  the ``["meta", "list-projects"]`` / ``["meta",
  "list-schemas"]`` form, and have their docstring prose
  updated to match. The cli-root help test
  ``tests/unit/commands/test_cli_root.py::test_help_lists_subcommand_groups``
  gains an ``assert "meta" in result.output`` line, and
  the existing assertions for ``profile`` / ``link`` and
  the negative-assert for the absent ``auth`` group from
  the prior commit are wrapped in a longer docstring that
  enumerates the post-cleanup verb-group layout. The
  schema-not-found error-text mention in
  ``tests/unit/auth/test_schema.py`` 's docstring has the
  verb-name updated as part of the bulk substring rename.
  The downstream tests that asserted on the
  user-facing remediation strings in ``test_show_cmd.py``
  and ``test_feedback_cmd.py`` (which were written against
  the old "mcs sql meta list-tables" wording for the
  fall-back-to-live-catalog suggestion in ``mcs show`` and
  for the wrong-table-name remediation in
  ``mcs feedback record`` respectively) had their literal
  expected-substring text shifted by the same bulk rename;
  the assertions and the source-side string they assert on
  move in lockstep.

  The previous ``[0.4.0aN]`` historical CHANGELOG sections
  contain references to the verbs in their then-current
  spelling. The bulk sed pass for the rename retroactively
  swept the verb names in those historical sections too —
  which is a documentation-hygiene cost (the
  release-notes-for-version-X prose now describes the
  version-Y spelling), accepted in exchange for the
  single-pass simplicity of the sed. The git log of this
  commit is the authoritative source for "which release
  first carried the new verb name". A future cleanup could
  do a per-section-aware sed that leaves the historical
  sections alone and only touches the ``[Unreleased]``
  prose, but the current state is consistent within itself
  (every doc reads the new name everywhere) and the
  trade-off seemed worth taking once.

### Removed (breaking)

- The ``mcs auth`` command group is gone. The two verbs it carried
  — ``mcs auth whoami`` (which only ever reported the configured
  AK prefix and env-var name, not a live RAM identity) and
  ``mcs auth test`` (the 3-step ``resolve_credentials`` → tier
  probe → ``SELECT 1`` smoke test) — were not earning their own
  CLI surface: ``whoami``'s job is taken over by the new
  ``mcs profile whoami`` (a real ODPS whoami probe rather than a
  config dump), and ``test``'s job falls out of any actual command
  running against the profile (``mcs sql execute "select 1"`` is
  the canonical "is this profile usable?" check and produces a
  richer error envelope on the failure path than the dedicated
  verb's per-step output did). The internal 3-step probe still
  runs inside ``mcs profile create`` and ``mcs profile update``
  when the auth has changed — the wizards' "Auth test failed.
  Save profile anyway?" prompt is unchanged.

  The ``commands/auth.py`` click-decorated module is deleted; the
  helper that runs the three steps lives on in
  ``commands/_auth_probe.py`` as a private function (no underscored
  variant of the click verbs is exposed elsewhere).

- The ``identity: str | None`` field that briefly existed on the
  ``Profile`` dataclass — added in a feat/claude-code-plugin
  intermediate commit to cache the captured RAM principal — is
  gone. Identity is a runtime property of the credential, not
  configured-state-on-disk, so persisting it across invocations
  was the wrong shape (it could go stale silently if the AK
  pointed at a rotated key). The new ``mcs profile whoami`` verb
  probes on every call, and the yaml round-trip carries no
  identity-related key. Pre-existing yaml files on disk that
  still carry an ``identity:`` key (written by the intermediate
  commit) load fine — the loader ignores unknown keys — and the
  key is dropped the next time ``mcs profile create / update /
  link`` rewrites the file.

### Added

- ``mcs profile whoami [NAME]`` — live identity probe for a
  profile. For AK profiles it issues
  ``odps.execute_security_query("whoami")`` and prints the
  ``principal_display`` string (matches the ``maxc auth
  whoami`` field of the same name, e.g.
  ``RAM$role-name:user-name``). For ProcessAuth profiles it
  calls the configured ncs helper's ``whoami()`` and emits
  ``"<identity_name> (employee.<id>)"``. The probe is not
  cached anywhere — every invocation hits the live source. JSON
  envelope on ``-f json``; quiet mode prints the bare identity
  string for shell pipelining.

  Resolution. When ``NAME`` is given it's a direct alias
  lookup, raising ``ProfileNotFoundError`` (exit 3) on miss
  the same way ``mcs profile show`` does. When ``NAME`` is
  omitted the verb routes through the standard active-profile
  chain: explicit ``--profile`` → ``MCS_PROFILE`` → ``mcs link
  bind`` cwd-binding → standard ``ALIBABA_CLOUD_*`` /
  ``MAXCOMPUTE_*`` env-var fallback. The env-vars-anonymous case
  (chain ends in the
  fallback Profile whose ``name`` is empty because
  ``$MAXCOMPUTE_PROJECT`` is itself unset) is labelled
  ``(env-vars)`` in the banner so the absence-of-saved-name is
  visible. ``--profile`` is an explicit profile override;
  ``--project`` only names the target MaxCompute project for the
  env-var fallback.

  Two new classified errors in
  ``mc_client.errors``: ``WhoAmIFailedError`` (code
  ``WhoAmIFailed``, exit 1) when the credential resolved and
  the connection opened but the ODPS security query gave
  nothing parseable back, and ``NoBoundProfileError`` (code
  ``NoBoundProfile``, exit 1) when the bare-NAME form of
  ``mcs profile update`` lands on the env-vars-anonymous
  fallback (which has no on-disk yaml entry to update).

- ``mcs profile show`` and ``mcs profile update`` now accept
  the bare form too: omit the positional ``NAME`` and the verb
  resolves the target through the same active-profile chain as
  the rest of the CLI. ``show`` 's title banner gains a small
  dim ``(resolved via MCS_PROFILE / cwd-link / env)`` suffix when
  the target was resolved (rather than explicitly named), so
  the reader can tell which path produced the answer.
  ``update`` refuses bare invocation when the chain lands on
  the env-vars-anonymous fallback, since there's no saved alias
  for the wizard to write back to (the new
  ``NoBoundProfileError`` covers that case with a clear "run
  ``mcs profile create`` then ``mcs link``" remediation).

- The auth section of ``mcs profile show`` annotates each env-ref
  field with the current shell's environment status — the
  ``${env:NAME}`` pointer stays verbatim and a small "(env var
  NAME set / NOT set in current shell)" tag is appended in green
  / yellow respectively. The annotation only checks ``name in
  os.environ`` — it never echoes the resolved literal AK to the
  terminal. The yaml round-trip is unchanged (the env-ref
  pointer is the round-trip-safe form, and the annotation is
  output-only).

### Added (previously)

- ``mcs profile import-creds`` — import auth from existing
  ``odpscmd`` / ``maxc-cli`` configs into a new mcs profile.
  ``--source auto`` (default) scans ``~/.maxc/config.yaml`` and the
  odpscmd default config (resolved via ``shutil.which("odpscmd")`` →
  ``<install_root>/conf/odps_config.ini`` per Aliyun docs);
  ``--source maxc|odpscmd --config-path PATH`` imports from a
  non-default location. Skips non-AK auth providers (RAM role / STS
  flows still need the wizard).
- Wizard auto-detection — ``mcs profile create`` (interactive) now
  checks for importable creds after the alias prompt and offers a
  one-keystroke "import these?" path that skips the endpoint /
  auth / discovery prompts entirely. Pass any of the per-prompt
  override flags (``--endpoint`` / ``--auth-type`` / ``--ak-id-env``
  etc.) to suppress the offer.

### Removed (breaking — alpha-no-compat)

- ``mcs profile create --non-interactive`` mode + the 12 flag-per-field
  options (``--project`` is kept as an override-skip-discovery flag,
  but the rest — ``--auth-type`` / ``--employee-id`` /
  ``--ncs-command`` / ``--ak-id-env`` / ``--ak-secret-env`` /
  ``--ak-literal`` / ``--ak-id`` / ``--ak-secret`` / ``--schema`` /
  ``--tag`` / ``--confirm-cny`` / ``--blocked-cny`` are now wizard-
  prompt-skip overrides only). The canonical non-interactive entry
  point is ``--from-file @path`` / ``--from-spec '<inline>'``.

### Added

- ``mcs profile spec-template`` — prints a fillable yaml template to
  stdout. Run ``mcs profile spec-template > p.yaml``, edit the
  placeholders, then ``mcs profile create --from-file @p.yaml`` /
  ``mcs profile update <name> --from-file @p.yaml``.
- ``--from-file`` / ``--from-spec`` help text now points at
  ``mcs profile spec-template`` so users know where to look for the
  schema.

### Changed

- ``--from-file`` and ``--from-spec`` accept BOTH yaml and json
  (json is a valid yaml subset; the loader is
  ``ruamel.yaml.YAML(typ='safe')``). Use yaml for hand-edits,
  json for scripted construction.
- CI yamls (``.aoneci/benchmark.yaml`` / ``benchmark-full.yaml``)
  now build profiles via ``--from-spec`` with a single-line JSON
  literal instead of the deleted ``--non-interactive`` flag form.

## [0.4.0a3] - 2026-05-17

Single-verb mutation surface — collapses the v0.4.0a2 ``add-source`` /
``update-source`` / ``remove-source`` triplet plus colon shorthand
(``--source 'p:s:t'``) and synthetic ``--source-key`` identifier into
one ``mcs profile update`` verb that opens a file-browser-style
multi-level editor (interactive) or accepts a complete-profile yaml/json
spec via ``--from-file`` / ``--from-spec`` (non-interactive). The
GET-mutate-PUT path for agents now works without exposing AK secrets.

### Removed (BREAKING)

- ``mcs profile add-source`` / ``update-source`` / ``remove-source`` —
  superseded by ``mcs profile update <name>`` (full-profile editor).
- ``--source 'project:schema:tables'`` colon shorthand grammar (was on
  ``add-source`` and ``create``).
- ``--source-spec`` flag (single-source JSON; was on ``add-source`` /
  ``update-source`` / ``create``) — replaced by ``--from-spec`` which
  takes a full-profile JSON.
- ``--source-key`` flag (was on ``update-source`` / ``remove-source``)
  — the (project, schema) tuple identifies a source naturally inside
  the editor; the synthetic key is no longer user-facing.
- ``--source`` flag on ``mcs profile create`` (replaced by ``--from-file``
  / ``--from-spec``).
- ``parse_source_string`` / ``parse_source_spec`` / ``add_source_loop``
  helpers in ``commands/_source_picker.py`` (used only by the deleted
  verbs; the picker's drill-down code stays for the new editor).

### Added

- ``mcs profile update <name>`` — single verb for all profile edits
  (compute_project, endpoint, auth, cost thresholds, tags, sources).
  Multi-level file-browser-style picker; ↩ Back at each level,
  ✓ Save and exit at top commits, ✗ Cancel discards. Each section
  has its own sub-picker (auth: AK / Process type select + per-type
  fields; sources: list + Add new + drill into existing for table-level
  edits; etc.).
- ``mcs profile update <name> --from-file @profile.yaml`` /
  ``--from-spec '<inline JSON>'`` — full-replace from complete-profile
  YAML / JSON. Spec shape matches ``mcs profile show <name> --format
  json`` output (the on-disk yaml block plus a top-level ``name`` field).
  Mutually exclusive (``--from-file`` XOR ``--from-spec``).
- ``mcs profile create <name> --from-file @yaml`` / ``--from-spec
  '<json>'`` — non-interactive create from full-profile spec. Refuses
  to clobber an existing profile; use ``mcs profile update`` to modify.

### Changed

- ``mcs profile create <name>`` interactive wizard now drops into the
  new editor after auth-test (was: source-only ``add_source_loop``).
  The prompt shifted from "Add a data source now?" to "Configure now
  (sources, tags, etc.)?" to reflect the broader scope. Cancel in the
  editor leaves the bare shell saved (Phase 1 still committed it).
- ``mcs profile show <name> --format json`` output is now a
  round-trippable shape — same as the on-disk yaml-block plus a
  top-level ``name`` field. AK secrets that are literal values are
  redacted to ``***REDACTED***``; ``${env:VAR}`` references pass
  through unchanged. The plain (default) text output keeps the older
  human-readable shape with ``source_key`` per source and a top-level
  ``default`` flag for clarity.
- ``update --from-spec`` honors the ``***REDACTED***`` marker: when the
  spec's ``auth.access_key_id`` / ``access_key_secret`` is the literal
  marker string, the loader substitutes the existing profile's stored
  value. This makes the GET-mutate-PUT loop work without the agent
  ever seeing the secret. ``create --from-spec`` rejects the marker
  (no existing profile to substitute from).
- ``mcs profile update`` (the verb itself) lost ~15 v0.x flags
  (``--project`` / ``--endpoint`` / ``--region`` / ``--auth-type`` /
  ``--employee-id`` / ``--ncs-command`` / ``--ak-id-env`` /
  ``--ak-secret-env`` / ``--ak-literal`` / ``--ak-id`` / ``--ak-secret``
  / ``--tag`` / ``--confirm-cny`` / ``--blocked-cny`` /
  ``--show-advanced`` / ``--non-interactive``). Use the editor
  (interactive) or ``--from-spec`` / ``--from-file`` (non-interactive)
  instead.

### Migration notes

- v0.4.0a2 ``add-source`` user → ``mcs profile update <name>`` →
  Sources → "+ Add new source"; or non-interactive: read the existing
  profile via ``show --format json``, append the new source dict to
  ``sources``, write back via ``update --from-spec``.
- v0.4.0a2 ``update-source --source-key K`` user → ``mcs profile
  update <name>`` → Sources → pick the matching source → drill in.
- v0.4.0a2 ``remove-source --source-key K`` user → ``mcs profile
  update <name>`` → Sources → pick → "× Remove this source" (with
  confirm); or non-interactive: filter the source out of the spec
  and PUT back via ``update --from-spec``.
- v0.4.0a2 ``--source 'p:s:t'`` colon shorthand user → use ``mcs
  profile update --from-spec`` with a JSON source dict; the editor
  is more discoverable for one-off interactive edits.

## [0.4.0a2] - 2026-05-16

Multi-source mutation surface — completes the v0.4.0a1 cutover with the
deferred ``add-source`` / ``update-source`` / ``remove-source`` verbs and
an interactive drill-down source picker (``project → schema → tables →
columns``) that serves both terminal users and agent-driven flows from
the same backend.

### Added

- **Interactive drill-down picker** in ``mcs profile create`` — Phase 1
  (alias / endpoint / auth / auth-test → save profile shell with
  ``sources=()``) auto-flows into Phase 2 (``Add a data source now?``
  → ``project → schema → tables → columns`` via questionary TUI).
  Phase 1 commits before Phase 2 so Ctrl-C mid-picker doesn't lose work.
  Skip with N at the Phase 2 prompt.
- ``mcs profile add-source <name>`` — interactive drill-down picker (same
  code path as create-wizard's Phase 2). Non-interactive forms:
  ``--source 'project:schema:tables'`` (repeatable; ``tables`` = ``*``
  or comma-separated) and ``--source-spec @file.json`` /
  inline JSON (column-scoped).
- ``mcs profile update-source <name> --source-key K`` — interactive
  picker pre-filled with the matched source's selections; or
  ``--source-spec`` for non-interactive replacement. Replaces in
  place (preserves the source's position in ``sources``).
- ``mcs profile remove-source <name> --source-key K [--yes]`` —
  idempotent on missing keys.
- **Data API verbs** for agent-driven drill-down:
  - ``mcs meta list-projects`` — JSON envelope of accessible projects
  - ``mcs meta list-schemas --project P`` — JSON envelope of schemas
    in P
  Existing ``mcs meta list-tables`` / ``describe-table`` cover steps
  3-4 of the drill-down (no new verbs needed).
- ``mcs profile create`` accepts ``--source 'p:s:t'`` (repeatable) and
  ``--source-spec`` for one-shot non-interactive create-with-sources
  (replaces the legacy single-auto-source default when set).
- ``MaxComputeClient.list_schemas(project=None)`` accepts an explicit
  ``project=`` kwarg (parity with ``list_tables`` / ``describe_table``)
  so cross-project schema listing works without rebinding the
  connection.
- Agent-side drill-down doc: ``_skill/references/source-picker.md``,
  indexed from SKILL.md decision matrix.
- Multi-source mutation section in ``_skill/references/onboarding.md``
  documenting the new verbs.

### Changed

- ``mcs profile create`` wizard returns a profile **shell** (empty
  ``sources``); the actual sources are populated by the picker (Phase
  2) or by ``--source`` / ``--source-spec`` flags. The deprecated
  ``--schema`` flag still works in ``--non-interactive`` mode (legacy
  single-auto-source behavior) for backward compat with eval scripts;
  in interactive mode it's superseded by the picker.
- ``commands/_source_picker.py`` is the shared backend used by both
  the wizard and the three new mutation verbs — single source of truth
  for the drill-down UX, prevents code drift.

### Dependency

- Adds ``questionary>=2.0`` (first non-click TUI dependency).

## [0.4.0a1] - 2026-05-16

Multi-source profile cutover. Hard-cutover (alpha-no-compat); v0.3.x
profile yamls are rejected with a "recreate via `mcs profile create`"
message. See `docs/superpowers/specs/2026-05-15-mcs-multi-source-profile.md`
for the full design.

### Added

- `Profile` dataclass v2 shape: `(name, compute_project, endpoint, auth,
  sources: tuple[DataSource, ...], cost_thresholds, tags, package_path)`.
  An AK identity has one `compute_project` (where SQL jobs spawn / billing
  accrues) and zero-or-more cross-project `DataSource(project, schema, tables)`
  entries that the AK reads via cross-project metadata privileges.
- `DataSource(project, schema="default", tables: tuple[TableSpec, ...] | str)`:
  one `(project, schema, tables)` triple per data source. `tables` is either
  the literal `"*"` (wildcard, expanded against the live MaxCompute catalog
  at build time) or a tuple of `TableSpec` entries. `source_key()` returns
  `<project>__<schema>` — the filesystem-portable disambiguator used in
  PackageDB rows, per-source markdown filenames, and tier-cache paths.
- `TableSpec(name, columns, columns_exclude)`: optional column whitelist /
  blacklist scoping (mutually exclusive). Filter is the agent's view of the
  package metadata; column-level access enforcement is MaxCompute-side
  (LabelSecurity), independent of mcs.
- Per-(profile, project) tier cache at `<profile_data_dir>/tier_cache/<project>`.
  Each MaxCompute project the profile addresses gets its own cached `"2"` /
  `"3"` sentinel. The v0.3.x single-file `<profile_data_dir>/.tier-level`
  sentinel is orphaned; `mcs build` re-populates the new layout from live
  probes on first invocation.
- `mc_client` methods (`list_tables`, `describe_table`, `search_tables`,
  `search_columns`, `list_partitions`, `freshness_info`, `sample_table`,
  `profile_table`) accept an explicit `project=` keyword (defaults to the
  profile's `compute_project`) so cross-project source metadata reads can
  be addressed without changing the connection's bound project.
- `mc_client` SQL methods (`execute_sql`, `cost_estimate`, `run_sql_async`,
  `explain`) accept an explicit `schema=` keyword (replaces the v0.3.x
  `self._profile.schema` fallback, which is gone — schema is per-source in
  v2 and threaded by the caller).
- `mcs profile export` manifest schema_version bumps from 1 to 2; the
  exported manifest serializes `compute_project` plus the full `sources`
  list (including each source's `tables` enumerated form with column
  scoping). The importer reconstructs `DataSource` and `TableSpec`
  instances from the v2 manifest.

### Changed

- `mcs profile create` (interactive wizard + non-interactive flags) writes
  the v2 shape: a single auto-generated `DataSource(project=<project>,
  schema=<schema>, tables="*")` matching the wizard's chosen project /
  schema. The multi-source `add-source` / `remove-source` / `update-source`
  verbs (for adding additional `DataSource` entries to a profile) land in
  0.4.0a2.
- `mcs profile list` / `mcs profile show`: list adds `compute_project` and
  `sources` count columns; show renders the full per-source list with
  source_key disambiguator.
- `mcs auth whoami`: top-level shape gains `compute_project` and a
  `sources[]` array with `source_key` for each source.
- `mcs build` accepts the v2 multi-source profile but still runs the
  pipeline against `profile.sources[0]` only — the per-source outer loop
  (one `BuildPipeline` invocation per `DataSource`, with per-source
  PackageDB rows keyed by `source_key`) lands in 0.4.0a2.
- `mcs meta list-tables` / `describe-table` / `search-tables` /
  `search-columns` / `list-partitions` / `freshness` thread the
  `--project` and `--schema` CLI flags through to the v2 `mc_client`
  method `project=` / `schema=` keywords, so a sql meta query targeting
  a cross-project source now reaches the correct project.
- `commands/sql.py` `_validate_schema_for_tier` drops the `profile_schema`
  fallback parameter; the v2 helper validates the CLI `--schema` flag
  against the tier rules without consulting profile defaults (the v1
  profile-level schema field doesn't exist in v2).
- `commands/build.py` falls back to `profile.sources[0].schema` for the
  build pipeline's active schema when `--schema` is omitted.

### Removed

- `Profile.project` / `Profile.schema` v1 fields. Hard-cutover, no
  compatibility shim. The profile-store yaml deserializer rejects v1
  yamls (`'project'` / `'schema'` keys without the v2 `'compute_project'`
  / `'sources'` keys) with `InvalidProfileError` and the canonical
  "recreate via `mcs profile create`" remediation message.
- `MCS_PROFILES_DIR` env-var still works but its semantics are now the
  per-profile-data root (matches the 2026-05-14 vocabulary cleanup); the
  legacy "profiles config dir" interpretation is gone.

### Migration

There is no migration tooling. The alpha-no-compat policy (spec §17 R1/R2)
is intentional — the v2 shape is incompatible with v1 in ways that can't
be unambiguously round-tripped (the v1 single `(project, schema)` could
mean either `compute_project` or the implicit single source's identity,
and the v2 `compute_project` vs `sources[0].project` distinction has no
v1 analog). Operators recreate via `mcs profile create` on first use.

## [0.3.0a26]

(See git log for the v0.3.x series.)

## Pre-mcs history (2026-04-23 → 2026-05-07)

> The following versions come from `odps-context-query`, the predecessor project before the mcs rewrite. **Not continuous with the mcs mainline** — mcs v2 restarted from `0.3.0a3` on 2026-05-14. Kept here for archival reference only.

<details>
<summary>Expand 16 pre-mcs versions (pre-mcs-0.1.30 → pre-mcs-0.9.1)</summary>

### [pre-mcs-0.9.1] - 2026-05-07

#### Fixed

- **`odps-context-query/SKILL.md` backfills high-value rules into the body and removes the directive that forces Read on large files.** After 0.9.0 landed, `benchmark-smoke` (run 39562032) regressed sharply across 4 arms: 3level-off 34.5% → 23.1%, 3level-with-history 50.0% → 29.6%, 2level-off 42.9% → 27.6% (avg -16 pp, plus 8 new timeouts). Root cause: the previous change added a directive at the top of SKILL.md saying "before generating any SQL, load sql_generation_guide.md (604 lines) + sql_query_patterns.md (421 lines)"; the agent actually went and read them → every case spent 2 extra turns reading >1000 lines of content, squeezing the 300s budget for generation / execution / retries. This fix:
  - Backfills 8 core MaxCompute syntax differences (ORDER BY+LIMIT, no implicit CROSS JOIN, CONCAT, RLIKE double-escaping, single quotes, partition WHERE, ratio CAST DOUBLE, SUM CASE WHEN) and the 10-line table of most-error-prone function-name mappings (`DATE_FORMAT→TO_CHAR`, `GROUP_CONCAT→WM_CONCAT`, `IFNULL→NVL`, etc.) **into the SKILL.md body** — this is dialect knowledge the LLM genuinely needs when writing MaxCompute SQL, not benchmark gaming, and is valuable to real users too. The SKILL.md body is the "auto-load layer" equivalent for an agent-portable skill; rules in the body = 0 extra turns.
  - Replaces the "must-read references/sql/sql_generation_guide.md + sql_query_patterns.md" directive with intent-driven guidance ("complex DQL generation → read sql_generation_guide.md", "matching common patterns → read sql_query_patterns.md", etc.), letting the agent load on demand instead of reading everything indiscriminately.
  - Fundamental design lesson: the mainline `app/skills/maxcompute_sql/references/` is auto-injected into the LLM context server-side via `app/skills/loader.py`; agent-portable skills have no such loader — only the SKILL.md body is the true auto-load layer. Inlining high-value rules into SKILL.md was the right call in 0.7.6; 0.9.0 mistakenly moved them back to references/ and forced Read, effectively undoing the 0.7.6 fix.

### [pre-mcs-0.9.0] - 2026-05-07

#### Changed

- **`odps-context-query` reference docs re-aligned with mainline.** Ported the contents under `references/sql/` to fully mirror mainline `app/skills/maxcompute_sql/references/` + `app/skills/runtime/`:
  - Added `references/sql/sql_generation_guide.md` (the mainline 604-line full DQL rules handbook, ported verbatim) as the default-load layer.
  - Replaced the branch's own English 14-pattern `sql_query_patterns.md` with the mainline Chinese 17-pattern version (restoring the 5 query patterns missing here: N consecutive active days / UNPIVOT / dynamic latest-partition lookup / multi-output / Range Join / pagination / multi-level CTE).
  - Filenames aligned with mainline: `sql_rules.md` → `dql_core.md`, `functions.md` → `dql_functions.md`, `hints.md` → `dql_hints.md`, `json_extraction.md` → `dql_json.md`, `session.md` → `sql_session.md`, `sql_error_recovery.md` → `sql_errors_optimization.md`.
  - Removed `ddl_dml_dcl.md` as unrelated to query (entire DDL/DML/DCL section gone — this skill only serves SELECT queries).
- **Three skills' SKILL.md rewritten in Chinese + agent-agnostic phrasing.**
  - `odps-context-query/SKILL.md`: English body translated to Chinese; added a "must-read base rules" block at the top instructing the agent to load `sql_generation_guide.md` + `sql_query_patterns.md` before generating SQL (mimicking the mainline references/ default-load layer); trimmed from 273 to 197 lines.
  - `odps-context-semantic/SKILL.md`: fully translated from English to Chinese.
  - `odps-context-report-issue/SKILL.md`: fully translated from English to Chinese.
  - All three docs uniformly removed Claude Code-specific tool terminology (`Read tool` / `Bash tool` / `Glob` / `${CLAUDE_SKILL_DIR}`, etc.) in favour of platform-agnostic descriptions.
- **Removed 8 benchmark-oriented SELECT-shape rules.** These rules ("return primary key not name", "no defensive NULL filter", "evidence field has highest priority", etc.) were added to lift Bird benchmark EX scores, but are too preachy and constraining for real users. The skill is for humans; it shouldn't embed benchmark optimizations.
- **Error-recovery suggestions uniformly route through `scripts/` wrappers; no more bare `maxc` calls.**
  - `references/sql/sql_common_errors.md`: 3 suggestions migrated from legacy `mcc table get / mcc table list / mcc sql execute` to `scripts/meta-describe-table` / `scripts/meta-list-tables` / `scripts/sql-run`.
  - `skills/odps-context-semantic/scripts/feedback-record`: error-classification remediation hints synced to the wrapper calls.
- `dictionary.txt`: added 10 SQL terms (`unpivot`, `tablesample`, `weekofyear`, `posexplode`, `listagg`, `localtimestamp`, `ilike`, `rangejoin`, `conditionaljoin`, `unpvt`) so the newly ported `sql_generation_guide.md` / `sql_query_patterns.md` pass cspell.

### [pre-mcs-0.8.0] - 2026-05-07

#### Changed (BREAKING)

- **Plugin rebranded from `maxcompute-context` to `odps-context`.** Single coordinated rename across every user-visible identifier:
  - **Plugin name** — `.claude-plugin/plugin.json`, `marketplace.json`, `.codex-plugin/plugin.json`, `.cursor-plugin/plugin.json`, `gemini-extension.json` all flip to `odps-context`. Slash invocation becomes `/odps-context:odps-context-query` (and friends).
  - **Skill names** — `mcc-query` → `odps-context-query`, `mcc-semantic` → `odps-context-semantic`, `mcc-report-issue` → `odps-context-report-issue`. The `mcc-` abbreviation was opaque to readers outside the team. Skill directory paths under `skills/` move accordingly.
  - **Profile data path** — `~/.local/share/maxcompute-context/profiles/` → `~/.local/share/odps-context/profiles/`. **Hard cutover, no migration shim.** Existing users must rebuild profiles (`/odps-context:odps-context-semantic` build per project). Old directory becomes orphaned.
  - **Environment variables** — `MCC_PROFILES_DIR` → `ODPS_CONTEXT_PROFILES_DIR`, `MCC_BIN` → `ODPS_CONTEXT_MAXC_BIN`, `MCC_TIER_OVERRIDE` → `ODPS_CONTEXT_TIER_OVERRIDE`, `MCC_NO_HISTORY` → `ODPS_CONTEXT_NO_HISTORY`, `MCC_NO_JOINS` → `ODPS_CONTEXT_NO_JOINS`. Any user scripts setting these need updating in lockstep.
  - **Eval persistent dir** — `.maxcompute-context/profiles` → `.odps-context/profiles` (project-relative; affects `eval/profiler.py` default and CI yaml).
  - **OpenCode plugin file** — `.opencode/plugins/maxcompute-context.js` → `odps-context.js` (export name changed too: `MaxcomputeContextPlugin` → `OdpsContextPlugin`).
  - Migration: re-install the plugin and run `/odps-context:odps-context-semantic` build for each project you previously profiled. There is no automatic migration path.

#### Added

- **Oracle diagnostic arms in the eval harness.** New `python -m eval run --oracle {none,tables,columns}` flag injects gold-SQL-derived hints into each prompt to measure the retrieval-vs-generation upper bound — `tables` leaks `expect_tables`, `columns` adds projected/filtered columns parsed from `gold_sql_mc`. Plumbed through `BenchmarkConfig.oracle` → adapter `oracle_block` kwarg → `_build_prompt`. `benchmark-full.yaml` gains two new matrix arm options (`oracle-tables`, `oracle-columns`); both build on top of the with-history profile and are opt-in only via the `arms` param (not in the default trigger, +$28/arm). Spec: [docs/superpowers/specs/2026-05-01-oracle-arms-diagnostic.md](docs/superpowers/specs/2026-05-01-oracle-arms-diagnostic.md).
- **Anti-cheating guardrails.** Three layers prevent oracle data from contaminating production baselines: (1) `ORACLE_MARKER` sentinel injected with every hint; new `python -m eval verify-no-oracle-leak --cases-dir <dir> --expect-oracle {yes,no}` greps the per-case transcripts and exits non-zero on either missing marker (oracle arm) or unexpected marker (production arm); (2) `eval/html_report.py:load_baseline_from_json` refuses any report.json with `config.oracle != "none"` so `python -m eval run --baseline …` can't accidentally compare against a contaminated reference; (3) loud `[ORACLE: <mode>]` banners on summary.md (title + callout), HTML report (red top bar via `_inject_oracle_banner` + title prefix), CI summary tile (ERROR-status `[ORACLE]` tile prepended). The benchmark-full pipeline runs the leak verifier after every arm.

#### Changed

- `eval/feature_detector.py` — added `extract_projected_columns(sql)` returning `(table_or_alias, col)` pairs from SELECT / WHERE / GROUP BY / ORDER BY / HAVING / ON clauses. Used by the oracle-columns hint formatter; same regex grade as the existing `extract_tables`, no sqlglot dep.
- `eval/runner.py` — `_AdapterProto.generate` gained an optional `oracle_block` kwarg; the runner pre-formats the per-case block from `BenchmarkCase.expect_tables` + `gold_sql_mc` before dispatching, so the adapter stays case-agnostic.

### [pre-mcs-0.7.6] - 2026-04-30

#### Changed

- **mcc-query/SKILL.md restructured to match master's auto-load model.** Pre-0.7.6 the high-value rules (8 critical syntax differences from MySQL, function-name traps, SELECT-clause guidance, preflight checklist, common error-code lookup) lived in `references/sql/*.md` files that the agent had to invoke `Read` on. Cross-arm transcript analysis on benchmark-full run 38954920 showed the agent only read `sql_style.md` 22.4% of cases (67/299), `sql_rules.md` 2% (6/299), and the other 7 reference files **never** (`ddl_dml_dcl.md`, `hints.md`, `json_extraction.md`, `session.md`, `sql_common_errors.md`, `sql_error_recovery.md`, `functions.md` were 0–0.7%). The original master design auto-loaded these via Claude Code's `.claude/rules/maxcompute/` mechanism — that layer was lost in the port to the Claude Code Plugin Skill format. Fix: inline the high-value content into SKILL.md body (which IS auto-loaded into context the moment the Skill is invoked). The `references/sql/` tree is retained for genuinely advanced lookups: `sql_rules.md` (LATERAL VIEW / GROUPING SETS / CUBE / ROLLUP / DISTRIBUTE BY / SELECT TRANSFORM / set operations), `functions.md` (full function catalog), `hints.md` (perf), `json_extraction.md`, `session.md`, `ddl_dml_dcl.md`, `sql_error_recovery.md` (extended error catalog beyond the inline lookup). SKILL.md grew 190 → 422 lines (~12 KB body, ~3 k tokens), still well within typical Skill size budgets.
- **mcc-semantic/SKILL.md consolidated.** Five per-operation reference files (`init.md`, `build.md`, `refresh.md`, `status.md`, `feedback.md`, totaling ~270 lines) collapsed into the SKILL.md body, since the agent never invoked Read on any of them across 30 profile-build runs in CI. Each operation now has a section in the SKILL.md body. Saves five round-trips per build attempt and removes a class of "agent forgot to read the reference and re-derived the wrong invocation" bugs. SKILL.md grew 113 → 275 lines.

#### Added

- **`references/sql/sql_query_patterns.md`** — ported from master (was missing in this branch). 17 reusable SELECT shapes: Top N, grouped Top N (ROW_NUMBER), PIVOT via SUM(CASE), running total, year-over-year change, dedupe-keep-latest, multi-metric one-pass aggregation, NULL handling, UNION ALL aggregation, date-range filter, LATERAL VIEW EXPLODE, self JOIN, LEFT SEMI JOIN, GROUPING SETS. Generic schemas (orders / users / employee) so the templates aren't BIRD-shaped. SKILL.md Step 2 references it for the residual 5% of questions that match a specific pattern.

#### Removed

- `skills/mcc-query/references/sql/sql_style.md` — content inlined into mcc-query/SKILL.md Step 2 ("SELECT-clause and shape" section).
- `skills/mcc-semantic/references/{init,build,refresh,status,feedback}.md` — content inlined into mcc-semantic/SKILL.md sections.

### [pre-mcs-0.7.5] - 2026-04-30

#### Changed (BREAKING)

- **`profile-build` history mining is now opt-in via `--with-history`** (was previously default-on, kill-switched by `MCC_NO_HISTORY=1` env var). Default behavior changed: mining is OFF unless the user explicitly asks for it. Rationale: mining needs tenant-level `odps:Select` on `system_catalog/information_schema/*`, adds 30-90s per schema, and is often unnecessary for first-time profile builds (project may have no history yet). The flag is the user-facing knob; the env var (`MCC_NO_HISTORY=1`) is retained as an internal ops kill switch but no longer documented in agent-visible places. Users / agents who want the previous "rich" behavior pass `--with-history`; mcc-semantic SKILL.md routes natural-language phrases ("with my query history", "rich/thorough/full profile", "include history") to that flag. Migration: existing profiles built before 0.7.5 are unaffected; future builds will lack mined samples unless `--with-history` is passed.

#### Fixed

- **Eval `with-history` arm hallucination class fixed at the source.** Pre-0.7.5 `eval/profiler.py` controlled the arm via env vars (`MCC_NO_HISTORY` / `MCC_ALLOW_HISTORY`) injected into the subagent's bash environment. qwen3.6-plus mis-applied this signal on ~10-20% of builds — sometimes prepending `MCC_NO_HISTORY=1 …/profile-build …` itself even on the with-history arm (smoke runs 38943741, 38944260, 38947479 each had 1-2 schemas mismatched out of 10). Root cause was that env vars are diffuse — they appear in `--help`, in source, in docstrings, in training data — and an LLM agent can absorb the pattern from any of those leaks and hallucinate it back. The 0.7.5 redesign carries the arm signal in the **prompt** instead: `eval/profiler.py` has two prompt templates and routes the `with_history` constructor flag to the WITH_HISTORY one ("include mining of past queries from TASKS_HISTORY"); mcc-semantic SKILL.md maps that natural-language phrasing to the `--with-history` CLI flag. Single source of truth, single hop of mapping. The `MCC_ALLOW_HISTORY` env var, the `_allow_history()` helper, and bash arm `export/unset` plumbing are all gone.
- **`verify-arm-mining` now checks bidirectionally and against output, not just the flag.** Previously asserted only `history_skipped == expected`. Now the no-history arm additionally requires `tables_with_sample_sqls == 0` for every schema (catches a flag/emit-logic disagreement bug class), and the with-history arm requires that at least one schema actually produced sample SQLs (catches the case where mining "ran" but TASKS_HISTORY auth failed across the board, leaving every per-table .md empty without setting the flag). Output is more descriptive: "history-mining fully skipped across N schema(s)" or "history-mining ran across N schema(s); M produced sample SQLs".

#### Added

- **`profile-build --with-history` flag.** Single-source-of-truth opt-in for past-query mining. See "Changed (BREAKING)" above.
- **`python -m eval build-profiles --with-history` flag.** Threads through to the `BirdProfiler(with_history=...)` constructor arg, which selects the WITH_HISTORY prompt template.
- **`references/sql/sql_style.md` — SELECT-clause + shape strategy guide.** New short reference (~110 lines) that mcc-query now reads before writing the SELECT. Distilled from analyzing 150 EX-failures in benchmark-full run 38881772: 31% picked the wrong column kind (e.g. `name` when gold returned `id`); 27% added extra columns the question didn't request (over-eager `CONCAT(street,city,zip)`, gratuitous joined-table fields); 16% added aggregation when the question wanted a list (`SELECT entity, COUNT(*) GROUP BY entity` instead of just `SELECT entity`). The doc gives five rules — interrogative-noun → column kind, no aggregation without trigger words, prefer `SUM(CASE WHEN ...)` over `COUNT(DISTINCT CASE ...)` for conditional counting, JOIN over scalar subquery in WHERE, no defensive `IS NOT NULL` / speculative `DISTINCT` — each with a generic HR / e-commerce example (deliberately not BIRD-shaped to avoid overfitting). Step 2 of `mcc-query/SKILL.md` now points at it explicitly. ~1k extra prompt tokens per SQL turn; current calls average ~30k input so the budget is fine. Validated against the 18 smoke fails on run 38944853 first, then full benchmark.

#### Fixed

- **`build.md` instructed agent to self-apply `MCC_NO_HISTORY=1`.** Reference doc had a one-liner "To skip the miner (eval mode), set `MCC_NO_HISTORY=1` in the environment" — qwen3.6-plus interpreted this as guidance to prepend `MCC_NO_HISTORY=1 …/profile-build …` on its Bash invocation, even when nobody asked it to. Caught by the new `verify-arm-mining` gate on smoke run [38943741]([internal]): 2/10 schemas (`bird_card_games`, `bird_thrombosis_prediction`) reported `history_skipped: true` even on the with-history arm — agent transcripts confirmed it had prepended the env var unilaterally on those two builds (sampling artifact — same model, same prompt, different rolls). Reframed the doc note: env-driven kill switches exist for the harness's A/B isolation, the agent should leave them alone. The miner now runs whenever the inherited environment doesn't say otherwise.

### [pre-mcs-0.7.4] - 2026-04-30

#### Fixed

- **Benchmark `with-history` arm was a placebo since 0.7.0** — measurable but invisible bug. [eval/profiler.py:163-173](eval/profiler.py#L163-L173) hardcoded `"MCC_NO_HISTORY": "1"` into the env passed to the build-profile subagent, so no matter what the upper bash script set (`unset MCC_NO_HISTORY` for the with-history arm), the script always saw `MCC_NO_HISTORY=1` and skipped the miner. Verified by inspecting `smoke-3level-with-history` artifact from run [38917952]([internal]): `_state.json` for every schema reported `history_skipped: true`, and `tables_with_sample_sqls: 0`; agent transcript in `_events/bird_card_games.jsonl` showed `"history_skipped": true` even on the with-history arm. This explains the long-standing observation that with-history and no-history arms had near-identical EX (~1pp gap, easily noise) and identical profile-build timing (111s vs 111s in run 38881772) — they were running the same code path. **Fix**: switch to positive opt-in. The profiler keeps `MCC_NO_HISTORY=1` as the default (eval-safe for unit tests + local dev + the `no-history` arm) but only if the caller hasn't set `MCC_ALLOW_HISTORY=1` first. Both benchmark yamls now `export MCC_ALLOW_HISTORY=1` on the with-history arm in addition to `unset MCC_NO_HISTORY`. Negative opt-out (just unsetting MCC_NO_HISTORY) wouldn't have worked here — the profiler re-injected it; positive opt-in makes the chain explicit at every layer.
- **`profile-build` now persists `history_skipped` + `tables_with_sample_sqls` to `_state.json`.** Previously these fields lived only in the JSON envelope on stdout, so post-hoc inspection of an already-built profile (e.g. via `mcc-semantic profile-status` or by digging into a CI artifact) couldn't tell whether the miner had actually run. Persisting them enables the new `verify-arm-mining` gate (below) and makes profile artifacts self-describing.

#### Added

- **`python -m eval verify-arm-mining --arm <name> --profiles-dir <dir>`** — post-build sanity gate for the benchmark CI. Walks every `_state.json` under the freshly built profile tree and asserts `history_skipped` matches the arm's intent (true for `no-history`, false for `with-history`). Both `benchmark.yaml` and `benchmark-full.yaml` now run this immediately after `cp -R ... profiles-snapshot`, before the eval starts. If a future change re-breaks the `MCC_ALLOW_HISTORY` plumbing the way 0.7.0 did, the with-history arm now fails fast with a per-schema breakdown instead of silently producing a placebo measurement.

### [pre-mcs-0.7.3] - 2026-04-30

#### Fixed

- **`profile-build` history-mining query was syntactically broken — never returned a single row** (Aone bug [#81634002]([internal])). The old form `SELECT script FROM SYSTEM_CATALOG.INFORMATION_SCHEMA.TASKS_HISTORY WHERE status='TERMINATED' AND start_time > DATE_SUB(GETDATE(), 30)` failed at parse time (parser column 20: `'system_catalog.information_schema.tasks_history' is not supported`) on **both 2-level and 3-level projects** — verified locally against `catalogapi_regression_test` (3-level) and `catalogapi_regression_test2` (2-level). SYSTEM_CATALOG addressing requires `SET odps.namespace.schema=true`; the SET is harmless on 2-level (no schema layer to enable, but server tolerates it), making the corrected form tier-independent. Three more issues sat behind the parse failure: column is `operation_text`, not `script`; status value is title-case `'Terminated'`, not `'TERMINATED'`; the view is partitioned on `ds` (YYYYMMDD) and rejects full-table scans without a partition predicate. Form follows the official tenant-level INFORMATION_SCHEMA spec ([Aliyun docs](https://help.aliyun.com/zh/maxcompute/user-guide/tenant-level-information-schema)). Effect on benchmark-full run 38881772: with-history arm's profile sample-SQL section was empty for all 10 schemas (`tables_with_sample_sqls=0` everywhere) — agent saw the same profile content as no-history arm, which explains the negligible EX gap (48.6% vs 47.3%) between the two arms. Mining now works; with-history arm should diverge from no-history in the next benchmark-full.
- New `_PROJECT_NAME_RE` validation in mining SQL builder — project name is interpolated into the WHERE clause and we don't have parameterized queries, so any non-`[A-Za-z][A-Za-z0-9_]*` project name fails fast with a skip message instead of injecting raw text.
- `Maxc.run` now accepts a per-call `timeout` keyword (default keeps the constructor-level `_timeout`). Used by mining to wait up to 300s for long async queries (`--wait 180` lets the CLI poll in-process; we cap the subprocess at 300s to give the CLI room to return its async job_id rather than be killed mid-wait).
- **Mining now dedups by `signature`.** TASKS_HISTORY records every retry of a query separately; the agent's `sql-run` retries on transient errors meant a single semantically-identical query showed up 3-5× in one table's sample list. We now keep only the most-recent row per `signature` (a server-side hash of the query AST that ignores whitespace/formatting). Verified locally: top 10 signatures alone in `catalogapi_regression_test`'s last 2 days had ~515 redundant rows. Rows with `signature=NULL` (rare, internal/system tasks) are kept under a per-row fallback key so they don't all collapse into one slot.
- **Mining `LIMIT 500 → 2000`.** With dedup eating ~5-10× of the rows on busy projects, 500 wasn't enough to give every schema/table reasonable coverage — the most-recent 500 raw rows could easily all come from one chatty schema, leaving siblings empty after the table-name regex bucket. 2000 is still cheap (~1-2 MB transfer) and gives a useful row budget of ~200-400 after dedup.

#### Changed

- **`benchmark-full.yaml` `--db-ids` list missed `california_schools`** — stale config from when the schema wasn't yet imported to MaxCompute. Effect on benchmark-full run 38881772: 16/299 cases per on-arm (5.4%) silently degraded to off-arm behavior — agent's `PROFILE_ABSENT` check returned true for `bird_california_schools_*` cases, agent fell back to cold-start `meta-list-tables` discovery. The schema actually exists in MC (verified by tracing one case: `frpm`/`satscores`/`schools` tables present, agent successfully wrote SQL via cold-start path), so the build should always have included it. Adds it to both `no-history` and `with-history` arms. Smoke yaml's [scripts/pick_smoke_cases.py](scripts/pick_smoke_cases.py) `--skip` default still excludes california_schools (separate, smoke-only setting); leaving that for now since smoke is fixed-30-case anyway.

#### Changed

- Renamed `agents/sql-expert.md` → `agents/maxcompute-sql-expert.md` for namespace clarity. Body refreshed: now references the current `scripts/sql-run` / `meta-*` wrappers (was: deprecated `mcc table list` CLI), and trimmed to delegate SQL syntax rules to `mcc-query`'s reference files instead of duplicating them.
- **Eval prompt template** ([eval/adapters/claude_code.py](eval/adapters/claude_code.py)) now spells out the `maxcompute-context:` plugin prefix requirement explicitly. qwen3.6-plus dropped the prefix on ~12% of cases in benchmark-full run 38676766 (37/287 Skill callers), causing `Unknown skill: mcc-query` errors → 20-turn syntax-variant burn → 300s timeout (≈22% of all timeouts in the no-history arm). Functionality unchanged for interactive slash-command users; this fixes only the eval-prompt path.

### [pre-mcs-0.7.2] - 2026-04-29

#### Changed

- **Script-discovery fallback in `mcc-query` / `mcc-semantic` SKILL.md** rewritten to point the agent at the `Base directory for this skill: <abs-path>` line that Claude Code injects at the top of SKILL.md content (see `loadSkillsDir.ts:346` in claude-code source) instead of running a brittle hardcoded `find` snippet. The previous form pre-enumerated every install root we knew about (`~/.claude`, `~/.codex`, `~/.cursor`, `~/.qwen`, `~/.gemini`, `~/.config/opencode`, `~/.hermes/skills`, `~/.local/share/maxcompute-context-src`, `$PWD`) plus a tricky `xargs dirname | xargs dirname` step to derive the skill root from a script path. Two failure modes observed in smoke run 38871362: (a) qwen often dropped the `xargs dirname` chain, then `"$SKILL_ROOT/scripts/sql-run"` resolved to a doubly-nested `scripts/sql-run/scripts/sql-run` path → exit 126 → wasted retry turns ([bird_european_football_2_0079](file:///tmp/run38871362/3level-with-history/eval/results/smoke-3level-with-history/report/bird_european_football_2_0079.jsonl)); (b) the install-root enumeration didn't cover hermes-agent / openclaw / future runtimes — adding each new agent target requires SKILL.md edits. **Why CC doesn't follow the agentskills.io spec's "bash CWD = skill root" convention**: it uses a different but equivalent mechanism — `loadSkillsDir.ts` prepends `Base directory for this skill: <abs-path>` to SKILL.md content and substitutes `${CLAUDE_SKILL_DIR}` with the same path before the model reads SKILL.md, so the model has the absolute path baked into its context without CC having to fork-with-cwd or interfere with project bash CWD. The new SKILL.md callout points the agent at that line first (works for CC), mentions `${CLAUDE_SKILL_DIR}` substitution as the secondary option, and falls back to disk search only when neither is available.

### [pre-mcs-0.7.1] - 2026-04-29

#### Fixed

- **Plugin manifest `agents` field broke entire plugin load.** Commit 180c4e0 added `"agents": "./claude-agents/"` (bare-string directory path) to [.claude-plugin/plugin.json](.claude-plugin/plugin.json), following the format shown in the official Claude Code plugin docs. In practice Claude Code's loader (verified locally on 2.1.119 and in CI on 2.1.121 / 2.1.123) silently rejected the entire plugin manifest when `agents` was a bare string pointing to a directory — `init` event reported `plugins: []` with zero `mcc-*` skills registered. Result: every smoke / benchmark run since 180c4e0 had 0% Skill-tool success, the agent fell through to inlining `maxc` commands, and the off-arm of the matrix beat the on-arms because the inline workflow was the only thing actually running. Fixed by switching to the array-of-explicit-file-paths form: `"agents": ["./claude-agents/maxcompute-sql-expert.md"]`. Local re-test now shows the plugin loads with all 3 skills + 1 agent registered under the `maxcompute-context:` namespace.

### [pre-mcs-0.7.0] - 2026-04-28

#### Added

- Codex plugin manifest at `.codex-plugin/plugin.json`.
- `AGENTS.md` symlink (→ `CLAUDE.md`) so Codex picks up the contributor guide.
- `.aoneci/multi-agent-install.yaml` CI pipeline — runs install + discovery for each supported agent.
- Gemini / Qwen extension manifest at `gemini-extension.json` (Qwen auto-converts).
- `GEMINI.md` symlink (→ `CLAUDE.md`) so Gemini/Qwen pick up the contributor guide.
- README "Others" section documenting raw-skills install for OpenClaw, Hermes Agent, and any agent that loads Anthropic SKILL.md directly.
- Extended SKILL.md path-discovery fallback to cover Qwen, Gemini, OpenCode, Hermes, and the "Others" raw-skills clone path.
- OpenCode install support — `.opencode/plugins/maxcompute-context.js` registers the `skills/` directory with OpenCode's discovery; `.opencode/INSTALL.md` documents setup.
- Cursor plugin manifest at `.cursor-plugin/plugin.json`.
- JSON manifest validation in CI (`quality.yaml` lint job).
- **Heuristic JOIN inference in `profile-build`.** New file
  `<profiles_root>/<project>/<schema>/_joins.md` carrying canonical
  JOIN paths derived from schema alone — 4 patterns from
  `docs/superpowers/specs/2026-04-23-semantic-layer-mvp-design.md`:
  same-name (cardinality-filtered), `xxx_id ↔ id` strict + English
  plural, loose `*_id` substring, `link_to_*`. Each edge carries
  `(source, pattern, confidence)` labels. Closes a gap on the
  cold-start arm: previously, when `MCC_NO_HISTORY=1` (the eval
  mode and any new project), the agent had no JOIN graph and burned
  6+ turns rediscovering keys via `maxc meta describe`. Bird-style
  schemas are well covered (link_to / xxx_id / cards-sets pluralization
  all hit; the column-cardinality filter avoids false positives on
  generic `name`/`year` columns).
- **`MCC_NO_JOINS=1` env var** in `profile-build` — A/B kill switch
  that skips inference and emits no `_joins.md`. Lets us isolate
  the JOIN inference's contribution by re-running the same commit
  with this on vs unset.
- **`mcc-query` SKILL.md Step 1** now reads `_joins.md` after
  `_overview.md` and before per-table `<table>.md`.

#### Fixed

- **Strip `agent_hints.next_actions` from `mcc-query` wrapper output.**
  `maxc <verb> --json` envelopes carry a top-level `agent_hints`
  field whose `next_actions` array suggests raw `maxc` commands
  (e.g. `maxc meta describe <table_name> --json` after a successful
  query). Those commands bypass the `sql-run` / `sql-cost` /
  `meta-list-tables` / `meta-describe-table` wrappers — the agent
  loses tier handling and the cost gate. All four wrappers now pipe
  their `maxc --json` output through `python3` to drop the
  `agent_hints` key before forwarding stdout. Other envelope fields
  (`status` / `data` / `error` / `metadata`) pass through unchanged.
- **Bundled-script invocations now have a discovery fallback for
  non-conformant runtimes.** The
  [agentskills.io spec](https://agentskills.io/skill-creation/using-scripts#referencing-scripts-from-skill-md)
  requires runtimes to set the bash CWD to the skill directory root
  before invoking scripts in `SKILL.md`, so bare `scripts/<name>`
  references resolve. Claude Code currently does not — bash CWD
  stays at the user's working directory, so `scripts/sql-run` fails
  to resolve. In smoke run 38559765, 9/30 cases hit "No such file or
  directory" and 5 of those failed EX after burning 2-3 turns
  searching for the absolute path. The skills keep the spec's
  bare-relative-path syntax (so they remain portable to Codex,
  Cursor, and other conformant runtimes), and add a short
  "Locating the bundled scripts" callout that gives the agent a
  one-liner `find` snippet to resolve the absolute path on
  non-conformant runtimes. Recompute per Bash call (each call is a
  fresh shell), or hardcode the resolved path within the call.
- **Eval prompt slash command namespace bug.** The prompt template
  used `/mcc-query` (short form), but `claude --print` requires the
  full plugin-namespace form `/maxcompute-context:mcc-query` —
  short form returns "Unknown command" and SKILL.md is never loaded.
  This caused all 60 benchmark cases (3 arms × 20) to run without
  SKILL.md guidance, making the profile-on arms indistinguishable
  from profile-off. Changed both `_PROMPT_TEMPLATE_3LEVEL` and
  `_PROMPT_TEMPLATE_2LEVEL` to use the full namespace form.
- **Eval gold leakage in CI.** `eval/cases/` (gold JSON) was in the
  workspace and visible to the agent via Grep/Read. financial_0285
  showed the agent grepping for gold SQL and copying it verbatim.
  Both benchmark YAML files now `mv eval/cases /tmp/_eval_cases_gold`
  before the eval run and restore it after.

#### Changed

- **`.aoneci/benchmark-full.yaml` infra fix.** Last full run produced
  EX 0/267 across all arms because the yaml was missing setup steps
  smoke yaml has: `maxc auth login --from-env`, `maxc query "SELECT 1"`
  connectivity probe, `rm -rf ~/.local/share/maxcompute-context/profiles`,
  and `--tier 3-level` on the eval `run` invocation. Added all four;
  the next manual trigger will produce real signal.
- **Project names parameterized via env vars.** Hardcoded
  `catalogapi_regression_test` / `catalogapi_regression_test2` literals
  replaced with `$MAXCOMPUTE_DEFAULT_PROJECT` / `$MAXCOMPUTE_DEFAULT_PROJECT_2`
  across `eval/__main__.py` (5 argparse defaults), `eval/adapters/claude_code.py`
  (constructor default), `.aoneci/benchmark.yaml` (smoke probes + matrix
  arm dispatch), and `eval/README.md` (example). Smoke yaml now
  fail-fast errors when `MAXCOMPUTE_DEFAULT_PROJECT_2` is unset; add
  it to project 3764008's CI vars before next push.
- **(BREAKING) Dropped `skills/mcc-query/scripts/`.** The `sql-run`
  and `sql-cost-check` wrappers were thin layers over `maxc query` /
  `maxc query cost`: tier-aware SET-prefix injection plus a CNY
  conversion + threshold check. Empirically, agents (especially weak
  ones like qwen) couldn't reliably resolve the skill scripts'
  absolute path, and the friction of "use the bundled wrapper" made
  EX worse than just calling `maxc query` directly with a profile-
  derived SET prefix. SKILL.md now teaches the agent to (a) read the
  cached `<profiles>/<project>/.tier-level` sentinel first to learn
  the tier, (b) compose the SET prefix inline, (c) call
  `maxc query` / `maxc query cost` directly. The cost gate (¥10 warn
  / ¥100 block) is now a SKILL.md-described policy the agent applies
  to `maxc query cost` output. The `.tier-level` sentinel is still
  written by `mcc-semantic` profile-build (via `_lib.py:_write_tier`)
  so cross-skill caching still works.
- `eval/adapters/claude_code.py` no longer injects `CLAUDE_PLUGIN_ROOT`
  — the env var existed only so script-path expansion would land
  somewhere; with no scripts to call, it's redundant.
- `make lint-sh` no longer runs shellcheck against the deleted scripts.
- `.aoneci/benchmark.yaml` smoke-arm tier-detection probe replaced
  with two direct `maxc query` calls (3-level bare-table SELECT and
  2-level SELECT 1) — same intent (verify auth + tier-correct SET
  prefix shape), same blast radius.
- **Eval prompt no longer injects a workflow.** The `claude_code`
  benchmark adapter used to inline a 70-line workflow (profile path,
  maxc commands, 7 hard SQL rules, even the literal
  `${CLAUDE_PLUGIN_ROOT}/skills/mcc-query/scripts/sql-run`) into every
  prompt because `--bare` disables SKILL.md auto-load. The on/off
  smoke arms then converged because the inline workflow already did
  what the profile would have done — meaning we were measuring the
  prompt, not the skill. The prompt is now the minimum a real user
  would type: `/mcc-query` + question + Bird→MaxCompute mapping +
  evidence. `--bare` and `--strict-mcp-config` are dropped; isolation
  comes entirely from `HOME=tmphome`. Skill activation routes through
  the standard `/mcc-query` slash on each invocation. Affects
  `eval/adapters/claude_code.py`, `eval/__main__.py`, and the related
  unit tests.
- **`make audit` now ignores CVE-2026-3219.** Affects pip 26.0.1; uv
  installs whatever pip is latest, no fix version is published yet.
  Same `--ignore-vuln` flag applied to CI.

### [pre-mcs-0.6.0] - 2026-04-26

#### Changed (BREAKING)

- **The Python package `maxcompute_context` is gone.** The plugin is
  now a pure bash + python3 (stdlib only) skill bundle. All five
  semantic operations were rewritten as executable scripts under
  [`skills/mcc-semantic/scripts/`](skills/mcc-semantic/scripts/):
  `profile-status`, `profile-init`, `profile-build`, `profile-refresh`,
  `feedback-record`. Each shells out to `maxc-cli` directly. **No
  install step**, no virtualenv, no third-party imports.
- **Sampler is replaced by `maxc data profile`.** ~190 lines of
  `profiler/sampler.py` collapse into a single `maxc data profile <fq>
  --json` call per table. Same fields (sample_values, top_values,
  null_ratio, distinct_count, min, max).
- **Miner is degraded.** The old sqlglot-based JOIN-graph extraction
  is replaced by regex-based table-mention lookup against
  `TASKS_HISTORY`. We attach recent SQLs that mention each table as
  "typical query examples" but no longer extract per-column filter stats or
  the cross-table JOIN graph. Trade-off accepted for dependency-freedom.
- **Two-layer profile dir layout.**
  `~/.local/share/maxcompute-context/profiles/<project>/<schema>/`
  (was `<project>_<schema>/` flat). 2-level projects always store
  under `<project>/default/` — forward-compatible with a 2→3 upgrade
  (MaxCompute attaches the existing flat tables to a `default`
  schema; the reverse is not allowed). The tier marker
  `<project>/.tier-level` (single byte: `2` or `3`) lives at the
  project layer and is shared by Python helpers and the bash
  `sql-run` wrapper.
- **`_history/` snapshots and `profile rollback` are gone.** Profile
  state is just the current `<table>.md` files plus a minimal
  `_state.json` (version + freshness + per-table schema_hash). If you
  need rollback, `git init` the profiles dir.

#### Removed

- `src/maxcompute_context/` (the entire Python package).
- `tests/test_*.py` for the package (kept `tests/eval/` for the
  benchmark harness).
- `pyproject.toml` no longer declares an installable
  `maxcompute-context` package — it's reduced to the eval harness's
  tool config (ruff / mypy / pytest) and the `maxc-cli` runtime dep.

#### Added

- `eval/_odps_config.py` vendors the tiny `OdpsConfig` +
  `load_odps_config` the eval importer needs (it uses pyodps for
  tunnel uploads of the Bird SQLite dataset, which is not part of
  the agent surface).
- `eval/evaluators/execution.py` now invokes `maxc query` directly
  (no Python module subprocess) and prepends SET hints inline for
  3-level projects.

#### Migration notes (0.6.0)

- Existing profiles built by 0.5.x (under `<project>_<schema>/`) are
  not migrated. Re-run `scripts/profile-init` (quick) or
  `scripts/profile-build` (full) once.
- The CLI surface `python -m maxcompute_context …` is gone. If you
  scripted against it, switch to `skills/mcc-semantic/scripts/<verb>`
  or `maxc <verb>` directly.

### [pre-mcs-0.5.0] - 2026-04-26

#### Changed (BREAKING)

- **Skill split into two**: the consolidated `maxcompute-context` skill
  is replaced by [`skills/mcc-semantic/`](skills/mcc-semantic/)
  (build / refresh / inspect the table profiles + record feedback) and
  [`skills/mcc-query/`](skills/mcc-query/) (generate +
  execute SQL, reading the semantic layer when present). The split
  matches the user's mental model — "set up the database knowledge" vs
  "run a query against it" are different sessions — and gives each
  skill a tighter description for activation.
- **No more wrapper scripts in the semantic skill**. Operations call
  `python -m maxcompute_context <verb>` directly. Users install the
  Python package once via `pip install maxcompute-context` (one-time
  setup, recorded in the skill's prerequisites). The 0.4.0 auto-install
  `scripts/run` + `scripts/install.sh` are gone — they always
  duplicated work the package install does once.
- **Query skill is Python-free**. It depends only on `maxc-cli`
  (already a prerequisite for auth) and `jq` (universal). The single
  `scripts/sql-cost-check` bash script wraps `maxc query cost` with
  configurable ¥10/¥100 thresholds (override via `MCC_COST_WARN_CNY`
  / `MCC_COST_BLOCK_CNY`). Truly portable to non-Claude-Code agents
  (Codex, Cursor) by copying the directory.

#### Added

- **Tier in profile overview**. The semantic builder now writes a
  `## Project info` block into `_overview.md` carrying the project's
  tier (`2-level` vs `3-level`), the table addressing form, and
  whether `odps.namespace.schema=true` is required. The query skill
  reads this rather than re-probing — single source of truth for
  per-project tier, no separate cache to keep fresh on the query
  side.

#### Fixed

- **Eval SQL extractor recognizes the new `maxc query` shape**.
  Previously anchored on `sql <verb>` only, which missed the 0.5.x
  query skill's direct `maxc query "<SQL>"` and bundled
  `sql-cost-check` invocations. Now also strips leading `SET ...;`
  hint prefixes so EX comparison sees the bare query.

#### Migration (existing CC plugin users)

1. `/plugin update` to pull the new layout (skill rename + split).
2. `pip install maxcompute-context` once (the 0.4.0 auto-install path
   is gone). Verify with `python -m maxcompute_context --help`.
3. The old `~/.local/share/maxcompute-context/venv/` from 0.4.0 is
   abandoned — safe to `rm -rf`.
4. Existing profiles at `~/.local/share/maxcompute-context/profiles/`
   are still read; rebuild after the upgrade so the new tier section
   appears in `_overview.md`.

### [pre-mcs-0.4.0] - 2026-04-25

#### Changed (BREAKING)

- **Single consolidated skill**. The 6 per-operation skill directories
  (`init-profile` / `build-profile` / `refresh-profile` / `status` /
  `query` / `feedback`) are merged into a single skill at
  [`skills/maxcompute-context/`](skills/maxcompute-context/) with one
  entry SKILL.md plus `references/<op>.md` for progressive disclosure
  and `references/sql/*` for SQL syntax docs. Activation is now via
  the entry SKILL.md's description rather than per-op slash commands.
  This avoids `query` / `status` / `feedback` colliding with other
  plugins' generic skill names.
- **Profile data moved to user-global XDG location**:
  `~/.local/share/maxcompute-context/profiles/` (honors
  `$XDG_DATA_HOME` if set). Previously cwd-relative
  `./.maxcompute-context/profiles/`, which the model frequently
  resolved against the wrong directory (the SKILL.md's own location
  vs. the user's project root). XDG path is stable regardless of cwd
  — the same profiles are visible no matter where the agent's working
  directory points. Tier cache moves to the same root.
- **Wrapper scripts moved INTO the skill**: previously at repo-root
  `scripts/run` + `scripts/install.sh`, now at
  `skills/maxcompute-context/scripts/{run,install.sh}`. Cleaner skill
  packaging — the skill is self-contained, including its own
  auto-install bootstrap. The wrapper now installs the package from
  PyPI (no longer from the editable source tree); venv lives at
  `~/.local/share/maxcompute-context/venv/`.

#### Fixed

- **Profile path resolution in SKILL.md** (regression from 0.3.0).
  Old text said `./.maxcompute-context/profiles/...` — Claude resolved
  `./` against the SKILL.md's own directory in 9/10 benchmark cases,
  probing the wrong path and silently degrading to "no profiles." The
  XDG path move (above) eliminates the ambiguity entirely.
- **Eval SQL extractor** (`eval/adapters/claude_code.py`). Previously
  matched only a `mcc sql ...` prefix, missing the
  `${CLAUDE_PLUGIN_ROOT}/...scripts/run sql ...` form. Now anchors
  on `sql <verb>` and tolerates any wrapper prefix.

#### Migration (existing CC plugin users)

1. `/plugin update` to pull the new layout.
2. Old slash commands like `/maxcompute-context:query` no longer
   exist — the agent activates the consolidated skill by description
   match. Just describe what you want ("query MaxCompute orders from
   last month", "build a profile for project X").
3. First skill invocation auto-rebuilds the venv at
   `~/.local/share/maxcompute-context/venv/`.
4. Re-build profiles once: the data location moved from the per-project
   `<project>/.maxcompute-context/profiles/` to the user-global
   `~/.local/share/maxcompute-context/profiles/`. Old profiles are
   abandoned (not migrated). `rm -rf <project>/.maxcompute-context/`
   when convenient.

### [pre-mcs-0.3.0] - 2026-04-25

#### Changed

- **Wrapper**: `bin/mcc` replaced by `scripts/run`. SKILL.md files now
  invoke commands via `${CLAUDE_PLUGIN_ROOT}/scripts/run <subcommand>`
  instead of the bare `mcc` console script. Same end-to-end behavior;
  the change unblocks shipping as standalone skill bundles for
  Codex / Cursor / other IDEs (forthcoming in v0.4).
- **Venv location**: `<cwd>/.maxcompute-context/venv/` (cwd-local,
  per-project) instead of `~/.claude/plugins/data/<plugin>-<marketplace>/venv`.
  Profile data already lived at `<cwd>/.maxcompute-context/profiles/`
  via the CLI default — both now share the same root for clean
  gitignore + per-project isolation.
- **CLI output shape (`mcc table get/list`, `sql execute/cost`,
  `table partition list`)**: now passes through the **raw maxc CLI
  envelope** (`{status, data, error}`) instead of the previous
  reshaped form (`{success, columns, partitionKeys, ...}`). Slash
  commands updated; agents that consumed those envelopes externally
  must read maxc's native field names. Profile / feedback commands
  unaffected.

#### Removed

- `bin/mcc` wrapper script (replaced by `scripts/run`).
- `[project.scripts] mcc` entry in `pyproject.toml`. Invoke via
  `python -m maxcompute_context <subcommand>` or `scripts/run <subcommand>`.
- Reshape layer in `src/maxcompute_context/catalog.py` (~200 LOC).
  See "CLI output shape" in **Changed** above for the user-visible
  consequence.

#### Migration notes

- After `/plugin update`, profile data for new builds writes to
  `<your-project>/.maxcompute-context/profiles/` instead of
  `~/.claude/plugins/data/maxcompute-context-<marketplace>/profiles/`.
  Old profile data is **not** auto-migrated — point the new build at
  the old path with `--profile-dir` if you want to keep it, or
  rebuild fresh.
- Old venv at `~/.claude/plugins/data/maxcompute-context-*/venv` is
  abandoned (~50MB orphan); safe to `rm -rf` after upgrading.

### [pre-mcs-0.2.0] - 2026-04-24

#### Added

- `mcc profile build --events-file <path>` — emit per-phase and
  per-table NDJSON events (`scan` / `sample` / `mine` / `compile`) to
  `<path>` for external observability (eval dashboards,
  build-timeline UIs). Silent no-op when the flag is unset.
- `MCC_NO_HISTORY` environment variable: when set to `1`/`true`/`yes`/`on`,
  `mcc profile build` / `mcc profile export` skip the miner phase entirely
  (no `TASKS_HISTORY` query, no local verified-queries read). Prevents
  answer leakage when evaluating on benchmarks like BIRD.

### [pre-mcs-0.1.30] - 2026-04-23

#### Added

- `mcc table partition list` — list partitions for a partitioned table.
- `mcc table search` — full-text metadata search across the catalog.
- `mcc instance logview` — resolve a logview URL from an existing instance ID.
- `mcc profile export` and `mcc profile compile` — separate the JSON snapshot phase from the markdown-render phase so exports can be reused without re-scanning.
- `default_schema` support: projects with schema-level namespacing now resolve correctly.

#### Changed
- CLI renamed from `maxcompute-context` to `mcc` with noun-first subcommand groups (`mcc table`, `mcc sql`, `mcc instance`, `mcc profile`, `mcc feedback`, `mcc skill`) — kubectl-style layout.

#### Fixed
- `mcc profile refresh` no longer silently discards Phase 2/3 enrichment (column value hints, related tables, JOIN samples, typical query SQLs) when schemas have not changed.
</details>
