"""Git commit message parser and ticket key extractor."""

from __future__ import annotations

import re
from dataclasses import dataclass

TICKET_KEY_RE = re.compile(r"\b([A-Z]{2,5}-\d+)\b")

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|perf|ci|build|revert)"
    r"(?:\([^)]+\))?!?:\s+.+",
    re.IGNORECASE,
)

CONVENTIONAL_WITH_TICKET_RE = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|perf|ci|build|revert)"
    r"\(([A-Z]{2,5}-\d+)\)!?:\s+.+",
    re.IGNORECASE,
)


@dataclass
class ParsedCommit:
    sha: str
    message: str
    author: str
    url: str
    ticket_keys: list[str]
    is_conventional: bool
    commit_type: str | None
    ticket_in_scope: str | None


def parse_commit(sha: str, message: str, author: str, url: str) -> ParsedCommit:
    first_line = message.splitlines()[0].strip()

    ticket_keys = list(dict.fromkeys(TICKET_KEY_RE.findall(first_line)))
    is_conventional = bool(CONVENTIONAL_COMMIT_RE.match(first_line))

    commit_type: str | None = None
    ticket_in_scope: str | None = None
    m = CONVENTIONAL_WITH_TICKET_RE.match(first_line)
    if m:
        commit_type = m.group(1).lower()
        ticket_in_scope = m.group(2).upper()
    elif is_conventional:
        tm = re.match(r"^(\w+)", first_line)
        if tm:
            commit_type = tm.group(1).lower()

    return ParsedCommit(
        sha=sha,
        message=first_line,
        author=author,
        url=url,
        ticket_keys=ticket_keys,
        is_conventional=is_conventional,
        commit_type=commit_type,
        ticket_in_scope=ticket_in_scope,
    )


def expected_branch_name(ticket_key: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
    return f"{ticket_key.lower()}-{slug}"
