"""Concept-tag service logic (PH-273, epic PH-271 child 2/7).

Builds the API/permission/serializer layer on top of the PH-272 data model
(``ConceptTag`` / ``TicketConceptTag`` / ``ConceptTagLink``). The models are
FROZEN — this layer adds:

- global CRUD (admin-gated via ``require_global_permission``: "admin on ANY board")
- tag↔tag directed link create/delete with app-layer self-loop (422) + duplicate
  (409) guards (PH-272 deferred these to PH-273)
- board-scoped ticket attach/detach (idempotent-success 200, never 409/404 on
  a benign no-op), gated by ``tag.assign`` on the TICKET'S board

selectinload discipline: ConceptTag has NO link back-ref collection (PH-272
deferred it to PH-275), so a tag's links are assembled with a SEPARATE
``select(ConceptTagLink)`` filtered on ``source==id OR target==id``, each row
eager-loading both endpoints — N+1-free. The ticket attach/detach path re-fetches
via ``get_ticket`` (whose shared load options now eager-load concept_tag_links)
before serializing.
"""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import Conflict, InvalidConceptLink, NotFound
from app.core.permissions import require_global_permission, require_permission
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    ConceptTag,
    ConceptTagLink,
    Ticket,
    TicketConceptTag,
)
from app.schemas import ConceptTagCreate, ConceptTagLinkCreate, ConceptTagUpdate
from app.services.boards import parse_uuid
from app.services.tickets import get_ticket

_TAG_MANAGE = "tag.manage"
_TAG_READ = "tag.read"
_TAG_ASSIGN = "tag.assign"


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


async def _actor_memberships_with_boards(
    session: AsyncSession, actor: Actor
) -> list[tuple[str, Board]]:
    """Load (role, board) pairs for ``actor`` with ``board.roles`` available.

    ``current_actor`` only selectinloads ``Actor.memberships`` (NOT the board), so
    the global gate would hit MissingGreenlet reading ``membership.board.roles``.
    This self-contained query feeds ``require_global_permission`` async-safely
    without broadening ``current_actor``'s blast radius.
    """
    rows = (
        await session.execute(
            select(BoardMembership)
            .where(BoardMembership.actor_id == actor.id)
            .options(selectinload(BoardMembership.board))
        )
    ).scalars()
    return [(m.role, m.board) for m in rows]


async def _require_tag_manage(session: AsyncSession, actor: Actor) -> None:
    """Admin-gate global tag writes: the actor must hold ``tag.manage`` (or ``*``)
    under the role of AT LEAST ONE of their board memberships."""
    memberships = await _actor_memberships_with_boards(session, actor)
    require_global_permission(actor, _TAG_MANAGE, memberships)


async def _require_tag_read(session: AsyncSession, actor: Actor) -> None:
    """Global read gate: ``tag.read`` is granted to every default role, so this
    only fails for an actor with no membership granting it."""
    memberships = await _actor_memberships_with_boards(session, actor)
    require_global_permission(actor, _TAG_READ, memberships)


# ---------------------------------------------------------------------------
# Tag resolution
# ---------------------------------------------------------------------------


async def get_concept_tag(session: AsyncSession, tag_id: str) -> ConceptTag:
    """Resolve a concept tag by UUID or slug, with ticket_links eager-loaded
    (ticket_count + cascade-safe). 404 NotFound("concept_tag") if missing."""
    tag_uuid = parse_uuid(tag_id)
    statement = select(ConceptTag).options(selectinload(ConceptTag.ticket_links))
    if tag_uuid is None:
        statement = statement.where(ConceptTag.slug == tag_id)
    else:
        statement = statement.where(ConceptTag.id == tag_uuid)

    tag = (await session.execute(statement)).scalar_one_or_none()
    if tag is None:
        raise NotFound("concept_tag")
    return tag


