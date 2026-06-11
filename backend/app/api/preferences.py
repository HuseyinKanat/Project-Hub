"""User preferences API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.services.user_preferences import (
    get_user_preferences,
    set_user_preference,
    get_notification_types_enabled,
    get_user_email,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])


class PreferenceUpdate(BaseModel):
    """Request model for updating a preference."""
    key: str
    value: str


class UserPreferencesResponse(BaseModel):
    """Response model for user preferences."""
    preferences: dict[str, str]
    email_notifications_enabled: bool
    notification_types: dict[str, bool]
    email_address: str | None


class EmailSettingsUpdate(BaseModel):
    """Request model for updating email settings."""
    email_address: str | None = None
    email_notifications: bool = True
    email_state_changes: bool = True
    email_comments: bool = True


@router.get("/", response_model=UserPreferencesResponse)
async def get_current_user_preferences(
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> UserPreferencesResponse:
    """Get current user's preferences."""
    preferences = await get_user_preferences(session, actor.id)
    notification_types = await get_notification_types_enabled(session, actor.id)
    email_address = await get_user_email(session, actor.id)

    # Check if email notifications are globally enabled
    email_enabled = preferences.get("email_notifications", "true").lower() in ("true", "1", "yes", "on")

    return UserPreferencesResponse(
        preferences=preferences,
        email_notifications_enabled=email_enabled,
        notification_types=notification_types,
        email_address=email_address,
    )


@router.put("/")
async def update_user_preference(
    update: PreferenceUpdate,
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Update a single user preference."""
    await set_user_preference(session, actor.id, update.key, update.value)
    await session.commit()

    return {"success": True, "key": update.key, "value": update.value}


@router.put("/email")
async def update_email_settings(
    settings: EmailSettingsUpdate,
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Update email notification settings."""

    # Update email address if provided
    if settings.email_address is not None:
        await set_user_preference(session, actor.id, "email_address", settings.email_address)

    # Update notification preferences
    await set_user_preference(
        session, actor.id, "email_notifications", str(settings.email_notifications).lower()
    )
    await set_user_preference(
        session, actor.id, "email_state_changes", str(settings.email_state_changes).lower()
    )
    await set_user_preference(
        session, actor.id, "email_comments", str(settings.email_comments).lower()
    )

    await session.commit()

    return {
        "success": True,
        "message": "Email settings updated successfully",
        "settings": {
            "email_address": settings.email_address,
            "email_notifications": settings.email_notifications,
            "email_state_changes": settings.email_state_changes,
            "email_comments": settings.email_comments,
        }
    }