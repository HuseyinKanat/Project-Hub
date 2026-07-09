"""Dialect-aware predicates over the inline ``Ticket.labels`` ARRAY (PH-281).

``Ticket.labels`` is ``STRING_ARRAY_TYPE`` =
``JSON().with_variant(ARRAY(String), "postgresql")`` (core.py:34/240) — a native
``text[]`` on Postgres prod, but a JSON-array-as-TEXT on the sqlite test dialect.
A single SQL predicate CANNOT match both array encodings, so every place that
touches labels in SQL branches on ``session.bind.dialect.name``:

- **Postgres (`text[]`)** — ``unnest(labels)`` as a column-valued table function;
  membership / ci-substring filters run over the unnested element column.
- **sqlite (`JSON` text)** — ``json_each(labels)`` table-valued function; a naive
  substring on the whole JSON text is FALSE-POSITIVE-prone (``bug`` would match
  ``debugger`` AND the JSON punctuation), so we unnest per element with
  ``json_each`` and match the element ``value``.

Case-insensitive substring reuses the PH-275 construct
``func.lower(col).contains(term.lower(), autoescape=True)`` → wildcard-safe
(``%``/``_``/``\\`` are literals, not LIKE wildcards) and ASCII-ci on BOTH
dialects. EXACT membership (the ``?labels`` AND-filter) compares the element
``value`` verbatim — ``bug`` does NOT match ``debugger`` (no substring leakage).

NOTE: sqlite's built-in ``lower()`` is ASCII-only, so non-ASCII case-folding
(Turkish İ) only folds on Postgres — an engine limitation present for ``ilike``
too, not a code choice; the AC is ASCII ci.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, TableValuedAlias, exists, func, select, true
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket


def _is_postgres(dialect: Dialect | str) -> bool:
    name = dialect if isinstance(dialect, str) else dialect.name
    return name == "postgresql"


def _labels_table_function(dialect: Dialect | str) -> TableValuedAlias:
    """Dialect-aware unnest of ``Ticket.labels`` as a TABLE-VALUED alias.

    Postgres → ``unnest(labels)``; sqlite → ``json_each(labels)``. The alias
    exposes a ``.c.value`` element column. It references ``Ticket.labels``, so a
    LATERAL/correlated join against the outer ``Ticket`` row drives one row per
    array element. Always JOIN it to ``Ticket`` with ``.join(alias, true())`` (an
    implicit lateral) so SQLAlchemy correlates it — never a true cross join.

    DIALECT PARITY (PH-282): ``.table_valued("value")`` only sets the *Python-side*
    column name; the emitted SQL is ``func(...) AS anon_1`` WITHOUT a column
    derivation list. sqlite's ``json_each`` natively exposes a real ``value``
    column, so ``anon_1.value`` resolves. But Postgres ``unnest(<array>)`` yields a
    single *unnamed* (function-named) column, so ``anon_1.value`` does NOT exist →
    asyncpg ``UndefinedColumnError`` (``column anon_1.value does not exist``) → the
    /api/search 500. The fix is ``.render_derived()`` on the PG branch, which emits
    the column-derivation list ``unnest(labels) AS anon_1(value)`` so the unnested
    element column is explicitly aliased ``value``. sqlite must NOT use
    ``render_derived()`` — sqlite rejects ``json_each(...) AS x(value)`` with a
    syntax error, and it already exposes ``value`` natively.
    """
    if _is_postgres(dialect):
        return func.unnest(Ticket.labels).table_valued("value").render_derived()
    return func.json_each(Ticket.labels).table_valued("value")


def label_substring_predicate(
    dialect: Dialect | str, term: str
) -> ColumnElement[bool]:
    """Correlated ``EXISTS``: the current ``Ticket`` carries a label whose value
    contains ``term`` (case-insensitive, wildcard-safe). Dialect-aware unnest."""
    elements = _labels_table_function(dialect)
    return exists(
        select(1)
        .select_from(elements)
        .where(func.lower(elements.c.value).contains(term.lower(), autoescape=True))
    )


def label_match_predicate(dialect: Dialect | str, value: str) -> ColumnElement[bool]:
    """Correlated ``EXISTS``: the current ``Ticket`` carries the EXACT label
    ``value`` (no substring leakage — ``bug`` ≠ ``debugger``). Used by the
    ``?labels`` AND-filter (one predicate per requested value)."""
    elements = _labels_table_function(dialect)
    return exists(select(1).select_from(elements).where(elements.c.value == value))


def labels_reach_query(dialect: Dialect | str, included: set[str], board_id):  # type: ignore[no-untyped-def]
    """SELECT ``(label_value, Ticket.board_id)`` for in-scope label values present
    on a non-deleted ticket on a DIFFERENT board (board-scope cross-board reach).

    Correlated LATERAL join (``Ticket`` ⋈ unnest(labels)) — no cartesian product.
    """
    elements = _labels_table_function(dialect)
    return (
        select(elements.c.value, Ticket.board_id)
        .select_from(Ticket)
        .join(elements, true())
        .where(
            elements.c.value.in_(included),
            Ticket.board_id != board_id,
            Ticket.deleted_at.is_(None),
        )
    )


async def distinct_ticket_labels(session: AsyncSession, term: str) -> list[str]:
    """Distinct label STRINGS containing ``term`` (ci) across non-deleted tickets.

    Dialect-aware LATERAL unnest of ``Ticket.labels`` (``Ticket`` ⋈ unnest) →
    DISTINCT element values matching ``term``. Sorted (stable order). Drives the
    search ``labels`` group.
    """
    dialect = session.bind.dialect if session.bind is not None else "sqlite"
    elements = _labels_table_function(dialect)
    stmt = (
        select(elements.c.value)
        .select_from(Ticket)
        .join(elements, true())
        .where(
            Ticket.deleted_at.is_(None),
            func.lower(elements.c.value).contains(term.lower(), autoescape=True),
        )
        .distinct()
    )
    rows = (await session.execute(stmt)).scalars().all()
    return sorted({str(value) for value in rows})
