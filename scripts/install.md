# Installing mcs — for LLM Agents

You are an AI agent. Your task is to install the `mcs` command-line tool on the
user's machine by following the steps below. Do not ask the user to choose
between install methods — detect what is available and proceed.

Before executing a remote installer or the final `uv tool install` command, show
the user the exact command that will be executed so they can see what is about
to run.

## Step 1: Check for an existing install

Run `mcs --version`. If it prints a version string, tell the user mcs is already
installed and suggest `mcs update` to upgrade instead of reinstalling. If the
command is not found, continue to step 2.

## Step 2: Ensure uv is on PATH

Check whether `uv` is on PATH by running `command -v uv` (macOS / Linux) or
`Get-Command uv` (Windows PowerShell). If uv is available, use it directly.

If uv is **not** available, install it from astral.sh:

- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `irm https://astral.sh/uv/install.ps1 | iex`

This is a third-party bootstrapper, not an mcs artifact. Show the exact command
to the user and ask for confirmation before running it. If the user declines,
stop and tell them to install `uv` manually, then re-run the mcs install flow.

The installer drops the `uv` binary in `~/.local/bin` (macOS / Linux) or
`%USERPROFILE%\.local\bin` (Windows). Prepend that directory to PATH for the
current shell session so the next step finds it:

- bash/zsh: `export PATH="$HOME/.local/bin:$PATH"`
- fish: `set -x PATH $HOME/.local/bin $PATH`
- PowerShell: `$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"`

Then proceed.

Do not fall back to `pip --user` — Debian 12 / Ubuntu 22.04+ enforce PEP 668
and refuse the `--user` install with an externally-managed-environment error,
and `--break-system-packages` would pollute the distro Python.

## Step 3: Install from PyPI

Run:

```
uv tool install maxcompute-semantic
```

If the user asked for a specific version, use:

```
uv tool install maxcompute-semantic==<version>
```

Accept version strings like `0.16.1` or `0.17.0a1` — the format is PEP 440.

To upgrade an existing install to the latest version:

```
uv tool install --reinstall maxcompute-semantic
```

PyPI handles version resolution, integrity verification (PEP 691 + TLS), and
wheel hosting natively — no separate metadata fetch or digest check is needed.

Do not use a shell wrapper — pass the command and its arguments directly as an
exec or subprocess call. If the command exits non-zero, stop and show the user
the error output. The previous install, if any, is not affected because uv
replaces the tool environment atomically.

## Step 4: Verify the install

Run `mcs --version`. If the command is not found, the console script installed
by `uv tool install` is not on PATH. The default location is:

- macOS / Linux: `~/.local/bin/mcs`
- Windows: `%USERPROFILE%\.local\bin\mcs.exe`

First, add it to the current session so the remaining steps work:

- bash/zsh: `export PATH="$HOME/.local/bin:$PATH"`
- fish: `set -x PATH $HOME/.local/bin $PATH`
- PowerShell: `$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"`

Then persist the change so future shells also find `mcs`. Check whether the
user's shell rc already contains `~/.local/bin` before appending — if it does,
skip the write.

- bash: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`
- zsh: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`
- fish: `fish_add_path ~/.local/bin` (built-in, persists automatically)
- PowerShell: guide the user to add `$env:USERPROFILE\.local\bin` to the
  system PATH via System Properties → Environment Variables, or run:
  `[Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.local\bin;$([Environment]::GetEnvironmentVariable('Path','User'))", "User")`

On Windows you can also run `Get-Command mcs` to confirm the actual path.

If `mcs --version` still cannot be found after the above, tell the user the
install appears to have succeeded (the wheel was written to uv's tool dir)
but the console script is not on PATH. Show the directory and suggest they
verify and source their shell rc.

## Step 5: Link the skill into the user's AI agent (optional)

After a successful install, the `mcs` CLI can register its skill bundle
(SKILL.md) into the user's AI agent slots so those agents learn how to call mcs
for MaxCompute SQL. Run `mcs skill install -p claude-code -g` to install for
Claude Code globally. For other agents, replace `claude-code` with the matching
platform name (`cursor`, `codex`, `gemini-cli`, `qwen-code`, `opencode`, etc.).

Run `mcs skill install --all -g` to install for every supported platform at
once, or `mcs skill install --detect -g` to install only for agents detected on
the machine.

Skip this step entirely if the user explicitly asked not to install the skill,
or if the environment variable `MCS_NO_SKILL_INSTALL` is set to 1, true, yes, or
on.

## Env-var overrides

The user may have set environment variables that change the behavior of the
steps above. Check for these before starting and adjust accordingly:

- `MCS_SKILL_PLATFORMS` — if set, pass its value (a comma-separated list, or
  `all`) to `mcs skill install -p <value> -g` in step 5 instead of the default
  `claude-code`.
- `MCS_NO_SKILL_INSTALL` — if set to a truthy value (1, true, yes, on), skip
  step 5 entirely. This is useful for CI runners and headless machines that do
  not run an AI agent.

The truthy check for `MCS_NO_SKILL_INSTALL` is case-insensitive: 1, true, TRUE,
yes, Yes, on, ON all mean "skip."

## Failure-mode reference

Use these mappings to diagnose problems without guessing:

- If `mcs --version` says `0+unknown` or the command is missing entirely: the
  console script was not registered correctly. The most common cause is that
  `~/.local/bin` (or `%USERPROFILE%\.local\bin` on Windows) is not on PATH.
  Re-check step 4.
- If `uv tool install` fails with a network error: the machine may be behind a
  corporate proxy. Check `HTTP_PROXY` / `HTTPS_PROXY` in the environment and
  ensure outbound HTTPS to `pypi.org` is allowed.
- If the installer complains `--reinstall is not a valid flag`: the uv version is
  too old (before 0.2.0). Upgrade uv and retry.
- If `uv tool install` fails with a resolver error mentioning Python version:
  `maxcompute-semantic` requires Python ≥ 3.10. Run `python3 --version` to
  check, and if needed use `uv tool install --python 3.12 maxcompute-semantic`.
