"""PH-295: backfill "hotfix" into existing workflows' field-gate exempt lists.

PH-291 added hotfix to DEFAULT_TRANSITIONS' exempt_ticket_types, but that
template only flows into NEWLY created boards/workflows — every pre-existing
board's Workflow.transitions JSON still gated hotfix tickets, so the Jarwis
hotfix flow (post-hoc technical_depth/AC/impact_analysis — flows/hotfix.md)
kept hitting field_gate_not_met churn on live boards. This data migration
walks every workflow row and adds "hotfix" next to "epic" in each
field_gates.exempt_ticket_types list (idempotent: skips lists that already
carry it). Downgrade removes only "hotfix", leaving any other exemptions.

Revision ID: ph295hotfixgates
Revises: ph294globscols
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "ph295hotfixgates"
down_revision = "ph294globscols"
branch_labels = None
depends_on = None

_workflows = sa.table(
    "workflows",
    sa.column("id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.column("transitions", sa.JSON),
)


def _edit_exempt_lists(add: bool) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(_workflows.c.id, _workflows.c.transitions)).fetchall()
    for wf_id, transitions in rows:
        if isinstance(transitions, str):  # driver-dependent JSON decode
            transitions = json.loads(transitions)
        if not isinstance(transitions, list):
            continue
        changed = False
        for t in transitions:
            gates = t.get("field_gates") if isinstance(t, dict) else None
            if not isinstance(gates, dict):
                continue
            exempt = gates.get("exempt_ticket_types")
            if not isinstance(exempt, list):
                continue
            if add and "hotfix" not in exempt:
                exempt.append("hotfix")
                changed = True
            elif not add and "hotfix" in exempt:
                gates["exempt_ticket_types"] = [x for x in exempt if x != "hotfix"]
                changed = True
        if changed:
            conn.execute(
                _workflows.update()
                .where(_workflows.c.id == wf_id)
                .values(transitions=transitions)
            )


def upgrade() -> None:
    _edit_exempt_lists(add=True)


def downgrade() -> None:
    _edit_exempt_lists(add=False)
