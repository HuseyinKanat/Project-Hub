"""Chunked-upload GC cron (PH-341): reap abandoned attachment upload sessions.

A companion to ``stale_claims`` for the ``add_attachment_begin/chunk/commit``
protocol. A remote agent that opens an upload session (``begin`` + some chunks)
but never ``commit``s would otherwise leave a row + up to 25 MiB of staging bytes
on disk forever. This background task sweeps every session whose ``expires_at``
(= ``begin`` + ``attachment_upload_ttl_seconds``) has passed — deleting the row AND
unlinking its staging blob (best-effort, log-not-raise, mirroring
``delete_attachment``). Belt-and-suspenders: ``append_chunk``/``commit_upload_session``
also abort an already-expired session inline, so this cron is the sweep for
sessions that simply went quiet.

Runs as an asyncio background task every ``INTERVAL_SECONDS`` (mirrors
``stale_claim_cron``); registered in ``app.main`` lifespan.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import AttachmentUploadSession
from app.db.session import SessionLocal
from app.services.attachments import _staging_path_for, unlink_staging_blob

logger = get_logger(__name__)

INTERVAL_SECONDS: int = 60  # sweep cadence (mirrors stale_claim_cron)


async def expire_upload_sessions() -> int:
    """Delete every expired upload session (row + staging blob). Returns the count.

    The blob paths are resolved BEFORE the rows are deleted, the rows are removed in
    one transaction, and only THEN are the staging blobs unlinked (post-commit,
    best-effort) — so a filesystem hiccup can never turn a committed row-delete into a
    crash, and a crash between the two just re-selects the (already gone) rows as a
    no-op next pass.
    """
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(AttachmentUploadSession).where(
                    AttachmentUploadSession.expires_at < now
                )
            )
        ).scalars().all()
        if not rows:
            return 0

        staging_paths = [_staging_path_for(row.staging_key) for row in rows]
        for row in rows:
            await session.delete(row)
        await session.commit()

    for path in staging_paths:
        await unlink_staging_blob(path)

    logger.info("upload_session_gc swept=%d", len(rows))
    return len(rows)


async def upload_session_gc_cron() -> None:
    """Background task: periodically sweep abandoned chunked-upload sessions."""
    logger.info("upload_session_gc_cron started interval=%ds", INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await expire_upload_sessions()
        except asyncio.CancelledError:
            # Cooperative cancellation: log, then re-raise so the framework sees the
            # task acknowledged the cancel (graceful shutdown — mirrors stale_claim_cron).
            logger.info("upload_session_gc_cron stopped")
            raise
        except Exception as exc:
            # A cron must survive a transient error (DB blip) — log and keep looping.
            logger.warning("upload_session_gc_cron error=%s", str(exc))
