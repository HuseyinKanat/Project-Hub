"""PH-150 G1 — add repositories table (board↔repo config)

Additive migration: creates the `repositories` table with a unique FK to
`boards.id` (ondelete CASCADE).  No existing tables are modified.

Downgrade drops the table + index — any stored config is lost (expected
behaviour for G1; export before downgrading if needed).

Revision ID: 20260604_0006
Revises: 20260522_0005
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_0006"
down_revision = "20260522_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "board_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.String(20),
            nullable=False,
            server_default="local",
        ),
        sa.Column("remote_url", sa.String(500), nullable=True),
        sa.Column(
            "default_branch",
            sa.String(120),
            nullable=False,
            server_default="main",
        ),
        sa.Column("local_path", sa.String(500), nullable=False),
        sa.Column("last_synced_sha", sa.String(40), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("board_id", name="uq_repository_board"),
        sa.CheckConstraint(
            "provider IN ('github','gitlab','local')",
            name="ck_repository_provider",
        ),
    )
    op.create_index("ix_repositories_board", "repositories", ["board_id"])


def downgrade() -> None:
    op.drop_index("ix_repositories_board", table_name="repositories")
    op.drop_table("repositories")
