"""User preferences service for managing notification settings."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Actor, UserPreference


async def get_user_preference(
    session: AsyncSession,
    actor_id: uuid.UUID,
    preference_key: str,
    default_value: str = "",
) -> str:
    """Get a user preference value. Returns default_value if not found."""
    result = await session.execute(
        select(UserPreference.preference_value).where(
            UserPreference.actor_id == actor_id,
            UserPreference.preference_key == preference_key,
        )
    )
    value = result.scalar_one_or_none()
    return value if value is not None else default_value


async def set_user_preference(
    session: AsyncSession,
    actor_id: uuid.UUID,
    preference_key: str,
    preference_value: str,
) -> UserPreference:
    """Set a user preference. Creates or updates as needed."""
    # Try to find existing preference
    result = await session.execute(
        select(UserPreference).where(
            UserPreference.actor_id == actor_id,
            UserPreference.preference_key == preference_key,
        )
    )
    preference = result.scalar_one_or_none()

    if preference:
        # Update existing
        preference.preference_value = preference_value
    else:
        # Create new
        preference = UserPreference(
            actor_id=actor_id,
            preference_key=preference_key,
            preference_value=preference_value,
        )
        session.add(preference)

    await session.flush()
    return preference


async def get_user_preferences(
    session: AsyncSession,
    actor_id: uuid.UUID,
) -> dict[str, str]:
    """Get all preferences for a user as a dictionary."""
    result = await session.execute(
        select(UserPreference.preference_key, UserPreference.preference_value).where(
            UserPreference.actor_id == actor_id
        )
    )
    return {key: value for key, value in result}


async def get_email_notification_enabled(
    session: AsyncSession,
    actor_id: uuid.UUID,
) -> bool:
    """Check if email notifications are enabled for this user."""
    value = await get_user_preference(session, actor_id, "email_notifications", "true")
    return value.lower() in ("true", "1", "yes", "on")


async def get_user_email(
    session: AsyncSession,
    actor_id: uuid.UUID,
) -> str | None:
    """Get the email address for a user. Returns None if not set."""
    email = await get_user_preference(session, actor_id, "email_address", "")
    return email if email else None


async def get_notification_types_enabled(
    session: AsyncSession,
    actor_id: uuid.UUID,
) -> dict[str, bool]:
    """Get which notification types are enabled for this user."""
    # Default: all notification types enabled
    state_changes = await get_user_preference(session, actor_id, "email_state_changes", "true")
    comments = await get_user_preference(session, actor_id, "email_comments", "true")

    return {
        "state_changes": state_changes.lower() in ("true", "1", "yes", "on"),
        "comments": comments.lower() in ("true", "1", "yes", "on"),
    }


async def get_actors_with_email_notifications(
    session: AsyncSession,
    actor_ids: list[uuid.UUID],
    notification_type: str = "state_changes",  # or "comments"
) -> list[dict[str, Any]]:
    """
    Get list of actors with email notifications enabled for a specific type.

    Returns list of dicts: [{"actor_id": UUID, "email": str}, ...]
    """
    from typing import Any

    results = []

    for actor_id in actor_ids:
        # Check if email notifications are globally enabled
        email_enabled = await get_email_notification_enabled(session, actor_id)
        if not email_enabled:
            continue

        # Check if this specific notification type is enabled
        types_enabled = await get_notification_types_enabled(session, actor_id)
        if not types_enabled.get(notification_type, True):
            continue

        # Get the email address
        email = await get_user_email(session, actor_id)
        if not email:
            continue

        results.append({
            "actor_id": actor_id,
            "email": email,
        })

    return results