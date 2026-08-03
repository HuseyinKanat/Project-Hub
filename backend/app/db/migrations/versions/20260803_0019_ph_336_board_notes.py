"""PH-336: board-scoped notes / guardrails store (one additive table).

The round's ONLY migration. Strictly ADDITIVE — a single new ``board_notes`` table,
no ALTER on any existing table, so ``upgrade`` is a plain ``CREATE TABLE`` and
``downgrade`` is a plain ``drop_table`` (NO ``batch_alter_table`` — that is only for
DROP COLUMN on the SQLite test DB, which a whole-table drop never needs). Follows the
``project_paths`` (PH-322) additive-table template exactly.

Schema: ``board_notes(id, board_id, body, created_by, created_at, updated_at)``.
  - ``board_id`` FKs ``boards.id`` with ``ON DELETE CASCADE`` (referential-integrity
    insurance — there is NO board-DELETE REST endpoint; the CASCADE only fires on a
    CLI/DB board delete). Mirrors ``project_paths.board_id`` / ``repositories.board_id``.
  - ``created_by`` is a NULLABLE actor FK with NO ondelete (mirrors ``boards.created_by``):
    actors are not deleted, and a deleted author resolves to a null name — it never
    blocks the note.
  - NO severity/tag column (cut in round-1 consensus — a note is body + author +
    timestamp + board_id).

Revision ID: ph336boardnotes
Revises: ph330agentowner  (the single verified Alembic head)
"""

import sqlalchemy as sa
from alembic import op

revision = "ph336boardnotes"
down_revision = "ph330agentowner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["actors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("board_notes")
