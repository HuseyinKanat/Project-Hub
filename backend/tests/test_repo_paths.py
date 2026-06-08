"""Unit tests for services.repo_paths — HOST↔container translation (PH-228).

Pure-function coverage (no DB, no mount):
- to_container_path: HOST → /repos for PH (Documents root) and GXA (non-Documents
  root), the two cases the AC names explicitly, plus the rest of the 6 boards.
- to_host_path: the inverse, round-trip.
- Safety: non-absolute, '..' traversal, and outside-HOST_HOME all raise
  RepoPathError (no silent wrong path).
- Override args (host_home / repos_root) are honored without touching settings.
"""

from __future__ import annotations

import pytest

from app.services.repo_paths import RepoPathError, to_container_path, to_host_path

HOME = "/Users/huseyinkanat"
ROOT = "/repos"

# The PH-228 BACKFILL TABLE host paths → expected container paths.
_CASES: list[tuple[str, str]] = [
    ("/Users/huseyinkanat/Documents/project-hub", "/repos/Documents/project-hub"),
    ("/Users/huseyinkanat/Documents/kims", "/repos/Documents/kims"),
    ("/Users/huseyinkanat/Documents/gamexios", "/repos/Documents/gamexios"),
    ("/Users/huseyinkanat/jarwis-bench", "/repos/jarwis-bench"),
    ("/Users/huseyinkanat/AndroidStudioProjects/GameX", "/repos/AndroidStudioProjects/GameX"),
    ("/Users/huseyinkanat/UnityProjects/fruit-ninja2", "/repos/UnityProjects/fruit-ninja2"),
]


@pytest.mark.parametrize(("host", "container"), _CASES)
def test_to_container_path_all_boards(host: str, container: str) -> None:
    assert to_container_path(host, host_home=HOME, repos_root=ROOT) == container


def test_to_container_path_ph_documents_root() -> None:
    # AC-named case: PH lives under Documents/.
    assert (
        to_container_path(
            "/Users/huseyinkanat/Documents/project-hub", host_home=HOME, repos_root=ROOT
        )
        == "/repos/Documents/project-hub"
    )


def test_to_container_path_gxa_non_documents_root() -> None:
    # AC-named case: GXA lives under a NON-Documents root (AndroidStudioProjects/).
    assert (
        to_container_path(
            "/Users/huseyinkanat/AndroidStudioProjects/GameX", host_home=HOME, repos_root=ROOT
        )
        == "/repos/AndroidStudioProjects/GameX"
    )


def test_home_itself_maps_to_root() -> None:
    # The HOST_HOME path itself translates to the bare repos_root.
    assert to_container_path(HOME, host_home=HOME, repos_root=ROOT) == ROOT


def test_trailing_slash_on_home_and_root_normalized() -> None:
    assert (
        to_container_path(
            "/Users/huseyinkanat/Documents/kims",
            host_home="/Users/huseyinkanat/",
            repos_root="/repos/",
        )
        == "/repos/Documents/kims"
    )


# --- inverse / round-trip ---------------------------------------------------


@pytest.mark.parametrize(("host", "container"), _CASES)
def test_to_host_path_inverse(host: str, container: str) -> None:
    assert to_host_path(container, host_home=HOME, repos_root=ROOT) == host


@pytest.mark.parametrize(("host", "_container"), _CASES)
def test_round_trip(host: str, _container: str) -> None:
    container = to_container_path(host, host_home=HOME, repos_root=ROOT)
    assert to_host_path(container, host_home=HOME, repos_root=ROOT) == host


# --- safety / validation ----------------------------------------------------


def test_relative_path_raises() -> None:
    with pytest.raises(RepoPathError):
        to_container_path("Documents/kims", host_home=HOME, repos_root=ROOT)


def test_traversal_dotdot_raises() -> None:
    with pytest.raises(RepoPathError):
        to_container_path(
            "/Users/huseyinkanat/../etc/passwd", host_home=HOME, repos_root=ROOT
        )


def test_outside_home_raises() -> None:
    with pytest.raises(RepoPathError):
        to_container_path("/var/data/secret", host_home=HOME, repos_root=ROOT)


def test_to_host_path_outside_root_raises() -> None:
    with pytest.raises(RepoPathError):
        to_host_path("/var/data/secret", host_home=HOME, repos_root=ROOT)


def test_to_host_path_traversal_raises() -> None:
    with pytest.raises(RepoPathError):
        to_host_path("/repos/../root", host_home=HOME, repos_root=ROOT)


def test_repo_path_error_is_value_error() -> None:
    # PH-230 PATCH handler relies on this for a clean 422 mapping.
    assert issubclass(RepoPathError, ValueError)
