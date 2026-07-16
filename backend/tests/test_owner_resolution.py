"""PH-322: owner-slug resolution (two sources) + human profile owner write.

resolve_owner_slug is the single contract every project-path consumer funnels
through; these tests pin both resolution sources + the None (unresolved) cases, and
the human-only / regex / uniqueness guards on set_owner_slug.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import Conflict, PermissionDenied, ProfileFieldInvalid
from app.db.models import Actor
from app.services.owners import resolve_owner_slug, set_owner_slug

# ---------------------------------------------------------------------------
# resolve_owner_slug — two sources + unresolved (pure, no DB)
# ---------------------------------------------------------------------------


def test_resolve_agent_from_display_name_suffix() -> None:
    """An agent's owner is parsed from its jarwis-<role>@<owner> display_name."""
    actor = Actor(kind="agent", display_name="jarwis-backend@alice", owner_slug=None)
    assert resolve_owner_slug(actor) == "alice"


def test_resolve_human_from_owner_slug_column() -> None:
    """A human's owner is read from the authoritative owner_slug column."""
    actor = Actor(kind="human", display_name="Alice", owner_slug="alice")
    assert resolve_owner_slug(actor) == "alice"


def test_resolve_human_null_column_is_unresolved() -> None:
    """A human who has not set owner_slug resolves to None (→ 422 upstream)."""
    actor = Actor(kind="human", display_name="Bob", owner_slug=None)
    assert resolve_owner_slug(actor) is None


def test_resolve_unnamespaced_agent_is_unresolved() -> None:
    """An agent with no @suffix has no owner (→ 422 upstream), NOT the column."""
    actor = Actor(kind="agent", display_name="Backend Bot", owner_slug="ignored")
    assert resolve_owner_slug(actor) is None


def test_resolve_empty_suffix_is_unresolved() -> None:
    """A trailing '@' with an empty owner resolves to None, not ''."""
    actor = Actor(kind="agent", display_name="jarwis-x@", owner_slug=None)
    assert resolve_owner_slug(actor) is None


def test_resolve_agent_suffix_wins_over_column() -> None:
    """The @suffix takes precedence — the fleet shares the human's owner."""
    actor = Actor(kind="agent", display_name="jarwis-qa@team", owner_slug="other")
    assert resolve_owner_slug(actor) == "team"


# ---------------------------------------------------------------------------
# set_owner_slug — human-only, regex, uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_owner_slug_human_ok(db_session, seed) -> None:
    updated = await set_owner_slug(db_session, seed.admin, "alice")
    assert updated.owner_slug == "alice"
    assert resolve_owner_slug(updated) == "alice"


@pytest.mark.asyncio
async def test_set_owner_slug_agent_forbidden(db_session, seed) -> None:
    """owner_slug is human-only — an agent token → 403 (it derives owner from @suffix)."""
    with pytest.raises(PermissionDenied):
        await set_owner_slug(db_session, seed.backend, "alice")
    # The guard rejects BEFORE any DB write, so nothing to roll back; owner stays unset.
    assert seed.backend.owner_slug is None


@pytest.mark.asyncio
async def test_set_owner_slug_bad_regex_422(db_session, seed) -> None:
    with pytest.raises(ProfileFieldInvalid) as exc:
        await set_owner_slug(db_session, seed.admin, "-leading-hyphen")
    assert exc.value.field == "owner_slug"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_set_owner_slug_duplicate_409(db_session, seed) -> None:
    """A slug already held by another human → 409 Conflict."""
    await set_owner_slug(db_session, seed.admin, "alice")
    with pytest.raises(Conflict):
        await set_owner_slug(db_session, seed.pm, "alice")
