"""History-aware context recall (PH-286, epic PH-283 child C — the FINAL tool).

Gives agents "we solved/decided this before" recall: given a ticket key OR a
free topic string, return the most relevant PAST tickets (prefer done/closed)
each with a compact CONTEXT block — the ``[HANDOFF...]`` decision trail + a
``technical_depth`` excerpt — ranked by relationship strength + recency.

A THIN orchestrator over THREE existing, already-gated seams (no re-derivation):

1. **Ranking / relationship strength** → ``services.relationships.related_tickets``
   (PH-284/287). The weighted, explainable, N+1-free model. Reused verbatim for
   the ticket path; recall.py imports NONE of relationships' private internals.
2. **Topic seeding** → ``services.search.search`` (PH-275/281) for the free-text
   path: find seed tickets, then expand + rank via ``related_tickets`` on the
   best seed, UNION the raw search hits (score 0.0, reasons []).
3. **Read gate** → BOTH seams call the identical global ``ticket.read`` gate, so
   recall.py adds NO new gate — any role with ``ticket.read`` (incl. pm /
   orchestrator per PH-287) may call it; a board-less stranger is still denied.

Ticket-vs-topic dispatch reuses the canonical ``services.graph._TICKET_KEY_RE``
(``[A-Z]{1,6}-\\d+``) with an ANCHORED ``.fullmatch`` of the stripped, upper-cased
query: a bare key ``PH-274`` → TICKET path; a topic that merely MENTIONS a key
("how PH-274 did scoring") stays a TOPIC (``.fullmatch`` not ``.search``).

Context extraction is N+1-free: AFTER the final ``limit`` trim, exactly TWO
batched queries fetch the survivors' rows (``Ticket.key.in_(...)`` — for
``updated_at`` re-rank + the ``technical_depth``/``impact_analysis``/``description``
inline columns) and their comments (``Comment.ticket_id.in_(...)``), independent
of ``limit``. Read-only over existing tickets + comments — NO migration.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Actor, Comment, Ticket
from app.schemas import RecallContextBlock, RecalledTicket, RelatedReason, RelatedTicket
from app.services.graph import _TICKET_KEY_RE
from app.services.relationships import related_tickets
from app.services.search import search

# --- Tunable caps (the value-add is signal, not noise) -----------------------
_MAX_HANDOFFS_PER_TICKET = 5  # most-recent [HANDOFF...] lines kept per ticket
_HANDOFF_CHARS = 200  # per-handoff truncation
_SUMMARY_CHARS = 400  # technical_depth/impact/description excerpt cap
_OVERFETCH = 2  # ranking head-room before the done/closed re-rank + trim

# Terminal/closed states sort FIRST in the history-aware re-rank ("we did this
# before" surfaces closed work above still-open work at equal relevance).
_HISTORY_STATES = frozenset({"done", "closed", "released"})

# Decision-section headings preferred for the summary excerpt (markdown ## …).
_DECISION_HEADINGS = ("## design decisions", "## approach", "## decision")

# The field a summary excerpt was taken from (mirrors RecallContextBlock).
SummarySource = Literal["technical_depth", "impact_analysis", "description", "none"]


def _is_ticket_key(query: str) -> bool:
    """Anchored dispatch: the WHOLE stripped, upper-cased query IS a ticket key.

    ``.fullmatch`` (not ``.search``) so a topic that merely mentions a key stays a
    topic; only an input that IS exactly a key anchors the ticket path.
    """
    return _TICKET_KEY_RE.fullmatch(query.strip().upper()) is not None


def _as_recalled_stub(hit: Ticket) -> RelatedTicket:
    """A pure search hit (no graph edge) → a score-0.0 / reasons-[] RelatedTicket
    fallback so a topic with weak graph edges still returns its direct matches."""
    return RelatedTicket(
        key=hit.key,
        title=hit.title,
        board=hit.board.key,
        state=hit.state,
        score=0.0,
        reasons=[],
    )


async def _gather_candidates(
    session: AsyncSession,
    actor: Actor,
    *,
    query: str,
    fetch: int,
) -> tuple[list[RelatedTicket], str | None]:
    """Dispatch on key-vs-topic → ``(candidate RelatedTickets, anchor_key | None)``.

    TICKET path: ``related_tickets(ticket=query)`` (already ranked, input excluded);
    anchor_key = the input key (already absent from the result, but returned so the
    common path is uniform). TOPIC path: ``search`` seeds → expand the best seed via
    ``related_tickets`` and UNION the raw search hits (de-duped by key); no anchor.
    """
    if _is_ticket_key(query):
        anchor = query.strip().upper()
        related = await related_tickets(
            session, actor, ticket=anchor, cross_board=True, limit=fetch
        )
        return related, anchor

    hits, _labels = await search(session, actor, q=query)
    if not hits:
        return [], None
    return await _expand_topic(session, actor, hits, fetch=fetch), None


async def _expand_topic(
    session: AsyncSession,
    actor: Actor,
    hits: list[Ticket],
    *,
    fetch: int,
) -> list[RelatedTicket]:
    """Topic expansion: rank the best seed's neighbourhood, then fold in the raw
    search hits as score-0.0 fallbacks (de-duped by key, scored entries win)."""
    seed = max(hits, key=lambda t: (t.updated_at is not None, t.updated_at))
    related = await related_tickets(
        session, actor, ticket=seed.key, cross_board=True, limit=fetch
    )
    by_key: dict[str, RelatedTicket] = {item.key: item for item in related}
    for hit in hits:
        by_key.setdefault(hit.key, _as_recalled_stub(hit))
    return list(by_key.values())


def _history_rank(state: str) -> int:
    """0 for terminal/closed states (sort FIRST), 1 otherwise (history bias)."""
    return 0 if state in _HISTORY_STATES else 1


def _recall_sort_key(
    item: RelatedTicket, updated_ts: float
) -> tuple[int, float, float, str]:
    """Re-rank: history-state-first, score desc, updated_at desc, key asc."""
    return (_history_rank(item.state), -item.score, -updated_ts, item.key)


def _handoff_lines(comments: list[Comment]) -> list[str]:
    """The decision trail: ONLY ``[HANDOFF...]`` comment lines, newest first,
    capped at ``_MAX_HANDOFFS_PER_TICKET`` and truncated to ``_HANDOFF_CHARS``.

    ``comments`` arrive oldest→newest; we walk newest→oldest and keep the marker
    lines (a comment body may carry the ``[HANDOFF X→Y]`` marker on its first
    non-blank line; we keep that whole comment's leading marker line).
    """
    out: list[str] = []
    for comment in reversed(comments):
        body = (comment.body or "").strip()
        if not body.startswith("[HANDOFF"):
            continue
        line = body.splitlines()[0].strip()
        out.append(line[:_HANDOFF_CHARS])
        if len(out) >= _MAX_HANDOFFS_PER_TICKET:
            break
    return out


def _decision_excerpt(text: str) -> str:
    """Capped excerpt, decision-section preferred: if a ``## Design decisions`` /
    ``## Approach`` / ``## Decision`` heading is present, start the excerpt there;
    else take from the top. Always capped to ``_SUMMARY_CHARS``."""
    lowered = text.lower()
    start = 0
    for heading in _DECISION_HEADINGS:
        idx = lowered.find(heading)
        if idx != -1:
            start = idx
            break
    return text[start : start + _SUMMARY_CHARS].strip()


def _extract_summary(ticket: Ticket) -> tuple[str, SummarySource]:
    """``(summary, summary_source)`` from the FIRST non-empty of technical_depth →
    impact_analysis → description; ``("", "none")`` when all are empty. Inline
    columns on the already-loaded row (NO extra query)."""
    sources: tuple[tuple[SummarySource, str | None], ...] = (
        ("technical_depth", ticket.technical_depth),
        ("impact_analysis", ticket.impact_analysis),
        ("description", ticket.description),
    )
    for source, value in sources:
        if value and value.strip():
            return _decision_excerpt(value), source
    return "", "none"


async def _load_rows(
    session: AsyncSession, keys: list[str]
) -> dict[str, Ticket]:
    """ONE batched query: ``{key: Ticket}`` for the trimmed survivors (board eager-
    loaded for ``board.key``; the summary columns are inline). Empty → no query."""
    if not keys:
        return {}
    stmt = (
        select(Ticket)
        .where(Ticket.key.in_(keys), Ticket.deleted_at.is_(None))
        .options(selectinload(Ticket.board))
    )
    return {t.key: t for t in (await session.execute(stmt)).scalars()}


async def _load_handoffs(
    session: AsyncSession, ticket_ids: list[UUID]
) -> dict[UUID, list[Comment]]:
    """ONE batched comment query over ALL survivor ids (NO per-ticket loop, NO
    author eager-load — we read ``body`` only). Grouped by ``ticket_id`` in Python,
    oldest→newest within each ticket."""
    if not ticket_ids:
        return {}
    stmt = (
        select(Comment)
        .where(Comment.ticket_id.in_(ticket_ids))
        .order_by(Comment.ticket_id, Comment.created_at.asc())
    )
    grouped: dict[UUID, list[Comment]] = {}
    for comment in (await session.execute(stmt)).scalars():
        grouped.setdefault(comment.ticket_id, []).append(comment)
    return grouped


def _build_context(ticket: Ticket | None, comments: list[Comment]) -> RecallContextBlock:
    """Assemble the CONTEXT block for one recalled ticket (no DB access — rows +
    comments are already loaded)."""
    if ticket is None:
        return RecallContextBlock(handoffs=[], summary="", summary_source="none")
    summary, source = _extract_summary(ticket)
    return RecallContextBlock(
        handoffs=_handoff_lines(comments),
        summary=summary,
        summary_source=source,
    )


async def recall_context(
    session: AsyncSession,
    actor: Actor,
    *,
    query: str,
    limit: int = 10,
) -> list[RecalledTicket]:
    """History-aware context recall for ``query`` (a ticket key OR a free topic).

    Read-gated through the reused seams (``ticket.read``). Dispatches on an
    anchored ``_TICKET_KEY_RE.fullmatch``: a bare key → ``related_tickets``; a topic
    → ``search`` seeds expanded + ranked. Re-ranks history-state-first (done/closed
    surface above open work at equal relevance), then score desc, updated_at desc,
    key asc; trims to ``limit``. AFTER the trim, TWO batched queries load the
    survivors' rows + comments (constant query budget, no N+1) and each result gets
    a compact CONTEXT block: the ``[HANDOFF...]`` decision trail + a capped
    ``technical_depth`` (decision-section preferred) / ``impact_analysis`` /
    ``description`` excerpt. Empty when nothing relevant (never raises); an unknown
    ticket key surfaces NotFound (same as ``related_tickets``).
    """
    fetch = min(max(limit * _OVERFETCH, limit), 100)
    candidates, _anchor = await _gather_candidates(
        session, actor, query=query, fetch=fetch
    )
    if not candidates:
        return []

    # Load rows up-front for the re-rank's updated_at (the seam returns no row).
    rows = await _load_rows(session, [c.key for c in candidates])

    def updated_ts(item: RelatedTicket) -> float:
        row = rows.get(item.key)
        return row.updated_at.timestamp() if row and row.updated_at else 0.0

    ranked = sorted(candidates, key=lambda c: _recall_sort_key(c, updated_ts(c)))
    trimmed = ranked[:limit]

    survivor_ids = [rows[c.key].id for c in trimmed if c.key in rows]
    handoffs = await _load_handoffs(session, survivor_ids)

    out: list[RecalledTicket] = []
    for item in trimmed:
        row = rows.get(item.key)
        comments = handoffs.get(row.id, []) if row is not None else []
        out.append(
            RecalledTicket(
                key=item.key,
                title=item.title,
                board=item.board,
                state=item.state,
                score=item.score,
                reasons=[RelatedReason(type=r.type, detail=r.detail) for r in item.reasons],
                context=_build_context(row, comments),
            )
        )
    return out