async def get_tag_links(
    session: AsyncSession, tag_id: UUID
) -> list[ConceptTagLink]:
    """All directed edges touching ``tag_id`` (as source OR target), each with
    both endpoints eager-loaded — N+1-free. ConceptTag has no link back-ref yet
    (PH-272 deferred to PH-275), so the edge list is assembled here."""
    rows = (
        await session.execute(
            select(ConceptTagLink)
            .where(
                or_(
                    ConceptTagLink.source_tag_id == tag_id,
                    ConceptTagLink.target_tag_id == tag_id,
                )
            )
            .options(
                selectinload(ConceptTagLink.source_tag),
                selectinload(ConceptTagLink.target_tag),
            )
            # ConceptTagLink is a bare junction (no TimestampMixin) — order by id
            # for a stable, deterministic edge list.
            .order_by(ConceptTagLink.id)
        )
    ).scalars()
    return list(rows)


# ---------------------------------------------------------------------------
# Tag CRUD (admin-gated)
# ---------------------------------------------------------------------------


async def list_concept_tags(
    session: AsyncSession, actor: Actor
) -> list[tuple[ConceptTag, list[ConceptTagLink]]]:
    await _require_tag_read(session, actor)
    tags = (
        await session.execute(
            select(ConceptTag)
            .options(selectinload(ConceptTag.ticket_links))
            .order_by(ConceptTag.slug)
        )
    ).scalars()
    result: list[tuple[ConceptTag, list[ConceptTagLink]]] = []
    for tag in tags:
        links = await get_tag_links(session, tag.id)
        result.append((tag, links))
    return result


async def read_concept_tag(
    session: AsyncSession, actor: Actor, tag_id: str
) -> tuple[ConceptTag, list[ConceptTagLink]]:
    await _require_tag_read(session, actor)
    tag = await get_concept_tag(session, tag_id)
    links = await get_tag_links(session, tag.id)
    return tag, links


async def create_concept_tag(
    session: AsyncSession, *, actor: Actor, payload: ConceptTagCreate
) -> tuple[ConceptTag, list[ConceptTagLink]]:
    await _require_tag_manage(session, actor)
    tag = ConceptTag(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        color=payload.color,
    )
    session.add(tag)
    try:
        await session.flush()
    except IntegrityError as exc:  # uq_concept_tag_slug
        await session.rollback()
        raise Conflict(f"concept tag slug '{payload.slug}' already exists") from exc
    await session.commit()
    refreshed = await get_concept_tag(session, str(tag.id))
    return refreshed, []


