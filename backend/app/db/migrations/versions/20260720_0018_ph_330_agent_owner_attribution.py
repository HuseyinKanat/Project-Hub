"""PH-330: attribute agent-created work to the human who runs the agent.

Multi-user boards need "who created this ticket" to name a PERSON, not a bot.
``tickets.reporter_id`` already records the acting actor durably, and PH-322 gave
every actor an owner identity — but only for the NAMESPACED fleet
(``jarwis-pm@emrehan``, owner parsed from the ``@`` suffix). The hub-host's
original fleet was minted BEFORE that convention (``jarwis-pm``, no suffix), so it
resolved to no owner at all and every ticket it opened was unattributable.

Two changes, both keyed on that gap:

1. ``uq_actors_owner_slug`` is narrowed to a PARTIAL unique index
   (``WHERE kind = 'human'``). The invariant was always human-only — PH-322's own
   comment reads "the index only stops two HUMANS from claiming the same slug" —
   and was merely expressed as a FULL index because agents never wrote the column.
   Now that an un-namespaced agent stores its owner there, a full index would both
   reject the second agent of a fleet and collide with the human holding the same
   slug. The partial index keeps the human guard exactly as strict.

2. A BACKFILL assigning ``owner_slug`` to every un-namespaced agent, so pre-existing
   tickets attribute correctly with no ticket rows touched (the reporter FK already
   points at the actor row; giving that row an owner retroactively names the human).

Backfill targeting is deliberately narrow — an agent is only claimed when ALL hold:
``kind <> 'human'`` (never rewrite a human's authoritative slug), ``owner_slug IS
NULL`` (never overwrite an explicit assignment — this is what makes a re-run a
no-op), and ``display_name NOT LIKE '%@%'`` (a namespaced agent already resolves via
its suffix and must keep deriving, so another owner's fleet is never re-homed).

The owner is the OLDEST human carrying a slug — the same "hub-host admin" rule
``backfill_project_paths`` (PH-325) and ``create_board`` already use. That is sound
precisely because of the ``NOT LIKE '%@%'`` filter: un-namespaced agents exist only
on the hub-host that minted them pre-convention; every later per-owner fleet was
minted namespaced. If NO human has a slug the ``EXISTS`` guard makes the statement
a no-op rather than writing NULLs.

Downgrade restores the full unique index, and must FIRST null the agent slugs it
backfilled — a whole fleet sharing one slug (and sharing it with a human) would
violate the un-narrowed index and abort the downgrade.

Revision ID: ph330agentowner
Revises: ph322userprofile
"""

import sqlalchemy as sa
from alembic import op

revision = "ph330agentowner"
down_revision = "ph322userprofile"
branch_labels = None
depends_on = None

# The oldest human carrying a slug — the hub-host admin. Ordered by ``created_at``
# then ``id`` so the pick is deterministic even if two humans share a timestamp.
_HUB_HOST_OWNER = """
    SELECT h.owner_slug FROM actors h
    WHERE h.kind = 'human' AND h.owner_slug IS NOT NULL
    ORDER BY h.created_at, h.id
    LIMIT 1
"""

# Un-namespaced agents with no owner yet. Reused verbatim by the backfill and by the
# downgrade's revert so the two can never drift out of alignment.
_UNNAMESPACED_AGENTS = """
    kind <> 'human'
    AND owner_slug IS NULL
    AND display_name NOT LIKE '%@%'
"""


def upgrade() -> None:
    # Index first: the backfill writes duplicate slugs, which the OLD full unique
    # index would reject.
    op.drop_index("uq_actors_owner_slug", table_name="actors")
    op.create_index(
        "uq_actors_owner_slug",
        "actors",
        ["owner_slug"],
        unique=True,
        postgresql_where=sa.text("kind = 'human'"),
        sqlite_where=sa.text("kind = 'human'"),
    )
    op.execute(
        sa.text(
            f"""
            UPDATE actors
            SET owner_slug = ({_HUB_HOST_OWNER})
            WHERE {_UNNAMESPACED_AGENTS}
              AND EXISTS ({_HUB_HOST_OWNER})
            """
        )
    )


def downgrade() -> None:
    # Release the backfilled agent slugs BEFORE restoring the full unique index —
    # a shared fleet slug violates it. Mirrors the upgrade's targeting so only rows
    # this migration could have written are cleared: a namespaced agent never had a
    # slug, and a human's is authoritative.
    op.execute(
        sa.text(
            """
            UPDATE actors
            SET owner_slug = NULL
            WHERE kind <> 'human' AND display_name NOT LIKE '%@%'
            """
        )
    )
    op.drop_index("uq_actors_owner_slug", table_name="actors")
    op.create_index(
        "uq_actors_owner_slug",
        "actors",
        ["owner_slug"],
        unique=True,
    )
