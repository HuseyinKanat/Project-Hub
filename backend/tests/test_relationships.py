"""Service-layer tests for the relationship-scoring service (PH-284, epic PH-283
child A — FOUNDATION).

Tests ``services.relationships.related_tickets`` DIRECTLY against an in-memory
sqlite ``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env),
mirroring ``tests/test_graph.py`` / ``test_search.py`` seed style. Also exercises
the MCP dispatch arm (``_dispatch_tool("related_tickets", ...)``) so the tool is
proven callable end-to-end.

Coverage:
- shared-label / reference (both directions) / epic relations + correct reasons.
- scoring order (reference > epic > single-label) + the multi-relation sum.
- cross_board on (other-board relations included) / off (same-board only).
- self-exclusion; no-relation ticket → [] ; limit truncates highest-first.
- read-gate: actor without ticket.read → PermissionDenied; normal role → success.
- unknown key → NotFound; dispatch arm returns the list shape + surfaces NotFound.
- N+1: statement count CONSTANT across 1 vs N related tickets.
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

from app.core.exceptions import NotFound, PermissionDenied
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Ticket, Workflow
from app.mcp.server import _dispatch_tool
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.relationships import related_tickets


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
    """Two boards (PH + KIM), an admin member of both, a no-membership stranger."""

    def __init__(
        self,
        *,
        board_ph: Board,
        board_kim: Board,
        admin: Actor,
        stranger: Actor,
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


_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _make_ticket(
    board: Board,
    reporter: Actor,
    key: str,
    *,
    epic_id: uuid.UUID | None = None,
    deleted: bool = False,
    description: str = "",
    labels: list[str] | None = None,
    updated_offset: int = 0,
) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=f"Ticket {key}",
        description=description,
        state="backlog",
        reporter_id=reporter.id,
        epic_id=epic_id,
        labels=labels or [],
        updated_at=_BASE_TIME + timedelta(minutes=updated_offset),
        deleted_at=datetime.now(UTC) if deleted else None,
    )


def _by_key(items: list, key: str):  # type: ignore[no-untyped-def]
    for item in items:
        if item.key == key:
            return item
    raise AssertionError(f"{key} not in {[i.key for i in items]}")


# ---------------------------------------------------------------------------
# shared-label relation + reasons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_label_relation(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["backend", "agent-mcp"])
    cand = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["backend", "ui"])
    other = _make_ticket(env.board_ph, env.admin, "PH-3", labels=["unrelated"])
    mem_session.add_all([src, cand, other])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")

    assert [r.key for r in out] == ["PH-2"]  # PH-3 shares nothing
    rel = _by_key(out, "PH-2")
    assert rel.score == 1.0  # one shared label
    assert len(rel.reasons) == 1
    assert rel.reasons[0].type == "shared_label"
    assert "backend" in rel.reasons[0].detail


@pytest.mark.asyncio
async def test_shared_label_count_capped(mem_session: AsyncSession, env: Env) -> None:
    labels = ["a", "b", "c", "d", "e"]
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=labels)
    cand = _make_ticket(env.board_ph, env.admin, "PH-2", labels=labels)  # 5 shared
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    # min(5, 3) * 1.0 = 3.0 — cap prevents hub-label runaway.
    assert _by_key(out, "PH-2").score == 3.0


# ---------------------------------------------------------------------------
# reference relation (both directions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_outbound(mem_session: AsyncSession, env: Env) -> None:
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="depends on PH-2 for the seam"
    )
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    rel = _by_key(out, "PH-2")
    assert rel.score == 5.0
    assert rel.reasons[0].type == "reference"
    assert "PH-1 → PH-2" in rel.reasons[0].detail


@pytest.mark.asyncio
async def test_reference_inbound(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1")
    cand = _make_ticket(
        env.board_ph, env.admin, "PH-2", description="implements part of PH-1"
    )
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    rel = _by_key(out, "PH-2")
    assert rel.score == 5.0
    assert rel.reasons[0].type == "reference"
    assert "PH-2 → PH-1" in rel.reasons[0].detail


@pytest.mark.asyncio
async def test_reference_word_boundary_no_false_positive(
    mem_session: AsyncSession, env: Env
) -> None:
    # PH-2 must NOT match a description that only mentions PH-28.
    src = _make_ticket(env.board_ph, env.admin, "PH-2")
    decoy = _make_ticket(
        env.board_ph, env.admin, "PH-28", description="see PH-280 and PH-281"
    )
    mem_session.add_all([src, decoy])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-2")
    assert out == []  # no spurious inbound reference from PH-28's description


# ---------------------------------------------------------------------------
# epic relation (sibling / parent / child)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_epic_sibling_and_child_and_parent(
    mem_session: AsyncSession, env: Env
) -> None:
    epic = _make_ticket(env.board_ph, env.admin, "PH-100")
    mem_session.add(epic)
    await mem_session.flush()
    src = _make_ticket(env.board_ph, env.admin, "PH-1", epic_id=epic.id)
    sibling = _make_ticket(env.board_ph, env.admin, "PH-2", epic_id=epic.id)
    mem_session.add_all([src, sibling])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    keys = {r.key for r in out}
    assert keys == {"PH-2", "PH-100"}  # sibling + parent
    sib = _by_key(out, "PH-2")
    assert sib.score == 3.0
    assert sib.reasons[0].type == "epic"
    parent = _by_key(out, "PH-100")
    assert parent.score == 3.0
    assert "epic of PH-1" in parent.reasons[0].detail


@pytest.mark.asyncio
async def test_epic_children_of_src(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-100")  # an epic
    mem_session.add(src)
    await mem_session.flush()
    child = _make_ticket(env.board_ph, env.admin, "PH-1", epic_id=src.id)
    mem_session.add(child)
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-100")
    assert [r.key for r in out] == ["PH-1"]
    assert "child of epic PH-100" in _by_key(out, "PH-1").reasons[0].detail


# ---------------------------------------------------------------------------
# multi-relation scoring sum + ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_relation_sum_and_ordering(
    mem_session: AsyncSession, env: Env
) -> None:
    epic = _make_ticket(env.board_ph, env.admin, "PH-100")
    mem_session.add(epic)
    await mem_session.flush()

    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        epic_id=epic.id,
        labels=["x", "y"],
        description="builds on PH-2",
    )
    # PH-2: referenced by src AND epic sibling AND shares 2 labels → 5+3+2 = 10.
    combo = _make_ticket(
        env.board_ph, env.admin, "PH-2", epic_id=epic.id, labels=["x", "y", "z"]
    )
    # PH-3: epic sibling only → 3.
    epic_only = _make_ticket(env.board_ph, env.admin, "PH-3", epic_id=epic.id)
    # PH-4: single shared label only → 1.
    label_only = _make_ticket(env.board_ph, env.admin, "PH-4", labels=["x"])
    mem_session.add_all([src, combo, epic_only, label_only])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    # PH-100 (parent epic) also relates (score 3). Order is score desc.
    assert _by_key(out, "PH-2").score == 10.0
    assert {r.type for r in _by_key(out, "PH-2").reasons} == {
        "reference",
        "epic",
        "shared_label",
    }
    scores = [r.score for r in out]
    assert scores == sorted(scores, reverse=True)  # score desc
    assert out[0].key == "PH-2"  # the combo wins
    assert _by_key(out, "PH-4").score == 1.0  # single label weakest


@pytest.mark.asyncio
async def test_tiebreak_updated_at_then_key(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    # Two equal-score (single shared label) candidates; newer updated_at first,
    # then key asc on a further tie.
    older = _make_ticket(
        env.board_ph, env.admin, "PH-2", labels=["shared"], updated_offset=1
    )
    newer = _make_ticket(
        env.board_ph, env.admin, "PH-3", labels=["shared"], updated_offset=99
    )
    mem_session.add_all([src, older, newer])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert [r.key for r in out] == ["PH-3", "PH-2"]  # newer updated_at first


# ---------------------------------------------------------------------------
# cross_board on / off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_board_true_includes_other_board(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    foreign = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["shared"])
    mem_session.add_all([src, foreign])
    await mem_session.commit()

    out = await related_tickets(
        mem_session, env.admin, ticket="PH-1", cross_board=True
    )
    rel = _by_key(out, "KIM-1")
    assert rel.board == "KIM"


@pytest.mark.asyncio
async def test_cross_board_false_restricts_to_src_board(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["shared"], description="ref KIM-1"
    )
    same = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["shared"])
    foreign = _make_ticket(
        env.board_kim,
        env.admin,
        "KIM-1",
        labels=["shared"],
        description="mentions PH-1",
    )
    mem_session.add_all([src, same, foreign])
    await mem_session.commit()

    out = await related_tickets(
        mem_session, env.admin, ticket="PH-1", cross_board=False
    )
    assert {r.key for r in out} == {"PH-2"}  # KIM-1 excluded by board filter
    assert all(r.board == "PH" for r in out)


# ---------------------------------------------------------------------------
# self-exclusion / empty / limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_excluded(mem_session: AsyncSession, env: Env) -> None:
    # src shares its own labels + references itself — must NOT appear.
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        labels=["x"],
        description="self ref PH-1",
    )
    mem_session.add(src)
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert out == []


@pytest.mark.asyncio
async def test_no_relations_returns_empty(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["lonely"])
    other = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["nothing"])
    mem_session.add_all([src, other])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert out == []


@pytest.mark.asyncio
async def test_limit_truncates_highest_first(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["shared"], description="ref PH-2"
    )
    high = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["shared"])  # ref+label
    lows = [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["shared"])
        for i in range(3, 8)
    ]
    mem_session.add_all([src, high, *lows])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1", limit=2)
    assert len(out) == 2
    assert out[0].key == "PH-2"  # highest score retained


# ---------------------------------------------------------------------------
# read-gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_gate_denies_stranger(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["x"])
    mem_session.add(src)
    await mem_session.commit()

    with pytest.raises(PermissionDenied):
        await related_tickets(mem_session, env.stranger, ticket="PH-1")


@pytest.mark.asyncio
async def test_read_gate_allows_member(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["x"])
    cand = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["x"])
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert [r.key for r in out] == ["PH-2"]


# ---------------------------------------------------------------------------
# unknown key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_key_raises_not_found(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(NotFound):
        await related_tickets(mem_session, env.admin, ticket="PH-999")


# ---------------------------------------------------------------------------
# MCP dispatch arm (tool is callable end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_arm_returns_list_shape(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["backend"], description="ref PH-2"
    )
    cand = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["backend"])
    mem_session.add_all([src, cand])
    await mem_session.commit()

    result = await _dispatch_tool(
        "related_tickets",
        {"ticket": "PH-1", "cross_board": True, "limit": 10},
        env.admin,
        mem_session,
    )
    assert isinstance(result, list)
    assert result[0]["key"] == "PH-2"
    assert result[0]["score"] == 6.0  # reference 5 + 1 shared label
    assert {r["type"] for r in result[0]["reasons"]} == {"reference", "shared_label"}


@pytest.mark.asyncio
async def test_dispatch_arm_surfaces_not_found(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(NotFound):
        await _dispatch_tool(
            "related_tickets", {"ticket": "PH-999"}, env.admin, mem_session
        )


# ---------------------------------------------------------------------------
# tool registration: TOOLS / _TOOL_INPUT_MODELS in sync → tools/list includes it
# ---------------------------------------------------------------------------


def test_tool_registered_in_tools_list() -> None:
    from app.mcp.server import _build_mcp_tool_list

    names = {t["name"] for t in _build_mcp_tool_list()}
    assert "related_tickets" in names
    entry = next(t for t in _build_mcp_tool_list() if t["name"] == "related_tickets")
    # inputSchema advertises the three params.
    props = entry["inputSchema"]["properties"]
    assert {"ticket", "cross_board", "limit"} <= set(props)


# ---------------------------------------------------------------------------
# N+1 — statement count CONSTANT across 1 vs N related tickets
# ---------------------------------------------------------------------------


class _StatementCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        self.count += 1


@pytest.mark.asyncio
async def test_no_n_plus_one_constant_statement_count(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    mem_session.add(src)
    mem_session.add(_make_ticket(env.board_ph, env.admin, "PH-2", labels=["shared"]))
    await mem_session.commit()

    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    counter_1 = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        await related_tickets(mem_session, env.admin, ticket="PH-1")
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    # Add 20 more related-by-label tickets.
    mem_session.add_all(
        [
            _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["shared"])
            for i in range(3, 23)
        ]
    )
    await mem_session.commit()

    counter_n = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_n)
    try:
        out = await related_tickets(mem_session, env.admin, ticket="PH-1", limit=100)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_n)

    assert len(out) == 21  # all label-related loaded
    assert counter_1.count == counter_n.count, (
        f"N+1 detected: 1 related={counter_1.count} stmts, "
        f"21 related={counter_n.count} stmts"
    )
