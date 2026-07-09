"""PH-294: structured parallel-independence fields on tickets.

files_touched_globs — Architect declares at approve; the Jarwis Coordinator's
parallel-independence test (contracts/parallel.md §1) computes path-disjointness
from it. blocked_by — ticket KEYS this ticket depends on; PM sets at epic
decompose / planning-council ticketize, the Coordinator topo-sorts from it.
Both were Jarwis-side "structured fields" that the server silently dropped as
unknown Pydantic fields until now. The reverse "blocks" direction is derived by
query, not stored.

Revision ID: ph294globscols
Revises: 2722c9066c32
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ph294globscols"
down_revision = "2722c9066c32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("files_touched_globs", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("blocked_by", postgresql.ARRAY(sa.String()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "blocked_by")
    op.drop_column("tickets", "files_touched_globs")
