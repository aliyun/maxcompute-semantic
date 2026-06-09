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
current shell session so the next step finds it. Then proceed.

Do not fall back to `pip --user` — Debian 12 / Ubuntu 22.04+ enforce PEP 668
and refuse the `--user` install with an externally-managed-environment error,
and `--break-system-packages` would pollute the distro Python.

## Step 3: Decide the version to install

The default is to fetch the latest version from the publisher. Read the JSON
metadata from this URL:

  https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com/latest.json

The response has fields named `latest_version` and `sha256`. Use
`latest_version` as the version string and keep `sha256` for the wheel digest
check in step 5. The digest must be a 64-character hex SHA256.

However, if the user asked for a specific version, use that instead and skip the
HTTP fetch entirely. Accept version strings like `0.5.0a1` — the format is
PEP 440. Tell the user that this pinned path bypasses the publisher digest from
`latest.json` and should only be used when they trust the requested version and
base URL.

If the metadata URL is unreachable (DNS failure, connection refused, timeout
after 5 seconds, or HTTP 4xx/5xx) and the user did not specify a version, stop
and tell the user to either check their network and retry, or set the version
manually and re-ask.

## Step 4: Build the wheel URL

The wheel filename follows PEP 427: the project name `maxcompute-semantic` is
normalized to `maxcompute_semantic` (hyphen becomes underscore) in the wheel
filename. With the version from step 3, construct the URL:

  https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com/wheels/maxcompute_semantic-<version>-py3-none-any.whl

For example, version `0.5.0a1` becomes:

  https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com/wheels/maxcompute_semantic-0.5.0a1-py3-none-any.whl

## Step 5: Verify the wheel and run the installer

If the version came from `latest.json`, download the wheel to a temporary file,
compute its SHA256, and compare it to `latest.json.sha256`. If the digest does
not match, stop immediately and do not run `uv`.

If the user pinned a version manually, there is no publisher digest in this
flow; continue with the remote wheel URL after warning the user as described in
step 3.

Run `uv tool install --reinstall <verified-wheel-path-or-wheel-url>` with the
verified local wheel path when a digest was available, or the remote URL for a
pinned version.

Do not use a shell wrapper — pass the command and its arguments directly as an
exec or subprocess call. If the command exits non-zero, stop and show the user
the error output. The previous install, if any, is not affected because uv
replaces the tool environment atomically.

## Step 6: Verify the install

Run `mcs --version`. If the command is not found, prepend `~/.local/bin` (macOS
/ Linux) or `%USERPROFILE%\.local\bin` (Windows) to PATH — that is where `uv
tool install` puts its console scripts. On Windows you can also locate
`mcs.exe` with `Get-Command mcs` to confirm the path before adding it to PATH.

If `mcs --version` still cannot be found, tell the user the install appears to
have succeeded (the wheel was written to uv's tool dir) but the console script
is not on PATH. Show the directory and tell the user to add it to their shell
rc or system PATH.

## Step 7: Link the skill into the user's AI agent (optional)

After a successful install, the `mcs` CLI can register its skill bundle
(SKILL.md) into the user's AI agent slots so those agents learn how to call mcs
for MaxCompute SQL. Run `mcs skill install -p claude-code -g` to install for
Claude Code globally. For other agents, replace `claude-code` with the matching
platform name. Run `mcs skill install --all -g` to install for every supported
platform at once.

Skip this step entirely if the user explicitly asked not to install the skill,
or if the environment variable `MCS_NO_SKILL_INSTALL` is set to 1, true, yes, or
on.

## Env-var overrides

The user may have set environment variables that change the behavior of the
steps above. Check for these before starting and adjust accordingly:

- `MCS_VERSION` — if set, skip the HTTP fetch in step 3 and use this value
  directly as the version string.
- `MCS_INSTALL_BASE_URL` — if set, replace `https://maxcompute-semantic.oss-cn-beijing.aliyuncs.com`
  with this value in steps 3 and 4.
- `MCS_ALLOW_UNTRUSTED_INSTALL_BASE_URL` — non-default HTTPS base URLs require
  this to be truthy. `http://`, `file://`, and other non-HTTPS schemes are
  always refused.
- `MCS_SKILL_PLATFORMS` — if set, pass its value (a comma-separated list, or
  `all`) to `mcs skill install -p <value> -g` in step 7 instead of the default
  `claude-code`.
- `MCS_NO_SKILL_INSTALL` — if set to a truthy value (1, true, yes, on), skip
  step 7 entirely. This is useful for CI runners and headless machines that do
  not run an AI agent.

The truthy check for `MCS_NO_SKILL_INSTALL` is case-insensitive: 1, true, TRUE,
yes, Yes, on, ON all mean "skip."

## Failure-mode reference

Use these mappings to diagnose problems without guessing:

- If `mcs --version` says `0+unknown` or the command is missing entirely: the
  console script was not registered correctly. The most common cause is that
  `~/.local/bin` (or `%USERPROFILE%\.local\bin` on Windows) is not on PATH.
  Re-check step 6.
- If the metadata fetch in step 3 fails with a DNS or connection error: the
  machine may be behind a corporate proxy. Check `HTTP_PROXY` / `HTTPS_PROXY` in
  the environment.
- If the metadata fetch succeeds but `latest_version` or `sha256` is empty,
  missing, or malformed: the publisher's `latest.json` is malformed. Stop and
  tell the user to retry later or pin a version with `MCS_VERSION` after they
  accept the no-digest warning.
- If the wheel SHA256 does not match `latest.json.sha256`: stop immediately.
  Do not install the wheel. Tell the user the publisher artifact may be stale,
  corrupted, or tampered with.
- If the installer complains `--reinstall is not a valid flag`: the uv version is
  too old (before 0.2.0). Upgrade uv and retry.
- If `uv tool install` itself fails (network error, sandboxed shell that
  blocks subprocess launching, etc.): show the user the error and tell them
  to ensure they have outbound HTTPS to PyPI and the publisher OSS bucket.
  The fall-back of `pip --user` is no longer offered because PEP 668 makes it
  unusable on modern Debian / Ubuntu.
