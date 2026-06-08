"""PH-239 — SonarQube auto-scan: sonar_scan_jobs lifecycle table.

Additive migration (safe online): creates the brand-new ``sonar_scan_jobs`` table
(scan-request lifecycle queued→running→done/failed) + two cheap indexes. No existing
table is rewritten, no column added to an existing table, no backfill required — a
production DB (Kims live) upgrades lock-light.

Autogenerate also surfaced unrelated cosmetic noise (JSON→JSON-with-JSONB-variant
``alter_column`` on git_commits/sonarqube_metrics and a spurious ``ix_repositories_board``
drop). Those are pre-existing model-vs-DB representation artifacts, NOT part of this
ticket, and dropping that index would be destructive — they are deliberately OMITTED
here so this migration is strictly additive.

Downgrade drops the indexes then the table (reverse order); queued/in-flight jobs are
lost on downgrade (expected — a fresh "Scan now" re-enqueues after re-upgrading).

Revision ID: e3a479aa5c01
Revises: 20260608_0010
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3a479aa5c01"
down_revision = "20260608_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sonar_scan_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("project_key", sa.String(length=400), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["actors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sonar_scan_jobs_board_id", "sonar_scan_jobs", ["board_id"], unique=False
    )
    op.create_index(
        "ix_sonar_scan_jobs_state", "sonar_scan_jobs", ["state"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sonar_scan_jobs_state", table_name="sonar_scan_jobs")
    op.drop_index("ix_sonar_scan_jobs_board_id", table_name="sonar_scan_jobs")
    op.drop_table("sonar_scan_jobs")
