"""Tests for workflow-based field gates configuration."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import Board, Ticket, Workflow
from app.services.defaults import DEFAULT_STATES
from app.services.tickets import FieldGateNotMet, transition_ticket_state
from app.services.workflows import (
    get_field_gates,
)


@pytest.fixture
async def workflow_with_field_gates(db_session: AsyncSession):
    """Create a workflow with field_gates configuration."""
    # Transitions with field gates (from DEFAULT_TRANSITIONS)
    transitions_with_gates = [
        {"from": "backlog", "to": "to_do", "allowed_roles": ["pm", "architect"]},
        {"from": "to_do", "to": "in_progress", "allowed_roles": ["assignee", "pm"]},
        {
            "from": "in_progress",
            "to": "in_review",
            "allowed_roles": ["assignee"],
            "field_gates": {
                "required_fields": ["technical_depth", "acceptance_criteria"],
                "exempt_ticket_types": ["epic"]
            }
        },
        {
            "from": "in_review",
            "to": "in_test",
            "allowed_roles": ["assignee", "pm", "qa", "reviewer"],
            "field_gates": {
                "required_fields": ["test_plan"],
                "exempt_ticket_types": ["epic"]
            }
        },
        {
            "from": "in_test",
            "to": "done",
            "allowed_roles": ["qa", "pm"],
            "field_gates": {
                "required_fields": ["impact_analysis"],
                "exempt_ticket_types": ["epic"]
            }
        },
        {"from": "*", "to": "done", "allowed_roles": ["pm", "admin"]},
    ]

    workflow = Workflow(
        name="Test Workflow with Field Gates",
        states=DEFAULT_STATES,
        transitions=transitions_with_gates,
        is_default=True,
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)
    return workflow


@pytest.fixture
async def workflow_without_field_gates(db_session: AsyncSession):
    """Create a workflow without field_gates configuration."""
    transitions_no_gates = [
        {"from": "backlog", "to": "to_do", "allowed_roles": ["pm"]},
        {"from": "to_do", "to": "in_progress", "allowed_roles": ["assignee"]},
        {"from": "in_progress", "to": "done", "allowed_roles": ["assignee"]},
    ]

    workflow = Workflow(
        name="Test Workflow without Field Gates",
        states=DEFAULT_STATES,
        transitions=transitions_no_gates,
        is_default=False,
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)
    return workflow


class TestGetFieldGates:
    """Test field gate lookup functions."""

    async def test_get_field_gates_with_gates(
        self, db_session: AsyncSession, workflow_with_field_gates: Workflow
    ):
        """Test getting field gates for transition that has gates configured."""
        required_fields, exempt_types = await get_field_gates(
            db_session, workflow_with_field_gates, "in_progress", "in_review"
        )

        assert required_fields == ["technical_depth", "acceptance_criteria"]
        assert exempt_types == frozenset(["epic"])

    async def test_get_field_gates_without_gates(
        self, db_session: AsyncSession, workflow_with_field_gates: Workflow
    ):
        """Test getting field gates for transition without gates configured."""
        required_fields, exempt_types = await get_field_gates(
            db_session, workflow_with_field_gates, "backlog", "to_do"
        )

        assert required_fields == []
        assert exempt_types == frozenset()

    async def test_get_field_gates_nonexistent_transition(
        self, db_session: AsyncSession, workflow_with_field_gates: Workflow
    ):
        """Test getting field gates for nonexistent transition."""
        required_fields, exempt_types = await get_field_gates(
            db_session, workflow_with_field_gates, "nonexistent", "state"
        )

        assert required_fields == []
        assert exempt_types == frozenset()

    async def test_get_field_gates_all_transitions(
        self, db_session: AsyncSession, workflow_with_field_gates: Workflow
    ):
        """Test all field gate configurations match expected values."""
        test_cases = [
            ("in_progress", "in_review", ["technical_depth", "acceptance_criteria"], ["epic"]),
            ("in_review", "in_test", ["test_plan"], ["epic"]),
            ("in_test", "done", ["impact_analysis"], ["epic"]),
        ]

        for from_state, to_state, expected_fields, expected_exempt in test_cases:
            required_fields, exempt_types = await get_field_gates(
                db_session, workflow_with_field_gates, from_state, to_state
            )

            assert required_fields == expected_fields
            assert exempt_types == frozenset(expected_exempt)


class TestFieldGateTicketTransition:
    """Test field gate checking during ticket transitions."""

    @pytest.fixture
    async def board_with_workflow(
        self, db_session: AsyncSession, workflow_with_field_gates: Workflow, seed
    ):
        """Create a board with field gates workflow."""
        board = seed.board
        board.workflow_id = workflow_with_field_gates.id
        await db_session.commit()
        return board

    @pytest.fixture
    async def ticket_in_progress(
        self, db_session: AsyncSession, board_with_workflow: Board, seed
    ):
        """Create a ticket in in_progress state."""
        ticket = Ticket(
            id=uuid4(),
            key="TEST-123",
            title="Test Ticket",
            description="Test description",
            type="feature",
            state="in_progress",
            board_id=board_with_workflow.id,
            reporter_id=seed.admin.id,
        )
        db_session.add(ticket)
        await db_session.commit()
        await db_session.refresh(ticket)
        return ticket

    async def test_transition_with_missing_fields_raises_error(
        self,
        db_session: AsyncSession,
        ticket_in_progress: Ticket,
        seed,
    ):
        """Test that transition fails when required fields are missing."""
        # ticket_in_progress has no technical_depth or acceptance_criteria
        with pytest.raises(FieldGateNotMet) as exc_info:
            await transition_ticket_state(
                db_session,
                actor=seed.admin,
                ticket_id=str(ticket_in_progress.id),
                to_state="in_review",
            )

        assert "technical_depth" in exc_info.value.missing_fields
        assert "acceptance_criteria" in exc_info.value.missing_fields

    async def test_transition_with_required_fields_succeeds(
        self,
        db_session: AsyncSession,
        ticket_in_progress: Ticket,
        seed,
    ):
        """Test that transition succeeds when required fields are present."""
        # Add required fields
        ticket_in_progress.technical_depth = "Some technical details"
        ticket_in_progress.acceptance_criteria = "Acceptance criteria defined"
        await db_session.commit()

        # Transition should succeed
        result = await transition_ticket_state(
            db_session,
            actor=seed.admin,
            ticket_id=str(ticket_in_progress.id),
            to_state="in_review",
        )

        assert result.state == "in_review"

    async def test_exempt_ticket_type_bypasses_field_gates(
        self,
        db_session: AsyncSession,
        board_with_workflow: Board,
        seed,
    ):
        """Test that exempt ticket types (like epic) bypass field gates."""
        # Create an epic ticket without required fields
        epic_ticket = Ticket(
            id=uuid4(),
            key="EPIC-1",
            title="Test Epic",
            description="Epic description",
            type="epic",  # This type is exempt from field gates
            state="in_progress",
            board_id=board_with_workflow.id,
            reporter_id=seed.admin.id,
        )
        db_session.add(epic_ticket)
        await db_session.commit()

        # Transition should succeed despite missing required fields
        result = await transition_ticket_state(
            db_session,
            actor=seed.admin,
            ticket_id=str(epic_ticket.id),
            to_state="in_review",
        )

        assert result.state == "in_review"

    async def test_transition_without_field_gates_always_succeeds(
        self,
        db_session: AsyncSession,
        workflow_without_field_gates: Workflow,
        seed,
    ):
        """Test that transitions without field_gates configuration always succeed."""
        # Create board with workflow that has no field gates
        board = seed.board
        board.workflow_id = workflow_without_field_gates.id
        await db_session.commit()

        # Create ticket without any fields
        ticket = Ticket(
            id=uuid4(),
            key="NO-GATES-1",
            title="No Gates Ticket",
            description="Test description",
            type="feature",
            state="to_do",
            board_id=board.id,
            reporter_id=seed.admin.id,
        )
        db_session.add(ticket)
        await db_session.commit()

        # Transition should succeed without any required fields
        result = await transition_ticket_state(
            db_session,
            actor=seed.admin,
            ticket_id=str(ticket.id),
            to_state="in_progress",
        )

        assert result.state == "in_progress"

    async def test_partial_field_requirements_still_fail(
        self,
        db_session: AsyncSession,
        ticket_in_progress: Ticket,
        seed,
    ):
        """Test that having only some required fields still fails transition."""
        # Add only one of the two required fields
        ticket_in_progress.technical_depth = "Some technical details"
        # Missing acceptance_criteria
        await db_session.commit()

        with pytest.raises(FieldGateNotMet) as exc_info:
            await transition_ticket_state(
                db_session,
                actor=seed.admin,
                ticket_id=str(ticket_in_progress.id),
                to_state="in_review",
            )

        # Should only list the missing field
        assert exc_info.value.missing_fields == ["acceptance_criteria"]

    async def test_empty_string_fields_count_as_missing(
        self,
        db_session: AsyncSession,
        ticket_in_progress: Ticket,
        seed,
    ):
        """Test that empty string fields count as missing."""
        # Set fields to empty strings or whitespace
        ticket_in_progress.technical_depth = ""
        ticket_in_progress.acceptance_criteria = "   "  # Just whitespace
        await db_session.commit()

        with pytest.raises(FieldGateNotMet) as exc_info:
            await transition_ticket_state(
                db_session,
                actor=seed.admin,
                ticket_id=str(ticket_in_progress.id),
                to_state="in_review",
            )

        assert "technical_depth" in exc_info.value.missing_fields
        assert "acceptance_criteria" in exc_info.value.missing_fields


class TestBackwardCompatibility:
    """Test backward compatibility with existing workflows."""

    async def test_legacy_workflow_without_field_gates_works(
        self, db_session: AsyncSession, workflow_without_field_gates: Workflow
    ):
        """Test that legacy workflows without field_gates continue to work."""
        # This simulates an existing workflow that was created before field_gates feature
        required_fields, exempt_types = await get_field_gates(
            db_session, workflow_without_field_gates, "to_do", "in_progress"
        )

        # Should return empty values - no field validation
        assert required_fields == []
        assert exempt_types == frozenset()