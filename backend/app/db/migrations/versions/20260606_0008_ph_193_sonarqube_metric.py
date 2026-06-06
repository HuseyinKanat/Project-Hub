"""PH-193 — SonarQube board-health: boards.sonarqube_project_key + sonarqube_metrics

Additive migration (safe online):
  1. Adds nullable ``boards.sonarqube_project_key`` (String(400)) — no backfill.
  2. Creates ``sonarqube_metrics`` (1 board : 0..1 latest-snapshot, upsert-in-place
     keyed by ``UniqueConstraint(board_id)``).

No existing table is rewritten; the new column is nullable and the new table is
brand-new, so this is a lock-light online migration.

Downgrade drops the table then the column (reverse order); any cached metrics are
lost on downgrade (expected — a poll re-populates after upgrading).

Revision ID: 20260606_0008
Revises: 20260604_0007
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260606_0008"
down_revision = "20260604_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. boards.sonarqube_project_key — additive nullable column, no backfill.
    op.add_column(
        "boards",
        sa.Column("sonarqube_project_key", sa.String(400), nullable=True),
    )

    # 2. sonarqube_metrics — latest snapshot per board (upsert target).
    op.create_table(
        "sonarqube_metrics",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "board_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_key", sa.String(400), nullable=False),
        sa.Column("quality_gate_status", sa.String(20), nullable=True),
        sa.Column("bugs", sa.Integer(), nullable=True),
        sa.Column("vulnerabilities", sa.Integer(), nullable=True),
        sa.Column("code_smells", sa.Integer(), nullable=True),
        sa.Column("coverage", sa.Numeric(5, 2), nullable=True),
        sa.Column("duplicated_lines_density", sa.Numeric(5, 2), nullable=True),
        sa.Column("ncloc", sa.Integer(), nullable=True),
        sa.Column("raw_measures", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("board_id", name="uq_sonarqube_metric_board"),
    )


def downgrade() -> None:
    op.drop_table("sonarqube_metrics")
    op.drop_column("boards", "sonarqube_project_key")
