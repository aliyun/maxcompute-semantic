---
name: report-issue
description: Use when the user wants to file an upstream bug / issue / feature request against the maxcompute-semantic skill itself ("report a bug", "file an issue", "提 issue", "file this upstream"). Routes to the internal Aone workitem tracker when the `a1` CLI is present, otherwise to GitHub Issues.
---

# Report an upstream issue for maxcompute-semantic

File a bug report or feature request upstream. There are two destinations and
the skill auto-selects based on the environment:

- **Internal (Aone)** — chosen when the `a1` CLI is installed (Alibaba-internal
  machines). The project still serves internal users; their reports land in the
  maintainer's Aone "My workitems" view.
- **GitHub** — chosen otherwise. The public upstream tracker.

**Always show the drafted title + body + destination to the user and get
confirmation before submitting. Never submit silently.**

## Choose the destination

1. Probe both CLIs:

   ```bash
   command -v a1; command -v gh
   ```

2. Route:
   - `a1` present → **internal Aone flow** (default for internal users).
   - else `gh` present → **GitHub flow**.
   - neither present → ask the user to install one (internal users: `a1`;
     external users: `gh`) — see each flow's prerequisites below.

3. The user may override the auto-choice (e.g. "file it on GitHub" even on an
   internal box). Honor the explicit request.

## When to trigger

- **Manual**: the user says "file an issue / report a bug / file this upstream / 提 issue".
- **Suggested**: when the conversation has clearly localized a defect, proactively
  offer to file one and wait for user confirmation. **Never submit silently.**

## Shared context to gather

Collect from the conversation regardless of destination:

- `title` — short, imperative, in the user's language. Do **not** include the
  `[maxcompute-semantic]` prefix in `{title}` itself — each flow adds its own.
- `category` — `bug` (default), feature request, or question/task.
- `summary` — one-line description.
- `repro` — failing command + observable symptom (for bugs).
- `root_cause` — if localized this session; otherwise `Unknown — needs triage`.
- `code_refs` — `path:line` entries touched this session.
- `plugin_version` — output of `mcs --version`. **Required**.
- `doctor_snapshot` — output of `mcs doctor` (plain form, not `-f json`).
  **Required** in every bug report. If `mcs doctor` errors, paste whatever it
  produced plus the error.

---

## Internal Aone flow

The workitem is created in **an Aone project the user has write access to**,
with `--related-space` pointing at the upstream project **2155299**
([Agent special project]) — the maintainer sees it in their personal
"My workitems" view. This lets internal teams report issues without needing
access to 2155299.

Use category `bug` for defects, `req` for feature requests, `task` for
miscellaneous tasks.

### Steps

0. **Preflight: confirm a1 CLI is installed**. Run `command -v a1`. If missing,
   **stop and ask the user** to install it:

   ```bash
   curl -fsSL https://git.cn-hangzhou.oss-cdn.aliyun-inc.com/aone-cli/install.sh | sh
   ```

   Docs: <https://a1.io.alibaba-inc.com/>. After install, the user must run
   `a1 auth login --buc` once before submitting.

1. **Pick the target project**:
   - User already named a project → use it.
   - Otherwise run `a1 project list --quiet` (add `--keyword <kw>` to filter),
     show the list, and ask which to file under. The ID is `<target_project>`.

2. **Show the drafted title and body to the user** for approval.

3. **Write the body to a temp file first, then submit.** Write straight into
   `/tmp/mcs-report-issue-<timestamp>.md`, **not** via a bash heredoc (heredocs
   are unreliable under `noclobber` — we've seen silent empty-body submissions).

   Body template:

   ```markdown
   ## Summary
   {summary}

   ## Reproduction
   - Plugin: maxcompute-semantic v{plugin_version}
   - Profile: {profile_name} (tier {2-level|3-level}, compute={compute_project})
   - Command: {command_or_NA}
   - Symptom: {observable}

   ### `mcs doctor` snapshot
   {doctor_snapshot}

   ## Root cause
   {root_cause}

   ## Code references
   - {file:line} — {what}
   ```

   Both the `Plugin` line and the `mcs doctor` block are **required**.

   Then submit:

   ```bash
   a1 project workitem create \
     --project <target_project> \
     --category {bug|req|task} \
     --related-space 2155299 \
     --assignee 292165 \
     --title "[maxcompute-semantic] {title}" \
     --body-file /tmp/mcs-report-issue-<timestamp>.md \
     --quiet
   ```

   `cat` the temp file once before submitting to confirm it isn't empty.

4. **Report back**: workitem ID + URL
   `https://project.aone.alibaba-inc.com/v2/project/<target_project>/{bug|req|task}/{id}`.

### Notes

- **`--related-space 2155299`** is this skill's identity. It does **not** require
  access to the target project, so the link lands even if the user can't read
  2155299. The target ID must reference a real Aone project (invalid IDs are
  silently discarded).
- **Default `--assignee 292165`** (`jiexian.hc`). Override when the user names
  someone else. Employee IDs avoid nickname ambiguity.
- **No `--tag`**: the `[maxcompute-semantic]` title prefix is the universal
  cross-project identifier and filter.
- Maintainer searches cross-project:
  `a1 project workitem list --scope personal --title "[maxcompute-semantic]"`
  (add `--category bug` for bugs only).
- a1 cannot delete workitems — only state transitions (Invalid / Won'tfix /
  Closed). Hard delete via web UI.
- Auth error → tell the user to run `a1 auth login --buc`.

---

## GitHub flow

### Prerequisites

The `gh` CLI must be installed and authenticated: `gh auth status`. If not
authenticated, ask the user to run `gh auth login` first.

### Steps

1. **Classify**: bug, feature request, or question.
2. **Show the drafted title and body to the user** for approval.
3. **Create**:

   ```bash
   gh issue create \
     --repo aliyun/maxcompute-semantic \
     --title "[bug] {title}" \
     --body "## Description
   {summary}

   ## Steps to reproduce
   1. ...

   ## Expected behavior
   <expected>

   ## Environment
   - mcs version: {plugin_version}
   - OS / Python version

   ## Additional context
   {doctor_snapshot}, error envelopes, code references, etc." \
     --label bug
   ```

   For feature requests: `--label enhancement`, title prefix `[feat]`.

4. **Report back**: show the issue URL to the user.
