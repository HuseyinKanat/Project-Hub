"""Git integration package.

Exports the hardened reader (G2) for use by G3-G5 consumers,
and the G3 sync service.
"""

from app.git.reader import (
    BranchInfo,
    CommitFileChange,
    CommitInfo,
    GitReaderError,
    NotARepository,
    RepoNotFound,
    RepoPathOutsideAllowlist,
    acommit_files,
    alist_branches,
    aopen_repo,
    awalk_commits,
    commit_files,
    list_branches,
    open_repo,
    walk_commits,
)
from app.git.sync import SyncResult, sync_repo

__all__ = [
    "BranchInfo",
    "CommitFileChange",
    "CommitInfo",
    "GitReaderError",
    "NotARepository",
    "RepoNotFound",
    "RepoPathOutsideAllowlist",
    "SyncResult",
    "acommit_files",
    "alist_branches",
    "aopen_repo",
    "awalk_commits",
    "commit_files",
    "list_branches",
    "open_repo",
    "sync_repo",
    "walk_commits",
]
