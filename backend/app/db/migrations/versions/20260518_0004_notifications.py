"""add notifications table

Revision ID: 20260518_0004
Revises: 20260515_0003
Create Date: 2026-05-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260518_0004"
down_revision = "20260515_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), default=False, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_notifications_actor_read", "notifications", ["actor_id", "is_read"])


def downgrade() -> None:
    op.drop_index("ix_notifications_actor_read", table_name="notifications")
    op.drop_table("notifications")
