"""ORM model tests for the concept-tag data layer (PH-272).

FOUNDATION child of epic PH-271. Exercises the three additive models built via
``Base.metadata.create_all`` on the in-memory sqlite test DB:

- ``ConceptTag``        — GLOBAL taxonomy entity (NO board_id, globally-unique slug)
- ``TicketConceptTag``  — M2M junction tickets↔concept_tags (CASCADE both FKs)
- ``ConceptTagLink``    — DIRECTED self-referential tag↔tag edge (explicit foreign_keys)

Covers the 4 data-layer acceptance criteria:
- AC1: 3 tables build + a ConceptTag/junction/link round-trips (no mapper-config error).
- AC2: duplicate ``slug`` raises IntegrityError (global uniqueness, no board_id).
- AC3: duplicate ``(ticket_id, concept_tag_id)`` raises IntegrityError; deleting a
       Ticket CASCADE-deletes its junction rows while the ConceptTag survives.
- AC4: a directed link ``relation="parent"`` persists with both self-FKs resolved;
       duplicate ``(source, target)`` rejected while the reverse ``(target, source)``
       is accepted as a distinct row.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    ConceptTag,
    ConceptTagLink,
    Ticket,
    TicketConceptTag,
    Workflow,
)
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    # AC1: create_all over the FULL Base.metadata — a mapper-configuration error
    # (e.g. missing explicit foreign_keys on the self-referential ConceptTagLink)
    # surfaces here on the first test as AmbiguousForeignKeysError.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # PRAGMA foreign_keys is OFF by default on sqlite → CASCADE FKs are not
    # enforced. Turn it on so AC3's ticket-delete cascade actually fires.
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(mem_session: AsyncSession) -> dict[str, object]:
    """Minimal seed: one workflow, one board, one admin actor."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    board = Board(
        key="CT",
        name="Concept Tag Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.commit()
    return {"board": board, "admin": admin, "workflow": workflow}


def _make_ticket(board: Board, admin: Actor, key: str) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=f"Ticket {key}",
        state="backlog",
        reporter_id=admin.id,
    )


# ---------------------------------------------------------------------------
# AC1: three models build + a ConceptTag / junction / link round-trips.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_tag_models_round_trip(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    admin = seeded["admin"]
    assert isinstance(board, Board)
    assert isinstance(admin, Actor)

    ticket = _make_ticket(board, admin, "CT-1")
    tag = ConceptTag(
        id=uuid.uuid4(),
        slug="async-safety",
        name="Async Safety",
        description="MissingGreenlet / selectinload concerns",
        color="#7dd3fc",
    )
    mem_session.add_all([ticket, tag])
    await mem_session.flush()

    junction = TicketConceptTag(
        id=uuid.uuid4(), ticket_id=ticket.id, concept_tag_id=tag.id
    )
    other_tag = ConceptTag(id=uuid.uuid4(), slug="db-layer", name="DB Layer")
    mem_session.add_all([junction, other_tag])
    await mem_session.flush()

    link = ConceptTagLink(
        id=uuid.uuid4(),
        source_tag_id=tag.id,
        target_tag_id=other_tag.id,
        relation="relates",
    )
    mem_session.add(link)
    await mem_session.commit()

    # ConceptTag fetched back with all fields intact (incl. nullable description/color).
    fetched_tag = (
        await mem_session.execute(select(ConceptTag).where(ConceptTag.slug == "async-safety"))
    ).scalar_one()
    assert fetched_tag.name == "Async Safety"
    assert fetched_tag.description == "MissingGreenlet / selectinload concerns"
    assert fetched_tag.color == "#7dd3fc"
    assert not hasattr(fetched_tag, "board_id")  # AC2 precondition: GLOBAL, no board_id

    # Junction relationships resolve in both directions (back_populates wired).
    fetched_junction = (
        await mem_session.execute(
            select(TicketConceptTag)
            .where(TicketConceptTag.id == junction.id)
            .options(
                selectinload(TicketConceptTag.ticket),
                selectinload(TicketConceptTag.concept_tag),
            )
        )
    ).scalar_one()
    assert fetched_junction.ticket.id == ticket.id
    assert fetched_junction.concept_tag.slug == "async-safety"

    # Directed link resolves both self-FK relationships (explicit foreign_keys).
    fetched_link = (
        await mem_session.execute(
            select(ConceptTagLink)
            .where(ConceptTagLink.id == link.id)
            .options(
                selectinload(ConceptTagLink.source_tag),
                selectinload(ConceptTagLink.target_tag),
            )
        )
    ).scalar_one()
    assert fetched_link.source_tag.slug == "async-safety"
    assert fetched_link.target_tag.slug == "db-layer"
    assert fetched_link.relation == "relates"


