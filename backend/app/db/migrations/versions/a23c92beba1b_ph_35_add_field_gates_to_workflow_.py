"""PH-35 add field_gates to workflow transitions

Migration to add field_gates configuration to existing workflow transitions.
This moves field gate logic from hardcoded TRANSITION_FIELD_GATES to JSON configuration.

Revision ID: a23c92beba1b
Revises: 2e0ab0aa83ce
Create Date: 2026-05-20 12:59:50.157004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
import json

revision = 'a23c92beba1b'
down_revision = '2e0ab0aa83ce'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add field_gates to existing workflow transitions.

    Updates existing workflows to include field_gates configuration
    based on the previously hardcoded TRANSITION_FIELD_GATES.
    """
    # Define the field gates mapping from the hardcoded constants
    field_gates_mapping = {
        ("in_progress", "in_review"): {
            "required_fields": ["technical_depth", "acceptance_criteria"],
            "exempt_ticket_types": ["epic"]
        },
        ("in_review", "in_test"): {
            "required_fields": ["test_plan"],
            "exempt_ticket_types": ["epic"]
        },
        ("in_test", "done"): {
            "required_fields": ["impact_analysis"],
            "exempt_ticket_types": ["epic"]
        }
    }

    # Get connection
    conn = op.get_bind()

    # Fetch all workflows
    result = conn.execute(text("SELECT id, transitions FROM workflows"))
    workflows = result.fetchall()

    for workflow_id, transitions in workflows:
        updated_transitions = []
        transitions_modified = False

        for transition in transitions:
            transition_key = (transition.get("from"), transition.get("to"))

            # Check if this transition needs field_gates
            if transition_key in field_gates_mapping:
                # Only add field_gates if not already present
                if "field_gates" not in transition:
                    transition["field_gates"] = field_gates_mapping[transition_key]
                    transitions_modified = True

            updated_transitions.append(transition)

        # Update the workflow if any transitions were modified
        if transitions_modified:
            conn.execute(
                text("UPDATE workflows SET transitions = :transitions WHERE id = :workflow_id"),
                {
                    "transitions": json.dumps(updated_transitions),
                    "workflow_id": workflow_id
                }
            )


def downgrade() -> None:
    """
    Remove field_gates from workflow transitions.

    This reverts the migration by removing field_gates configuration
    from all workflow transitions.
    """
    # Get connection
    conn = op.get_bind()

    # Fetch all workflows
    result = conn.execute(text("SELECT id, transitions FROM workflows"))
    workflows = result.fetchall()

    for workflow_id, transitions in workflows:
        updated_transitions = []
        transitions_modified = False

        for transition in transitions:
            if "field_gates" in transition:
                # Remove field_gates from transition
                transition_copy = transition.copy()
                del transition_copy["field_gates"]
                updated_transitions.append(transition_copy)
                transitions_modified = True
            else:
                updated_transitions.append(transition)

        # Update the workflow if any transitions were modified
        if transitions_modified:
            conn.execute(
                text("UPDATE workflows SET transitions = :transitions WHERE id = :workflow_id"),
                {
                    "transitions": json.dumps(updated_transitions),
                    "workflow_id": workflow_id
                }
            )
