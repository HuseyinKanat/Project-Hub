"""PH-320: O(1) indexed token-lookup auth (L2 tier + single-flight + lazy backfill).

Complements ``test_auth_cache.py`` (the PH-319 L1 tier, which must stay green).
Exercises the acceptance criteria the indexed L2 tier adds:

  AC2  valid + L1 cold / L2 hit → exactly ONE indexed fetch + ONE bcrypt, L1 warmed.
  AC3  invalid + zero legacy NULLs → 403 via one indexed lookup, ZERO bcrypt (no scan).
  AC4  legacy (token_lookup NULL) → matches via NULL-restricted fallback, backfills
       its digest, and the next cold-L1 request is O(1) (indexed, no NULL scan).
  AC5  403 cost is bounded by the shrinking NULL subset (m), not the actor count (K).
  AC6  a concurrent same-token burst collapses to ONE resolve (single-flight).
  AC7  rotation via ``set_actor_token`` → OLD token 403s immediately (digest MISS),
       NEW token authenticates; the in-process L1 hook drops the token's entry.
  AC8  UNIQUE index: two rows cannot share a non-null token_lookup; NULLs coexist.
  AC9  mint sites populate token_lookup forward == sha256hex(token); L1 key == L2 col.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import current_actor
from app.cli import _provision_jarwis_role, set_actor_token
from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDenied
from app.core.security import hash_token, token_lookup_digest, verify_token
from app.core.single_flight import AuthSingleFlight, auth_single_flight
from app.core.token_cache import VerifiedTokenCache, verified_token_cache
from app.db.base import Base
from app.db.models import Actor, Board


def _hash(token: str) -> str:
    # Low round count keeps bcrypt fast while still exercising the REAL
    # verify_token / checkpw path (the cost factor is embedded in the hash).
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


def _fast_settings() -> Settings:
    """Real Settings with a fast bcrypt cost so mint-site tests stay quick."""
    return get_settings().model_copy(update={"token_hash_rounds": 4})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Single-connection in-memory DB for the sequential tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_fixture(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """File-backed DB so N concurrent requests each get their OWN connection to the
    SAME database (models production, where every request has its own session)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_shared_state() -> Iterator[None]:
    """L1 cache and the single-flight map are process-wide singletons -- reset them
    around every test so counts and hits are deterministic."""
    verified_token_cache.clear()
    auth_single_flight._inflight.clear()
    yield
    verified_token_cache.clear()
    auth_single_flight._inflight.clear()


# ---------------------------------------------------------------------------
# AC9 (digest linkage) — L1 key IS the L2 column, from one source
# ---------------------------------------------------------------------------


def test_l1_key_equals_l2_digest() -> None:
    token = "some-bearer-token"
    assert token_lookup_digest(token) == VerifiedTokenCache._key(token)
    # sha256 hexdigest is 64 chars — matches the String(64) column width.
    assert len(token_lookup_digest(token)) == 64


# ---------------------------------------------------------------------------
# AC2 — valid, L1 cold / L2 hit: one indexed fetch, exactly one bcrypt, L1 warmed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_l2_hit_one_bcrypt_no_scan(session: AsyncSession) -> None:
    token = "valid-token-A"
    session.add(_make_actor("Target", token, lookup=True))
    # Decoys ALSO carry a populated lookup, so a full-table scan (if it wrongly
    # ran) would bcrypt them — proving the indexed fetch touched only the target.
    for i in range(4):
        session.add(_make_actor(f"Decoy{i}", f"decoy-{i}", lookup=True))
    await session.commit()

    assert verified_token_cache.get(token) is None  # L1 cold
    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await current_actor(_creds(token), session)
        assert actor.display_name == "Target"
        assert spy.call_count == 1  # exactly ONE bcrypt (defense-in-depth), no scan

    # L1 populated for the next request (subsequent call is 0-bcrypt).
    entry = verified_token_cache.get(token)
    assert entry is not None
    assert entry.actor_id == str(actor.id)


# ---------------------------------------------------------------------------
# AC3 — invalid, steady state (0 legacy NULLs): 403 via one lookup, zero bcrypt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_invalid_zero_bcrypt_when_no_nulls(session: AsyncSession) -> None:
    for i in range(5):
        session.add(_make_actor(f"Modern{i}", f"tok-{i}", lookup=True))  # all backfilled
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        with pytest.raises(PermissionDenied):
            await current_actor(_creds("garbage-token"), session)
        assert spy.call_count == 0  # digest miss + empty NULL subset => NO bcrypt

    assert verified_token_cache.get("garbage-token") is None  # 403 never cached


