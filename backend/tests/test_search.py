"""Service-layer tests for cross-board search (PH-275, epic PH-271 4/7).

Tests ``services.search.search`` DIRECTLY against an in-memory sqlite
``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env; mirrors
the PH-272/273/274 service-test approach). Seeds via ``Base.metadata.create_all``.

Coverage maps to the ACs:
- AC1: q matches ticket title OR description OR key, cross-board (TWO boards).
- AC2: q matches concept tag name OR slug → concept_tags group (ConceptTagSummary).
- AC3: ?tags AND-filter (ticket with BOTH returned, only-one NOT); unknown slug → empty.
- AC4: grouped shape {tickets:[TicketSearchHit], concept_tags:[ConceptTagSummary]}.
- AC5: case-insensitive (lower AND upper both hit); wildcard-injection safety (%/_ literal).
- AC6: blank/whitespace q → no scan, empty; result cap (LIMIT 50) + ordering.
- AC7: N+1 — statement count CONSTANT across 1 vs 50 tickets (selectinload, no lazy).
- AC8: permission — actor lacking tag.read → 403 PermissionDenied.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.exceptions import PermissionDenied
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    ConceptTag,
    Ticket,
    TicketConceptTag,
    Workflow,
)
from app.schemas import ConceptTagSummary, TicketSearchHit
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.search import _SEARCH_LIMIT, search
from app.services.serializers import concept_tag_summary, ticket_search_hit


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
    await engine.dispose()


async def _reload_actor(session: AsyncSession, actor_id: uuid.UUID) -> Actor:
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor_id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()


class Env:
    """Two boards (PH + KIM), an admin member of both, and a no-membership stranger."""

    def __init__(
        self, *, board_ph: Board, board_kim: Board, admin: Actor, stranger: Actor
    ) -> None:
        self.board_ph = board_ph
        self.board_kim = board_kim
        self.admin = admin
        self.stranger = stranger


@pytest_asyncio.fixture
async def env(mem_session: AsyncSession) -> Env:
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    # `stranger` has NO board membership → no tag.read → 403.
    stranger = Actor(
        kind="agent", display_name="Stranger", token_hash="z", is_active=True
    )
    mem_session.add_all([admin, stranger])
    await mem_session.flush()

    board_ph = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    board_kim = Board(
        key="KIM",
        name="Kims",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add_all([board_ph, board_kim])
    await mem_session.flush()

    mem_session.add_all(
        [
            BoardMembership(board_id=board_ph.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_kim.id, actor_id=admin.id, role="admin"),
        ]
    )
    await mem_session.commit()

    return Env(
        board_ph=board_ph,
        board_kim=board_kim,
        admin=await _reload_actor(mem_session, admin.id),
        stranger=await _reload_actor(mem_session, stranger.id),
    )


def _make_ticket(
    board: Board,
    reporter: Actor,
    key: str,
    *,
    title: str = "",
    description: str = "",
    updated_at: datetime | None = None,
) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=title or f"Ticket {key}",
        description=description,
        state="backlog",
        reporter_id=reporter.id,
        updated_at=updated_at,
    )


def _make_tag(slug: str, name: str, *, color: str | None = None) -> ConceptTag:
    return ConceptTag(id=uuid.uuid4(), slug=slug, name=name, color=color)


def _attach(ticket: Ticket, tag: ConceptTag) -> TicketConceptTag:
    return TicketConceptTag(
        id=uuid.uuid4(), ticket_id=ticket.id, concept_tag_id=tag.id
    )


# ---------------------------------------------------------------------------
# AC1: q matches ticket title/description/key, cross-board.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_matches_ticket_title_description_key(
    mem_session: AsyncSession, env: Env
) -> None:
    by_title = _make_ticket(env.board_ph, env.admin, "PH-1", title="Quokka feature")
    by_desc = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="Other", description="has quokka inside"
    )
    by_key = _make_ticket(env.board_ph, env.admin, "QUOKKA-9", title="Other")
    miss = _make_ticket(env.board_ph, env.admin, "PH-3", title="nope", description="nope")
    mem_session.add_all([by_title, by_desc, by_key, miss])
    await mem_session.commit()

    tickets, _ = await search(mem_session, env.admin, q="quokka")
    assert {t.key for t in tickets} == {"PH-1", "PH-2", "QUOKKA-9"}


@pytest.mark.asyncio
async def test_q_cross_board(mem_session: AsyncSession, env: Env) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", title="cross board thing")
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", title="cross board other")
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    tickets, _ = await search(mem_session, env.admin, q="cross")
    # Both boards represented — NO board filter on the ticket query.
    assert {t.key for t in tickets} == {"PH-1", "KIM-1"}
    # board is eager-loaded (selectinload) → reading .key is MissingGreenlet-safe.
    assert {t.board.key for t in tickets} == {"PH", "KIM"}


# ---------------------------------------------------------------------------
# AC2: q matches concept tag name OR slug.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_matches_tag_name_and_slug(
    mem_session: AsyncSession, env: Env
) -> None:
    by_name = _make_tag("authn", "Authentication", color="#abc")
    by_slug = _make_tag("auth-flow", "Login")
    miss = _make_tag("graph", "Graph")
    mem_session.add_all([by_name, by_slug, miss])
    await mem_session.commit()

    _, tags = await search(mem_session, env.admin, q="auth")
    # "auth" hits name "Authentication" AND slug "auth-flow"; "graph" excluded.
    assert {t.slug for t in tags} == {"authn", "auth-flow"}


# ---------------------------------------------------------------------------
# AC3: ?tags AND-filter (ALL/intersection); unknown slug → empty.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tags_and_filter_intersection(
    mem_session: AsyncSession, env: Env
) -> None:
    both = _make_ticket(env.board_ph, env.admin, "PH-1", title="match all")
    only_one = _make_ticket(env.board_ph, env.admin, "PH-2", title="match one")
    neither = _make_ticket(env.board_ph, env.admin, "PH-3", title="match none")
    tag_a = _make_tag("auth", "Auth")
    tag_g = _make_tag("graph", "Graph")
    mem_session.add_all([both, only_one, neither, tag_a, tag_g])
    await mem_session.flush()
    mem_session.add_all(
        [_attach(both, tag_a), _attach(both, tag_g), _attach(only_one, tag_a)]
    )
    await mem_session.commit()

    # q matches all three by "match"; ?tags=auth,graph → only the ticket with BOTH.
    tickets, _ = await search(mem_session, env.admin, q="match", tags="auth,graph")
    assert {t.key for t in tickets} == {"PH-1"}  # only_one (auth-only) excluded


@pytest.mark.asyncio
async def test_tags_unknown_slug_empty_not_404(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", title="match me")
    tag_a = _make_tag("auth", "Auth")
    mem_session.add_all([t, tag_a])
    await mem_session.flush()
    mem_session.add(_attach(t, tag_a))
    await mem_session.commit()

    # Unknown slug in the AND-filter → no ticket qualifies → empty (NO raise/404).
    tickets, _ = await search(
        mem_session, env.admin, q="match", tags="auth,does-not-exist"
    )
    assert tickets == []


@pytest.mark.asyncio
async def test_tags_filter_case_insensitive_slug(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", title="match me")
    tag_a = _make_tag("auth", "Auth")
    mem_session.add_all([t, tag_a])
    await mem_session.flush()
    mem_session.add(_attach(t, tag_a))
    await mem_session.commit()

    # Slug resolved case-insensitively (lower(slug)); "AUTH" still matches "auth".
    tickets, _ = await search(mem_session, env.admin, q="match", tags="AUTH")
    assert {t.key for t in tickets} == {"PH-1"}


# ---------------------------------------------------------------------------
# AC4: grouped shape — separate typed lists, hit shapes correct.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grouped_shape_and_hit_fields(
    mem_session: AsyncSession, env: Env
) -> None:
    ticket = _make_ticket(env.board_ph, env.admin, "PH-1", title="shared term here")
    tag = _make_tag("shared", "Shared term tag", color="#7dd3fc")
    mem_session.add_all([ticket, tag])
    await mem_session.commit()

    tickets, tags = await search(mem_session, env.admin, q="shared")

    # Two SEPARATE typed groups (never a mixed list).
    hit = ticket_search_hit(tickets[0])
    assert isinstance(hit, TicketSearchHit)
    assert hit.key == "PH-1" and hit.board == "PH" and hit.state == "backlog"
    assert hit.board_id == env.board_ph.id and hit.title == "shared term here"

    summary = concept_tag_summary(tags[0])
    assert isinstance(summary, ConceptTagSummary)
    assert summary.slug == "shared" and summary.name == "Shared term tag"
    assert summary.color == "#7dd3fc"


# ---------------------------------------------------------------------------
# AC5: case-insensitive (lower AND upper hit); wildcard-injection safety.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_insensitive_lower_and_upper(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", title="CrOsS Board")
    mem_session.add(t)
    await mem_session.commit()

    lower_hit, _ = await search(mem_session, env.admin, q="cross")
    upper_hit, _ = await search(mem_session, env.admin, q="CROSS")
    assert {t.key for t in lower_hit} == {"PH-1"}
    assert {t.key for t in upper_hit} == {"PH-1"}


@pytest.mark.asyncio
async def test_wildcard_injection_literal(
    mem_session: AsyncSession, env: Env
) -> None:
    pct = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="done", description="100% complete"
    )
    underscore = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="snake", description="a_b naming"
    )
    plain = _make_ticket(
        env.board_ph, env.admin, "PH-3", title="plain", description="nothing special"
    )
    mem_session.add_all([pct, underscore, plain])
    await mem_session.commit()

    # "%" must be LITERAL — match only the row containing "%", NOT every row.
    pct_hits, _ = await search(mem_session, env.admin, q="%")
    assert {t.key for t in pct_hits} == {"PH-1"}

    # "_" must be LITERAL — match only "a_b", NOT any single character.
    us_hits, _ = await search(mem_session, env.admin, q="a_b")
    assert {t.key for t in us_hits} == {"PH-2"}


# ---------------------------------------------------------------------------
# AC6: blank q → no scan, empty; result cap + ordering.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blank_q_short_circuits_empty(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", title="anything")
    tag = _make_tag("anything", "Anything")
    mem_session.add_all([t, tag])
    await mem_session.commit()

    for blank in (None, "", "   ", "\t\n"):
        tickets, tags = await search(mem_session, env.admin, q=blank)
        assert tickets == [] and tags == []

    # tags-only (no q) still empty — q-centric v1 (tags is a restriction, not browse).
    tickets, tags = await search(mem_session, env.admin, q="  ", tags="anything")
    assert tickets == [] and tags == []


@pytest.mark.asyncio
async def test_blank_q_issues_no_scan(mem_session: AsyncSession, env: Env) -> None:
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        seen.append(statement)

    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", _record)
    try:
        await search(mem_session, env.admin, q="   ")
    finally:
        event.remove(sync_engine, "before_cursor_execute", _record)
    # The permission gate loads memberships (+ board selectinload); that is the
    # ONLY DB traffic. The blank-q short-circuit must issue NO scan over the
    # tickets or concept_tags tables (no LIKE).
    assert not any("LIKE" in stmt.upper() for stmt in seen), seen
    assert not any("FROM tickets" in stmt for stmt in seen), seen
    assert not any("FROM concept_tags" in stmt for stmt in seen), seen


@pytest.mark.asyncio
async def test_result_cap_and_ordering(mem_session: AsyncSession, env: Env) -> None:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    # 60 matching tickets (> _SEARCH_LIMIT=50), each a distinct updated_at.
    tickets = [
        _make_ticket(
            env.board_ph,
            env.admin,
            f"PH-{i}",
            title=f"capword {i}",
            updated_at=base + timedelta(minutes=i),
        )
        for i in range(60)
    ]
    # 60 matching tags too (> cap).
    tags = [_make_tag(f"capslug-{i:02d}", f"capword {i}") for i in range(60)]
    mem_session.add_all([*tickets, *tags])
    await mem_session.commit()

    hits, tag_hits = await search(mem_session, env.admin, q="capword")
    assert len(hits) == _SEARCH_LIMIT == 50
    assert len(tag_hits) == _SEARCH_LIMIT == 50

    # tickets newest-first (updated_at desc) → PH-59..PH-10 the top 50.
    assert hits[0].key == "PH-59"
    # tags slug-ascending → capslug-00..capslug-49.
    assert tag_hits[0].slug == "capslug-00"
    assert tag_hits[-1].slug == "capslug-49"


# ---------------------------------------------------------------------------
# AC7: N+1 — statement count CONSTANT across 1 vs 50 tickets.
# ---------------------------------------------------------------------------


class _StatementCounter:
    """Counts SQL statements via the engine's before_cursor_execute event."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        self.count += 1


