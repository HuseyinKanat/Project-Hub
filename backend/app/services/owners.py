"""Owner-slug resolution + human profile owner write (PH-322).

The single source of truth for "who is the owner behind this token". Every
consumer (MCP project-path tools, REST profile + project-paths, members-list
enrichment) resolves an owner the SAME way here so the human ``owner_slug`` column
and the agent ``@<owner>`` display_name suffix never diverge.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Conflict, PermissionDenied, ProfileFieldInvalid
from app.core.logging import get_logger
from app.db.models import Actor

logger = get_logger(__name__)

# PH-322: twin of ``cli._OWNER_SLUG_RE`` (PH-317). DELIBERATELY duplicated rather
# than imported from ``app.cli`` — cli.py is ``.codemap``-mapped to
# git-integration.md, so importing it here would drag this module into that page's
# Reviewer sync gate. The contract (1-20 chars: lowercase letters, digits, hyphens;
# no leading hyphen) is stable (PH-317); DRY-into-a-shared-module is a follow-up.
OWNER_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")


def resolve_owner_slug(actor: Actor) -> str | None:
    """Resolve the owner slug behind ``actor``'s token, or ``None`` if unresolved.

    Three cases (matching the mint contract in ``cli._jarwis_actor_name``), keyed on
    ``kind`` FIRST so the source is never ambiguous:
      * a HUMAN → the authoritative ``actor.owner_slug`` column, ALWAYS. A human's
        ``display_name`` may legitimately be an email (``devicelabai@gmail.com``);
        that ``@`` is NOT an owner suffix and must never be parsed (F1/PH-322).
      * an AGENT namespaced ``jarwis-<role>@<owner>`` → the ``@`` suffix;
      * neither (an un-namespaced agent, or a human who has not set a slug) → None.

    Checking ``kind == "human"`` BEFORE the ``@`` parse is load-bearing: the old
    ``@``-first order let a human whose display_name contained ``@`` silently
    override the ``owner_slug`` column — two humans sharing an email domain
    (``a@corp.com``/``b@corp.com``) collided onto one ``corp.com`` owner, bypassing
    the ``owner_slug`` uniqueness (409) guard and sharing each other's paths.

    Pure in-memory: the ``@`` suffix is a string parse and ``owner_slug`` is already
    loaded on the actor — no DB round-trip. ``None`` is the signal callers translate
    into a 422 ``owner_unresolved`` (there is no identity to key a path under).
    """

    if actor.kind == "human":
        # Authoritative column only — never the display_name (may be an email).
        return actor.owner_slug
    display_name = actor.display_name or ""
    if "@" in display_name:
        # Agent namespaced form — owner is everything after the LAST '@' (owner
        # slugs contain no '@', so rsplit yields the owner even if the role part
        # somehow held one). Empty suffix ("jarwis-x@") → unresolved.
        return display_name.rsplit("@", 1)[1].strip() or None
    return None


async def set_owner_slug(session: AsyncSession, actor: Actor, owner_slug: str) -> Actor:
    """Set a HUMAN actor's ``owner_slug`` (the ``PUT /api/profile`` write).

    Guards, in order (each rejecting BEFORE any mutation, so a bad write leaves no
    side effect):
      * a non-human actor (an agent derives its owner from the display_name suffix,
        so ``owner_slug`` is human-only) → 403 ``PermissionDenied``;
      * a slug failing ``^[a-z0-9][a-z0-9-]{0,19}$`` → 422 ``ProfileFieldInvalid``;
      * a slug already held by ANOTHER actor → 409 ``Conflict`` (the DB UNIQUE index
        is the backstop — an IntegrityError on a concurrent claim also maps to 409).
    Commits and returns the refreshed actor on success.
    """

    if actor.kind != "human":
        # owner_slug is human-only — agents are namespaced via display_name @suffix.
        raise PermissionDenied(required="profile.owner_slug:human", have=[])

    if OWNER_SLUG_RE.fullmatch(owner_slug) is None:
        raise ProfileFieldInvalid(
            "invalid owner_slug; must match ^[a-z0-9][a-z0-9-]{0,19}$ "
            "(1-20 chars: lowercase letters, digits, hyphens; no leading hyphen)",
            field="owner_slug",
        )

    existing = (
        await session.execute(
            select(Actor).where(Actor.owner_slug == owner_slug, Actor.id != actor.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"owner_slug '{owner_slug}' is already taken")

    actor.owner_slug = owner_slug
    try:
        await session.commit()
    except IntegrityError as err:
        # Concurrent claim of the same slug raced past the read check → the UNIQUE
        # index rejected it. Surface the same 409 the explicit check would have.
        await session.rollback()
        raise Conflict(f"owner_slug '{owner_slug}' is already taken") from err
    # NO session.refresh(actor): the session is expire_on_commit=False so owner_slug
    # survives the commit, and a refresh would EXPIRE the eager ``memberships`` the
    # next require_board_member iterates (async lazy-load → MissingGreenlet).
    logger.info("profile_updated owner=%s actor_id=%s", owner_slug, actor.id)
    return actor
