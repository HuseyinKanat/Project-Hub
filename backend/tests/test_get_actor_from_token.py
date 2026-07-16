"""PH-321: ``get_actor_from_token`` (WebSocket / attachment-content auth) is now
routed through the SAME PH-319/PH-320 resolver as ``current_actor`` instead of the
O(n) bcrypt scan that froze the event loop on every board-open WS handshake.

Companion to ``test_auth_lookup.py`` / ``test_auth_cache.py`` (the ``current_actor``
side, which must stay green unchanged). Here we assert the WS-side helper inherits
the indexed L2 hit + L1 cache + NULL-bounded fallback, and that BOTH entry points
share exactly one scan seam (AC1/AC2: "the scan SELECT is now in one place").
"""

from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps
from app.api.deps import current_actor, resolve_actor_by_token
from app.core.security import hash_token, token_lookup_digest, verify_token
from app.core.single_flight import auth_single_flight
from app.core.token_cache import verified_token_cache
from app.db.base import Base
from app.db.models import Actor
from app.services.actors import get_actor_from_token


def _hash(token: str) -> str:
    # Low round count keeps bcrypt fast while still exercising the REAL verify_token
    # path (the cost factor is embedded in the hash).
    return hash_token(token, rounds=4)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _make_actor(name: str, token: str, *, lookup: bool, active: bool = True) -> Actor:
    """Build an Actor. ``lookup=True`` populates token_lookup (post-migration/minted);
    ``lookup=False`` leaves it NULL (a legacy pre-migration actor)."""
    return Actor(
        kind="agent",
        display_name=name,
        token_hash=_hash(token),
        token_lookup=token_lookup_digest(token) if lookup else None,
        is_active=active,
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_shared_state() -> Iterator[None]:
    """L1 cache + single-flight map are process-wide singletons -- reset around each
    test so bcrypt counts and cache hits stay deterministic."""
    verified_token_cache.clear()
    auth_single_flight._inflight.clear()
    yield
    verified_token_cache.clear()
    auth_single_flight._inflight.clear()


# ---------------------------------------------------------------------------
# valid token -> actor, memberships eager-loaded (no lazy-load outside session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_token_returns_actor_with_memberships(session: AsyncSession) -> None:
    token = "ws-valid-token"
    session.add(_make_actor("WsTarget", token, lookup=True))
    # Decoys with a populated lookup: a stray full-table scan would bcrypt them.
    for i in range(4):
        session.add(_make_actor(f"Decoy{i}", f"decoy-{i}", lookup=True))
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await get_actor_from_token(session, token)
        assert actor is not None
        assert actor.display_name == "WsTarget"
        assert spy.call_count == 1  # indexed L2 hit -> exactly ONE bcrypt, no scan

    # memberships were eager-loaded -> accessing them needs no live lazy-load.
    assert actor.memberships == []
    # L1 warmed for the next handshake (the steady-state 0-bcrypt path).
    assert verified_token_cache.get(token) is not None


# ---------------------------------------------------------------------------
# second call is bcrypt-free (L1 cache) -- the whole point of the hotfix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_call_skips_bcrypt(session: AsyncSession) -> None:
    token = "ws-repeat-token"
    session.add(_make_actor("Target", token, lookup=True))
    for i in range(3):
        session.add(_make_actor(f"Decoy{i}", f"decoy-{i}", lookup=True))
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        first = await get_actor_from_token(session, token)
        assert first is not None
        assert spy.call_count == 1  # cold: one indexed bcrypt

        spy.reset_mock()
        second = await get_actor_from_token(session, token)
        assert second is not None
        assert second.id == first.id
        assert spy.call_count == 0  # HIT: repeat handshake runs ZERO bcrypt


# ---------------------------------------------------------------------------
# wrong token -> None, and (steady state) zero bcrypt -- no O(n) sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_token_returns_none_no_scan(session: AsyncSession) -> None:
    for i in range(5):
        session.add(_make_actor(f"Modern{i}", f"tok-{i}", lookup=True))  # all backfilled
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await get_actor_from_token(session, "not-a-real-token")
        assert actor is None
        assert spy.call_count == 0  # digest miss + empty NULL subset => NO bcrypt

    assert verified_token_cache.get("not-a-real-token") is None  # 403-path never cached


# ---------------------------------------------------------------------------
# legacy (token_lookup NULL) -> matched via NULL-restricted fallback + backfill,
# and the 403 cost is bounded by the shrinking NULL subset, not the actor count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_null_fallback_then_backfilled(session: AsyncSession) -> None:
    token = "legacy-ws-token"
    target = _make_actor("Legacy", token, lookup=False)  # pre-migration NULL
    session.add(target)
    session.add(_make_actor("NullDecoy", "null-decoy", lookup=False))
    await session.commit()
    assert target.token_lookup is None

    actor = await get_actor_from_token(session, token)
    assert actor is not None
    assert actor.id == target.id

    await session.refresh(target)
    assert target.token_lookup == token_lookup_digest(token)  # lazy backfill persisted


@pytest.mark.asyncio
async def test_wrong_token_bounded_by_null_subset(session: AsyncSession) -> None:
    # K=10 active actors, only m=2 legacy NULLs -> a bad token bcrypts at most m, not K.
    for i in range(8):
        session.add(_make_actor(f"Modern{i}", f"modern-{i}", lookup=True))
    for i in range(2):
        session.add(_make_actor(f"Legacy{i}", f"legacy-{i}", lookup=False))
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await get_actor_from_token(session, "intruder-token")
        assert actor is None
        assert spy.call_count == 2  # bounded by m=2 NULLs, NOT K=10


# ---------------------------------------------------------------------------
# AC1/AC2 -- both entry points share ONE resolver seam (the scan is in one place)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_callers_share_single_scan_seam(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_actor_from_token`` (WS/content) and ``current_actor`` (HTTP) both funnel
    through the single ``deps._resolve_token`` scan seam -- monkeypatching that ONE
    point is observed by BOTH callers, proving neither re-implements the O(n) sweep."""
    token = "shared-seam-token"
    session.add(_make_actor("Seam", token, lookup=True))
    await session.commit()

    seen: list[str] = []
    real_resolve = deps._resolve_token

    async def counting(sess: AsyncSession, tok: str, digest: str) -> str | None:
        seen.append(tok)
        return await real_resolve(sess, tok, digest)

    monkeypatch.setattr(deps, "_resolve_token", counting)

    # WS/content path (cold L1) hits the seam...
    ws_actor = await get_actor_from_token(session, token)
    assert ws_actor is not None

    # ...and the HTTP dependency path (cold L1 again) hits the SAME seam.
    verified_token_cache.clear()
    http_actor = await current_actor(_creds(token), session)
    assert http_actor.id == ws_actor.id

    assert seen == [token, token]  # one shared seam served both entry points


@pytest.mark.asyncio
async def test_get_actor_from_token_matches_current_actor_resolution(
    session: AsyncSession,
) -> None:
    """Same token -> same actor object identity via either entry point (behavioural
    parity with the ``current_actor`` dependency)."""
    token = "parity-token"
    session.add(_make_actor("Parity", token, lookup=True))
    await session.commit()

    direct = await resolve_actor_by_token(session, token)
    via_helper = await get_actor_from_token(session, token)
    via_dep = await current_actor(_creds(token), session)

    assert direct is not None
    assert via_helper is not None
    assert via_helper.id == direct.id == via_dep.id
