"""Service-layer tests for cross-board search (PH-275; re-pointed to labels PH-281).

Tests ``services.search.search`` DIRECTLY against an in-memory sqlite
``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env). Seeds via
``Base.metadata.create_all``.

PH-281: the ConceptTag group is gone; search now matches/returns inline
``Ticket.labels`` strings via the dialect-aware unnest. Coverage:
- q matches ticket title OR description OR key, cross-board (TWO boards).
- q matches a label value → ticket appears in the ticket group + labels group.
- labels group = distinct matching label strings (list[str]).
- ?labels AND-filter (ALL/intersection, EXACT membership, no substring leakage).
- unknown label in ?labels → empty (NOT 404).
- case-insensitive; wildcard-injection safety (%/_ literal); LIKE-char label.
- blank/whitespace q → no scan, empty; result cap (LIMIT 50).
- N+1 — statement count CONSTANT across 1 vs 50 tickets.
- permission — actor lacking ticket.read → 403 PermissionDenied.
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
    Ticket,
    Workflow,
)
from app.schemas import TicketSearchHit
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.search import _SEARCH_LIMIT, search
from app.services.serializers import ticket_search_hit


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
    # `stranger` has NO board membership → no ticket.read → 403.
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
    labels: list[str] | None = None,
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
        labels=labels or [],
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# q matches ticket title/description/key, cross-board.
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
    assert {t.key for t in tickets} == {"PH-1", "KIM-1"}
    # board is eager-loaded (selectinload) → reading .key is MissingGreenlet-safe.
    assert {t.board.key for t in tickets} == {"PH", "KIM"}


# ---------------------------------------------------------------------------
# q matches a label value → ticket group + labels group.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_matches_label_value(mem_session: AsyncSession, env: Env) -> None:
    labeled = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="no term here", labels=["authflow"]
    )
    other = _make_ticket(env.board_ph, env.admin, "PH-2", title="unrelated")
    mem_session.add_all([labeled, other])
    await mem_session.commit()

    # "auth" hits ONLY the label value (not title/desc/key) → ticket still returned.
    tickets, labels = await search(mem_session, env.admin, q="auth")
    assert {t.key for t in tickets} == {"PH-1"}
    # labels group = distinct matching label strings.
    assert labels == ["authflow"]


@pytest.mark.asyncio
async def test_labels_group_distinct_strings(
    mem_session: AsyncSession, env: Env
) -> None:
    a = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["graphview", "graphql"])
    b = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["graphview"])
    miss = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["unrelated"])
    mem_session.add_all([a, b, miss])
    await mem_session.commit()

    _, labels = await search(mem_session, env.admin, q="graph")
    # Distinct + sorted; "graphview" appears on two tickets but ONCE here.
    assert labels == ["graphql", "graphview"]


# ---------------------------------------------------------------------------
# ?labels AND-filter (ALL/intersection, EXACT membership); unknown → empty.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_labels_and_filter_intersection(
    mem_session: AsyncSession, env: Env
) -> None:
    both = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="match all", labels=["auth", "graph"]
    )
    only_one = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="match one", labels=["auth"]
    )
    neither = _make_ticket(env.board_ph, env.admin, "PH-3", title="match none")
    mem_session.add_all([both, only_one, neither])
    await mem_session.commit()

    # q matches all three by "match"; ?labels=auth,graph → only the ticket with BOTH.
    tickets, _ = await search(mem_session, env.admin, q="match", labels="auth,graph")
    assert {t.key for t in tickets} == {"PH-1"}  # only_one (auth-only) excluded


@pytest.mark.asyncio
async def test_labels_exact_no_substring_leakage(
    mem_session: AsyncSession, env: Env
) -> None:
    # EXACT membership: ?labels=bug must NOT match a ticket carrying "debugger".
    exact = _make_ticket(env.board_ph, env.admin, "PH-1", title="match", labels=["bug"])
    leak = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="match", labels=["debugger"]
    )
    mem_session.add_all([exact, leak])
    await mem_session.commit()

    tickets, _ = await search(mem_session, env.admin, q="match", labels="bug")
    assert {t.key for t in tickets} == {"PH-1"}  # "debugger" NOT matched


@pytest.mark.asyncio
async def test_labels_unknown_value_empty_not_404(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", title="match me", labels=["auth"])
    mem_session.add(t)
    await mem_session.commit()

    # Unknown value in the AND-filter → no ticket qualifies → empty (NO raise/404).
    tickets, _ = await search(
        mem_session, env.admin, q="match", labels="auth,does-not-exist"
    )
    assert tickets == []


@pytest.mark.asyncio
async def test_labels_filter_like_wildcard_char(
    mem_session: AsyncSession, env: Env
) -> None:
    # A label containing LIKE-wildcard chars (%/_) must match LITERALLY (exact),
    # not as a LIKE pattern. EXACT membership compares the value verbatim.
    pct = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="match", labels=["50%off"]
    )
    other = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="match", labels=["50xoff"]
    )
    mem_session.add_all([pct, other])
    await mem_session.commit()

    tickets, _ = await search(mem_session, env.admin, q="match", labels="50%off")
    # Exact "50%off" — the "%" is a literal, NOT a wildcard → PH-2 excluded.
    assert {t.key for t in tickets} == {"PH-1"}


# ---------------------------------------------------------------------------
# grouped shape — separate typed lists, hit shapes correct.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grouped_shape_and_hit_fields(
    mem_session: AsyncSession, env: Env
) -> None:
    ticket = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="shared term here", labels=["sharedlabel"]
    )
    mem_session.add(ticket)
    await mem_session.commit()

    tickets, labels = await search(mem_session, env.admin, q="shared")

    # Ticket group is TicketSearchHit; labels group is a list[str].
    hit = ticket_search_hit(tickets[0])
    assert isinstance(hit, TicketSearchHit)
    assert hit.key == "PH-1" and hit.board == "PH" and hit.state == "backlog"
    assert hit.board_id == env.board_ph.id and hit.title == "shared term here"

    assert labels == ["sharedlabel"]
    assert all(isinstance(value, str) for value in labels)


# ---------------------------------------------------------------------------
# case-insensitive (lower AND upper hit); wildcard-injection safety.
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
async def test_label_search_case_insensitive(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["MixedCase"])
    mem_session.add(t)
    await mem_session.commit()

    _, labels = await search(mem_session, env.admin, q="mixedcase")
    assert labels == ["MixedCase"]  # ci match, original casing preserved


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
# blank q → no scan, empty; result cap + ordering.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blank_q_short_circuits_empty(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", title="anything", labels=["any"])
    mem_session.add(t)
    await mem_session.commit()

    for blank in (None, "", "   ", "\t\n"):
        tickets, labels = await search(mem_session, env.admin, q=blank)
        assert tickets == [] and labels == []

    # labels-only (no q) still empty — q-centric v1 (labels is a restriction).
    tickets, labels = await search(mem_session, env.admin, q="  ", labels="any")
    assert tickets == [] and labels == []


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
    # ONLY DB traffic. The blank-q short-circuit must issue NO LIKE scan over the
    # tickets table.
    assert not any("LIKE" in stmt.upper() for stmt in seen), seen
    assert not any("FROM tickets" in stmt for stmt in seen), seen


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
    mem_session.add_all(tickets)
    await mem_session.commit()

    hits, _ = await search(mem_session, env.admin, q="capword")
    assert len(hits) == _SEARCH_LIMIT == 50
    # tickets newest-first (updated_at desc) → PH-59..PH-10 the top 50.
    assert hits[0].key == "PH-59"


# ---------------------------------------------------------------------------
# N+1 — statement count CONSTANT across 1 vs 50 tickets.
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
    tickets = [
        _make_ticket(
            board,
            reporter,
            f"{board.key}-{uuid.uuid4().hex[:6]}",
            title="needle row",
            labels=["n1tag"],
        )
        for _ in range(n)
    ]
    session.add_all(tickets)
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

    # Serialize all hits (walks board.key) — would trip lazy access if N+1.
    [ticket_search_hit(t) for t in tickets_50]
    assert len(tickets_1) == 1
    assert len(tickets_50) == _SEARCH_LIMIT  # capped at 50 (51 match)
    assert counter_1.count == counter_50.count, (
        f"N+1 detected: 1 ticket={counter_1.count} stmts, "
        f"51 tickets={counter_50.count} stmts"
    )


# ---------------------------------------------------------------------------
# permission — actor lacking ticket.read → 403.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_global_ticket_read(
    mem_session: AsyncSession, env: Env
) -> None:
    # Stranger has no membership → no ticket.read → PermissionDenied (gate runs
    # first, even before the q short-circuit).
    with pytest.raises(PermissionDenied):
        await search(mem_session, env.stranger, q="anything")
    with pytest.raises(PermissionDenied):
        await search(mem_session, env.stranger, q="")

    # Admin (member, role "*") passes — no raise.
    tickets, labels = await search(mem_session, env.admin, q="anything")
    assert isinstance(tickets, list) and isinstance(labels, list)


# ---------------------------------------------------------------------------
# PH-282 — compiled-SQL guard: the Postgres unnest path MUST alias the unnested
# element column as `value` (column-derivation list), so `anon_X.value` resolves.
# SQLite CANNOT reproduce the original 500 (json_each natively exposes `value`),
# so this dialect-compiled assertion is the CI guard that catches a regression
# WITHOUT a live Postgres — the original defect was `unnest(labels) AS anon_1`
# (no derived column) → asyncpg `column anon_1.value does not exist`.
# ---------------------------------------------------------------------------


def _compile_pg(stmt: object) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def test_pg_unnest_aliases_element_column_as_value() -> None:
    """On the PG dialect the unnested column must be derived as ``value``.

    Asserts the compiled SQL contains ``unnest(... .labels) AS <alias>(value)``
    (the parenthesised column-derivation list) rather than the bare
    ``unnest(...) AS <alias>`` that triggered the /api/search 500. We assert via
    the public service callables so the guard tracks however ``labels.py`` builds
    the table-valued alias.
    """
    import re

    from app.services.labels import (
        label_match_predicate,
        label_substring_predicate,
        labels_reach_query,
    )

    # Every label predicate routes through the same dialect helper; check all three.
    sql_substr = _compile_pg(select(label_substring_predicate("postgresql", "bug")))
    sql_match = _compile_pg(select(label_match_predicate("postgresql", "bug")))
    sql_reach = _compile_pg(labels_reach_query("postgresql", {"bug"}, "board-x"))

    derived = re.compile(r"unnest\([^)]*\.labels\)\s+AS\s+\w+\(value\)", re.IGNORECASE)
    for label, sql in (
        ("substring", sql_substr),
        ("match", sql_match),
        ("reach", sql_reach),
    ):
        assert "unnest" in sql.lower(), f"{label}: PG branch must use unnest(), got: {sql}"
        assert derived.search(sql), (
            f"{label}: PG unnest must alias the element column as `value` "
            f"(expected `unnest(...) AS <alias>(value)`), got: {sql}"
        )


def test_sqlite_json_each_keeps_native_value_column() -> None:
    """sqlite must NOT render a derived column list — ``json_each`` rejects
    ``json_each(...) AS x(value)`` (syntax error) and natively exposes ``value``.
    Guards against accidentally applying the PG ``render_derived`` fix to sqlite.
    """
    from sqlalchemy.dialects import sqlite

    from app.services.labels import label_match_predicate

    sql = str(
        select(label_match_predicate("sqlite", "bug")).compile(dialect=sqlite.dialect())
    )
    assert "json_each" in sql, f"sqlite branch must use json_each, got: {sql}"
    # No parenthesised column-derivation list after the json_each alias.
    assert "(value)" not in sql.replace(" ", ""), (
        f"sqlite json_each must NOT carry a derived column list, got: {sql}"
    )
