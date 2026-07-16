"""PH-311: add nullable ``phase`` tag column to attachments.

Adds a single nullable ``phase VARCHAR(40)`` column to the ``attachments`` table
so evidence files can be labelled with the workflow phase/iteration they belong
to (e.g. ``repro``/``iter-2-fail``/``iter-2-pass``). The value is a free slug
validated at the service layer (``_validate_phase``); the column itself is a
plain nullable string mirroring the ``run_id`` provenance column. Existing rows
get NULL (back-compat with PH-296/297 attachments). No RBAC / role change — the
write is still gated on ``attachment.add``.

Downgrade drops the column via ``batch_alter_table`` so the SQLite test DB (and
any SQLite snapshot) can perform the copy-and-move table rebuild that native
``ALTER TABLE ... DROP COLUMN`` needs there; on PostgreSQL batch mode emits a
plain ``ALTER TABLE`` (mirrors PH-246's batch_alter_table precedent).

Revision ID: ph311attachmentphase
Revises: ph296attachments
"""

import sqlalchemy as sa
from alembic import op

revision = "ph311attachmentphase"
down_revision = "ph296attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("phase", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("attachments") as batch:
        batch.drop_column("phase")
