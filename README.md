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
writes correct MaxCompute SQL on the first try, not the fifth.

[Documentation](https://aliyun.github.io/maxcompute-semantic/) · [PyPI](https://pypi.org/project/maxcompute-semantic/) · [Changelog](CHANGELOG.md)

## Why mcs?

AI agents can query MaxCompute, but they don't know *your* data. They guess
table names, miss JOIN keys, and write SQL that fails or returns wrong results.

`mcs` closes the gap:

- **Semantic package** — `mcs build` scans your project's schema and produces a
  structured knowledge base (SQLite + markdown) the agent reads before writing SQL.
- **Memory** — verified queries, failed patterns, and domain notes accumulate
  over time. The agent gets better the more you use it.
- **SQL guard rails** — cost estimation, write protection, dialect review, and
  tier-aware schema resolution, all before the query hits MaxCompute.
- **Agent-agnostic** — works with Claude Code, Cursor, Codex, Gemini CLI,
  Qwen Code, OpenCode, and 50+ more. One `mcs skill install --all` and every
  agent on your machine picks up the skill.

## Quick Start

```bash
# 1. Install
uv tool install maxcompute-semantic

# 2. Create a profile (interactive wizard)
mcs profile create
mcs link bind <profile-name>

# 3. Build the semantic package
mcs build

# Done — your agent can now use mcs commands via the skill.
```

## Install

### For humans

```bash
uv tool install maxcompute-semantic    # recommended
# or: pip install maxcompute-semantic
```

If `~/.local/bin` is not on your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

### For LLM agents

Paste this to your agent — it will handle everything:

```
Fetch the full guide and follow it step by step:
curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md
```

### Skill registration

```bash
mcs skill install --all -g   # all supported agents, global
mcs skill install --detect -g  # only agents found on this machine
```

Supported agents: `claude-code`, `cursor`, `codex`, `gemini-cli`, `qwen-code`,
`opencode`, and [50+ more](https://aliyun.github.io/maxcompute-semantic/docs.html).

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
| **Proposals** | `mcs package propose` | Suggest semantic annotations from build |
| **Doctor** | `mcs doctor` | Diagnose profile / auth / skill state |

Run `mcs <command> --help` for the full option surface.

## Configuration

Profiles store auth, compute project, data sources, and cost thresholds:

```bash
mcs profile create                    # interactive wizard
mcs profile create --from-file @p.yaml  # scripted
mcs link bind <name>                  # bind cwd to profile
```

Profile resolution order: `--profile` flag → `MCS_PROFILE` env → cwd binding → ODPS env vars.

For CI / one-off use without a saved profile:

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
export MAXCOMPUTE_ENDPOINT=https://service.<region>.maxcompute.aliyun.com/api
export MAXCOMPUTE_PROJECT=<project>
```

## Contributing

```bash
uv sync --extra dev
uv run pytest tests/ -m 'not live'
uv run ruff check src/ tests/
uv run mypy src/
```

## License

Apache License 2.0 — see [LICENSE](LICENSE). Third-party notices in [NOTICE](NOTICE).
