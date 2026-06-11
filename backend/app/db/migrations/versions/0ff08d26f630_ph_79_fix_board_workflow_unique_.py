"""PH-79 fix board_workflow unique constraint to partial index on is_active true

Revision ID: 0ff08d26f630
Revises: a23c92beba1b
Create Date: 2026-05-22 11:30:27.845288
"""

from alembic import op
import sqlalchemy as sa


revision = '0ff08d26f630'
down_revision = 'a23c92beba1b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace the full unique constraint on (board_id, is_active) with a partial
    # unique index that only enforces uniqueness when is_active = true.
    # This allows a board to have multiple inactive workflows in board_workflows,
    # while still ensuring at most one active workflow per board.
    op.drop_constraint("uq_board_active_workflow", "board_workflows", type_="unique")
    op.create_index(
        "uq_board_active_workflow",
        "board_workflows",
        ["board_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    # Restore the original full unique constraint on (board_id, is_active)
    op.drop_index("uq_board_active_workflow", table_name="board_workflows")
    op.create_unique_constraint(
        "uq_board_active_workflow",
        "board_workflows",
        ["board_id", "is_active"],
    )
