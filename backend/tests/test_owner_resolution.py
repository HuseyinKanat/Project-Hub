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


def test_resolve_unnamespaced_agent_without_slug_is_unresolved() -> None:
    """An agent with no @suffix AND no column value has no owner (→ 422 upstream)."""
    actor = Actor(kind="agent", display_name="Backend Bot", owner_slug=None)
    assert resolve_owner_slug(actor) is None


def test_resolve_unnamespaced_agent_falls_back_to_column() -> None:
    """PH-330: no @suffix to derive from → the owner_slug column is authoritative.

    Supersedes the pre-PH-330 rule that an agent's column was ALWAYS ignored. The
    hub-host fleet (``jarwis-pm``) was minted before the ``@<owner>`` convention, so
    the column is the only place its owner can live; without this, every ticket it
    ever opened is unattributable.
    """
    actor = Actor(kind="agent", display_name="jarwis-pm", owner_slug="huseyin")
    assert resolve_owner_slug(actor) == "huseyin"


def test_resolve_empty_suffix_is_unresolved() -> None:
    """A trailing '@' with an empty owner resolves to None, not ''."""
    actor = Actor(kind="agent", display_name="jarwis-x@", owner_slug=None)
    assert resolve_owner_slug(actor) is None


def test_resolve_agent_suffix_wins_over_column() -> None:
    """The @suffix takes precedence — the fleet shares the human's owner."""
    actor = Actor(kind="agent", display_name="jarwis-qa@team", owner_slug="other")
    assert resolve_owner_slug(actor) == "team"


# ---------------------------------------------------------------------------
# F1 (PH-322 revision) — kind-first precedence: a human's '@'-bearing display_name
# (an email) must NEVER override the authoritative owner_slug column.
# ---------------------------------------------------------------------------


def test_resolve_human_with_at_in_display_name_uses_column() -> None:
    """A human whose display_name is an email resolves to owner_slug, NOT 'gmail.com'.

    Pre-fix the '@' parse ran before kind==human and silently overrode the column.
    """
    actor = Actor(
        kind="human", display_name="devicelabai@gmail.com", owner_slug="realowner"
    )
    assert resolve_owner_slug(actor) == "realowner"  # column wins, not 'gmail.com'


def test_two_email_humans_do_not_collide_on_domain() -> None:
    """Two humans sharing an email DOMAIN keep DISTINCT owners (no 409-guard bypass).

    Pre-fix both 'alice@corp.com' and 'bob@corp.com' resolved to 'corp.com',
    collapsing onto one owner and sharing/overwriting each other's paths.
    """
    alice = Actor(kind="human", display_name="alice@corp.com", owner_slug="alice")
    bob = Actor(kind="human", display_name="bob@corp.com", owner_slug="bob")
    assert resolve_owner_slug(alice) == "alice"
    assert resolve_owner_slug(bob) == "bob"
    assert resolve_owner_slug(alice) != resolve_owner_slug(bob)  # no domain collision


def test_at_suffix_only_parsed_for_agents_not_humans() -> None:
    """The '@'-suffix parse is AGENT-only — kind, not the '@', selects the source.

    Same '@corp' string: the agent path is UNCHANGED (resolves the suffix); the
    human resolves its column. Pins that the kind-first reorder left agents intact.
    """
    agent = Actor(kind="agent", display_name="jarwis-backend@corp", owner_slug=None)
    human = Actor(kind="human", display_name="carol@corp", owner_slug="carol")
    assert resolve_owner_slug(agent) == "corp"  # agent path unchanged
    assert resolve_owner_slug(human) == "carol"  # human uses column, not '@corp'


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
