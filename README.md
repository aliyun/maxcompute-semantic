# maxcompute-semantic (`mcs`)

[![PyPI](https://img.shields.io/pypi/v/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![Python](https://img.shields.io/pypi/pyversions/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![License](https://img.shields.io/github/license/aliyun/maxcompute-semantic)](LICENSE)
[![CI](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml/badge.svg)](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml)
[![English](https://img.shields.io/badge/lang-English-blue)](README.md)
[![中文](https://img.shields.io/badge/lang-中文-red)](README.zh-cn.md)

**Give your AI agent a semantic understanding of your MaxCompute data.**

`mcs` builds a local semantic package — table descriptions, column hints, JOIN
relationships, verified SQL patterns, and business metrics — so your AI agent
can write correct MaxCompute SQL sooner, with fewer retry loops.

[Documentation](https://aliyun.github.io/maxcompute-semantic/) · [PyPI](https://pypi.org/project/maxcompute-semantic/) · [Changelog](CHANGELOG.md)

## Three Things

**You configure a `profile` → `mcs build` produces a semantic package → agent reads it to write SQL**

- **`[A] profile`** — your identity + which tables to cover (AK or keyless auth + a set of sources). One profile = one business scenario.
- **`[B] semantic package`** — local knowledge base (tables / columns / JOINs / UDFs) produced by `mcs build`. The agent reads it before writing SQL instead of re-scanning MaxCompute metadata every time.
- **`[C] agent`** — connects via SKILL.md; runs `mcs sql cost` (cost gate) then `mcs sql execute` to query.

Business scenario = `[A]` profile + `[B]` semantic package + accumulated *annotations* / *memory* (gets better over time).

## Why mcs?

AI agents can query MaxCompute, but they don't know *your* data. They guess
table names, miss JOIN keys, and write SQL that fails or returns wrong results.

`mcs` closes the gap:

- **Semantic package** — `mcs build` scans your project's schema and produces a structured knowledge base (SQLite + markdown) the agent reads before writing SQL.
- **Memory** — verified queries, failed patterns, and domain notes accumulate over time. The agent gets better the more you use it.
- **SQL guard rails** — cost estimation, write protection, dialect review, and tier-aware schema resolution, all before the query hits MaxCompute.
- **Agent-agnostic** — works with Claude Code, Cursor, Codex, Gemini CLI, Qwen Code, OpenCode, and 50+ more. Run `mcs skill install --detect -g` for agents found on your machine, or `--all -g` for every supported platform.

## Quick Start

Before you start, have these ready: a MaxCompute project, region or endpoint,
auth method (AK, keyless/NCS, or process), the tables or schemas you want to
cover, and SELECT permission on those tables.

### 1. Install

Tell any connected AI agent:

> Install mcs for me, read this guide fully then follow step by step: curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md

The agent will install the CLI and skill in one go. The guide tells it to show
you the exact command before running any remote bootstrap or final install step.
Prefer manual setup? See [Manual Install](#manual-install).

### 2. Let the agent build your semantic layer

Once the skill is installed, **describe your business scenario** and let the agent set up the profile + semantic package:

> *"I'm doing monthly analysis on warehouse A, mainly looking at the order and user tables in the dwd/dws layers of `your_project` — help me build the semantic layer"*

The agent will:

1. **profile create** — guide you through MaxCompute identity setup, auto-probe auth.
2. **link bind** — bind the current directory to the profile so future commands auto-resolve.
3. **mcs build** — scan all tables in scope, produce the local semantic package.

### 3. Ask in natural language

> *"How did last month's order GMV compare year-over-year?"*

The agent runs `mcs show` (read semantic package) → `mcs sql cost` (cost gate) → `mcs sql execute` (run query). Have it record the working SQL so similar questions get BM25 recall next time:

```bash
mcs memory verify --question "How did last month's order GMV compare year-over-year?" --sql "SELECT ..." --tables your_project.your_schema.orders
```

## Key Features

| Feature | Command | What it does |
|---------|---------|--------------|
| **Build** | `mcs build` | Scan schema → produce semantic package |
| **Query** | `mcs sql execute '...'` | Run SQL with tier-aware resolution |
| **Cost gate** | `mcs sql cost '...'` | Estimate cost before running |
| **Review** | `mcs sql review '...'` | Lint SQL for dialect / schema issues |
| **Memory** | `mcs memory verify ...` | Record a verified query for future recall |
| **Recall** | `mcs memory recall '<q>'` | BM25 search across verified SQL + notes |
| **Metrics** | `mcs metric add ...` | Define reusable business metrics |
| **Proposals** | `mcs package propose --from-suggestions` | Suggest semantic annotations from build |
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

Apache License 2.0 — see [LICENSE](LICENSE). Third-party notices in [NOTICE](NOTICE).
