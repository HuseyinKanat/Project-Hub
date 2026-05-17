"""Tests for CLI commands — update_board_roles."""

import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.defaults import DEFAULT_WEB_ROLES


def _make_board(roles: object) -> MagicMock:
    board = MagicMock()
    board.roles = roles
    return board


def _make_session(boards: list[MagicMock]) -> AsyncMock:
    """Build an async context-manager session mock that returns *boards*."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = boards

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = boards

    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.commit = AsyncMock()

    # Support `async with SessionLocal() as session`
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_update_board_roles_dirty_becomes_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A board with non-default roles is updated; stdout reports 1 updated."""
    from app.cli import update_board_roles

    dirty_board = _make_board({"foo": "bar"})
    session_cm = _make_session([dirty_board])

    with patch("app.cli.SessionLocal", return_value=session_cm), patch(
        "app.cli.flag_modified"
    ) as mock_flag:
        await update_board_roles()

    # roles should have been replaced with a deep copy of DEFAULT_WEB_ROLES
    assert dirty_board.roles == DEFAULT_WEB_ROLES
    mock_flag.assert_called_once_with(dirty_board, "roles")

    captured = capsys.readouterr()
    assert "Updated 1 board(s), 0 unchanged." in captured.out


@pytest.mark.asyncio
async def test_update_board_roles_idempotent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A board already matching DEFAULT_WEB_ROLES is counted as unchanged."""
    from app.cli import update_board_roles

    clean_board = _make_board(copy.deepcopy(DEFAULT_WEB_ROLES))
    session_cm = _make_session([clean_board])

    with patch("app.cli.SessionLocal", return_value=session_cm), patch(
        "app.cli.flag_modified"
    ) as mock_flag:
        # First call
        await update_board_roles()
        out1 = capsys.readouterr().out
        assert "Updated 0 board(s), 1 unchanged." in out1

    # Second call — same board still clean
    session_cm2 = _make_session([clean_board])
    with patch("app.cli.SessionLocal", return_value=session_cm2), patch(
        "app.cli.flag_modified"
    ) as mock_flag2:
        await update_board_roles()
        out2 = capsys.readouterr().out
        assert "Updated 0 board(s), 1 unchanged." in out2

    mock_flag.assert_not_called()
    mock_flag2.assert_not_called()


@pytest.mark.asyncio
async def test_update_board_roles_no_boards(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No boards in DB → Updated 0 board(s), 0 unchanged."""
    from app.cli import update_board_roles

    session_cm = _make_session([])

    with patch("app.cli.SessionLocal", return_value=session_cm):
        await update_board_roles()

    captured = capsys.readouterr()
    assert "Updated 0 board(s), 0 unchanged." in captured.out
