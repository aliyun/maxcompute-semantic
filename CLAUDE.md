# maxcompute-semantic Agent Guide

`maxcompute-semantic` is the `mcs` CLI: a local semantic-layer helper for
MaxCompute profiles, schema packages, query review, memory, metrics, UDFs, and
agent-facing skill bundles.

## Commands

| Command | Purpose |
|---------|---------|
| `uv sync --extra dev` | Install project and developer dependencies. |
| `uv lock --check` | Verify `uv.lock` is in sync with `pyproject.toml`. |
| `uv run pytest tests/ -m 'not live' -q` | Fast non-live test pass. |
| `uv run pytest tests/ -m 'not live' -n auto --dist loadscope --maxfail=1 -q --cov=maxcompute_semantic --cov-report=xml --cov-report=term-missing:skip-covered --cov-fail-under=90` | CI-shaped Python 3.10 coverage gate. |
| `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=80` | PR diff coverage gate after producing `coverage.xml`. |
| `uv run ruff check src/ tests/` | Blocking lint gate. |
| `uv run mypy src/` | Blocking type-check gate. |
| `uv run python -m compileall -q src tests` | Cheap import/syntax smoke check. |
| `grammar/generate.sh` | Regenerate ANTLR parser files after editing `grammar/odps/*.g4`. |

There is no `Makefile` in this repo; prefer the explicit `uv` commands above.

## CLI Smoke

```bash
uv run mcs --help
uv run mcs profile create
uv run mcs link bind <profile-name>
uv run mcs build
uv run mcs doctor
```

Profile resolution order is `--profile` flag, `MCS_PROFILE`, cwd binding, then
`ALIBABA_CLOUD_*` / `MAXCOMPUTE_*` environment variables.

## Architecture

```text
src/maxcompute_semantic/
  cli.py                 # Click entrypoint for `mcs`
  commands/              # CLI command surfaces and command helpers
  auth/                  # Profile schema, credential resolution, link store
  mc_client/             # PyODPS-backed MaxCompute client, guards, tier/cost logic
  build/                 # Semantic package build pipeline and package storage
  memory/                # FTS/vector/searchable query memory
  dialect/               # sqlglot MaxCompute dialect extensions
  commands/sql_review/   # Offline SQL review rules and advisory hints
  osi/                   # Open Semantic Interchange import/export
  versioning/            # Package git history, forks, locks, hooks
  _skill*/               # Bundled agent skill prompts and data

tests/
  unit/                  # Main regression surface, mirrors source modules
  integration/           # Local lifecycle tests, not live MaxCompute
  grammar/               # ANTLR/sqlglot grammar comparisons

docs/adr/                # Domain decisions, especially measure vs metric
grammar/                 # ODPS grammar source and generated parser
scripts/lint_diff.py     # GitHub Actions lint-diff summarizer
.github/workflows/       # CI, lint, release, dependency review, Zizmor
```

## Testing Notes

- CI runs Python 3.10/3.11/3.12 non-live tests; only Python 3.10 enforces 90%
  total coverage and 80% PR diff coverage.
- `src/maxcompute_semantic/build/*` is omitted from the unit coverage
  denominator because the build pipeline is validated through dedicated build
  unit tests, integration tests, and benchmark/full-run coverage rather than by
  counting every build module in the general unit gate.
- Pytest uses `--import-mode=importlib` because several test files share
  basenames across subdirectories. Do not remove this without checking the
  duplicate module-name failure mode.
- Live MaxCompute tests are marked `live` and are excluded from CI-shaped local
  commands with `-m 'not live'`.

## Workflow Rules

- Keep `CHANGELOG.md` current for user-visible CLI, storage, auth, release, and
  semantic-model changes.
- When `pyproject.toml` dependencies change, run `uv lock` and commit
  `uv.lock`; when reviewing, run `uv lock --check`.
- For code changes, run at least `uv run pytest tests/ -m 'not live' -q`,
  `uv run ruff check src/ tests/`, `uv run mypy src/`, and `git diff --check`.
- For dependency PRs, `.github/dependabot.yml` targets `dev`; GitHub security
  updates still depend on repository Code security settings.
- `AGENTS.md` is a symlink to this file. Keep `CLAUDE.md` as the single source
  of truth for shared agent guidance.

## Domain Terms

- **measure**: a column-level annotation marking a physical column as suitable
  for aggregation, stored as `columns.semantic_role = "measure"` plus `agg`.
  A measure has no standalone business identity.
- **metric**: a profile-level business measure with a name, expression, and
  optional description/context. Metrics live in the `metrics` table with
  `UNIQUE(name)` across the profile and are copied into generated SQL; `mcs`
  does not compile metric names as SQL references.
- **measure vs metric**: use a measure for a column property; promote it to a
  metric when the aggregation has reusable business meaning.
- **agent bootstrap playbook**: an agent-facing install workflow that can
  diagnose the local environment before installing `mcs`, while staying
  transparent before risky actions and checking artifact integrity.
- **process-auth helper**: a local command that `mcs` runs to obtain temporary
  MaxCompute credentials. Imported helpers must be visible to the user or
  explicitly trusted by the import workflow.
- **managed write path**: a product-owned workflow that performs a bounded
  MaxCompute mutation after user intent is captured. Treat it differently from
  open-ended arbitrary SQL execution.

## Gotchas

- `mcs sql review` is designed to be offline and package-backed; do not add
  live MaxCompute probes to normal review paths unless the command explicitly
  asks for execution or cost/tier checks.
- `mcs build` writes local package artifacts and versioning metadata. Avoid
  surprise rebuilds while answering read-only data questions.
- The grammar cache under `grammar/.cache/` is ignored; generated parser files
  under `grammar/generated/` are committed.
- Markdown/docs/site-only pushes are ignored by several CI workflows, so run
  local validation when documentation changes alter commands or operational
  guidance.
