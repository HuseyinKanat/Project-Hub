"""PH-320: O(1) indexed token-lookup key on actors.

Adds ``actors.token_lookup`` (VARCHAR(64), nullable) plus a UNIQUE index
``uq_actors_token_lookup``. The column stores ``sha256hex(token)`` -- a
deterministic INDEX key that lets ``current_actor`` resolve a bearer token with a
single indexed row fetch instead of the O(n) bcrypt scan. (PH-319's process-local
cache fixed only the warm steady state; the indexed key closes the cold/restart
path AND the 403/wrong-token path, both still O(n) before this.)

BACKFILL IS IMPOSSIBLE AND NOT ATTEMPTED: the plaintext token cannot be recovered
from the salted bcrypt ``token_hash``, so pre-existing rows are left NULL. They
are populated FORWARD two ways: (1) lazy backfill -- the first successful auth of
a legacy token writes its digest via the NULL-restricted fallback in
``app.api.deps.current_actor``; (2) every mint site (``bootstrap`` admin,
``create_jarwis_actors`` new-actor + ``--rotate``) sets ``token_lookup`` at write
time via ``cli.set_actor_token``. Multiple NULL rows coexist under the UNIQUE
index because both PostgreSQL and SQLite treat NULLs as distinct.

Downgrade drops the index then the column via ``batch_alter_table`` so the SQLite
test DB (and any SQLite snapshot) can perform the copy-and-move table rebuild that
native ``ALTER TABLE ... DROP COLUMN`` needs there; on PostgreSQL batch mode emits
a plain ``ALTER TABLE`` (mirrors the PH-311 / PH-246 batch_alter_table precedent).

Revision ID: ph320tokenlookup
Revises: ph313attachmentcrud
"""

import sqlalchemy as sa
from alembic import op

revision = "ph320tokenlookup"
down_revision = "ph313attachmentcrud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "actors",
        sa.Column("token_lookup", sa.String(64), nullable=True),
    )
    op.create_index(
        "uq_actors_token_lookup",
        "actors",
        ["token_lookup"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_actors_token_lookup", table_name="actors")
    with op.batch_alter_table("actors") as batch:
        batch.drop_column("token_lookup")
