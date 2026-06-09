"""PH-246 — per-repo SonarQube project keys + multi-repo scan plan (foundation).

ADDITIVE-ONLY, backfilled, SQLite-safe migration. Moves SonarQube from per-BOARD to
per-REPO without renaming any existing Sonar project or losing any cached health, so
KIM/PH/GXA-primary keep their EXACT current dashboard (kims-production-constraint:
snapshot the live KIM SQLite DB before applying — this migration runs clean on it).

What it does (every column NULLABLE → lock-light online, safe on a live DB):
  1. ``repositories.sonarqube_project_key`` (String(400), nullable) — the per-repo key,
     the new scan source of truth.
  2. ``sonar_scan_jobs.repo_id`` (FK repositories, nullable, CASCADE) + ``repo_slug``.
  3. ``sonarqube_metrics.repo_id`` (FK repositories, nullable, CASCADE).
  4. BACKFILL (before the relaxed unique constraint — R3):
       * each board's PRIMARY repo gets ``sonarqube_project_key`` = the board's legacy
         ``boards.sonarqube_project_key`` (so the primary keeps the exact same Sonar
         project — no rename, no orphaned dashboard); non-primary repos stay NULL.
       * every existing ``sonarqube_metrics`` row gets ``repo_id`` = its board's primary
         repo id (so KIM/PH health is unchanged and ``(board_id, repo_id)`` is unique).
       * every existing ``sonar_scan_jobs`` row gets ``repo_id`` / ``repo_slug`` = its
         board's primary repo (history audit; queued legacy jobs become repo-targeted).
  5. RELAX the metric unique constraint ``uq_sonarqube_metric_board (board_id)`` →
     ``uq_sonarqube_metric_board_repo (board_id, repo_id)`` inside a
     ``batch_alter_table`` so SQLite (test DB / KIM snapshot) does the required table
     rebuild; Postgres (project-hub prod) applies it as plain DDL.

``Board.sonarqube_project_key`` is KEPT (legacy fallback + PH self-scan identity) — NOT
dropped (a later cleanup ticket). The autogen JSON-variant ``alter_column`` noise on
git_commits/sonarqube_metrics and the spurious ``ix_repositories_board`` drop are
pre-existing model-vs-DB representation artifacts (same as PH-239) — deliberately OMITTED
so this migration is STRICTLY additive (dropping that index would be destructive).

Downgrade reverses (drop the relaxed constraint → restore ``uq_sonarqube_metric_board``
→ drop the FKs/columns). Backfilled per-repo keys are lost on downgrade (expected — the
legacy ``boards.sonarqube_project_key`` they were copied FROM is untouched, so a
re-upgrade re-derives them).

Revision ID: b1468dc15870
Revises: e3a479aa5c01
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1468dc15870"
down_revision = "e3a479aa5c01"
branch_labels = None
depends_on = None


# A board's PRIMARY repo id (the partial-unique ``is_primary=true`` repo), used by all
# three backfills. Dialect-portable boolean literal: SQLite stores bools as 0/1 and
# Postgres accepts ``true`` — ``is_primary IN (true, 1)`` is wrong on PG (1 ≠ true), so
# we compare against the canonical truthy value per dialect via ``= true`` / ``= 1``.
def _primary_repo_subquery(bind_dialect: str) -> str:
    truthy = "1" if bind_dialect == "sqlite" else "true"
    return (
        "SELECT id FROM repositories r "
        "WHERE r.board_id = {board_col} AND r.is_primary = " + truthy + " LIMIT 1"
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1) Additive nullable columns (no NOT NULL → no lock-heavy rewrite/backfill gate).
    op.add_column(
        "repositories",
        sa.Column("sonarqube_project_key", sa.String(length=400), nullable=True),
    )
    op.add_column(
        "sonar_scan_jobs", sa.Column("repo_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "sonar_scan_jobs", sa.Column("repo_slug", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "sonarqube_metrics", sa.Column("repo_id", sa.Uuid(), nullable=True)
    )

    # 2) BACKFILL — done BEFORE relaxing the unique constraint so (board_id, repo_id) is
    # already unique when the constraint is created (R3). Each primary-repo subquery
    # resolves the board's single ``is_primary`` repo.
    primary_for_metric = _primary_repo_subquery(dialect).format(
        board_col="sonarqube_metrics.board_id"
    )
    primary_for_job = _primary_repo_subquery(dialect).format(
        board_col="sonar_scan_jobs.board_id"
    )

    # 2a) Primary repo inherits the board's legacy key verbatim (no rename). Only set
    # where the board HAS a key and the primary repo's key is still NULL (idempotent).
    op.execute(
        sa.text(
            "UPDATE repositories SET sonarqube_project_key = ("
            "  SELECT b.sonarqube_project_key FROM boards b WHERE b.id = repositories.board_id"
            ") "
            "WHERE repositories.is_primary = "
            + ("1" if dialect == "sqlite" else "true")
            + " AND repositories.sonarqube_project_key IS NULL "
            "AND EXISTS ("
            "  SELECT 1 FROM boards b WHERE b.id = repositories.board_id "
            "  AND b.sonarqube_project_key IS NOT NULL"
            ")"
        )
    )

    # 2b) Existing metric rows → the board's primary repo (so KIM/PH health is unchanged
    # AND (board_id, repo_id) is unique for the relaxed constraint below).
    op.execute(
        sa.text(
            "UPDATE sonarqube_metrics SET repo_id = ("
            + primary_for_metric
            + ") WHERE repo_id IS NULL"
        )
    )

    # 2c) Existing scan-job rows → the board's primary repo (history audit).
    op.execute(
        sa.text(
            "UPDATE sonar_scan_jobs SET repo_id = ("
            + primary_for_job
            + ") WHERE repo_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE sonar_scan_jobs SET repo_slug = ("
            "  SELECT r.slug FROM repositories r WHERE r.id = sonar_scan_jobs.repo_id"
            ") WHERE repo_slug IS NULL AND repo_id IS NOT NULL"
        )
    )

    # 3) Foreign keys for the new repo_id columns. SQLite has NO ``ALTER TABLE ... ADD
    # CONSTRAINT`` — both the FKs AND the metric unique-constraint relax MUST go through
    # ``batch_alter_table`` (copy-and-move table rebuild) so the live KIM SQLite snapshot
    # migrates. On Postgres (project-hub prod) batch mode emits plain in-place DDL.

    # 3a) sonar_scan_jobs.repo_id FK (batch — SQLite ALTER-ADD-CONSTRAINT support).
    with op.batch_alter_table("sonar_scan_jobs") as batch:
        batch.create_foreign_key(
            "fk_sonar_scan_jobs_repo_id",
            "repositories",
            ["repo_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # 3b) sonarqube_metrics: relax the unique constraint board → (board, repo) AND add the
    # repo_id FK in ONE batch rebuild (cheaper than two SQLite table copies).
    with op.batch_alter_table("sonarqube_metrics") as batch:
        batch.drop_constraint("uq_sonarqube_metric_board", type_="unique")
        batch.create_unique_constraint(
            "uq_sonarqube_metric_board_repo", ["board_id", "repo_id"]
        )
        batch.create_foreign_key(
            "fk_sonarqube_metrics_repo_id",
            "repositories",
            ["repo_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    # Reverse order, all through batch (SQLite has no ALTER DROP CONSTRAINT either). The
    # legacy single-board unique constraint can only be restored if every board has ≤1
    # metric row (true right after the upgrade's primary-repo backfill); multi-repo metric
    # rows created post-upgrade would violate it — downgrade then fails loudly (expected,
    # not silent data loss).
    with op.batch_alter_table("sonarqube_metrics") as batch:
        batch.drop_constraint("fk_sonarqube_metrics_repo_id", type_="foreignkey")
        batch.drop_constraint("uq_sonarqube_metric_board_repo", type_="unique")
        batch.create_unique_constraint("uq_sonarqube_metric_board", ["board_id"])
        batch.drop_column("repo_id")

    with op.batch_alter_table("sonar_scan_jobs") as batch:
        batch.drop_constraint("fk_sonar_scan_jobs_repo_id", type_="foreignkey")
        batch.drop_column("repo_slug")
        batch.drop_column("repo_id")

    op.drop_column("repositories", "sonarqube_project_key")
