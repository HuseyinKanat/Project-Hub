"""HOST↔container path translation for per-board repository roots (PH-228).

Each board stores its **HOST** filesystem path (``Board.repos_path``) — the
user-meaningful path matching Finder/CLI (e.g.
``/Users/huseyinkanat/Documents/kims``). The docker-compose backend mount
``${HOME}:/repos:ro`` maps the host ``$HOME`` onto the container ``/repos``, so
translating a stored host path into the path that ``git/reader`` can actually
``open_repo`` is a pure string substitution: swap the ``HOST_HOME`` prefix for
``repos_root`` (``/repos``).

This module is the single shared implementation of that mapping. The PH-229
consumers (detect + sonar init) import it and never reconstruct the mapping
themselves. It is dependency-light (config only) and pure / unit-testable.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.core.config import get_settings


class RepoPathError(ValueError):
    """Raised when a path is not absolute, contains ``..``, or is outside the root.

    PH-229 consumers treat this as "unresolvable" — they catch it and skip the
    board gracefully (no 500), mirroring detect's existing skip-and-continue
    contract. It is a ``ValueError`` subclass so a PATCH handler (PH-230) can map
    it straight to a 422.
    """


def to_container_path(
    host_path: str,
    *,
    host_home: str | None = None,
    repos_root: str | None = None,
) -> str:
    """Translate a board's stored HOST path to its in-container path under /repos.

    ``container = host_path`` with the ``HOST_HOME`` prefix swapped for
    ``repos_root``. Example (defaults)::

        /Users/huseyinkanat/Documents/kims        -> /repos/Documents/kims
        /Users/huseyinkanat/AndroidStudioProjects/GameX -> /repos/AndroidStudioProjects/GameX

    Args:
        host_path: Absolute HOST path stored on ``Board.repos_path``.
        host_home: Override for ``settings.host_home`` (the mounted host $HOME).
        repos_root: Override for ``settings.repos_root`` (the container mount root).

    Raises:
        RepoPathError: if ``host_path`` is not absolute, contains a ``..``
            traversal segment, or is not under ``host_home``.
    """
    settings = get_settings()
    home = (host_home if host_home is not None else settings.host_home).rstrip("/")
    root = (repos_root if repos_root is not None else settings.repos_root).rstrip("/")
    p = PurePosixPath(host_path)
    if not p.is_absolute() or ".." in p.parts:
        raise RepoPathError(f"{host_path!r} must be an absolute path without '..'")
    try:
        rel = p.relative_to(home)
    except ValueError as exc:
        raise RepoPathError(f"{host_path!r} is not under HOST_HOME {home!r}") from exc
    return str(PurePosixPath(root) / rel)


def to_host_path(
    container_path: str,
    *,
    host_home: str | None = None,
    repos_root: str | None = None,
) -> str:
    """Inverse of :func:`to_container_path` — container path back to HOST path.

    Provided for symmetry (PH-230 may only have the container path and want to
    show the host path). ``host = container_path`` with the ``repos_root`` prefix
    swapped for ``HOST_HOME``. Example (defaults)::

        /repos/Documents/kims -> /Users/huseyinkanat/Documents/kims

    Raises:
        RepoPathError: if ``container_path`` is not absolute, contains a ``..``
            traversal segment, or is not under ``repos_root``.
    """
    settings = get_settings()
    home = (host_home if host_home is not None else settings.host_home).rstrip("/")
    root = (repos_root if repos_root is not None else settings.repos_root).rstrip("/")
    p = PurePosixPath(container_path)
    if not p.is_absolute() or ".." in p.parts:
        raise RepoPathError(f"{container_path!r} must be an absolute path without '..'")
    try:
        rel = p.relative_to(root)
    except ValueError as exc:
        raise RepoPathError(f"{container_path!r} is not under repos_root {root!r}") from exc
    return str(PurePosixPath(home) / rel)