# ---------------------------------------------------------------------------
# AC4 — legacy lazy backfill: first auth persists the digest, second is O(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4_legacy_lazy_backfill_then_o1(session: AsyncSession) -> None:
    token = "legacy-token"
    target = _make_actor("Legacy", token, lookup=False)  # pre-migration NULL
    session.add(target)
    # A second NULL decoy that would be re-scanned if the fallback ran again.
    session.add(_make_actor("NullDecoy", "null-decoy", lookup=False))
    await session.commit()
    assert target.token_lookup is None

    # First auth: matches via the NULL-restricted fallback, backfills the digest.
    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await current_actor(_creds(token), session)
        assert actor.id == target.id
        assert spy.call_count >= 1  # fallback scan ran

    await session.refresh(target)
    assert target.token_lookup == token_lookup_digest(token)  # persisted

    # Second cold-L1 request is O(1): single indexed fetch + one bcrypt, NO NULL
    # scan (the NullDecoy is never verified again).
    verified_token_cache.clear()
    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor2 = await current_actor(_creds(token), session)
        assert actor2.id == target.id
        assert spy.call_count == 1  # indexed hit only, no re-scan of the NULL decoy


# ---------------------------------------------------------------------------
# AC5 — 403 cost bounded by the NULL subset (m), not the total actor count (K)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5_403_bounded_by_null_subset(session: AsyncSession) -> None:
    # K=10 active actors, only m=2 legacy NULLs.
    for i in range(8):
        session.add(_make_actor(f"Modern{i}", f"modern-{i}", lookup=True))
    for i in range(2):
        session.add(_make_actor(f"Legacy{i}", f"legacy-{i}", lookup=False))
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        with pytest.raises(PermissionDenied):
            await current_actor(_creds("intruder-token"), session)
        # At most m=2 bcrypts (the NULL subset), NOT K=10.
        assert spy.call_count == 2


# ---------------------------------------------------------------------------
# AC6 — single-flight collapses the concurrent same-token stampede
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac6_single_flight_valid_one_bcrypt(
    sessionmaker_fixture: async_sessionmaker[AsyncSession],
) -> None:
    maker = sessionmaker_fixture
    token = "burst-valid"
    async with maker() as seed:
        seed.add(_make_actor("Target", token, lookup=True))
        for i in range(3):
            seed.add(_make_actor(f"Decoy{i}", f"decoy-{i}", lookup=True))
        await seed.commit()

    async def one(s: AsyncSession) -> Actor:
        return await current_actor(_creds(token), s)

    # Pre-open the sessions so there is no connection-acquire yield mid-flight:
    # coroutine 1 becomes the leader and parks on the L2 query; the rest reach
    # the shared future synchronously and await it. One resolve => one bcrypt.
    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        async with AsyncExitStack() as stack:
            sessions = [await stack.enter_async_context(maker()) for _ in range(5)]
            results = await asyncio.gather(*(one(s) for s in sessions))
    assert all(r.display_name == "Target" for r in results)
    assert spy.call_count == 1  # the 5-request stampede collapsed to ONE bcrypt


@pytest.mark.asyncio
async def test_ac6_single_flight_garbage_one_scan(
    sessionmaker_fixture: async_sessionmaker[AsyncSession],
) -> None:
    maker = sessionmaker_fixture
    async with maker() as seed:
        for i in range(2):  # m=2 legacy NULLs
            seed.add(_make_actor(f"Legacy{i}", f"legacy-{i}", lookup=False))
        for i in range(3):  # backfilled — never scanned
            seed.add(_make_actor(f"Modern{i}", f"modern-{i}", lookup=True))
        await seed.commit()

    async def one(s: AsyncSession) -> None:
        with pytest.raises(PermissionDenied):
            await current_actor(_creds("burst-garbage"), s)

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        async with AsyncExitStack() as stack:
            sessions = [await stack.enter_async_context(maker()) for _ in range(6)]
            await asyncio.gather(*(one(s) for s in sessions))
    # ONE NULL-scan over the 2 legacy actors — NOT 6 x 2. Modern actors untouched.
    assert spy.call_count == 2


# ---------------------------------------------------------------------------
# AC7 — rotation invalidate hook: old token immediate 403, new token authenticates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac7_rotation_old_rejected_new_accepted(session: AsyncSession) -> None:
    old_token = "old-secret"
    actor = _make_actor("Rotatable", old_token, lookup=True)
    session.add(actor)
    await session.commit()

    # Prime L1 with the old token.
    await current_actor(_creds(old_token), session)
    assert verified_token_cache.get(old_token) is not None

    # Rotate through the shared mint helper: token_hash + token_lookup move to the
    # NEW token together, and the in-process hook drops the (old) token's L1 entry.
    new_token = "new-secret"
    set_actor_token(actor, new_token, _fast_settings())
    verified_token_cache.invalidate(old_token)  # simulate an in-process rotation caller
    await session.commit()

    # OLD token 403s immediately: its digest no longer maps to the row (L2 MISS),
    # and the actor is not in the NULL subset — no reliance on the 600s L1 TTL.
    with pytest.raises(PermissionDenied):
        await current_actor(_creds(old_token), session)

    # NEW token authenticates via the indexed L2 hit.
    who = await current_actor(_creds(new_token), session)
    assert who.id == actor.id


