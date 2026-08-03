"""PH-338: per-board singleton project-summary store (one additive table).

The round's ONLY migration. Strictly ADDITIVE — a single new ``board_summaries``
table, no ALTER on any existing table, so ``upgrade`` is a plain ``CREATE TABLE`` and
``downgrade`` is a plain ``drop_table`` (NO ``batch_alter_table`` — that is only for
DROP COLUMN on the SQLite test DB, which a whole-table drop never needs). Follows the
``board_notes`` (PH-336) additive-table template exactly; the ONE structural
difference is the ``uq_board_summary_board`` UNIQUE on ``board_id`` — that is the
singleton (0..1 per board) invariant that turns an upsert into an update, not an
append.

Schema: ``board_summaries(id, board_id, purpose, status, progress, highlights,
milestones, updated_by, created_at, updated_at)``.
  - ``board_id`` FKs ``boards.id`` with ``ON DELETE CASCADE`` (referential-integrity
    insurance — there is NO board-DELETE REST endpoint; the CASCADE only fires on a
    CLI/DB board delete) AND is UNIQUE (``uq_board_summary_board``) → 0..1 per board.
    Mirrors ``board_notes.board_id`` / ``project_paths.board_id`` for the CASCADE.
  - ``purpose``/``status``/``progress``/``highlights`` are nullable Text (a partial
    summary is valid — the fixed section set is a stable FE contract).
  - ``milestones`` is a ``sa.JSON()`` list, ``nullable=False`` with ``server_default
    "[]"`` (git_cache PH-152 ``parents``/``ticket_keys`` precedent) — a summary always
    has a (possibly empty) milestone array, never NULL.
  - ``updated_by`` is a NULLABLE actor FK with NO ondelete (mirrors
    ``board_notes.created_by`` / ``boards.created_by``): records the last writer; a
    deleted author resolves to a null name, never blocking the summary.

Revision ID: ph338boardsummary
Revises: ph336boardnotes  (the single verified Alembic head — full revision-graph walk
                           at implement time confirmed exactly one head)
"""

import sqlalchemy as sa
from alembic import op

revision = "ph338boardsummary"
down_revision = "ph336boardnotes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("progress", sa.Text(), nullable=True),
        sa.Column("highlights", sa.Text(), nullable=True),
        sa.Column("milestones", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["updated_by"], ["actors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_id", name="uq_board_summary_board"),
    )


def downgrade() -> None:
    op.drop_table("board_summaries")
