"""PH-221 — Repository 1:1 → 1:N per board (slug / name / is_primary)

Backward-compatible migration. The Repository model goes from "1 board : 0..1
repo" (gated by ``uq_repository_board``) to "1 board : 0..N repos" with exactly
one primary per board.

upgrade() order (data-safe):
  1. add ``slug`` / ``name`` as NULLABLE first (so the backfill can populate them).
  2. add ``is_primary`` with a ``false`` server_default (every existing row gets it).
  3. backfill: every pre-existing row is the board's sole repo today (old
     ``uq_repository_board`` guaranteed ≤1 repo/board), so it becomes the primary
     with a slug + name derived from its ``local_path`` basename. No rows are
     deleted, no FK changes → all ``git_commits`` / ``git_branches`` cache rows
     survive untouched (MEMORY: Kims migrate-don't-drop pattern; the live PH
     board's repo keeps its cache + last_synced_sha).
  4. drop the old 1:1 gate ``uq_repository_board``.
  5. tighten ``slug`` / ``name`` to NOT NULL (now fully backfilled).
  6. add ``uq_repository_board_slug`` (per-board slug identity).
  7. add partial unique index ``uq_repository_one_primary`` (≤1 primary/board;
     Postgres-only predicate — SQLite ignores it, service invariant covers it).
  8. drop the ``is_primary`` server_default so the app owns the flag going forward.

downgrade() reverses the change. It ASSUMES each board has ≤1 repo (the state at
upgrade time): recreating ``uq_repository_board`` (board_id unique) FAILS LOUDLY
if any board has >1 repo. A real downgrade from a genuinely multi-repo DB needs a
manual repo-selection step first — documented limitation, not a code path
(forward migration is the production path). Mirrors the 0006 "config lost on
downgrade" asymmetry note.

Revision ID: 20260608_0009
Revises: 20260606_0008
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260608_0009"
down_revision = "20260606_0008"
branch_labels = None
depends_on = None


# Dialect-portable basename extraction for the slug/name backfill.
# Postgres uses split_part + array_length over string_to_array; SQLite (test DB)
# has no split_part, so we use a recursive-free trick: strip everything up to and
# including the last '/'. Both reduce '/repos/foo/bar' → 'bar'.
_BACKFILL_PG = sa.text(
    """
    UPDATE repositories
    SET
        slug = regexp_replace(
            lower(
                split_part(
                    local_path, '/',
                    array_length(string_to_array(local_path, '/'), 1)
                )
            ),
            '[^a-z0-9_-]+', '-', 'g'
        ),
        name = split_part(
            local_path, '/',
            array_length(string_to_array(local_path, '/'), 1)
        ),
        is_primary = true
    WHERE slug IS NULL
    """
)

# SQLite fallback: last path segment via reverse/instr is awkward; use a simpler
# expression that trims the trailing slash then takes the substring after the
# final '/'. SQLite lacks regexp by default, so the slug == name here (basenames
# from /repos/ paths are already filename-safe in practice). Tests exercise the
# service-layer slug derivation, not this SQL, so an exact match to the PG
# normalisation is not required for the cache to be valid.
_BACKFILL_SQLITE = sa.text(
    """
    UPDATE repositories
    SET
        name = replace(
            substr(local_path, length(rtrim(local_path, replace(local_path, '/', ''))) + 1),
            '/', ''
        ),
        slug = lower(
            replace(
                substr(local_path, length(rtrim(local_path, replace(local_path, '/', ''))) + 1),
                '/', ''
            )
        ),
        is_primary = 1
    WHERE slug IS NULL
    """
)


def upgrade() -> None:
    # 1-2. Add new columns; slug/name nullable so the backfill can populate them.
    op.add_column("repositories", sa.Column("slug", sa.String(120), nullable=True))
    op.add_column("repositories", sa.Column("name", sa.String(160), nullable=True))
    op.add_column(
        "repositories",
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # 3. Backfill: each existing row → primary, slug/name from local_path basename.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(_BACKFILL_PG)
    else:
        bind.execute(_BACKFILL_SQLITE)

    # 4. Drop the old 1:1 gate.
    op.drop_constraint("uq_repository_board", "repositories", type_="unique")

    # 5. Tighten backfilled columns to NOT NULL.
    op.alter_column("repositories", "slug", existing_type=sa.String(120), nullable=False)
    op.alter_column("repositories", "name", existing_type=sa.String(160), nullable=False)

    # 6. Per-board slug identity.
    op.create_unique_constraint(
        "uq_repository_board_slug", "repositories", ["board_id", "slug"]
    )

    # 7. Partial unique index: ≤1 primary per board (Postgres-only predicate).
    op.create_index(
        "uq_repository_one_primary",
        "repositories",
        ["board_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )

    # 8. Drop the is_primary server_default — the app owns the flag now.
    op.alter_column(
        "repositories",
        "is_primary",
        existing_type=sa.Boolean(),
        server_default=None,
    )


def downgrade() -> None:
    # Reverse order. ASSUMES ≤1 repo/board — recreating uq_repository_board will
    # fail loudly if a board has >1 repo (documented limitation; manual repo
    # selection required to downgrade a genuinely multi-repo DB).
    op.drop_index("uq_repository_one_primary", table_name="repositories")
    op.drop_constraint("uq_repository_board_slug", "repositories", type_="unique")
    op.create_unique_constraint(
        "uq_repository_board", "repositories", ["board_id"]
    )
    op.drop_column("repositories", "is_primary")
    op.drop_column("repositories", "name")
    op.drop_column("repositories", "slug")