@pytest.mark.asyncio
async def test_ac7_set_actor_token_drops_l1_entry() -> None:
    """The in-process hook evicts the token's L1 entry at write time."""
    token = "in-proc-token"
    verified_token_cache.put(token, str(uuid.uuid4()), _hash(token))
    assert verified_token_cache.get(token) is not None

    set_actor_token(_make_actor("X", "unused", lookup=True), token, _fast_settings())
    assert verified_token_cache.get(token) is None  # hook dropped it


# ---------------------------------------------------------------------------
# AC8 — UNIQUE index on token_lookup; multiple NULLs coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac8_unique_lookup_rejects_duplicate(session: AsyncSession) -> None:
    digest = token_lookup_digest("shared")
    a = Actor(kind="agent", display_name="A", token_hash=_hash("a"), token_lookup=digest)
    b = Actor(kind="agent", display_name="B", token_hash=_hash("b"), token_lookup=digest)
    session.add_all([a, b])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_ac8_multiple_nulls_coexist(session: AsyncSession) -> None:
    session.add_all(
        [
            Actor(kind="agent", display_name="N1", token_hash=_hash("n1"), token_lookup=None),
            Actor(kind="agent", display_name="N2", token_hash=_hash("n2"), token_lookup=None),
        ]
    )
    await session.commit()  # NULLs are distinct under UNIQUE — no IntegrityError
    count = len((await session.execute(select(Actor))).scalars().all())
    assert count == 2


# ---------------------------------------------------------------------------
# AC9 — mint sites populate token_lookup forward == sha256hex(token)
# ---------------------------------------------------------------------------


def test_ac9_set_actor_token_sets_hash_and_lookup() -> None:
    actor = Actor(kind="agent", display_name="Mint", token_hash="", token_lookup=None)
    token = "freshly-minted-token"
    set_actor_token(actor, token, _fast_settings())
    assert actor.token_lookup == token_lookup_digest(token)
    assert verify_token(token, actor.token_hash)  # bcrypt credential set too


@pytest.mark.asyncio
async def test_ac9_provision_new_and_rotate_populate_lookup(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.cli.get_settings", _fast_settings)
    board = Board(
        key="TS",
        name="Test",
        description="",
        project_type="web_app",
        workflow_id=uuid.uuid4(),
        roles={},
    )
    session.add(board)
    await session.flush()

    # New-actor mint site.
    token = await _provision_jarwis_role(
        session, board, "backend_dev", "jarwis-backend", rotate=False
    )
    await session.commit()
    actor = (
        await session.execute(select(Actor).where(Actor.display_name == "jarwis-backend"))
    ).scalar_one()
    assert token and actor.token_lookup == token_lookup_digest(token)
    assert verify_token(token, actor.token_hash)

    # --rotate mint site: new token, lookup digest updated in lock-step.
    new_token = await _provision_jarwis_role(
        session, board, "backend_dev", "jarwis-backend", rotate=True
    )
    await session.commit()
    await session.refresh(actor)
    assert new_token and new_token != token
    assert actor.token_lookup == token_lookup_digest(new_token)


# ---------------------------------------------------------------------------
# Single-flight helper — unit invariants (deterministic, no DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_flight_collapses_concurrent_to_one_call() -> None:
    sf = AuthSingleFlight()
    calls = 0
    gate = asyncio.Event()

    async def resolver() -> str | None:
        nonlocal calls
        calls += 1
        await gate.wait()  # hold the leader so followers pile onto the future
        return "actor-x"

    tasks = [asyncio.create_task(sf.resolve("digest", resolver)) for _ in range(5)]
    for _ in range(3):
        await asyncio.sleep(0)  # let all 5 register / await the shared future
    gate.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1  # only the leader ran the body
    assert results == ["actor-x"] * 5
    assert sf._inflight == {}  # key popped after settle


@pytest.mark.asyncio
async def test_single_flight_distinct_digests_run_independently() -> None:
    sf = AuthSingleFlight()
    seen: list[str] = []

    def make(d: str) -> Callable[[], Awaitable[str | None]]:
        async def r() -> str | None:
            seen.append(d)
            return d

        return r

    assert await sf.resolve("a", make("a")) == "a"
    assert await sf.resolve("b", make("b")) == "b"
    assert seen == ["a", "b"]
    assert sf._inflight == {}


@pytest.mark.asyncio
async def test_single_flight_propagates_exception_and_frees_slot() -> None:
    sf = AuthSingleFlight()

    async def boom() -> str | None:
        raise ValueError("resolve failed")

    with pytest.raises(ValueError, match="resolve failed"):
        await sf.resolve("d", boom)
    assert sf._inflight == {}  # leader slot freed even on failure

    async def ok() -> str | None:
        return "recovered"

    assert await sf.resolve("d", ok) == "recovered"  # digest resolvable again


@pytest.mark.asyncio
async def test_single_flight_followers_share_leader_exception() -> None:
    sf = AuthSingleFlight()
    gate = asyncio.Event()

    async def boom() -> str | None:
        await gate.wait()
        raise ValueError("shared failure")

    tasks = [asyncio.create_task(sf.resolve("d", boom)) for _ in range(3)]
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, ValueError) for r in results)  # leader + 2 followers
    assert sf._inflight == {}
