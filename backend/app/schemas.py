"""Pydantic request and response schemas."""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TicketType = Literal["feature", "bug", "task", "epic"]
Priority = Literal["low", "medium", "high", "urgent"]


class ActorSummary(BaseModel):
    id: UUID
    kind: str
    display_name: str
    agent_id: str | None = None
    agent_role_hint: str | None = None


class WorkflowResponse(BaseModel):
    id: UUID
    name: str
    states: list[dict[str, object]]
    transitions: list[dict[str, object]]
    is_default: bool


class BoardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    project_type: str | None = None
    roles: dict[str, object] | None = None


class BoardResponse(BaseModel):
    id: UUID
    key: str
    name: str
    description: str
    project_type: str
    roles: dict[str, object]
    workflow: WorkflowResponse
    created_at: datetime
    updated_at: datetime


class BoardListResponse(BaseModel):
    boards: list[BoardResponse]


class TicketCreate(BaseModel):
    board_id: str = Field(..., description="Board UUID or key")
    type: TicketType
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    priority: Priority = "medium"
    epic_id: UUID | None = None
    labels: list[str] = Field(default_factory=list)
    acceptance_criteria: str | None = None
    technical_depth: str | None = None
    steps_to_reproduce: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    story_points: int | None = None
    due_date: date | None = None


class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    priority: Priority | None = None
    epic_id: UUID | None = None
    labels: list[str] | None = None
    acceptance_criteria: str | None = None
    technical_depth: str | None = None
    impact_analysis: str | None = None
    test_plan: str | None = None
    steps_to_reproduce: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    branch_name: str | None = None
    story_points: int | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def prevent_required_field_nulls(self) -> Self:
        required_when_present = {"title", "description", "priority", "labels"}
        for field_name in required_when_present & self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class AssignTicket(BaseModel):
    assignee_id: str | None = Field(default=None, description="Actor UUID or agent_id")


class AgentPhaseUpdate(BaseModel):
    phase: Literal["planning", "analyzing", "coding", "testing", "reviewing", "idle"]
    message: str = Field(default="", max_length=500)


class DeleteTicket(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class TransitionState(BaseModel):
    to_state: str
    comment: str | None = None


class TicketResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    key: str
    board_id: UUID
    type: str
    title: str
    description: str
    state: str
    agent_phase: dict[str, object] | None
    assignee: ActorSummary | None
    reporter: ActorSummary
    priority: str
    epic_id: UUID | None
    labels: list[str]
    acceptance_criteria: str | None
    technical_depth: str | None
    impact_analysis: str | None
    test_plan: str | None
    steps_to_reproduce: str | None
    expected_behavior: str | None
    actual_behavior: str | None
    story_points: int | None
    due_date: date | None
    branch_name: str | None
    claimed_by: UUID | None
    claimed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    links: dict[str, str] = Field(alias="_links")


class TicketListResponse(BaseModel):
    tickets: list[TicketResponse]


class LinkPR(BaseModel):
    ticket_id: str
    pr_url: str = Field(..., min_length=1)
    pr_number: int | None = None
    pr_title: str = ""


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1)


class CommentResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    author: ActorSummary
    body: str
    created_at: datetime
    edited_at: datetime | None


class HistoryResponse(BaseModel):
    id: UUID
    event_type: str
    field: str | None
    old_value: object | None
    new_value: object | None
    metadata: dict[str, object]
    actor: ActorSummary
    created_at: datetime


class NotificationResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_key: str
    event_type: str
    message: str
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    unread_count: int
