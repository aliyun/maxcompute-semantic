## What does this PR do?

<!-- What problem does it solve? Why is this the right approach? -->

## How to test

<!-- Steps for a reviewer to verify, or "CI only" if no manual steps needed. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behavior change)
- [ ] CI / infrastructure
- [ ] Documentation

## Checklist

- [ ] Lint, type check, tests, overall coverage, and diff coverage pass locally/CI (`uv run ruff check src/ tests/`, `uv run mypy src/`, `uv run pytest tests/ -m 'not live' --cov=maxcompute_semantic --cov-report=xml --cov-fail-under=85`, `uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=80`)
- [ ] SPDX license headers added to new `.py` files
- [ ] Related issue linked below (if applicable)

Fixes #
