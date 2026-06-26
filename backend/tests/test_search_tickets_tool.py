"""MCP-dispatch tests for the ``search_tickets`` tool (PH-285, epic PH-283 child B).

A THIN read-level wrapper over ``app.services.search.search`` (the same
cross-board service powering ``/api/search``). These tests exercise the MCP
dispatch arm (``_dispatch_tool("search_tickets", ...)``) + the tool registration
(``_build_mcp_tool_list``) directly against an in-memory sqlite session — mirroring
``tests/test_relationships.py`` seed style (NOT via HTTP TestClient, which hangs in
this Docker env).

Coverage (AC-aligned):
- grouped {tickets, labels} SearchResponse shape; q matches title|label.
- labels exact AND-membership filter (list[str] → CSV passthrough).
- boards post-filter on board.key; states post-filter on Ticket.state.
- limit applied AFTER the post-filters (labels group not truncated).
- blank/whitespace q short-circuits to {tickets: [], labels: []}.
- read-gate: stranger denied (PermissionDenied surfaced); pm allowed.
- tool registered in tools/list with {q, labels, boards, states, limit} props
  (TOOLS catalog + _TOOL_INPUT_MODELS map stay in sync).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.exceptions import PermissionDenied
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Ticket, Workflow
from app.mcp.server import _build_mcp_tool_list, _dispatch_tool
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


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
    """Two boards (PH + KIM), an admin member of both, a no-membership stranger,
    plus a `pm`-role member of PH (read-gate)."""

    def __init__(
        self,
        *,
        board_ph: Board,
        board_kim: Board,
        admin: Actor,
        stranger: Actor,
        pm: Actor,
    ) -> None:
        self.board_ph = board_ph
        self.board_kim = board_kim
        self.admin = admin
        self.stranger = stranger
        self.pm = pm


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
    pm = Actor(kind="agent", display_name="jarwis-pm", token_hash="p", is_active=True)
    mem_session.add_all([admin, stranger, pm])
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
            BoardMembership(board_id=board_ph.id, actor_id=pm.id, role="pm"),
        ]
    )
    await mem_session.commit()

    return Env(
        board_ph=board_ph,
        board_kim=board_kim,
        admin=await _reload_actor(mem_session, admin.id),
        stranger=await _reload_actor(mem_session, stranger.id),
        pm=await _reload_actor(mem_session, pm.id),
    )


def _make_ticket(
    board: Board,
    reporter: Actor,
    key: str,
    *,
    title: str | None = None,
    description: str = "",
    labels: list[str] | None = None,
    state: str = "backlog",
) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=title if title is not None else f"Ticket {key}",
        description=description,
        state=state,
        reporter_id=reporter.id,
        labels=labels or [],
    )


async def _call(
    session: AsyncSession, actor: Actor, **payload: object
) -> dict[str, object]:
    result = await _dispatch_tool("search_tickets", payload, actor, session)
    assert isinstance(result, dict)
    return result


def _keys(result: dict[str, object]) -> set[str]:
    return {t["key"] for t in result["tickets"]}  # type: ignore[union-attr,index]


# ---------------------------------------------------------------------------
# grouped shape + q match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_tickets_returns_grouped_shape(
    mem_session: AsyncSession, env: Env
) -> None:
    hit = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="graph rendering bug", labels=["frontend"]
    )
    miss = _make_ticket(env.board_ph, env.admin, "PH-2", title="unrelated thing")
    mem_session.add_all([hit, miss])
    await mem_session.commit()

    result = await _call(mem_session, env.admin, q="graph")

    # Grouped SearchResponse: tickets + labels keys, never a mixed list.
    assert set(result.keys()) == {"tickets", "labels"}
    assert isinstance(result["tickets"], list)
    assert isinstance(result["labels"], list)
    assert _keys(result) == {"PH-1"}
    hit_row = result["tickets"][0]  # type: ignore[index]
    # Identity-only TicketSearchHit shape.
    assert set(hit_row.keys()) == {"id", "key", "title", "board", "board_id", "state"}
    assert hit_row["board"] == "PH"


@pytest.mark.asyncio
async def test_q_matches_label_value(mem_session: AsyncSession, env: Env) -> None:
    """A ticket whose ONLY match is a LABEL value still surfaces (service OR-clause)."""
    via_label = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="zzz", labels=["relationship-scoring"]
    )
    mem_session.add(via_label)
    await mem_session.commit()

    result = await _call(mem_session, env.admin, q="relationship-scoring")
    assert "PH-1" in _keys(result)
    # The labels group surfaces the matching distinct label string.
    assert "relationship-scoring" in result["labels"]  # type: ignore[operator]


# ---------------------------------------------------------------------------
# labels AND-membership filter (list[str] → CSV)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_labels_filter_and_membership(
    mem_session: AsyncSession, env: Env
) -> None:
    both = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="search wrapper", labels=["backend", "agent-mcp"]
    )
    one = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="search helper", labels=["backend"]
    )
    mem_session.add_all([both, one])
    await mem_session.commit()

    # Single label → both carriers.
    out_one = await _call(mem_session, env.admin, q="search", labels=["backend"])
    assert _keys(out_one) == {"PH-1", "PH-2"}

    # Two labels → only the ticket carrying BOTH (AND-intersection).
    out_both = await _call(
        mem_session, env.admin, q="search", labels=["backend", "agent-mcp"]
    )
    assert _keys(out_both) == {"PH-1"}

    # A label absent everywhere → empty tickets (not 404).
    out_absent = await _call(
        mem_session, env.admin, q="search", labels=["backend", "no-such-label"]
    )
    assert out_absent["tickets"] == []


# ---------------------------------------------------------------------------
# boards / states in-Python post-filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boards_post_filter(mem_session: AsyncSession, env: Env) -> None:
    ph = _make_ticket(env.board_ph, env.admin, "PH-1", title="shared term")
    kim = _make_ticket(env.board_kim, env.admin, "KIM-1", title="shared term")
    mem_session.add_all([ph, kim])
    await mem_session.commit()

    # No board filter → both boards.
    assert _keys(await _call(mem_session, env.admin, q="shared")) == {"PH-1", "KIM-1"}
    # boards=["PH"] → only PH (case-sensitive key match).
    out = await _call(mem_session, env.admin, q="shared", boards=["PH"])
    assert _keys(out) == {"PH-1"}


@pytest.mark.asyncio
async def test_states_post_filter(mem_session: AsyncSession, env: Env) -> None:
    done = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="state term", state="done"
    )
    backlog = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="state term", state="backlog"
    )
    mem_session.add_all([done, backlog])
    await mem_session.commit()

    out = await _call(mem_session, env.admin, q="state", states=["done"])
    assert _keys(out) == {"PH-1"}


@pytest.mark.asyncio
async def test_boards_and_states_combine(mem_session: AsyncSession, env: Env) -> None:
    ph_done = _make_ticket(
        env.board_ph, env.admin, "PH-1", title="combo term", state="done"
    )
    ph_backlog = _make_ticket(
        env.board_ph, env.admin, "PH-2", title="combo term", state="backlog"
    )
    kim_done = _make_ticket(
        env.board_kim, env.admin, "KIM-1", title="combo term", state="done"
    )
    mem_session.add_all([ph_done, ph_backlog, kim_done])
    await mem_session.commit()

    out = await _call(
        mem_session, env.admin, q="combo", boards=["PH"], states=["done"]
    )
    assert _keys(out) == {"PH-1"}


# ---------------------------------------------------------------------------
# limit applied AFTER post-filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_applies_after_filter(
    mem_session: AsyncSession, env: Env
) -> None:
    for i in range(1, 6):  # 5 PH matches
        mem_session.add(
            _make_ticket(env.board_ph, env.admin, f"PH-{i}", title="limit term")
        )
    await mem_session.commit()

    out = await _call(mem_session, env.admin, q="limit", limit=2)
    assert len(out["tickets"]) == 2  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_limit_after_board_filter(
    mem_session: AsyncSession, env: Env
) -> None:
    """limit caps the count AFTER boards drops the foreign hits."""
    for i in range(1, 5):
        mem_session.add(
            _make_ticket(env.board_ph, env.admin, f"PH-{i}", title="cap term")
        )
    for i in range(1, 5):
        mem_session.add(
            _make_ticket(env.board_kim, env.admin, f"KIM-{i}", title="cap term")
        )
    await mem_session.commit()

    out = await _call(mem_session, env.admin, q="cap", boards=["PH"], limit=3)
    assert len(out["tickets"]) == 3  # type: ignore[arg-type]
    assert _keys(out) <= {"PH-1", "PH-2", "PH-3", "PH-4"}


# ---------------------------------------------------------------------------
# blank q short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blank_q_returns_empty(mem_session: AsyncSession, env: Env) -> None:
    mem_session.add(
        _make_ticket(env.board_ph, env.admin, "PH-1", title="anything", labels=["x"])
    )
    await mem_session.commit()

    out = await _call(mem_session, env.admin, q="   ")
    assert out == {"tickets": [], "labels": []}


# ---------------------------------------------------------------------------
# read-gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_gate_denies_stranger(
    mem_session: AsyncSession, env: Env
) -> None:
    mem_session.add(_make_ticket(env.board_ph, env.admin, "PH-1", title="gate term"))
    await mem_session.commit()

    with pytest.raises(PermissionDenied):
        await _call(mem_session, env.stranger, q="gate")


@pytest.mark.asyncio
async def test_read_gate_allows_pm(mem_session: AsyncSession, env: Env) -> None:
    mem_session.add(_make_ticket(env.board_ph, env.admin, "PH-1", title="gate term"))
    await mem_session.commit()

    out = await _call(mem_session, env.pm, q="gate")  # no PermissionDenied
    assert _keys(out) == {"PH-1"}


# ---------------------------------------------------------------------------
# tool registration (TOOLS / map sync guard)
# ---------------------------------------------------------------------------


def test_tool_registered_in_tools_list() -> None:
    names = {t["name"] for t in _build_mcp_tool_list()}
    assert "search_tickets" in names
    entry = next(
        t for t in _build_mcp_tool_list() if t["name"] == "search_tickets"
    )
    props = entry["inputSchema"]["properties"]
    assert {"q", "labels", "boards", "states", "limit"} <= set(props)