async def _seed_n_matching_tickets(
    session: AsyncSession, board: Board, reporter: Actor, n: int
) -> None:
    tag = _make_tag(f"n1-{uuid.uuid4().hex[:6]}", "N1 Tag")
    session.add(tag)
    await session.flush()
    tickets = [
        _make_ticket(board, reporter, f"{board.key}-{uuid.uuid4().hex[:6]}", title="needle row")
        for _ in range(n)
    ]
    session.add_all(tickets)
    await session.flush()
    session.add_all([_attach(t, tag) for t in tickets])
    await session.commit()


@pytest.mark.asyncio
async def test_no_n_plus_one_constant_statement_count(
    mem_session: AsyncSession, env: Env
) -> None:
    await _seed_n_matching_tickets(mem_session, env.board_ph, env.admin, 1)
    counter_1 = _StatementCounter()
    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        tickets_1, _ = await search(mem_session, env.admin, q="needle")
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    await _seed_n_matching_tickets(mem_session, env.board_kim, env.admin, 50)
    counter_50 = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_50)
    try:
        tickets_50, _ = await search(mem_session, env.admin, q="needle")
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_50)

    # Serialize all hits (walks board.key + tags) — would trip lazy access if N+1.
    [ticket_search_hit(t) for t in tickets_50]
    assert len(tickets_1) == 1
    assert len(tickets_50) == _SEARCH_LIMIT  # capped at 50 (51 match)
    assert counter_1.count == counter_50.count, (
        f"N+1 detected: 1 ticket={counter_1.count} stmts, "
        f"51 tickets={counter_50.count} stmts"
    )


# ---------------------------------------------------------------------------
# AC8: permission — actor lacking tag.read → 403.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_global_tag_read(
    mem_session: AsyncSession, env: Env
) -> None:
    # Stranger has no membership → no tag.read → PermissionDenied (gate runs first,
    # even before the q short-circuit).
    with pytest.raises(PermissionDenied):
        await search(mem_session, env.stranger, q="anything")
    with pytest.raises(PermissionDenied):
        await search(mem_session, env.stranger, q="")

    # Admin (member, role "*") passes — no raise.
    tickets, tags = await search(mem_session, env.admin, q="anything")
    assert isinstance(tickets, list) and isinstance(tags, list)
