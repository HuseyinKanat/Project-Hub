"""PH-322: user profile owner slug + per-owner project-path registry.

Adds two things, both strictly ADDITIVE (no backfill):

1. ``actors.owner_slug`` (VARCHAR(20), nullable) + a UNIQUE index
   ``uq_actors_owner_slug``. The human-facing owner slug a human AND their whole
   ``jarwis-*@<owner>`` agent fleet share. Nullable because agents derive their
   owner from the display_name ``@`` suffix (never stored) and pre-existing humans
   have none until they set it via ``PUT /api/profile``. Multiple NULL rows coexist
   under the UNIQUE index — both PostgreSQL and SQLite treat NULLs as distinct — so
   the index only stops two HUMANS from claiming the same slug.

2. ``project_paths`` — one row per ``(owner_slug, board_id)`` mapping an owner to
   the absolute local checkout path of that board's repo. ``owner_slug`` is a PLAIN
   string key (NOT a FK to actors — the owner is a shared identity, not a single
   row); ``board_id`` FKs boards with ``ON DELETE CASCADE``; ``local_path`` is an
   opaque VARCHAR(255). Uniqueness on ``(owner_slug, board_id)`` makes the write an
   upsert (one path per owner per board).

Downgrade drops the table, then the actors index, then the column via
``batch_alter_table`` so the SQLite test DB (and any SQLite snapshot) can perform
the copy-and-move table rebuild that native ``ALTER TABLE ... DROP COLUMN`` needs
there; on PostgreSQL batch mode emits a plain ``ALTER TABLE`` (mirrors the
PH-320 / PH-311 / PH-246 batch_alter_table precedent).

Revision ID: ph322userprofile
Revises: ph320tokenlookup
"""

import sqlalchemy as sa
from alembic import op

revision = "ph322userprofile"
down_revision = "ph320tokenlookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "actors",
        sa.Column("owner_slug", sa.String(20), nullable=True),
    )
    op.create_index(
        "uq_actors_owner_slug",
        "actors",
        ["owner_slug"],
        unique=True,
    )
    op.create_table(
        "project_paths",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_slug", sa.String(20), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("local_path", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_slug", "board_id", name="uq_project_path_owner_board"),
    )


def downgrade() -> None:
    op.drop_table("project_paths")
    op.drop_index("uq_actors_owner_slug", table_name="actors")
    with op.batch_alter_table("actors") as batch:
        batch.drop_column("owner_slug")