# ---------------------------------------------------------------------------
# AC2: ConceptTag.slug is globally unique (no board_id scope).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_tag_slug_globally_unique(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    tag1 = ConceptTag(id=uuid.uuid4(), slug="duplicate", name="First")
    tag2 = ConceptTag(id=uuid.uuid4(), slug="duplicate", name="Second")
    mem_session.add(tag1)
    await mem_session.flush()
    mem_session.add(tag2)
    with pytest.raises(IntegrityError):
        await mem_session.flush()
    await mem_session.rollback()


# ---------------------------------------------------------------------------
# AC3: junction UniqueConstraint dedupes double-attach.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_concept_tag_unique_dedupes_double_attach(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    admin = seeded["admin"]
    assert isinstance(board, Board)
    assert isinstance(admin, Actor)

    ticket = _make_ticket(board, admin, "CT-2")
    tag = ConceptTag(id=uuid.uuid4(), slug="graph", name="Graph")
    mem_session.add_all([ticket, tag])
    await mem_session.flush()

    link1 = TicketConceptTag(id=uuid.uuid4(), ticket_id=ticket.id, concept_tag_id=tag.id)
    mem_session.add(link1)
    await mem_session.flush()

    link2 = TicketConceptTag(id=uuid.uuid4(), ticket_id=ticket.id, concept_tag_id=tag.id)
    mem_session.add(link2)
    with pytest.raises(IntegrityError):  # uq_ticket_concept_tag
        await mem_session.flush()
    await mem_session.rollback()


# ---------------------------------------------------------------------------
# AC3 (cascade): deleting the Ticket prunes its junction rows; the tag survives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_delete_cascades_junction_tag_survives(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    admin = seeded["admin"]
    assert isinstance(board, Board)
    assert isinstance(admin, Actor)

    ticket = _make_ticket(board, admin, "CT-3")
    tag = ConceptTag(id=uuid.uuid4(), slug="cascade", name="Cascade")
    mem_session.add_all([ticket, tag])
    await mem_session.flush()

    junction = TicketConceptTag(
        id=uuid.uuid4(), ticket_id=ticket.id, concept_tag_id=tag.id
    )
    mem_session.add(junction)
    await mem_session.commit()
    junction_id = junction.id
    tag_id = tag.id

    await mem_session.delete(ticket)
    await mem_session.commit()

    orphan = (
        await mem_session.execute(
            select(TicketConceptTag).where(TicketConceptTag.id == junction_id)
        )
    ).scalar_one_or_none()
    assert orphan is None, "junction row should be CASCADE-deleted with the ticket"

    surviving_tag = (
        await mem_session.execute(select(ConceptTag).where(ConceptTag.id == tag_id))
    ).scalar_one_or_none()
    assert surviving_tag is not None, "the ConceptTag must survive the ticket delete"


# ---------------------------------------------------------------------------
# AC4: ConceptTagLink — directed, both self-FKs resolved, dedupe one direction
# while the reverse pair is a distinct row.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_tag_link_directed_dedupe_and_reverse_distinct(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    parent = ConceptTag(id=uuid.uuid4(), slug="parent-tag", name="Parent")
    child = ConceptTag(id=uuid.uuid4(), slug="child-tag", name="Child")
    mem_session.add_all([parent, child])
    await mem_session.flush()

    # Directed link parent → child with an asymmetric relation persists with
    # both self-FKs resolved (no AmbiguousForeignKeysError).
    forward = ConceptTagLink(
        id=uuid.uuid4(),
        source_tag_id=parent.id,
        target_tag_id=child.id,
        relation="parent",
    )
    mem_session.add(forward)
    await mem_session.flush()

    fetched = (
        await mem_session.execute(
            select(ConceptTagLink).where(ConceptTagLink.id == forward.id)
        )
    ).scalar_one()
    assert fetched.source_tag_id == parent.id
    assert fetched.target_tag_id == child.id
    assert fetched.relation == "parent"

    # The reverse (child → parent) is a DISTINCT row by design — accepted.
    reverse = ConceptTagLink(
        id=uuid.uuid4(),
        source_tag_id=child.id,
        target_tag_id=parent.id,
        relation="depends",
    )
    mem_session.add(reverse)
    await mem_session.flush()
    rows = (
        await mem_session.execute(select(ConceptTagLink))
    ).scalars().all()
    assert len(rows) == 2
    assert {(r.source_tag_id, r.target_tag_id) for r in rows} == {
        (parent.id, child.id),
        (child.id, parent.id),
    }

    # A duplicate of an existing direction (parent → child) is rejected by
    # uq_concept_tag_link_source_target.
    dup = ConceptTagLink(
        id=uuid.uuid4(),
        source_tag_id=parent.id,
        target_tag_id=child.id,
        relation="relates",
    )
    mem_session.add(dup)
    with pytest.raises(IntegrityError):
        await mem_session.flush()
    await mem_session.rollback()