async def update_concept_tag(
    session: AsyncSession, *, actor: Actor, tag_id: str, payload: ConceptTagUpdate
) -> tuple[ConceptTag, list[ConceptTagLink]]:
    await _require_tag_manage(session, actor)
    tag = await get_concept_tag(session, tag_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(tag, field, value)
    try:
        await session.flush()
    except IntegrityError as exc:  # uq_concept_tag_slug on a slug change
        await session.rollback()
        raise Conflict(f"concept tag slug '{data.get('slug')}' already exists") from exc
    await session.commit()
    links = await get_tag_links(session, tag.id)
    return tag, links


async def delete_concept_tag(
    session: AsyncSession, *, actor: Actor, tag_id: str
) -> None:
    await _require_tag_manage(session, actor)
    tag = await get_concept_tag(session, tag_id)
    # DB ondelete=CASCADE prunes junction rows + links (core.py:592/596/632/635).
    await session.delete(tag)
    await session.commit()


# ---------------------------------------------------------------------------
# Tag↔tag link manage (admin-gated, app-layer guards)
# ---------------------------------------------------------------------------


async def create_link(
    session: AsyncSession, *, actor: Actor, tag_id: str, payload: ConceptTagLinkCreate
) -> ConceptTagLink:
    """Create a directed source→target edge.

    Guards (PH-273): self-loop → 422 InvalidConceptLink; duplicate (source,target)
    → 409 Conflict (the reverse pair is a DISTINCT allowed row by design). Cycle
    detection beyond the immediate self-loop is PH-275's concern.
    """
    await _require_tag_manage(session, actor)
    source = await get_concept_tag(session, tag_id)
    target = await get_concept_tag(session, str(payload.target_tag_id))  # 404 if missing

    if source.id == target.id:
        raise InvalidConceptLink("a concept tag cannot link to itself")

    existing = (
        await session.execute(
            select(ConceptTagLink).where(
                ConceptTagLink.source_tag_id == source.id,
                ConceptTagLink.target_tag_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("link already exists for this (source, target) direction")

    link = ConceptTagLink(
        source_tag_id=source.id,
        target_tag_id=target.id,
        relation=payload.relation,
    )
    session.add(link)
    try:
        await session.flush()
    except IntegrityError as exc:  # uq_concept_tag_link_source_target (race)
        await session.rollback()
        raise Conflict(
            "link already exists for this (source, target) direction"
        ) from exc
    await session.commit()
    return link


async def delete_link(
    session: AsyncSession, *, actor: Actor, tag_id: str, target_tag_id: str
) -> None:
    await _require_tag_manage(session, actor)
    source = await get_concept_tag(session, tag_id)
    target_uuid = parse_uuid(target_tag_id)
    if target_uuid is None:
        raise NotFound("concept_tag")
    link = (
        await session.execute(
            select(ConceptTagLink).where(
                ConceptTagLink.source_tag_id == source.id,
                ConceptTagLink.target_tag_id == target_uuid,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFound("concept_tag_link")
    await session.delete(link)
    await session.commit()


# ---------------------------------------------------------------------------
# Ticket attach / detach (board-scoped, idempotent-success)
# ---------------------------------------------------------------------------


async def attach_concept_tag(
    session: AsyncSession, *, actor: Actor, ticket_id: str, tag_id: str
) -> Ticket:
    """Attach a tag to a ticket (board-scoped, idempotent-200).

    Gated by ``tag.assign`` on the TICKET'S board. Pre-checks the junction so a
    re-attach is a benign no-op (returns the current ticket, 200 — NOT 409, NOT a
    duplicate row). IntegrityError on a concurrent race is swallowed as the same
    idempotent-success path. Returns the re-fetched ticket (concept_tag_links
    eager-loaded via the shared load options) for serialization.
    """
    ticket = await get_ticket(session, ticket_id)
    tag = await get_concept_tag(session, tag_id)  # 404 if tag missing
    require_permission(actor, ticket.board, _TAG_ASSIGN, resource=ticket)

    existing = (
        await session.execute(
            select(TicketConceptTag).where(
                TicketConceptTag.ticket_id == ticket.id,
                TicketConceptTag.concept_tag_id == tag.id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            TicketConceptTag(ticket_id=ticket.id, concept_tag_id=tag.id)
        )
        try:
            await session.flush()
        except IntegrityError:  # uq_ticket_concept_tag race → idempotent-success
            await session.rollback()
        else:
            await session.commit()
        # expire_on_commit=False keeps the ticket's already-loaded (stale, empty)
        # concept_tag_links in the identity map; expire it so the re-fetch's
        # selectinload reloads the freshly-inserted junction row.
        session.expire(ticket, ["concept_tag_links"])

    # Re-fetch so concept_tag_links is fresh + eager-loaded for ticket_response.
    return await get_ticket(session, ticket_id)


async def detach_concept_tag(
    session: AsyncSession, *, actor: Actor, ticket_id: str, tag_id: str
) -> Ticket:
    """Detach a tag from a ticket (board-scoped, idempotent-200 no-op if absent)."""
    ticket = await get_ticket(session, ticket_id)
    tag = await get_concept_tag(session, tag_id)  # 404 if tag missing
    require_permission(actor, ticket.board, _TAG_ASSIGN, resource=ticket)

    link = (
        await session.execute(
            select(TicketConceptTag).where(
                TicketConceptTag.ticket_id == ticket.id,
                TicketConceptTag.concept_tag_id == tag.id,
            )
        )
    ).scalar_one_or_none()
    if link is not None:
        await session.delete(link)
        await session.commit()
        # See attach_concept_tag: expire the stale collection so the re-fetch
        # reflects the removed junction row (expire_on_commit=False).
        session.expire(ticket, ["concept_tag_links"])

    return await get_ticket(session, ticket_id)
