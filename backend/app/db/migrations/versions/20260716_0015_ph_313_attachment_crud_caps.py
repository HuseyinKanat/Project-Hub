"""PH-313: backfill attachment.update / attachment.delete caps into board roles.

PH-313 adds two capabilities to DEFAULT_WEB_ROLES:

  * ``attachment.update`` — every implementer variant + qa + pm (the same
    recipients as the PH-296 ``attachment.add`` backfill).
  * ``attachment.delete`` — qa + pm ONLY (the evidence curators who may destroy
    a row+blob). Architect + reviewer + implementers get neither.

Like the PH-296 backfill, the template change only reaches NEWLY created boards —
this DATA migration walks every existing board's ``roles`` JSON and appends each
cap to the relevant roles by a role→caps map (idempotent: skips lists that
already carry a cap, so re-running is a no-op). NO schema change. Downgrade
removes only the two caps it added, leaving every other permission intact.

Revision ID: ph313attachmentcrud
Revises: ph311attachmentphase
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "ph313attachmentcrud"
down_revision = "ph311attachmentphase"
branch_labels = None
depends_on = None

_UPDATE_CAP = "attachment.update"
_DELETE_CAP = "attachment.delete"

# Every implementer variant gains update ONLY (mirrors defaults._IMPLEMENTER_PERMISSIONS
# recipients). qa + pm additionally gain delete.
_IMPLEMENTER_ROLES = frozenset(
    {
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
# role → the caps that role should carry after upgrade.
_ROLE_CAPS: dict[str, frozenset[str]] = {
    **{role: frozenset({_UPDATE_CAP}) for role in _IMPLEMENTER_ROLES},
    "pm": frozenset({_UPDATE_CAP, _DELETE_CAP}),
    "qa": frozenset({_UPDATE_CAP, _DELETE_CAP}),
}

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
        for role_name, caps in _ROLE_CAPS.items():
            entry = role_map.get(role_name)
            if not isinstance(entry, dict):
                continue
            perms = entry.get("permissions")
            if not isinstance(perms, list):
                continue
            for cap in caps:
                if add and cap not in perms:
                    perms.append(cap)
                    changed = True
                elif not add and cap in perms:
                    perms = [p for p in perms if p != cap]
                    entry["permissions"] = perms
                    changed = True
        if changed:
            conn.execute(
                _boards.update().where(_boards.c.id == board_id).values(roles=roles)
            )


def upgrade() -> None:
    _edit_role_caps(add=True)


def downgrade() -> None:
    _edit_role_caps(add=False)
