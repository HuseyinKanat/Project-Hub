"""Cross-board search assembly (PH-275, epic PH-271 child 4/7).

Read-only over the FROZEN PH-272 tables; no migration. Serves the unified
search surface for PH-278 (search UI): a ``q`` term matched case-insensitively
over tickets (title OR description OR key) AND concept tags (name OR slug),
cross-board, plus an optional ``tags`` AND-filter. Reuses the PH-273 global
``tag.read`` gate — search exposes the same graph-equivalent identity projection
PH-274 already returns cross-board, so it is a GLOBAL read (no new permission).

Results are GROUPED by type (tickets, concept_tags) — never a mixed list — and
each group is capped at ``_SEARCH_LIMIT`` (no unbounded scan).

Dialect-aware case-insensitivity (AC5): ``func.lower(col).contains(term.lower(),
autoescape=True)`` emits ``lower(col) LIKE lower(?) ESCAPE '\\'`` identically on
Postgres (prod) and sqlite (test). ``autoescape=True`` escapes ``%``/``_``/``\\``
so a user typing ``100%`` or ``a_b`` is matched LITERALLY, not as LIKE wildcards
(validated empirically on sqlite+aiosqlite before commit). NOTE: sqlite's
built-in ``lower()`` is ASCII-only, so non-ASCII case-folding (e.g. Turkish İ)
only folds on Postgres prod — an engine limitation present for ``ilike`` too,
not a code choice; ASCII case-insensitivity (the AC) holds on both.

N+1 / MissingGreenlet (PH-272 rule): the ticket query selectinload-eager-loads
``board`` (serializer reads ``ticket.board.key``) and ``concept_tag_links →
concept_tag`` (so PH-278 can later chip-render hits) — a FIXED number of
follow-up SELECTs regardless of row count. The tags-AND filter is expressed
INSIDE the one ticket query (per-slug EXISTS), not a Python post-filter loop, so
no extra round-trips. The tag group reads only ConceptTag columns — no eager
load needed.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from app.db.models import Actor, ConceptTag, Ticket, TicketConceptTag
from app.services.concept_tags import _require_tag_read

# Per-group result cap — no unbounded full-table scan returned to the client.
_SEARCH_LIMIT = 50

# Eager-load only the hops the serializer/PH-278 walk: board.key on the hit and
# the junction→tag chain (chips). Constant follow-up SELECT count vs row count.
_SEARCH_TICKET_OPTIONS = (
    selectinload(Ticket.board),
    selectinload(Ticket.concept_tag_links).selectinload(TicketConceptTag.concept_tag),
)


def _ci_contains(col: InstrumentedAttribute[str], term: str) -> ColumnElement[bool]:
    """Dialect-portable case-insensitive substring match, wildcard-safe.

    ``func.lower(col).contains(term.lower(), autoescape=True)`` → identical ASCII
    case-folding on Postgres + sqlite; ``autoescape`` treats ``%``/``_``/``\\`` in
    ``term`` as literals (no LIKE-wildcard injection). Reused for ticket
    title/description/key and tag name/slug.
    """
    return func.lower(col).contains(term.lower(), autoescape=True)


def _parse_tag_slugs(tags: str | None) -> list[str]:
    """CSV ``tags`` → de-duplicated, lower-cased, non-empty slug list (order kept)."""
    if not tags:
        return []
    seen: set[str] = set()
    slugs: list[str] = []
    for raw in tags.split(","):
        slug = raw.strip().lower()
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def _has_slug_clause(slug: str) -> ColumnElement[bool]:
    """Correlated EXISTS: the current ``Ticket`` is linked to the tag with ``slug``.

    Slug resolved case-insensitively inline — an unknown slug matches no tag, so
    the EXISTS is false → the ticket is excluded (empty result, NOT 404).
    """
    tag_id = (
        select(ConceptTag.id).where(func.lower(ConceptTag.slug) == slug).scalar_subquery()
    )
    return exists().where(
        TicketConceptTag.ticket_id == Ticket.id,
        TicketConceptTag.concept_tag_id == tag_id,
    )


async def _search_tickets(
    session: AsyncSession, term: str, slugs: list[str]
) -> list[Ticket]:
    """Tickets matching ``term`` (title|description|key) AND owning ALL ``slugs``.

    Cross-board (no board filter); soft-deleted excluded; ``updated_at desc``;
    capped at ``_SEARCH_LIMIT``. The tags-AND filter is ALL requested slugs
    (intersection) via one correlated EXISTS per slug.
    """
    stmt = (
        select(Ticket)
        .where(Ticket.deleted_at.is_(None))
        .where(
            or_(
                _ci_contains(Ticket.title, term),
                _ci_contains(Ticket.description, term),
                _ci_contains(Ticket.key, term),
            )
        )
        .options(*_SEARCH_TICKET_OPTIONS)
        .order_by(Ticket.updated_at.desc())
        .limit(_SEARCH_LIMIT)
    )
    for slug in slugs:
        stmt = stmt.where(_has_slug_clause(slug))
    return list((await session.execute(stmt)).scalars())


async def _search_tags(session: AsyncSession, term: str) -> list[ConceptTag]:
    """Concept tags matching ``term`` (name|slug), ``slug`` order, capped."""
    stmt = (
        select(ConceptTag)
        .where(or_(_ci_contains(ConceptTag.name, term), _ci_contains(ConceptTag.slug, term)))
        .order_by(ConceptTag.slug)
        .limit(_SEARCH_LIMIT)
    )
    return list((await session.execute(stmt)).scalars())


async def search(
    session: AsyncSession,
    actor: Actor,
    *,
    q: str | None = None,
    tags: str | None = None,
) -> tuple[list[Ticket], list[ConceptTag]]:
    """Cross-board search → (tickets, concept_tags), grouped by type.

    Permission: global ``tag.read`` (PH-273 gate, reused). A blank/whitespace
    ``q`` short-circuits to ``([], [])`` WITHOUT issuing any scan (a bare wildcard
    would match every row up to the cap — meaningless). ``tags`` is a RESTRICTION
    on the ``q`` match (AND-intersection), not a standalone browse: blank ``q``
    returns empty even when ``tags`` is present (v1 is q-centric; PH-278 always
    calls with a q). Unknown slug in ``tags`` → empty tickets (NOT 404).
    """
    await _require_tag_read(session, actor)

    term = (q or "").strip()
    if not term:
        return [], []

    slugs = _parse_tag_slugs(tags)
    tickets = await _search_tickets(session, term, slugs)
    concept_tags = await _search_tags(session, term)
    return tickets, concept_tags
