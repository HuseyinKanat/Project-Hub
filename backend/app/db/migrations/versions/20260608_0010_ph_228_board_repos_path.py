"""PH-228 — Board.repos_path (per-board HOST filesystem path) + backfill + PH relocation

Additive, data-safe migration that gives every board its own filesystem root.

upgrade():
  1. add nullable ``boards.repos_path`` String(500) (the HOST path — what the user
     types in Finder/CLI; the docker mount maps $HOME → /repos so the in-container
     path is derived on demand by services.repo_paths.to_container_path).
  2. backfill the 6 known boards with their verified HOST paths, keyed by ``key``
     and gated on ``repos_path IS NULL`` so the step is idempotent (upgrade →
     downgrade → upgrade round-trips cleanly, no drift). SMK is intentionally
     absent (deleted board). Plain parametrized UPDATEs — dialect-portable across
     Postgres (prod) and SQLite (test DB), no split_part/regexp needed.
  3. **Back-compat relocation (THE trap):** the broadened compose mount relocates
     project-hub from ``/repos/project-hub`` to ``/repos/Documents/project-hub``.
     Migrate the PH repo row in lock-step:
     ``UPDATE repositories SET local_path='/repos/Documents/project-hub'
       WHERE local_path='/repos/project-hub'`` (idempotent — only the exact legacy
     value). The new path STILL satisfies the ``/repos/`` allowlist (no validator
     change) and the git_commits/git_branches cache is repo_id-keyed (NOT
     path-keyed), so readback keeps working immediately — the 375-commit / 3-branch
     PH cache survives untouched (MEMORY: migrate-don't-drop).

downgrade() reverses BOTH the column drop AND the PH local_path relocation
(restores ``/repos/project-hub`` — the old-mount-compatible value). NOTE: the
docker-compose mount is NOT under alembic; a real downgrade also requires
reverting the compose mount line back to ``.../project-hub:/repos/project-hub:ro``
by hand.

Revision ID: 20260608_0010
Revises: 20260608_0009
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260608_0010"
down_revision = "20260608_0009"
branch_labels = None
depends_on = None

# Verified HOST paths for the 6 live boards (PH-228 BACKFILL TABLE). Keyed by the
# stable, human-readable board ``key`` (resilient even if a board was recreated
# with a new id). SMK is deliberately excluded (deleted board).
_BACKFILL: dict[str, str] = {
    "PH": "/Users/huseyinkanat/Documents/project-hub",
    "KIM": "/Users/huseyinkanat/Documents/kims",
    "GXI": "/Users/huseyinkanat/Documents/gamexios",
    "BENCH": "/Users/huseyinkanat/jarwis-bench",
    "GXA": "/Users/huseyinkanat/AndroidStudioProjects/GameX",
    "FN": "/Users/huseyinkanat/UnityProjects/fruit-ninja2",
}

# PH repo local_path relocation (old single-path mount → broadened $HOME mount).
_PH_LOCAL_PATH_OLD = "/repos/project-hub"
_PH_LOCAL_PATH_NEW = "/repos/Documents/project-hub"

# Parametrized, dialect-portable UPDATEs (work identically on Postgres + SQLite).
_BACKFILL_SQL = sa.text(
    "UPDATE boards SET repos_path = :path WHERE key = :key AND repos_path IS NULL"
)
_RELOCATE_PH_SQL = sa.text(
    "UPDATE repositories SET local_path = :new WHERE local_path = :old"
)


def upgrade() -> None:
    # 1. Additive nullable column (matches Repository.local_path's String(500)).
    op.add_column("boards", sa.Column("repos_path", sa.String(500), nullable=True))

    bind = op.get_bind()

    # 2. Idempotent backfill (WHERE repos_path IS NULL), keyed by board key.
    for key, path in _BACKFILL.items():
        bind.execute(_BACKFILL_SQL, {"key": key, "path": path})

    # 3. Relocate the PH repo row in lock-step with the broadened mount. Idempotent:
    #    only the exact legacy value is touched; re-runs no-op.
    bind.execute(
        _RELOCATE_PH_SQL,
        {"old": _PH_LOCAL_PATH_OLD, "new": _PH_LOCAL_PATH_NEW},
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Reverse the PH relocation so a rollback restores the old-mount-compatible
    # value (the compose mount must also be reverted by hand — see module docstring).
    bind.execute(
        _RELOCATE_PH_SQL,
        {"old": _PH_LOCAL_PATH_NEW, "new": _PH_LOCAL_PATH_OLD},
    )

    op.drop_column("boards", "repos_path")
