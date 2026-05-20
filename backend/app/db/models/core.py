"""Core ProjectHub ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

# Dialect-aware types: native Postgres in production, generic JSON/Uuid for tests.
UUID_TYPE = Uuid(as_uuid=True)
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
STRING_ARRAY_TYPE = JSON().with_variant(ARRAY(String), "postgresql")


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Actor(Base, TimestampMixin):
    __tablename__ = "actors"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    agent_role_hint: Mapped[str | None] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list[BoardMembership]] = relationship(back_populates="actor")


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    states: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False)
    transitions: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    boards: Mapped[list[Board]] = relationship(back_populates="workflow")


class Board(Base, TimestampMixin):
    __tablename__ = "boards"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(5), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    project_type: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    roles: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("actors.id"))
    next_ticket_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    workflow: Mapped[Workflow] = relationship(back_populates="boards")
    memberships: Mapped[list[BoardMembership]] = relationship(back_populates="board")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="board")


class BoardMembership(Base, TimestampMixin):
    __tablename__ = "board_memberships"
    __table_args__ = (UniqueConstraint("board_id", "actor_id", name="uq_board_actor"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    board_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boards.id"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actors.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)

    board: Mapped[Board] = relationship(back_populates="memberships")
    actor: Mapped[Actor] = relationship(back_populates="memberships")


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("board_id", "key", name="uq_ticket_board_key"),
        Index("ix_tickets_board_state", "board_id", "state"),
        Index("ix_tickets_board_key", "board_id", "key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(24), nullable=False)
    board_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boards.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_phase: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("actors.id"))
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actors.id"), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    epic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"))
    labels: Mapped[list[str]] = mapped_column(STRING_ARRAY_TYPE, default=list, nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(200))
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    technical_depth: Mapped[str | None] = mapped_column(Text)
    impact_analysis: Mapped[str | None] = mapped_column(Text)
    test_plan: Mapped[str | None] = mapped_column(Text)
    steps_to_reproduce: Mapped[str | None] = mapped_column(Text)
    expected_behavior: Mapped[str | None] = mapped_column(Text)
    actual_behavior: Mapped[str | None] = mapped_column(Text)
    story_points: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("actors.id"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    board: Mapped[Board] = relationship(back_populates="tickets")
    reporter: Mapped[Actor] = relationship(foreign_keys=[reporter_id])
    assignee: Mapped[Actor | None] = relationship(foreign_keys=[assignee_id])
    epic: Mapped[Ticket | None] = relationship(remote_side=[id])
    comments: Mapped[list[Comment]] = relationship(back_populates="ticket")
    history: Mapped[list[TicketHistory]] = relationship(back_populates="ticket")


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actors.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[Ticket] = relationship(back_populates="comments")
    author: Mapped[Actor] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_actor_read", "actor_id", "is_read"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actors.id"), nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    actor: Mapped[Actor] = relationship(foreign_keys=[actor_id])
    ticket: Mapped[Ticket] = relationship()


class TicketHistory(Base):
    __tablename__ = "ticket_history"
    __table_args__ = (Index("ix_ticket_history_ticket_created", "ticket_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actors.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    field: Mapped[str | None] = mapped_column(String(80))
    old_value: Mapped[dict[str, Any] | list[Any] | str | int | bool | None] = mapped_column(
        JSON_TYPE
    )
    new_value: Mapped[dict[str, Any] | list[Any] | str | int | bool | None] = mapped_column(
        JSON_TYPE
    )
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ticket: Mapped[Ticket] = relationship(back_populates="history")
    actor: Mapped[Actor] = relationship()


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actors.id"), nullable=False)
    preference_key: Mapped[str] = mapped_column(String(80), nullable=False)
    preference_value: Mapped[str] = mapped_column(Text, nullable=False)

    # Unique constraint to prevent duplicate preferences for the same actor
    __table_args__ = (UniqueConstraint("actor_id", "preference_key", name="uk_actor_preference"),)

    actor: Mapped[Actor] = relationship()
