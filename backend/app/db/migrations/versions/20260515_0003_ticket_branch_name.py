"""add ticket.branch_name column

Revision ID: 20260515_0003
Revises: 20260513_0002
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260515_0003"
down_revision = "20260513_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("branch_name", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "branch_name")
