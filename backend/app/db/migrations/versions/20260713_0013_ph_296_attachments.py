"""PH-296: ticket evidence attachments table + attachment.add role backfill.

Adds the ``attachments`` table (a Comment-shaped, ticket-owned, author-stamped
record whose payload lives on disk under a UUID shard key) and grants the new
``attachment.add`` capability to every existing board's implementer roles plus
qa + pm. Like the PH-295 hotfix-gate backfill, the DEFAULT_WEB_ROLES template
change only reaches NEWLY created boards — this data migration walks every
board's ``roles`` JSON and appends ``attachment.add`` to the relevant roles
(idempotent: skips lists that already carry it). Downgrade removes only the
capability it added and drops the table.

Revision ID: ph296attachments
Revises: ph295hotfixgates
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "ph296attachments"
down_revision = "ph295hotfixgates"
branch_labels = None
depends_on = None

# Roles that gain attachment.add: every implementer variant + qa + pm (the roles
# that produce/curate ticket evidence). Mirrors defaults._IMPLEMENTER_PERMISSIONS
# recipients plus qa + pm.
_ATTACH_ROLES = frozenset(
    {
        "pm",
        "qa",
        "backend_dev",
        "frontend_dev",
        "unity_dev",
        "unity_scene_manager",
        "unity_platform",
        "android_dev",
        "ios_dev",
        "data_engineer",
        "data_labeler",
        "ml_engineer",
        "ml_analyst",
    }
)
_ATTACH_CAP = "attachment.add"

_boards = sa.table(
    "boards",
    sa.column("id", sa.Uuid(as_uuid=True)),
    sa.column("roles", sa.JSON),
)


def _edit_role_caps(add: bool) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(_boards.c.id, _boards.c.roles)).fetchall()
    for board_id, roles in rows:
        if isinstance(roles, str):  # driver-dependent JSON decode
            roles = json.loads(roles)
        if not isinstance(roles, dict):
            continue
        role_map = roles.get("roles")
        if not isinstance(role_map, dict):
            continue
        changed = False
        for role_name in _ATTACH_ROLES:
            entry = role_map.get(role_name)
            if not isinstance(entry, dict):
                continue
            perms = entry.get("permissions")
            if not isinstance(perms, list):
                continue
            if add and _ATTACH_CAP not in perms:
                perms.append(_ATTACH_CAP)
                changed = True
            elif not add and _ATTACH_CAP in perms:
                entry["permissions"] = [p for p in perms if p != _ATTACH_CAP]
                changed = True
        if changed:
            conn.execute(
                _boards.update().where(_boards.c.id == board_id).values(roles=roles)
            )


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ticket_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tickets.id"), nullable=False
        ),
        sa.Column(
            "author_id", sa.Uuid(as_uuid=True), sa.ForeignKey("actors.id"), nullable=False
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(120), nullable=True),
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
    )
    op.create_index("ix_attachments_ticket_id", "attachments", ["ticket_id"])

    _edit_role_caps(add=True)


def downgrade() -> None:
    _edit_role_caps(add=False)
    op.drop_index("ix_attachments_ticket_id", table_name="attachments")
    op.drop_table("attachments")
