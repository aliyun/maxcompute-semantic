---
name: report-issue
description: Report a bug or feature request to the maxcompute-semantic GitHub repository.
---

# Report Issue

File a bug report or feature request on GitHub.

## Prerequisites

The `gh` CLI must be installed and authenticated: `gh auth status`

If not authenticated, ask the user to run `gh auth login` first.

## Workflow

1. **Classify**: bug, feature request, or question.
2. **Gather** from conversation: title, repro steps, expected vs actual, `mcs --version`.
3. **Create**:

```bash
gh issue create \
  --repo aliyun/maxcompute-semantic \
  --title "[bug] <title>" \
  --body "## Description
<what happened>

## Steps to reproduce
1. ...

## Expected behavior
<expected>

## Environment
- mcs version: <mcs --version>
- OS / Python version

## Additional context
<error envelopes, mcs doctor output, etc.>" \
  --label bug
```

For feature requests: `--label enhancement`, title prefix `[feat]`.

4. **Report back**: show the issue URL to the user.
