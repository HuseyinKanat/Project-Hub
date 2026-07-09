"""Cross-board search assembly (PH-275; re-pointed to labels in PH-281).

Read-only; no migration. Serves the unified search surface for PH-278/PH-280: a
``q`` term matched case-insensitively over tickets (title OR description OR key)
AND over ``Ticket.labels`` values, cross-board, plus an optional ``labels``
AND-filter. Read-auth = global ``ticket.read`` (PH-281, replaces the removed
``tag.read``) — search exposes the same graph-equivalent cross-board ticket
identity projection.

Results are GROUPED by type (``tickets``, ``labels``) — never a mixed list — and
each group is capped at ``_SEARCH_LIMIT`` (no unbounded scan).

PH-281 PIVOT (epic PH-271): the ConceptTag group is gone. The ``labels`` group is
a ``list[str]`` of distinct matching label STRINGS read from the inline
``Ticket.labels`` ARRAY via the dialect-aware unnest (PG ``unnest`` / sqlite
``json_each`` — see ``services/labels``). The ``?labels`` AND-filter (exact
per-element membership) replaces ``?tags``.

Dialect-aware case-insensitivity: ``func.lower(col).contains(term.lower(),
autoescape=True)`` (PH-275) — identical ASCII case-folding + wildcard-safe on
Postgres + sqlite. The label predicates branch on the dialect (see
``services/labels``). sqlite ``lower()`` is ASCII-only (Turkish-İ caveat) — an
engine limitation, the AC is ASCII ci.

N+1 / MissingGreenlet: the ticket query selectinload-eager-loads ``board``
(serializer reads ``ticket.board.key``) — labels are inline on the row (no eager
load). The label match + ``?labels`` AND-filter live INSIDE the one ticket query
(correlated EXISTS per the dialect helper), not a Python post-filter, so no extra
round-trips beyond the labels-group query.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from app.db.models import Actor, BoardMembership, Ticket
from app.services.labels import (
    distinct_ticket_labels,
    label_match_predicate,
    label_substring_predicate,
)

# Per-group result cap — no unbounded full-table scan returned to the client.
_SEARCH_LIMIT = 50

# Eager-load only board.key on the hit (labels are inline on the row). Constant
# follow-up SELECT count vs row count.
_SEARCH_TICKET_OPTIONS = (selectinload(Ticket.board),)


def _ci_contains(col: InstrumentedAttribute[str], term: str) -> ColumnElement[bool]:
    """Dialect-portable case-insensitive substring match, wildcard-safe.

    ``func.lower(col).contains(term.lower(), autoescape=True)`` → identical ASCII
    case-folding on Postgres + sqlite; ``autoescape`` treats ``%``/``_``/``\\`` in
    ``term`` as literals (no LIKE-wildcard injection). Reused for ticket
    title/description/key.
    """
    return func.lower(col).contains(term.lower(), autoescape=True)


def _parse_label_filter(labels: str | None) -> list[str]:
    """CSV ``labels`` → de-duplicated, non-empty list (order kept).

    PH-281: labels are case-SENSITIVE free text (no slug registry), so values are
    NOT lower-cased here — exact membership is enforced by the dialect predicate.
    """
    if not labels:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in labels.split(","):
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


async def _search_tickets(
    session: AsyncSession, term: str, label_filter: list[str]
) -> list[Ticket]:
    """Tickets matching ``term`` (title|description|key|label value) AND carrying
    ALL of ``label_filter`` (AND-intersection, exact per-element membership).

    Cross-board (no board filter); soft-deleted excluded; ``updated_at desc``;
    capped at ``_SEARCH_LIMIT``. A ticket whose ONLY hit is a matching LABEL is
    still returned (the label predicate is OR-ed into the term clause).
    """
    dialect = session.bind.dialect if session.bind is not None else "sqlite"
    stmt = (
        select(Ticket)
        .where(Ticket.deleted_at.is_(None))
        .where(
            or_(
                _ci_contains(Ticket.title, term),
                _ci_contains(Ticket.description, term),
                _ci_contains(Ticket.key, term),
                label_substring_predicate(dialect, term),
            )
        )
        .options(*_SEARCH_TICKET_OPTIONS)
        .order_by(Ticket.updated_at.desc())
        .limit(_SEARCH_LIMIT)
    )
    for value in label_filter:
        stmt = stmt.where(label_match_predicate(dialect, value))
    return list((await session.execute(stmt)).scalars())


async def _require_ticket_read(session: AsyncSession, actor: Actor) -> None:
    """Global read gate (PH-281): the actor must hold ``ticket.read`` (or ``*``)
    under at least one board membership. Replaces the removed ``tag.read`` gate.

    Loads ``(role, board)`` pairs with ``board.roles`` eager-loaded
    (``current_actor`` only selectinloads ``Actor.memberships``, NOT the board).
    """
    from app.core.permissions import require_global_permission

    rows = (
        await session.execute(
            select(BoardMembership)
            .where(BoardMembership.actor_id == actor.id)
            .options(selectinload(BoardMembership.board))
        )
    ).scalars()
    require_global_permission(actor, "ticket.read", [(m.role, m.board) for m in rows])


async def search(
    session: AsyncSession,
    actor: Actor,
    *,
    q: str | None = None,
    labels: str | None = None,
) -> tuple[list[Ticket], list[str]]:
    """Cross-board search → (tickets, labels), grouped by type.

    Permission: global ``ticket.read`` (PH-281). A blank/whitespace ``q``
    short-circuits to ``([], [])`` WITHOUT issuing any scan. ``labels`` is a
    RESTRICTION on the ``q`` match (AND-intersection), not a standalone browse:
    blank ``q`` returns empty even when ``labels`` is present. An unknown value in
    ``labels`` → empty tickets (NOT 404 — labels have no registry).
    """
    await _require_ticket_read(session, actor)

    term = (q or "").strip()
    if not term:
        return [], []

    label_filter = _parse_label_filter(labels)
    tickets = await _search_tickets(session, term, label_filter)
    matching_labels = await distinct_ticket_labels(session, term)
    return tickets, matching_labels[:_SEARCH_LIMIT]
