"""FastAPI dependencies."""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound, PermissionDenied
from app.core.security import token_lookup_digest, verify_token
from app.core.single_flight import auth_single_flight
from app.core.token_cache import verified_token_cache
from app.db.models import Actor, Board, BoardMembership
from app.db.session import get_db_session
from app.services.boards import get_board

bearer_scheme = HTTPBearer(auto_error=False)


async def _load_active_actor(session: AsyncSession, actor_id: str) -> Actor | None:
    """Re-materialise an active actor by id via the caller's OWN session.

    Every request (leader AND single-flight followers) loads the ORM instance
    through its own session with ``memberships`` eager-loaded -- the single-flight
    future only carries the ``actor_id`` string, never a cross-session instance.
    """
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == uuid.UUID(actor_id), Actor.is_active.is_(True))
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one_or_none()


async def _resolve_token(session: AsyncSession, token: str, digest: str) -> str | None:
    """Resolve a bearer token to an actor id via the O(1) indexed path (PH-320).

    Single-flighted by the caller, so this body runs at most once per (token,
    worker) before L1 warms. Returns the actor-id string on success, or ``None``
    -> 403. NOTE: returns the id (session-agnostic), never the ORM row -- the
    caller re-materialises via its own session.
    """
    # Fast path: one indexed row by the deterministic lookup digest (UNIQUE).
    row = (
        await session.execute(
            select(Actor).where(Actor.token_lookup == digest, Actor.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if row is not None:
        # Defense in depth: the digest is an INDEX key, NOT the credential. A
        # tampered/collided token_lookup still cannot authenticate without the
        # plaintext the bcrypt hash was minted from. Exactly ONE bcrypt here.
        if verify_token(token, row.token_hash):
            verified_token_cache.put(token, str(row.id), row.token_hash)
            return str(row.id)
        # Digest matched but bcrypt failed -> forged/stale lookup. Do NOT fall
        # through to the legacy scan (a NULL-lookup actor cannot own this digest).
        return None

    # Fallback: legacy actors minted before the migration have token_lookup IS
    # NULL. Restrict the scan to that write-once-SHRINKING subset so the invalid
    # -token cost is bounded by the remaining legacy count (drains to 0 rows ->
    # O(1) once every legacy token has authenticated once / been re-minted).
    legacy = (
        await session.execute(
            select(Actor).where(Actor.token_lookup.is_(None), Actor.is_active.is_(True))
        )
    ).scalars()
    for actor in legacy:
        if verify_token(token, actor.token_hash):
            actor_id = str(actor.id)
            token_hash = actor.token_hash  # snapshot BEFORE commit (rollback expires attrs)
            # Lazy backfill: persist the digest so the NEXT auth is O(1). Auth is
            # the FIRST dependency, so this commit is isolated from the endpoint's
            # own work (get_db_session does NOT auto-commit -> explicit commit).
            actor.token_lookup = digest
            try:
                await session.commit()
            except IntegrityError:
                # Concurrent backfill of the same row, or an (infeasible) sha256
                # collision -> benign: the credential still verified. Roll back
                # the failed write and let L1 re-resolve next time.
                await session.rollback()
                verified_token_cache.invalidate(token)
            verified_token_cache.put(token, actor_id, token_hash)
            return actor_id

    return None


async def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Actor:
    if credentials is None:
        raise PermissionDenied(required="authenticated_actor", have=[])

    token = credentials.credentials
    digest = token_lookup_digest(token)

    # L1 fast path (PH-319): a previously verified token skips bcrypt entirely
    # (0 bcrypt, ~5ms -- the steady state). The snapshot check makes a rotated/
    # revoked/deactivated token fall through safely.
    cached = verified_token_cache.get(token)
    if cached is not None:
        actor = await _load_active_actor(session, cached.actor_id)
        if actor is not None and actor.token_hash == cached.token_hash:
            return actor
        verified_token_cache.invalidate(token)

    # L2 resolve (PH-320), single-flighted on the digest so a concurrent same
    # -token burst (the post-restart 5-request stampede) collapses to ONE resolve
    # body -- the followers await the shared future instead of each re-scanning.
    async def _resolve() -> str | None:
        return await _resolve_token(session, token, digest)

    actor_id = await auth_single_flight.resolve(digest, _resolve)
    if actor_id is None:
        raise PermissionDenied(required="valid_bearer_token", have=[])

    actor = await _load_active_actor(session, actor_id)
    if actor is None:
        # Resolved but vanished/deactivated between resolve and re-read.
        raise PermissionDenied(required="valid_bearer_token", have=[])
    return actor


async def get_board_by_key(
    board_key: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Board:
    board = (
        await session.execute(select(Board).where(Board.key == board_key.upper()))
    ).scalar_one_or_none()
    if board is None:
        raise NotFound("board")
    return board


async def require_board_admin(
    board_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Actor:
    """Dependency that ensures the current actor is an admin on ``board_id``.

    PH-39: members POST/PATCH/DELETE.  PH-223: sonarqube setup/sync.
    Resolves ``board_id`` the same way ``get_board`` does (KEY or UUID) so a
    board KEY is accepted -- the UI always sends the key (PH-233).  Resolution
    is load-bearing and ordered: unknown board -> 404 NotFound (via get_board)
    FIRST; a resolved board with no admin membership -> 403 PermissionDenied
    SECOND.  The two paths never bleed into each other.
    """
    board = await get_board(session, board_id)  # 404 on unknown board (NotFound)
    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor.id,
                BoardMembership.role == "admin",
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise PermissionDenied(required="board.admin", have=[])

    return actor
