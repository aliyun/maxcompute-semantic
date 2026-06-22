# maxcompute-semantic (`mcs`)

[![PyPI](https://img.shields.io/pypi/v/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![Python](https://img.shields.io/pypi/pyversions/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![License](https://img.shields.io/github/license/aliyun/maxcompute-semantic)](LICENSE)
[![CI](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml/badge.svg)](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aliyun/maxcompute-semantic/graph/badge.svg)](https://codecov.io/gh/aliyun/maxcompute-semantic)
[![English](https://img.shields.io/badge/lang-English-blue)](README.md)
[![中文](https://img.shields.io/badge/lang-中文-red)](README.zh-cn.md)

**Give AI agents the context they need to query your MaxCompute data.**

`mcs` is a CLI for building a local semantic package from your MaxCompute
project: table and column descriptions, JOIN relationships, UDFs, business
metrics, verified SQL, and notes from past queries. Your agent reads that
package before it writes SQL, then uses `mcs` to review, cost-check, and execute
the query.

[Documentation](https://aliyun.github.io/maxcompute-semantic/) · [PyPI](https://pypi.org/project/maxcompute-semantic/) · [Changelog](CHANGELOG.md)

## Why mcs?

AI agents can call MaxCompute, but they do not know your data warehouse. They
guess table names, miss JOIN keys, and write SQL that runs but answers the wrong
question.

`mcs` gives the agent a local source of truth:

- **Semantic package** - `mcs build` scans table metadata and produces a
  structured package the agent can read before writing SQL.
- **Memory** - verified queries, failed patterns, and domain notes accumulate
  over time. Similar questions can reuse known-good SQL instead of starting
  cold.
- **SQL guard rails** - cost estimation, write protection, dialect review, and
  tier-aware schema resolution run before the query reaches MaxCompute.
- **Agent integration** - the packaged skill works with Claude Code, Cursor,
  Codex, Gemini CLI, Qwen Code, OpenCode, and many other agent platforms.

## How it works

**You configure a `profile` → `mcs build` produces a semantic package → agent reads it to write SQL**

- **`[A] profile`** - identity, compute project, cost thresholds, and table
  scope. One profile usually maps to one business scenario.
- **`[B] semantic package`** - local SQLite + markdown knowledge base produced
  by `mcs build`.
- **`[C] agent`** - connects through the generated skill; reads the package,
  runs `mcs sql cost`, and then runs `mcs sql execute` or the async SQL
  lifecycle.

Business scenario = profile + semantic package + metrics + query memory.

## Quick Start

Before you start, have these ready: a MaxCompute project, region or endpoint,
auth method (AK, keyless/NCS, or process), the tables or schemas you want to
cover, and SELECT permission on those tables.

### 1. Install

```bash
uv tool install maxcompute-semantic
mcs --version
```

Then register the skill with the agents on this machine:

```bash
mcs skill install --detect -g
```

Prefer an agent-assisted install? Tell any connected AI agent:

> Install mcs for me, read this guide fully then follow step by step: curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md

The guide tells the agent to show you the exact command before running any
remote bootstrap or final install step.

### 2. Create a profile

```bash
mcs profile create
mcs link bind <profile-name>
```

`profile create` opens an interactive wizard for endpoint, auth, project,
sources, and cost thresholds. `link bind` ties the current directory to that
profile so future commands can resolve it automatically.

### 3. Build the semantic package

```bash
mcs build
mcs doctor
```

### 4. Ask through your agent

> *"How did last month's order GMV compare year-over-year?"*

The agent reads `mcs show`, checks the query with `mcs sql review`, estimates
cost with `mcs sql cost`, and then runs `mcs sql execute` when the query is
safe to run. Have it record working SQL so similar questions get BM25 recall
next time:

```bash
mcs memory verify --question "How did last month's order GMV compare year-over-year?" --sql "SELECT ..." --tables your_project.your_schema.orders
```

You can also let the agent do steps 2 and 3. After installing the skill, describe
your business scenario:

> *"I'm doing monthly analysis on warehouse A, mainly looking at the order and user tables in the dwd/dws layers of `your_project`; help me build the semantic layer."*

The agent will guide profile setup, bind the working directory, and run
`mcs build`.

## Safety and privacy

- `mcs` stores profiles and semantic packages locally. The semantic package is
  SQLite + markdown, not a hosted service.
- Credentials stay in your profile configuration. You can store AK values as
  environment-variable references instead of literals.
- `mcs build` reads metadata and optional samples only for the sources you put
  in the profile.
- `mcs sql cost`, `mcs sql review`, and write protection are designed to catch
  expensive or unsafe SQL before execution.
- Agents use `mcs` as the gatekeeper. They do not need direct MaxCompute
  credentials outside the configured profile.

## Key Features

| Feature | Command | What it does |
|---------|---------|--------------|
| **Build** | `mcs build` | Scan schema → produce semantic package |
| **Inspect** | `mcs show` / `mcs status` | Read package data and build status |
| **Catalog** | `mcs meta ...` | Discover projects, schemas, tables, and columns |
| **Query** | `mcs sql execute '...'` | Run SQL with tier-aware resolution |
| **Async SQL** | `mcs sql submit` / `wait` / `result` | Run and retrieve long queries |
| **Cost gate** | `mcs sql cost '...'` | Estimate cost before running |
| **Review** | `mcs sql review '...'` | Lint SQL for dialect / schema issues |
| **Memory** | `mcs memory verify ...` | Record a verified query for future recall |
| **Recall** | `mcs memory recall '<q>'` | BM25 search across verified SQL + notes |
| **Metrics** | `mcs metric add ...` | Define reusable business metrics |
| **Proposals** | `mcs package propose --from-suggestions` | Turn build suggestions into reviewable package changes |
| **UDFs** | `mcs udf ...` | List, inspect, create, test, and remove UDFs |
| **Doctor** | `mcs doctor` | Diagnose profile / auth / skill state |

Run `mcs <command> --help` for the full option surface.

## Manual Install

```bash
uv tool install maxcompute-semantic    # recommended, Python >= 3.10
# in a virtualenv / managed Python: pip install maxcompute-semantic
```

If `~/.local/bin` is not on your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

Then register the skill with your agents. Choose one:

```bash
mcs skill install --detect -g  # recommended: only agents found on this machine
mcs skill install --all -g     # every supported agent platform
```

### Upgrade / Uninstall

The uninstall example uses `-g` because the install examples above are global;
omit it for local skill installs.

```bash
mcs update                                    # check for latest and upgrade
mcs skill uninstall --all -g                  # remove global skill symlinks
uv tool uninstall maxcompute-semantic         # remove CLI
```

## Configuration

Profiles store auth, compute project, data sources, and cost thresholds:

```bash
mcs profile create                    # interactive wizard
mcs profile create --from-file @p.yaml  # scripted
mcs link bind <name>                  # bind cwd to profile
```

Profile resolution order: `--profile` flag → `MCS_PROFILE` env → cwd binding → `ALIBABA_CLOUD_*` / `MAXCOMPUTE_*` env vars.

## Contributing

```bash
uv sync --extra dev
uv run pytest tests/ -m 'not live'
uv run pytest tests/ -m 'not live' --cov=maxcompute_semantic --cov-report=xml --cov-fail-under=85
uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=80
uv run ruff check src/ tests/
uv run mypy src/
```

## License

Apache License 2.0 - see [LICENSE](LICENSE). Third-party notices in [NOTICE](NOTICE).
