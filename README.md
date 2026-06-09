# maxcompute-semantic (`mcs`)

`maxcompute-semantic` is the package that ships the `mcs` CLI and the
bare `maxcompute-semantic` agent skill. Agents do not import Python
internals from this package at runtime; they load `SKILL.md` and call
`mcs` commands.

This README is the short reference for the installed distribution.

## What Is Included

- `mcs` CLI: profile management, metadata discovery, SQL execution,
  build/status/show, memory, metrics, UDF, and skill installation.
- Bare skill bundle:
  [`src/maxcompute_semantic/_skill/`](src/maxcompute_semantic/_skill/)
  with `SKILL.md` and lazily loaded `references/`.
- Profile store and semantic package builder. Profiles describe auth,
  compute project, data sources, cost thresholds, tags, and optional
  package path. Builds materialize a local SQLite + markdown semantic
  package for the profile.
- Agent install guide at [`scripts/install.md`](scripts/install.md) —
  a step-by-step skill for LLM agents to install `mcs` on the user's
  machine (uv bootstrap, PyPI install, PATH setup, skill registration).

## Install

### For humans

```bash
pip install maxcompute-semantic
```

Or with uv (recommended):

```bash
uv tool install maxcompute-semantic
```

If `~/.local/bin` is not on your PATH, add it:

```bash
# bash/zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# fish
fish_add_path ~/.local/bin
```

Verify:

```bash
mcs --help
```

### For LLM Agents

Fetch the full guide and follow it step by step:

```
curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md
```

The guide covers: checking for an existing install, bootstrapping `uv`,
installing from PyPI, persisting `~/.local/bin` on PATH, and registering
the skill into agent directories. Don't summarize it; read it end to end.

### Skill registration

Install the skill as a symlink into an agent skill directory:

```bash
mcs skill install        # local .agents/skills/ by default
mcs skill install -g     # global ~/.agents/skills/ by default
mcs skill install --all  # install to every supported platform path
mcs skill install --detect -g  # install only for detected agents
```

Supported platform names for `mcs skill install -p <platform>`:
`agents`, `claude-code`, `cursor`, `codex`, `gemini-cli`,
`qwen-code`, and `opencode`. Deprecated aliases `gemini` and `qwen`
still work but emit a warning.

## Configure A Profile

Interactive setup:

```bash
mcs profile create
mcs link bind <profile-name>
```

Scripted setup:

```bash
mcs profile spec-template > profile.yaml
mcs profile create --from-file @profile.yaml
mcs link bind <profile-name>
```

Profiles live in `~/.config/maxcompute-semantic/profiles.yaml` unless
`MCS_CONFIG_DIR` overrides the config root. `mcs link bind` stores a
cwd-to-profile binding in `link.json`.

For CI or one-off shells, you can skip a saved profile and use the
standard ODPS environment variables:

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
export MAXCOMPUTE_ENDPOINT=https://service.<region>.maxcompute.aliyun.com/api
export MAXCOMPUTE_PROJECT=<compute-project>
```

## Profile Resolution

Commands that support profiles resolve context in this order:

1. Explicit `--profile NAME`.
2. `MCS_PROFILE=NAME`.
3. The cwd binding written by `mcs link bind NAME`.
4. The standard ODPS environment variables as an in-memory profile.

## Common Commands

```bash
# profile and identity
mcs profile list
mcs profile show [NAME]
mcs profile update <NAME>
mcs profile whoami [NAME]
mcs link status
mcs link unlink

# semantic package
mcs build
mcs build --refresh
mcs status --tables
mcs show
mcs show --table <TABLE>

# semantic maintenance
mcs package propose --from-suggestions
mcs package list-proposals
mcs package apply <id>
mcs metric list
mcs metric add total_revenue --expression 'SUM(orders.amount)'
mcs metric show total_revenue

# metadata and SQL
mcs meta list-tables
mcs meta describe-table <TABLE>
mcs sql review 'SELECT ...'
mcs sql cost 'SELECT ...'
mcs sql explain 'SELECT ...'
mcs sql execute 'SELECT ...'

# memory
mcs memory recall --project <P> '<question>'
mcs memory verify --project <P> --question '<question>' --sql '<SQL>' --tables <T1,T2>

# skill and UDFs
mcs skill list
mcs skill diff --all
mcs udf list --project <P>
```

Run `mcs <command> --help` for the current option surface.

## Data Layout

Config and built semantic packages are separate:

```text
~/.config/maxcompute-semantic/
├── profiles.yaml
└── link.json

<XDG_DATA_HOME>/maxcompute-semantic/data/<profile-name>/
├── package.db
├── _overview.md
├── _joins.md
├── _state.json
├── _udfs.md
├── tier_cache/
└── <source_key>/              # per-source subdirectory (e.g. acme__warehouse/)
    └── <table>.md
```

`profile.package_path` can override the per-profile package directory.

## Development

From the repository root:

```bash
uv sync --extra dev
uv run pytest tests/ -m 'not live'
uv run ruff check src/ tests/
uv run mypy src/
```

## License

maxcompute-semantic is developed by Alibaba Cloud and licensed under the
Apache License (Version 2.0). This product contains various third-party
components under other open source licenses. See the NOTICE file for more
information.
