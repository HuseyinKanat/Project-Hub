"""Git integration package.

Exports the hardened reader (G2) for use by G3-G5 consumers.
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

__all__ = [
    "BranchInfo",
    "CommitFileChange",
    "CommitInfo",
    "GitReaderError",
    "NotARepository",
    "RepoNotFound",
    "RepoPathOutsideAllowlist",
    "acommit_files",
    "alist_branches",
    "aopen_repo",
    "awalk_commits",
    "commit_files",
    "list_branches",
    "open_repo",
    "walk_commits",
]
