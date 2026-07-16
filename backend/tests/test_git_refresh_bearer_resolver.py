"""PH-326: the git-refresh bearer resolver (``_resolve_actor_from_bearer``) now
delegates to the SAME shared PH-319/320/321 resolver as ``current_actor`` and the
WS ``get_actor_from_token`` helper, instead of the last remaining O(n) bcrypt scan
(a PH-155 leftover).

Companion to ``test_get_actor_from_token.py`` (WS side) and ``test_auth_lookup.py``
(HTTP side) — both must stay green unchanged. Here we assert the bearer git-refresh
path inherits the L1 verified-token cache + O(1) indexed L2 + NULL-bounded legacy
fallback, funnels through the single ``deps._resolve_token`` seam, and that the
header-parse contract (no header / non-Bearer scheme -> None, resolver untouched)
is preserved byte-for-byte.
"""

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps
from app.api.repositories import _resolve_actor_from_bearer
from app.core.security import hash_token, token_lookup_digest, verify_token
from app.core.single_flight import auth_single_flight
from app.core.token_cache import verified_token_cache
from app.db.base import Base
from app.db.models import Actor


def _hash(token: str) -> str:
    # Low round count keeps bcrypt fast while still exercising the REAL verify_token
    # path (the cost factor is embedded in the hash).
    return hash_token(token, rounds=4)


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
# AC1 -- L1 parity: cold call exactly ONE indexed bcrypt, repeat call ZERO;
# memberships eager-loaded (no lazy-IO outside an await context)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_bearer_second_call_skips_bcrypt(session: AsyncSession) -> None:
    token = "git-refresh-token"
    session.add(_make_actor("RefreshAdmin", token, lookup=True))
    # Decoys with a populated lookup: a stray full-table scan would bcrypt them.
    for i in range(4):
        session.add(_make_actor(f"Decoy{i}", f"decoy-{i}", lookup=True))
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        first = await _resolve_actor_from_bearer(session, f"Bearer {token}")
        assert first is not None
        assert first.display_name == "RefreshAdmin"
        assert spy.call_count == 1  # cold: indexed L2 hit -> exactly ONE bcrypt

        spy.reset_mock()
        second = await _resolve_actor_from_bearer(session, f"Bearer {token}")
        assert second is not None
        assert second.id == first.id
        assert spy.call_count == 0  # L1 HIT: repeat refresh runs ZERO bcrypt

    # memberships were eager-loaded -> accessing them needs no live lazy-load
    # (a lazy load here would raise MissingGreenlet under aiosqlite).
    assert first.memberships == []


# ---------------------------------------------------------------------------
# AC2 -- invalid token: None (caller maps to 401) + bcrypt cost bounded by the
# NULL-lookup legacy subset (m), never the full actor count (K)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_token_none_bounded_by_null_subset(session: AsyncSession) -> None:
    # K=8 modern actors (token_lookup populated) + m=2 legacy NULLs -> a bad
    # token bcrypts at most m=2 rows (the old scan bcrypted all 10).
    for i in range(8):
        session.add(_make_actor(f"Modern{i}", f"modern-{i}", lookup=True))
    for i in range(2):
        session.add(_make_actor(f"Legacy{i}", f"legacy-{i}", lookup=False))
    await session.commit()

    with patch("app.api.deps.verify_token", wraps=verify_token) as spy:
        actor = await _resolve_actor_from_bearer(session, "Bearer intruder-token")
        assert actor is None  # _evaluate_bearer_auth turns this into the 401
        assert spy.call_count == 2  # bounded by m=2 NULLs, NOT K=10


# ---------------------------------------------------------------------------
# AC3 -- single seam: the bearer git-refresh path funnels through the ONE
# deps._resolve_token scan seam (no re-implemented sweep in repositories.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_path_uses_shared_scan_seam(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkeypatching the single ``deps._resolve_token`` seam is observed by the
    git-refresh bearer resolver too -- proving it delegates instead of keeping its
    own O(n) bcrypt sweep."""
    token = "seam-bearer-token"
    session.add(_make_actor("Seam", token, lookup=True))
    await session.commit()

    seen: list[str] = []
    real_resolve = deps._resolve_token

    async def counting(sess: AsyncSession, tok: str, digest: str) -> str | None:
        seen.append(tok)
        return await real_resolve(sess, tok, digest)

    monkeypatch.setattr(deps, "_resolve_token", counting)

    actor = await _resolve_actor_from_bearer(session, f"Bearer {token}")  # cold L1
    assert actor is not None
    assert seen == [token]  # the shared seam served the bearer path


# ---------------------------------------------------------------------------
# AC4 -- header-parse parity: no header / non-Bearer scheme short-circuits to
# None BEFORE the shared resolver is ever invoked (parse lines unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Basic xyz"])
async def test_no_or_non_bearer_header_short_circuits(
    session: AsyncSession, authorization: str | None
) -> None:
    with patch(
        "app.api.repositories.resolve_actor_by_token", new=AsyncMock()
    ) as resolver:
        actor = await _resolve_actor_from_bearer(session, authorization)
        assert actor is None
        resolver.assert_not_called()  # parse short-circuits before the resolver
