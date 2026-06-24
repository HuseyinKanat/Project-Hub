"""Service/permission/serializer tests for ConceptTag CRUD + attach/detach (PH-273).

Epic PH-271 child 2/7. Exercises the API service layer DIRECTLY against the
in-memory async session (the same pattern PH-272's model tests use), NOT the HTTP
TestClient — the websocket/auth TestClient fixture hangs in this Docker env
(CLAUDE.md "Test DB" note), so service-layer coverage is what is real + runnable
here. Each test maps to an acceptance criterion:

- AC1: tag.read is GLOBAL — a board-A-only member lists ALL tags board-independently.
- AC2: tag.manage is admin-gated — non-admin create → 403; admin → created.
- AC3: tag.assign is board-scoped — holder attaches (200); non-holder → 403.
- AC4: attach is idempotent-success — re-attach creates no 2nd row, detach no-op.
- AC5: tag↔tag link manage + selectinload N+1-free + self-loop(422)/dup(409)/reverse-ok.
- AC6: ticket serializer renders concept_tags SEPARATE from labels (no MissingGreenlet).
- AC8: the new caps live in DEFAULT_WEB_ROLES + KNOWN_PERMISSIONS; no migration.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    Conflict,
    InvalidConceptLink,
    NotFound,
    PermissionDenied,
)
from app.core.permissions import KNOWN_PERMISSIONS
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    ConceptTagLink,
    Ticket,
    TicketConceptTag,
    Workflow,
)
from app.schemas import ConceptTagCreate, ConceptTagLinkCreate, ConceptTagUpdate
from app.services.concept_tags import (
    attach_concept_tag,
    create_concept_tag,
    create_link,
    delete_concept_tag,
    delete_link,
    detach_concept_tag,
    list_concept_tags,
    read_concept_tag,
    update_concept_tag,
)
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.serializers import concept_tag_response, ticket_response
from app.services.tickets import get_ticket


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


class Env:
    """Two boards, an admin (on board A), and a non-admin pm (on board A).

    board_a has the admin + pm; board_b is a separate board the actors are NOT
    members of (used to prove tag.read is board-independent).
    """

    def __init__(
        self,
        board_a: Board,
        board_b: Board,
        admin: Actor,
        pm: Actor,
        ticket_a: Ticket,
        ticket_b: Ticket,
    ) -> None:
        self.board_a = board_a
        self.board_b = board_b
        self.admin = admin
        self.pm = pm
        self.ticket_a = ticket_a
        self.ticket_b = ticket_b


async def _reload_actor(session: AsyncSession, actor_id: uuid.UUID) -> Actor:
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor_id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


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
    pm = Actor(kind="agent", display_name="PM Bot", token_hash="y", is_active=True)
    mem_session.add_all([admin, pm])
    await mem_session.flush()

    board_a = Board(
        key="AA",
        name="Board A",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    board_b = Board(
        key="BB",
        name="Board B",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add_all([board_a, board_b])
    await mem_session.flush()

    mem_session.add_all(
        [
            BoardMembership(board_id=board_a.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_a.id, actor_id=pm.id, role="pm"),
        ]
    )

    ticket_a = Ticket(
        id=uuid.uuid4(),
        key="AA-1",
        board_id=board_a.id,
        type="feature",
        title="Ticket A",
        state="backlog",
        reporter_id=admin.id,
        labels=["free-text-label"],
    )
    ticket_b = Ticket(
        id=uuid.uuid4(),
        key="BB-1",
        board_id=board_b.id,
        type="feature",
        title="Ticket B",
        state="backlog",
        reporter_id=admin.id,
    )
    mem_session.add_all([ticket_a, ticket_b])
    await mem_session.commit()

    return Env(
        board_a=board_a,
        board_b=board_b,
        admin=await _reload_actor(mem_session, admin.id),
        pm=await _reload_actor(mem_session, pm.id),
        ticket_a=ticket_a,
        ticket_b=ticket_b,
    )


async def _make_tag(
    session: AsyncSession, admin: Actor, slug: str, name: str
) -> uuid.UUID:
    tag, _ = await create_concept_tag(
        session, actor=admin, payload=ConceptTagCreate(slug=slug, name=name)
    )
    return tag.id


# ---------------------------------------------------------------------------
# AC8 (static): caps registered in the registry + role defaults; no migration.
# ---------------------------------------------------------------------------


def test_caps_registered_in_known_permissions() -> None:
    assert {"tag.read", "tag.manage", "tag.assign"} <= KNOWN_PERMISSIONS


def test_caps_mapped_into_default_roles() -> None:
    roles = DEFAULT_WEB_ROLES["roles"]
    assert isinstance(roles, dict)

    def perms(role: str) -> list[str]:
        return list(roles[role]["permissions"])  # type: ignore[index]

    # tag.read on every working role (admin via "*").
    for role in ("pm", "architect", "backend_dev", "reviewer", "qa", "orchestrator"):
        assert "tag.read" in perms(role), role
        assert "tag.assign" in perms(role), role
    # tag.manage is admin-only: never on a non-admin role list.
    non_admin = (
        "pm", "architect", "backend_dev", "frontend_dev",
        "reviewer", "qa", "orchestrator",
    )
    for role in non_admin:
        assert "tag.manage" not in perms(role), role
    assert perms("admin") == ["*"]


# ---------------------------------------------------------------------------
# AC2: tag.manage admin-gated — non-admin create 403, admin create 201.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tag_admin_succeeds(mem_session: AsyncSession, env: Env) -> None:
    tag, links = await create_concept_tag(
        mem_session,
        actor=env.admin,
        payload=ConceptTagCreate(slug="graph", name="Graph", color="#7dd3fc"),
    )
    assert tag.slug == "graph"
    assert len(tag.ticket_links) == 0  # ticket_count source (eager-loaded collection)
    assert links == []
    # The response serializer projects ticket_count off that collection.
    assert concept_tag_response(tag, links).ticket_count == 0


@pytest.mark.asyncio
async def test_create_tag_non_admin_forbidden(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(PermissionDenied) as exc:
        await create_concept_tag(
            mem_session, actor=env.pm, payload=ConceptTagCreate(slug="x", name="X")
        )
    assert exc.value.required == "tag.manage"


@pytest.mark.asyncio
async def test_update_and_delete_tag_admin_gated(
    mem_session: AsyncSession, env: Env
) -> None:
    tag_id = await _make_tag(mem_session, env.admin, "perf", "Perf")
    # non-admin PATCH → 403
    with pytest.raises(PermissionDenied):
        await update_concept_tag(
            mem_session,
            actor=env.pm,
            tag_id=str(tag_id),
            payload=ConceptTagUpdate(name="Renamed"),
        )
    # admin PATCH succeeds
    tag, _ = await update_concept_tag(
        mem_session,
        actor=env.admin,
        tag_id=str(tag_id),
        payload=ConceptTagUpdate(name="Renamed", color="#abcdef"),
    )
    assert tag.name == "Renamed"
    assert tag.color == "#abcdef"
    # non-admin DELETE → 403
    with pytest.raises(PermissionDenied):
        await delete_concept_tag(mem_session, actor=env.pm, tag_id=str(tag_id))
    # admin DELETE succeeds
    await delete_concept_tag(mem_session, actor=env.admin, tag_id=str(tag_id))
    with pytest.raises(NotFound):
        await read_concept_tag(mem_session, env.admin, str(tag_id))


@pytest.mark.asyncio
async def test_create_tag_duplicate_slug_conflict(
    mem_session: AsyncSession, env: Env
) -> None:
    await _make_tag(mem_session, env.admin, "dup", "Dup")
    with pytest.raises(Conflict):
        await create_concept_tag(
            mem_session, actor=env.admin, payload=ConceptTagCreate(slug="dup", name="Again")
        )


# ---------------------------------------------------------------------------
# AC1: tag.read is GLOBAL — a board-A member sees a tag attached to a board-B ticket.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_read_is_global_board_independent(
    mem_session: AsyncSession, env: Env
) -> None:
    tag_id = await _make_tag(mem_session, env.admin, "cross-board", "Cross")
    # Attach to a board-B ticket directly (admin holds tag.assign on board B? no —
    # admin is only on board A. Attach via raw junction to simulate cross-board state).
    mem_session.add(
        TicketConceptTag(ticket_id=env.ticket_b.id, concept_tag_id=tag_id)
    )
    await mem_session.commit()

    # The pm (member of board A ONLY) can still list the tag — global read.
    pairs = await list_concept_tags(mem_session, env.pm)
    by_slug = {tag.slug: (tag, links) for tag, links in pairs}
    assert "cross-board" in by_slug
    # The response carries identity + ticket_count, not board-B ticket payloads.
    tag, links = by_slug["cross-board"]
    resp = concept_tag_response(tag, links)
    assert resp.ticket_count == 1
    assert set(resp.model_dump().keys()) == {
        "id", "slug", "name", "description", "color",
        "created_at", "updated_at", "ticket_count", "links",
    }


# ---------------------------------------------------------------------------
# AC3 + AC4: board-scoped attach (200/403) + idempotent re-attach + detach no-op.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_board_scoped_and_idempotent(
    mem_session: AsyncSession, env: Env
) -> None:
    tag_id = await _make_tag(mem_session, env.admin, "attachable", "Attachable")

    # pm holds tag.assign on board A → attach succeeds, ticket renders concept_tags.
    ticket = await attach_concept_tag(
        mem_session, actor=env.pm, ticket_id="AA-1", tag_id=str(tag_id)
    )
    resp = ticket_response(ticket)
    assert [t.slug for t in resp.concept_tags] == ["attachable"]

    # Idempotent re-attach: no 2nd row, returns ticket (NOT 409).
    await attach_concept_tag(
        mem_session, actor=env.pm, ticket_id="AA-1", tag_id=str(tag_id)
    )
    rows = (
        await mem_session.execute(
            select(TicketConceptTag).where(
                TicketConceptTag.ticket_id == env.ticket_a.id,
                TicketConceptTag.concept_tag_id == tag_id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1, "re-attach must not create a duplicate junction row"

    # Detach removes it; a second detach is a benign no-op (no error).
    ticket = await detach_concept_tag(
        mem_session, actor=env.pm, ticket_id="AA-1", tag_id=str(tag_id)
    )
    assert ticket_response(ticket).concept_tags == []
    await detach_concept_tag(
        mem_session, actor=env.pm, ticket_id="AA-1", tag_id=str(tag_id)
    )  # no-op, no raise


@pytest.mark.asyncio
async def test_attach_forbidden_without_tag_assign_on_board(
    mem_session: AsyncSession, env: Env
) -> None:
    tag_id = await _make_tag(mem_session, env.admin, "boardb", "BoardB")
    # admin is NOT a member of board B → lacks tag.assign on ticket_b's board.
    with pytest.raises(PermissionDenied) as exc:
        await attach_concept_tag(
            mem_session, actor=env.admin, ticket_id="BB-1", tag_id=str(tag_id)
        )
    assert exc.value.required == "tag.assign"


@pytest.mark.asyncio
async def test_attach_missing_tag_404(mem_session: AsyncSession, env: Env) -> None:
    with pytest.raises(NotFound):
        await attach_concept_tag(
            mem_session, actor=env.pm, ticket_id="AA-1", tag_id=str(uuid.uuid4())
        )


# ---------------------------------------------------------------------------
# AC5: tag↔tag link manage — selectinload N+1-free, self-loop 422, dup 409, reverse ok.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_create_self_loop_rejected_422(
    mem_session: AsyncSession, env: Env
) -> None:
    tag_id = await _make_tag(mem_session, env.admin, "selfloop", "SelfLoop")
    with pytest.raises(InvalidConceptLink):
        await create_link(
            mem_session,
            actor=env.admin,
            tag_id=str(tag_id),
            payload=ConceptTagLinkCreate(target_tag_id=tag_id),
        )


@pytest.mark.asyncio
async def test_link_create_duplicate_409_reverse_ok(
    mem_session: AsyncSession, env: Env
) -> None:
    src = await _make_tag(mem_session, env.admin, "src", "Src")
    dst = await _make_tag(mem_session, env.admin, "dst", "Dst")

    await create_link(
        mem_session,
        actor=env.admin,
        tag_id=str(src),
        payload=ConceptTagLinkCreate(target_tag_id=dst, relation="parent"),
    )
    # duplicate (src, dst) → 409
    with pytest.raises(Conflict):
        await create_link(
            mem_session,
            actor=env.admin,
            tag_id=str(src),
            payload=ConceptTagLinkCreate(target_tag_id=dst),
        )
    # reverse (dst, src) is a DISTINCT allowed row
    rev = await create_link(
        mem_session,
        actor=env.admin,
        tag_id=str(dst),
        payload=ConceptTagLinkCreate(target_tag_id=src, relation="depends"),
    )
    assert rev.source_tag_id == dst
    assert rev.target_tag_id == src


@pytest.mark.asyncio
async def test_link_create_non_admin_forbidden(
    mem_session: AsyncSession, env: Env
) -> None:
    src = await _make_tag(mem_session, env.admin, "lsrc", "LSrc")
    dst = await _make_tag(mem_session, env.admin, "ldst", "LDst")
    with pytest.raises(PermissionDenied):
        await create_link(
            mem_session,
            actor=env.pm,
            tag_id=str(src),
            payload=ConceptTagLinkCreate(target_tag_id=dst),
        )


@pytest.mark.asyncio
async def test_tag_detail_links_eager_loaded_n1_free(
    mem_session: AsyncSession, env: Env
) -> None:
    a = await _make_tag(mem_session, env.admin, "a", "A")
    b = await _make_tag(mem_session, env.admin, "b", "B")
    c = await _make_tag(mem_session, env.admin, "c", "C")
    await create_link(
        mem_session, actor=env.admin, tag_id=str(a),
        payload=ConceptTagLinkCreate(target_tag_id=b, relation="relates"),
    )
    await create_link(
        mem_session, actor=env.admin, tag_id=str(c),
        payload=ConceptTagLinkCreate(target_tag_id=a, relation="parent"),
    )
    # read_concept_tag returns both incoming + outgoing edges for tag A, with
    # both endpoints eager-loaded (no MissingGreenlet when serialized).
    tag, links = await read_concept_tag(mem_session, env.admin, str(a))
    assert tag.slug == "a"
    assert len(links) == 2
    # Eager-loaded endpoints are accessible without a fresh async query.
    endpoints = {(link.source_tag.slug, link.target_tag.slug) for link in links}
    assert endpoints == {("a", "b"), ("c", "a")}


@pytest.mark.asyncio
async def test_link_delete_directed(mem_session: AsyncSession, env: Env) -> None:
    src = await _make_tag(mem_session, env.admin, "dsrc", "DSrc")
    dst = await _make_tag(mem_session, env.admin, "ddst", "DDst")
    await create_link(
        mem_session, actor=env.admin, tag_id=str(src),
        payload=ConceptTagLinkCreate(target_tag_id=dst),
    )
    await delete_link(
        mem_session, actor=env.admin, tag_id=str(src), target_tag_id=str(dst)
    )
    remaining = (
        await mem_session.execute(select(ConceptTagLink))
    ).scalars().all()
    assert remaining == []
    # deleting a missing edge → 404
    with pytest.raises(NotFound):
        await delete_link(
            mem_session, actor=env.admin, tag_id=str(src), target_tag_id=str(dst)
        )


# ---------------------------------------------------------------------------
# AC6: ticket serializer — concept_tags SEPARATE from labels, no MissingGreenlet
# on EVERY ticket read path (shared _ticket_load_options eager-loads both hops).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_concept_tags_separate_from_labels(
    mem_session: AsyncSession, env: Env
) -> None:
    tag_id = await _make_tag(mem_session, env.admin, "sep", "Separate")
    await attach_concept_tag(
        mem_session, actor=env.pm, ticket_id="AA-1", tag_id=str(tag_id)
    )
    # Re-fetch via the SHARED get_ticket path (used by get/list/create/update) and
    # serialize — concept_tags renders without MissingGreenlet, labels untouched.
    ticket = await get_ticket(mem_session, "AA-1")
    resp = ticket_response(ticket)
    assert resp.labels == ["free-text-label"]
    assert [t.slug for t in resp.concept_tags] == ["sep"]
    # labels is still list[str]; concept_tags is list of {id,slug,name,color}.
    assert all(isinstance(label, str) for label in resp.labels)
    assert resp.concept_tags[0].name == "Separate"
