"""The standard contents of a profile's ``.gitignore``.

The constant is the source of truth for what gets committed to a
profile's per-directory git history vs what's runtime/binary noise
filtered out. The matching "what files mcs writes to the data
directory" list is the surface area of ``build.markdown``,
``build.storage`` (``package.db``), and ``versioning.lock``
(``.mcs-lock``); the standard mcs writes are the complement of
the patterns below.

The constant ends with a trailing newline so files written from
it look right under ``cat`` / ``less``.

Spec source: the "Architecture / .gitignore (committed at init)"
section of ``2026-05-23-mcs-profile-git-versioning-design.md``.
"""

PROFILE_GITIGNORE = """\
# Binary SQLite — regenerable from package.sql via lazy materialize
package.db
package.db-journal
package.db-wal
package.db-shm

# Runtime caches (per-project tier sentinel etc.)
tier_cache/

# Concurrency lock — body file (PID hint) + filelock OS anchor sibling
.mcs-lock
.mcs-lock.flock

# OS noise
.DS_Store
Thumbs.db
"""
