"""Profile git-versioning subpackage.

Public re-exports for the versioning layer. T1 (sibling task) lands
``WriteLock`` and the lock-related errors alongside the entries
below; T3 (this task) lands the ``package.sql`` round-trip helpers.
Downstream tasks add ``commit_after_command``, ``GitRepo``, and the
fork helpers; each task appends to ``__all__`` rather than
replacing it so the parallel sub-agent wave can converge without
overwriting each other's exports.
"""

from __future__ import annotations

# T2 lands this minimal version. Each downstream task appends its
# new symbols to the imports + __all__ when the corresponding module
# materializes — the "intended final shape" comment block in the
# plan lists where each comes from so the engineer can update the
# __init__.py incrementally without having to re-derive the layout.
from maxcompute_semantic.versioning.env import (
    is_git_available,
    is_versioning_disabled,
    warn_git_missing_once,
)
from maxcompute_semantic.versioning.errors import (
    GitNotAvailable,
    LockedByOtherProcessError,
    PackageSqlCorrupt,
    ProfileReadOnly,
    StaleLockClearedWarning,
)
from maxcompute_semantic.versioning.forks import (
    parent_repo,
    register_fork,
    unregister_fork,
)
from maxcompute_semantic.versioning.git_repo import (
    CommitInfo,
    GitRepo,
    WorktreeInfo,
)
from maxcompute_semantic.versioning.gitignore_default import PROFILE_GITIGNORE
from maxcompute_semantic.versioning.hook import (
    ACTION_BUILD,
    ACTION_INIT,
    ACTION_MEMORY_PREFIX,
    ACTION_METRIC_PREFIX,
    ACTION_PACKAGE_PREFIX,
    ACTION_RECOVER,
    ACTION_REFRESH,
    ACTION_UDF_PREFIX,
    commit_after_command,
    commit_if_uncommitted_on_entry,
)
from maxcompute_semantic.versioning.hook import (
    _reject_if_fork as reject_if_fork,
)
from maxcompute_semantic.versioning.lock import WriteLock
from maxcompute_semantic.versioning.sql_dump import (
    dump_db_to_sql,
    restore_sql_to_db,
)

__all__ = [
    "ACTION_BUILD",
    "ACTION_INIT",
    "ACTION_MEMORY_PREFIX",
    "ACTION_METRIC_PREFIX",
    "ACTION_PACKAGE_PREFIX",
    "ACTION_RECOVER",
    "ACTION_REFRESH",
    "ACTION_UDF_PREFIX",
    "PROFILE_GITIGNORE",
    "CommitInfo",
    "GitNotAvailable",
    "GitRepo",
    "LockedByOtherProcessError",
    "PackageSqlCorrupt",
    "ProfileReadOnly",
    "StaleLockClearedWarning",
    "WorktreeInfo",
    "WriteLock",
    "commit_after_command",
    "commit_if_uncommitted_on_entry",
    "dump_db_to_sql",
    "is_git_available",
    "is_versioning_disabled",
    "parent_repo",
    "register_fork",
    "reject_if_fork",
    "restore_sql_to_db",
    "unregister_fork",
    "warn_git_missing_once",
]
