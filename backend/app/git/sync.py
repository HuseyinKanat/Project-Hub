"""G3 — git cache sync service.

``sync_repo(session, board)`` is the single entry-point for G3. It:

1. Looks up the Repository row for the board; returns SyncResult(skipped=True)
   if none is configured (no git I/O performed).
2. Opens the local repo via the hardened G2 reader.
3. Upserts git_branches (delete stale branches that have vanished).
4. Walks commits since last_synced_sha (delta) or up to git_backfill_limit
   (initial/force-push fallback).
5. For each new commit: inserts git_commits + git_commit_files (ON CONFLICT DO
   NOTHING for idempotency); for each ticket key in the commit message: inserts
   git_commit_tickets (unique constraint = dedupe gate); if freshly inserted,
   writes a git_commit_linked TicketHistory row and publishes via EventBus.
6. Updates repository.last_synced_sha / last_synced_at.
7. Publishes a board-scoped git_synced EventEnvelope with ticket_id='system'
   sentinel (reusing the existing system_degradation sentinel pattern from bus.py).
8. Returns SyncResult.

Safety contract (G3 scope):
- sync.py NEVER calls any write-side git function (no checkout, pull, stash).
- All git I/O goes through the read-only G2 reader.
- Upserts use INSERT … ON CONFLICT DO NOTHING for idempotency.
- Force-push (unreachable since_sha) is caught; sync falls back to full backfill.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from git.exc import GitCommandError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Board, GitBranch, GitCommit, GitCommitFile
from app.events.bus import EventBus, EventEnvelope
from app.git._linkage import (
    enrich_commit_row,
    ensure_commit_ticket_link,
    find_ticket_by_key,
    get_system_actor_id,
    insert_ignore,
)
from app.git.parser import TICKET_KEY_RE, parse_commit
from app.git.reader import (
    BranchInfo,
    CommitInfo,
    acommit_files,
    alist_branches,
    aopen_repo,
    awalk_commits,
)
from app.services.history import write_history
from app.services.repositories import get_repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a single sync_repo() call."""

    skipped: bool = False
    """True when no Repository is configured for the board (no git I/O performed)."""

    new_commits: int = 0
    """Number of git_commits rows inserted in this run (0 = already cached)."""

    updated_branches: int = 0
    """Branch rows inserted or updated (head_sha changed)."""

    deleted_branches: int = 0
    """Branch rows removed (branch no longer exists in the repo)."""

    linked_tickets: int = 0
    """New (commit, ticket) pairs inserted into git_commit_tickets (= history writes)."""

    last_synced_sha: str | None = None
    """SHA of the default-branch HEAD after sync (None if repo has no commits)."""


# ---------------------------------------------------------------------------
# Core sync function
# ---------------------------------------------------------------------------


