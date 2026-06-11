"""Unit tests for git commit message parser."""


from app.git.parser import expected_branch_name, parse_commit


def _commit(message: str) -> object:
    return parse_commit(sha="abc123", message=message, author="Dev", url="https://example.com")


def test_conventional_with_ticket_scope():
    pc = _commit("feat(PH-17): add webhook handler")
    assert pc.is_conventional is True
    assert pc.commit_type == "feat"
    assert pc.ticket_in_scope == "PH-17"
    assert "PH-17" in pc.ticket_keys


def test_conventional_without_ticket_scope():
    pc = _commit("docs: update readme")
    assert pc.is_conventional is True
    assert pc.ticket_in_scope is None
    assert pc.ticket_keys == []


def test_non_conventional():
    pc = _commit("quick fix for tests")
    assert pc.is_conventional is False
    assert pc.commit_type is None


def test_ticket_keys_from_message_body():
    pc = _commit("fix(PH-10): resolve PH-11 regression")
    assert "PH-10" in pc.ticket_keys
    assert "PH-11" in pc.ticket_keys


def test_breaking_change():
    pc = _commit("feat(PH-5)!: redesign schema")
    assert pc.is_conventional is True
    assert pc.commit_type == "feat"
    assert pc.ticket_in_scope == "PH-5"


def test_multiple_types():
    for t in ("fix", "refactor", "test", "docs", "chore", "perf", "ci", "build", "revert"):
        pc = _commit(f"{t}(PH-1): some message")
        assert pc.is_conventional is True
        assert pc.commit_type == t


def test_invalid_type_not_conventional():
    pc = _commit("update(PH-1): something")
    assert pc.is_conventional is False


def test_expected_branch_name_basic():
    assert expected_branch_name("PH-17", "Add webhook handler") == "ph-17-add-webhook-handler"


def test_expected_branch_name_slug_truncation():
    title = "A" * 100
    result = expected_branch_name("PH-1", title)
    assert result.startswith("ph-1-")
    assert len(result) <= 60


def test_expected_branch_name_special_chars():
    result = expected_branch_name("PH-5", "Fix: auth/login bug!")
    assert result == "ph-5-fix-auth-login-bug"
