"""G6 — Live refresh: RefreshRegistry + background git_poll_cron.

RefreshRegistry is an in-process singleton that coordinates per-repo
asyncio.Lock acquisition and debounce decisions.  It is shared between
the POST /git/refresh endpoint (immediate trigger) and the git_poll_cron
background task (periodic catch-up), ensuring a single repo is never
synced concurrently.

Scope: single-process (single uvicorn worker).  Multi-worker coordination
       requires a Redis SETNX registry; settings.redis_url is already wired
       for that future upgrade.  The contract (get_lock / should_coalesce /
       acquire_sync_lock) does not change — only the backing store.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Board, Repository
from app.db.session import SessionLocal
from app.git.sync import sync_repo

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# RefreshRegistry — per-repo lock + debounce coordinator
# ---------------------------------------------------------------------------


class RefreshRegistry:
    """In-process coordinator for per-repo asyncio.Lock + monotonic debounce.

    Thread-safety: asyncio single-threaded; no additional locking needed.
    Lifecycle: module-level singleton (same pattern as background_runner).
    """

    def __init__(self) -> None:
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        # monotonic timestamp of the last *dispatch* (not request) per repo.
        # Reset on process restart (intentional — volatile by design).
        self._last_request_monotonic: dict[uuid.UUID, float] = {}

    def get_lock(self, repo_id: uuid.UUID) -> asyncio.Lock:
        """Return the asyncio.Lock for repo_id, creating one on first access."""
        if repo_id not in self._locks:
            self._locks[repo_id] = asyncio.Lock()
        return self._locks[repo_id]

    def should_coalesce(self, repo_id: uuid.UUID, debounce_seconds: float) -> bool:
        """Return True if the last dispatch was within the debounce window.

        Uses monotonic clock so NTP jumps cannot cause spurious triggers.
        """
        last = self._last_request_monotonic.get(repo_id)
        if last is None:
            return False
        return (monotonic() - last) < debounce_seconds

    def mark_dispatched(self, repo_id: uuid.UUID) -> None:
        """Record now as the last dispatch time for debounce tracking."""
        self._last_request_monotonic[repo_id] = monotonic()

    @asynccontextmanager
    async def acquire_sync_lock(self, repo_id: uuid.UUID) -> AsyncIterator[None]:
        """Async context manager: acquire per-repo lock, yield, release.

        Lock is released even if the body raises (asyncio.Lock guarantees this
        via its own __aexit__).  No explicit try/finally required.
        """
        lock = self.get_lock(repo_id)
        async with lock:
            yield


# Module-level singleton — same lifecycle as background_runner.
registry = RefreshRegistry()


# ---------------------------------------------------------------------------
# _locked_sync_repo — shared worker used by endpoint + poller
# ---------------------------------------------------------------------------


async def _locked_sync_repo(board_id: uuid.UUID) -> None:
    """Open a fresh DB session, fetch Board, acquire per-repo lock, call sync_repo.

    Fresh session per call is intentional (matches stale_claim_cron pattern —
    session must not be reused across an await gap that includes asyncio.sleep).
    """
    async with SessionLocal() as session:
        board = await session.get(Board, board_id)
        if board is None:
            logger.warning("_locked_sync_repo: board_id=%s not found, skipping", board_id)
            return

        repo = (
            await session.execute(
                select(Repository).where(Repository.board_id == board_id)
            )
        ).scalar_one_or_none()
        if repo is None:
            logger.debug("_locked_sync_repo: no repo for board_id=%s, skipping", board_id)
            return

        async with registry.acquire_sync_lock(repo.id):
            try:
                result = await sync_repo(session, board)
                logger.info(
                    "_locked_sync_repo board=%s new_commits=%d linked=%d",
                    board.key,
                    result.new_commits,
                    result.linked_tickets,
                )
            except Exception as exc:
                logger.warning(
                    "_locked_sync_repo: sync_repo failed board=%s err=%s",
                    board.key,
                    exc,
                    exc_info=True,
                )


# ---------------------------------------------------------------------------
# git_poll_cron — lifespan background task
# ---------------------------------------------------------------------------


async def _scan_and_sync_due_repos() -> None:
    """One poll tick: find repos due for sync and sync them (lock-aware)."""
    settings = get_settings()
    interval = settings.git_poll_interval_seconds
    cutoff = datetime.now(UTC) - timedelta(seconds=interval)

    async with SessionLocal() as session:
        # Select Repository rows whose last_synced_at is stale OR NULL.
        due_repos = (
            await session.execute(
                select(Repository).where(
                    (Repository.last_synced_at.is_(None))
                    | (Repository.last_synced_at < cutoff)
                )
            )
        ).scalars().all()

        if not due_repos:
            logger.debug("git_poll_cron: no repos due for sync")
            return

        for repo in due_repos:
            lock = registry.get_lock(repo.id)
            if lock.locked():
                # A refresh-in-flight already covers this repo this tick.
                logger.debug(
                    "git_poll_cron: skip locked repo=%s (refresh in flight)", repo.id
                )
                continue

            board = await session.get(Board, repo.board_id)
            if board is None:
                logger.warning(
                    "git_poll_cron: orphaned repo=%s (no board), skipping", repo.id
                )
                continue

            async with registry.acquire_sync_lock(repo.id):
                try:
                    result = await sync_repo(session, board)
                    logger.info(
                        "git_poll_cron: synced board=%s new_commits=%d linked=%d",
                        board.key,
                        result.new_commits,
                        result.linked_tickets,
                    )
                except Exception as exc:
                    logger.warning(
                        "git_poll_cron: sync_repo failed repo=%s err=%s",
                        repo.id,
                        exc,
                        exc_info=True,
                    )


async def git_poll_cron() -> None:
    """Lifespan background task: periodically sync repos due for polling.

    Mirrors stale_claim_cron shape exactly:
    - CancelledError breaks the loop cleanly on shutdown.
    - Other exceptions are swallowed per-tick so one bad repo can't kill the loop.
    - Disable conditions: git_refresh_enabled=False OR git_poll_interval_seconds<=0
      (checked by lifespan before creating the task — cron itself does not check).
    """
    settings = get_settings()
    interval = settings.git_poll_interval_seconds
    logger.info("git_poll_cron started interval=%ds", interval)

    while True:
        try:
            await asyncio.sleep(interval)
            await _scan_and_sync_due_repos()
        except asyncio.CancelledError:
            logger.info("git_poll_cron stopped")
            break
        except Exception as exc:
            logger.warning("git_poll_cron error=%s", exc)
