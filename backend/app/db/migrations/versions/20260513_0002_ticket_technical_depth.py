"""add ticket.technical_depth column

Revision ID: 20260513_0002
Revises: 20260513_0001
Create Date: 2026-05-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260513_0002"
down_revision = "20260513_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("technical_depth", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "technical_depth")
