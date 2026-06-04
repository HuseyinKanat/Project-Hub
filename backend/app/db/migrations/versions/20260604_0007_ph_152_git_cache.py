"""PH-152 G3 — git cache tables (git_commits, git_branches, git_commit_files, git_commit_tickets)

Additive migration: creates 4 new tables for the local-first git readback cache
used by G3 sync + G4-G5 read endpoints.  No existing tables are modified.

Downgrade drops all 4 tables in reverse dependency order — any cached data is
lost on downgrade (expected; run sync again after upgrading).

Revision ID: 20260604_0007
Revises: 20260604_0006
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_0007"
down_revision = "20260604_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. git_commits — one row per (repo, sha); parent of files + ticket junction
    op.create_table(
        "git_commits",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "repo_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("short_sha", sa.String(12), nullable=False),
        sa.Column("parents", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("author_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("author_email", sa.String(200), nullable=False, server_default=""),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committer_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("committer_email", sa.String(200), nullable=False, server_default=""),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_conventional", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("commit_type", sa.String(20), nullable=True),
        sa.Column("ticket_keys", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("repo_id", "sha", name="uq_git_commit_repo_sha"),
    )
    op.create_index(
        "ix_git_commits_repo_committed_at", "git_commits", ["repo_id", "committed_at"]
    )
    op.create_index("ix_git_commits_repo_sha", "git_commits", ["repo_id", "sha"])

    # 2. git_branches — one row per (repo, branch name); refreshed on every sync
    op.create_table(
        "git_branches",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "repo_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_key", sa.String(24), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("repo_id", "name", name="uq_git_branch_repo_name"),
    )
    op.create_index("ix_git_branches_repo_id", "git_branches", ["repo_id"])

    # 3. git_commit_files — per-file diff entry for a cached commit (immutable)
    op.create_table(
        "git_commit_files",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "commit_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("git_commits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("old_path", sa.String(1000), nullable=True),
        sa.Column("change_type", sa.String(1), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_binary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_git_commit_files_commit_id", "git_commit_files", ["commit_id"])

    # 4. git_commit_tickets — junction table; unique constraint is the dedupe gate
    op.create_table(
        "git_commit_tickets",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "commit_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("git_commits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ticket_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("commit_id", "ticket_id", name="uq_git_commit_ticket"),
    )
    op.create_index(
        "ix_git_commit_tickets_ticket_id", "git_commit_tickets", ["ticket_id"]
    )


def downgrade() -> None:
    # Drop in reverse dependency order (children before parents)
    op.drop_index("ix_git_commit_tickets_ticket_id", table_name="git_commit_tickets")
    op.drop_table("git_commit_tickets")

    op.drop_index("ix_git_commit_files_commit_id", table_name="git_commit_files")
    op.drop_table("git_commit_files")

    op.drop_index("ix_git_branches_repo_id", table_name="git_branches")
    op.drop_table("git_branches")

    op.drop_index("ix_git_commits_repo_sha", table_name="git_commits")
    op.drop_index("ix_git_commits_repo_committed_at", table_name="git_commits")
    op.drop_table("git_commits")
