"""PH-319 hotfix: verified-token cache removes the O(n) bcrypt scan from auth.

`current_actor` previously ran bcrypt against every active actor on every
request (~12s with 24 actors). `VerifiedTokenCache` remembers which actor a
token verified against so repeat requests skip bcrypt, while a rotated/revoked
token can never authenticate from a stale entry.

Acceptance criteria exercised:
  AC1  second request with the same token does NOT call bcrypt (verify_token).
  AC2  after rotation the old token is rejected (403) -- stale cache must NOT pass.
  AC3  a fresh valid token scans once, then serves from cache.
  AC4  a wrong token is rejected (403) with no regression and is never cached.
Plus the cache's own invariants: TTL expiry, LRU eviction, no plaintext storage.
"""

from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_actor
from app.core.exceptions import PermissionDenied
from app.core.security import hash_token, verify_token
from app.core.token_cache import VerifiedTokenCache, verified_token_cache
from app.db.base import Base
from app.db.models import Actor


def _hash(token: str) -> str:
    # bcrypt embeds the cost factor in the hash, so a low round count keeps the
    # test fast while still exercising the *real* verify_token / checkpw path.
    return hash_token(token, rounds=4)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_cache() -> Iterator[None]:
    """The cache is a process-wide singleton -- clear it around every test."""
    verified_token_cache.clear()
    yield
    verified_token_cache.clear()


async def _seed(session: AsyncSession, decoys: int, target_token: str) -> Actor:
    """Seed ``decoys`` non-matching actors plus one target for ``target_token``.

    The target is inserted LAST so it sits at the end of the scan -- this proves
    the second-call speedup comes from the cache, not from a lucky scan order.
    """
    actors = [
        Actor(
            kind="agent",
            display_name=f"Decoy {i}",
            token_hash=_hash(f"decoy-token-{i}"),
            is_active=True,
        )
        for i in range(decoys)
    ]
    target = Actor(
        kind="human",
        display_name="Target",
        token_hash=_hash(target_token),
        is_active=True,
    )
    session.add_all([*actors, target])
    await session.commit()
    await session.refresh(target)
    return target


# ---------------------------------------------------------------------------
# AC1 / AC3 -- cache removes bcrypt from the repeat request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac1_second_request_skips_bcrypt(session: AsyncSession) -> None:
    token = "valid-token-A"
    target = await _seed(session, decoys=4, target_token=token)

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        first = await current_actor(_creds(token), session)
        assert first.id == target.id
        assert spy.call_count >= 1  # first call scanned + ran bcrypt

        spy.reset_mock()
        second = await current_actor(_creds(token), session)
        assert second.id == target.id
        assert spy.call_count == 0  # HIT: no bcrypt at all


@pytest.mark.asyncio
async def test_ac3_fresh_token_scans_once_then_cached(session: AsyncSession) -> None:
    token = "brand-new-token"
    target = await _seed(session, decoys=3, target_token=token)

    # Before first use the token is absent from the cache.
    assert verified_token_cache.get(token) is None

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await current_actor(_creds(token), session)
        assert actor.id == target.id
        assert spy.call_count >= 1  # miss -> full scan happened

    # First request populated the cache with the correct actor + snapshot.
    entry = verified_token_cache.get(token)
    assert entry is not None
    assert entry.actor_id == str(target.id)
    assert entry.token_hash == target.token_hash


# ---------------------------------------------------------------------------
# AC2 -- rotation invalidates the stale cache entry (must NOT authenticate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_rotated_token_rejected_despite_cache(session: AsyncSession) -> None:
    token = "valid-token-A"
    target = await _seed(session, decoys=2, target_token=token)

    # Prime the cache with a successful verify.
    await current_actor(_creds(token), session)
    assert verified_token_cache.get(token) is not None

    # Rotate: the actor's token_hash changes to a new secret.
    target.token_hash = _hash("rotated-secret")
    await session.commit()

    # The OLD token must be rejected -- the snapshot no longer matches, so the
    # request falls through to the full scan, which also fails to verify it.
    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        with pytest.raises(PermissionDenied):
            await current_actor(_creds(token), session)
        assert spy.call_count >= 1  # stale entry did NOT short-circuit auth

    # The stale entry has been invalidated.
    assert verified_token_cache.get(token) is None


@pytest.mark.asyncio
async def test_deactivated_actor_not_served_from_cache(session: AsyncSession) -> None:
    """Sibling of AC2: an actor deactivated after caching is no longer authorized."""
    token = "valid-token-A"
    target = await _seed(session, decoys=1, target_token=token)

    await current_actor(_creds(token), session)
    assert verified_token_cache.get(token) is not None

    target.is_active = False
    await session.commit()

    with pytest.raises(PermissionDenied):
        await current_actor(_creds(token), session)
    assert verified_token_cache.get(token) is None


# ---------------------------------------------------------------------------
# AC4 -- wrong / missing token regression (403), never cached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4_wrong_token_rejected_and_never_cached(session: AsyncSession) -> None:
    await _seed(session, decoys=3, target_token="valid-token-A")

    with pytest.raises(PermissionDenied):
        await current_actor(_creds("totally-wrong-token"), session)

    # A failed verify must never populate the cache (no 403-path poisoning).
    assert verified_token_cache.get("totally-wrong-token") is None


@pytest.mark.asyncio
async def test_missing_credentials_rejected(session: AsyncSession) -> None:
    with pytest.raises(PermissionDenied):
        await current_actor(None, session)


# ---------------------------------------------------------------------------
# VerifiedTokenCache unit invariants
# ---------------------------------------------------------------------------


def test_cache_ttl_expiry() -> None:
    cache = VerifiedTokenCache(ttl_seconds=600.0)
    # put() reads the clock once (cached_at=100); get() reads it once (now=701).
    with patch("app.core.token_cache.time.monotonic", side_effect=[100.0, 701.0]):
        cache.put("tok", "actor-1", "hash-1")
        assert cache.get("tok") is None  # 601s elapsed > 600s TTL


def test_cache_hit_within_ttl() -> None:
    cache = VerifiedTokenCache(ttl_seconds=600.0)
    with patch("app.core.token_cache.time.monotonic", side_effect=[100.0, 200.0]):
        cache.put("tok", "actor-1", "hash-1")
        entry = cache.get("tok")
    assert entry is not None
    assert entry.actor_id == "actor-1"
    assert entry.token_hash == "hash-1"


def test_cache_lru_eviction() -> None:
    cache = VerifiedTokenCache(max_entries=2)
    cache.put("a", "1", "h")
    cache.put("b", "2", "h")
    cache.get("a")  # touch "a" so "b" becomes the least-recently-used
    cache.put("c", "3", "h")  # exceeds cap -> evict LRU ("b")
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_cache_never_stores_plaintext_token() -> None:
    cache = VerifiedTokenCache()
    secret = "super-secret-plaintext-token"
    cache.put(secret, "actor-1", "hash-1")
    # The plaintext must not appear anywhere in the internal state -- only its
    # sha256 digest is used as the key.
    assert secret not in repr(cache._entries)
    assert list(cache._entries.keys()) != [secret]


def test_cache_invalidate_is_idempotent() -> None:
    cache = VerifiedTokenCache()
    cache.put("tok", "actor-1", "hash-1")
    cache.invalidate("tok")
    cache.invalidate("tok")  # no error on second call
    assert cache.get("tok") is None
