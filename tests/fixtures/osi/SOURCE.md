# OSI Schema Vendoring Record

`osi-schema.json` is vendored from:

- Upstream: https://github.com/open-semantic-interchange/OSI
- Path: `core-spec/osi-schema.json`
- Pinned commit: `b1f67b5c8ee9a227bbab5f81c6bbcf05e19b9e1d`
- Commit date: `2026-05-21T06:49:19Z`
- Schema version (const on `properties.version`): `0.2.0.dev0`
- Vendored on: 2026-05-26

## Bump procedure

1. Re-download from the same path at the new commit.
2. Run `pytest packages/maxcompute-semantic/tests/unit/osi/` and fix any mapping drift in `src/maxcompute_semantic/osi/vocabulary.py` and `src/maxcompute_semantic/osi/export.py`.
3. Bump `OSI_SCHEMA_VERSION` in `src/maxcompute_semantic/osi/__init__.py` to the new schema `version` field.
4. Regenerate the golden file by re-running the snippet in plan Task 10 step 4.
5. Update this file's pinned commit + date.
6. CHANGELOG entry under `[Unreleased]`.
