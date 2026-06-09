from maxcompute_semantic.versioning.gitignore_default import PROFILE_GITIGNORE


def test_gitignore_lists_the_documented_paths() -> None:
    """The four leaf patterns from the spec's gitignore section all
    appear as standalone lines in the constant. Comments and blank
    lines between them are allowed and not asserted on, so the
    spec's prose can shift without breaking this test."""
    lines = {line.strip() for line in PROFILE_GITIGNORE.splitlines()}
    required = {
        "package.db",
        "package.db-journal",
        "package.db-wal",
        "package.db-shm",
        "tier_cache/",
        ".mcs-lock",
        ".DS_Store",
        "Thumbs.db",
    }
    missing = required - lines
    assert not missing, f"gitignore is missing entries: {missing!r}"


def test_gitignore_ends_with_newline() -> None:
    """The constant ends with a single trailing newline so a tool
    that opens the file appending more entries doesn't end up with
    the new content concatenated to the last existing line."""
    assert PROFILE_GITIGNORE.endswith("\n")
    assert not PROFILE_GITIGNORE.endswith("\n\n"), (
        "the trailing whitespace shape should be exactly one ``\\n`` — "
        "git tools handle that uniformly. Two trailing newlines is "
        "cosmetic noise that diff viewers render as a blank line at "
        "end-of-file and is annoying in PR reviews."
    )