async def sync_repo(session: AsyncSession, board: Board) -> SyncResult:
    """Sync git cache tables for ``board`` from its configured local repo.

    This is the G3 entry-point.  Callers are:
    - G6 manual refresh endpoint (not yet built — stub caller in tests).
    - G6 git post-commit hook IPC (not yet built).

    Returns:
        SyncResult describing what happened.  Always returns without raising
        (force-push, missing repo, Git errors are handled internally).
    """
    settings = get_settings()

    # Step 1: resolve the Repository row.
    repo_row = await get_repository(session, board)
    if repo_row is None:
        logger.debug("sync_repo: no repository configured for board %s — skip", board.key)
        return SyncResult(skipped=True)

    # Step 2: open the repo via the hardened G2 reader.
    gitrepo = await aopen_repo(repo_row.local_path)

    # Step 3: fetch current branches.
    branches: list[BranchInfo] = await alist_branches(gitrepo)

    # Upsert branches -------------------------------------------------------
    upserted_branch_names: list[str] = []
    existing_branch_rows = (
        await session.execute(
            select(GitBranch).where(GitBranch.repo_id == repo_row.id)
        )
    ).scalars().all()
    existing_by_name: dict[str, GitBranch] = {b.name: b for b in existing_branch_rows}

    current_names: set[str] = set()
    for bi in branches:
        current_names.add(bi.name)
        # Derive last_commit_at from the commit object (one cheap GitPython lookup).
        try:
            commit_dt: datetime | None = (
                gitrepo.commit(bi.head_sha).committed_datetime.astimezone(UTC)
            )
        except Exception:
            commit_dt = None

        # Derive ticket_key from branch name.
        keys_in_name = TICKET_KEY_RE.findall(bi.name.upper())
        ticket_key: str | None = keys_in_name[0] if keys_in_name else None

        if bi.name in existing_by_name:
            row = existing_by_name[bi.name]
            changed = (
                row.head_sha != bi.head_sha
                or row.is_default != bi.is_default
                or row.ticket_key != ticket_key
            )
            if changed:
                row.head_sha = bi.head_sha
                row.is_default = bi.is_default
                row.last_commit_at = commit_dt
                row.ticket_key = ticket_key
                upserted_branch_names.append(bi.name)
        else:
            new_branch = GitBranch(
                id=uuid.uuid4(),
                repo_id=repo_row.id,
                name=bi.name,
                head_sha=bi.head_sha,
                is_default=bi.is_default,
                last_commit_at=commit_dt,
                ticket_key=ticket_key,
            )
            session.add(new_branch)
            upserted_branch_names.append(bi.name)

    # Delete stale branches (in DB but not in repo anymore).
    vanished_names: list[str] = [
        name for name in existing_by_name if name not in current_names
    ]
    if vanished_names:
        await session.execute(
            delete(GitBranch).where(
                GitBranch.repo_id == repo_row.id,
                GitBranch.name.in_(vanished_names),
            )
        )
    await session.flush()

    # Step 4: walk commits (delta or backfill).
    since = repo_row.last_synced_sha
    try:
        raw_commits: list[CommitInfo] = await awalk_commits(
            gitrepo,
            refs=None,
            limit=settings.git_backfill_limit,
            since_sha=since,
        )
    except GitCommandError:
        # Force-push or unreachable SHA — fall back to full backfill.
        logger.warning(
            "sync_repo: GitCommandError walking since_sha=%s for repo %s — "
            "falling back to full backfill (force-push?)",
            since,
            repo_row.id,
        )
        try:
            raw_commits = await awalk_commits(
                gitrepo,
                refs=None,
                limit=settings.git_backfill_limit,
                since_sha=None,
            )
        except GitCommandError:
            logger.error(
                "sync_repo: GitCommandError even on full backfill for repo %s — abort",
                repo_row.id,
            )
            return SyncResult(skipped=False)

    # Walk oldest-first so parent commits are cached before children (locality).
    commits_oldest_first: list[CommitInfo] = list(reversed(raw_commits))

    # Determine default branch name for "branch" metadata on history rows.
    default_branch_name: str = next(
        (b.name for b in branches if b.is_default),
        repo_row.default_branch,
    )

    # System actor for history writes.
    system_actor_id: Any = await get_system_actor_id(session, board)

    new_commit_shas: list[str] = []
    linked_count = 0

    # Step 5: process each commit.
    for ci in commits_oldest_first:
        parsed = parse_commit(
            sha=ci.sha,
            message=ci.summary + ("\n" + ci.body if ci.body else ""),
            author=ci.author_name,
            url="",
        )

        commit_values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "repo_id": repo_row.id,
            "sha": ci.sha,
            "short_sha": ci.sha[:8],
            "parents": list(ci.parents),
            "author_name": ci.author_name,
            "author_email": ci.author_email,
            "authored_at": ci.authored_at,
            "committer_name": ci.committer_name,
            "committer_email": ci.committer_email,
            "committed_at": ci.committed_at,
            "summary": ci.summary,
            "body": ci.body,
            "is_conventional": parsed.is_conventional,
            "commit_type": parsed.commit_type,
            "ticket_keys": parsed.ticket_keys,
        }

        is_new_commit = await insert_ignore(
            session, GitCommit, commit_values, ["repo_id", "sha"]
        )

        commit_uuid: uuid.UUID = commit_values["id"]

        if not is_new_commit:
            # A row already exists for (repo_id, sha). This is either a fully
            # cached commit (prior sync — skip to avoid redundant I/O) OR a
            # webhook-first stub that never received its files/parents/committer.
            # PH-166: enrich the stub in place instead of skipping past it, so
            # webhook+sync boards don't end up with 0-file / parents=[] commits
            # that corrupt commit-detail views and branch-graph ahead/behind BFS.
            enriched = await enrich_commit_row(
                session,
                repo_id=repo_row.id,
                commit_values=commit_values,
            )
            if enriched is None:
                # Already fully cached (has file rows) — nothing to repair.
                continue
            # Stub enriched: reuse the existing row's id for the file rows below
            # (the row identity is preserved so the git_commit_tickets FK stays
            # valid). Fall through to file fetch + linkage.
            commit_uuid = enriched.id
        else:
            new_commit_shas.append(ci.sha)

        # Fetch and insert file changes for this new (or freshly enriched) commit.
        try:
            file_changes = await acommit_files(gitrepo, ci.sha)
        except Exception as exc:
            logger.warning("sync_repo: acommit_files failed for %s: %s", ci.sha[:8], exc)
            file_changes = []

        file_rows: list[GitCommitFile] = [
            GitCommitFile(
                id=uuid.uuid4(),
                commit_id=commit_uuid,
                path=fc.path,
                old_path=fc.old_path,
                change_type=fc.change_type,
                additions=fc.additions,
                deletions=fc.deletions,
                is_binary=fc.is_binary,
            )
            for fc in file_changes
        ]
        if file_rows:
            session.add_all(file_rows)
        await session.flush()

        # Link commit to tickets mentioned in the message.
        for key in parsed.ticket_keys:
            ticket = await find_ticket_by_key(session, key, board.id)
            if ticket is None:
                logger.debug(
                    "sync_repo: commit %s references unknown ticket %s on board %s",
                    ci.sha[:8],
                    key,
                    board.key,
                )
                continue

            # Route through the shared dedupe gate (same gate webhook.py uses).
            # The git_commits row already exists (inserted above for file rows),
            # so ensure_commit_ticket_link resolves it by (repo_id, sha) and only
            # performs the dedupe-gated junction insert.
            is_new_link = await ensure_commit_ticket_link(
                session,
                repo_id=repo_row.id,
                commit_factory=commit_values,
                ticket_id=ticket.id,
            )

            if not is_new_link:
                # Already linked (webhook or previous sync) — do NOT re-write history.
                continue

            linked_count += 1

            # Write git_commit_linked history (same shape as webhook.py).
            history = await write_history(
                session,
                ticket_id=ticket.id,
                actor_id=system_actor_id,
                event_type="git_commit_linked",
                metadata={
                    "sha": ci.sha,
                    "sha_short": ci.sha[:8],
                    "message": parsed.message,
                    "author": parsed.author,
                    "url": "",  # local origin — no GitHub link
                    "commit_type": parsed.commit_type,
                    "is_conventional": parsed.is_conventional,
                    "branch": default_branch_name,
                },
            )
            await session.flush()

            # Publish per-ticket event (same pattern as webhook.py).
            try:
                from app.events import publish_ticket_event

                await publish_ticket_event(history, ticket, None)
            except Exception as exc:
                logger.warning("sync_repo: publish_ticket_event failed: %s", exc)

    # Step 6: update last_synced_sha / last_synced_at on the repo row.
    default_head_sha: str | None = next(
        (b.head_sha for b in branches if b.is_default), None
    )
    repo_row.last_synced_sha = default_head_sha
    repo_row.last_synced_at = datetime.now(UTC)
    await session.flush()

    # Step 7: publish board-scoped git_synced envelope.
    envelope = EventEnvelope(
        event_id=str(uuid.uuid4()),
        type="git_synced",
        board_id=str(board.id),
        ticket_id="system",      # sentinel — reuses system_degradation pattern from bus.py
        ticket_key="SYSTEM",
        actor_id=None,
        payload={
            "new_commit_shas": new_commit_shas,
            "updated_branches": upserted_branch_names,
            "deleted_branches": vanished_names,
            "commits_count": len(new_commit_shas),
            "last_synced_sha": default_head_sha,
            "last_synced_at": repo_row.last_synced_at.isoformat(),
        },
        occurred_at=datetime.now(UTC).isoformat(),
    )
    try:
        await EventBus.publish(envelope)
    except Exception as exc:
        logger.warning("sync_repo: EventBus.publish(git_synced) failed: %s", exc)

    # Step 8: commit + return.
    await session.commit()

    return SyncResult(
        skipped=False,
        new_commits=len(new_commit_shas),
        updated_branches=len(upserted_branch_names),
        deleted_branches=len(vanished_names),
        linked_tickets=linked_count,
        last_synced_sha=default_head_sha,
    )
