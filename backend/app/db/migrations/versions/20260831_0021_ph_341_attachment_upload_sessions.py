"""PH-341: chunked-upload session store (one additive table).

The round's ONLY migration. Strictly ADDITIVE — a single new
``attachment_upload_sessions`` table, no ALTER on any existing table, so
``upgrade`` is a plain ``CREATE TABLE`` (+ two indexes) and ``downgrade`` is a
plain ``drop_table`` (indexes drop with the table; NO ``batch_alter_table`` —
that is only for DROP COLUMN on the SQLite test DB). Follows the ``board_summaries``
(PH-338) additive-table template.

Backs the ``add_attachment_begin`` / ``add_attachment_chunk`` /
``add_attachment_commit`` protocol (remote MCP-only agents streaming evidence
larger than the 8 MiB inline cap up to the 25 MiB disk cap without raw REST).

Schema: ``attachment_upload_sessions(id, ticket_id, author_id, filename,
content_type, kind, run_id, phase, staging_key, bytes_received, next_seq,
declared_size, expires_at, created_at, updated_at)``.
  - ``ticket_id`` FKs ``tickets.id`` (indexed) — the evidence target.
  - ``author_id`` FKs ``actors.id`` — the ONLY actor allowed to chunk/commit this
    session (ownership guard; a different actor → 404, no existence leak).
  - ``staging_key`` is the on-disk staging blob path (``.uploads/{id[:2]}/{id}``
    under the attachments volume; disjoint from the 2-hex final shards).
  - ``bytes_received``/``next_seq`` are the cumulative-size + ordering accounting.
  - ``expires_at`` (indexed) = created_at + ``attachment_upload_ttl_seconds``; the
    GC cron (``upload_session_gc_cron``) sweeps rows whose ``expires_at`` has passed.

Revision ID: ph341uploadsessions
Revises: ph338boardsummary  (the single verified Alembic head — PH-340 added no
                             migration, so no multi-head at implement time)
"""

import sqlalchemy as sa
from alembic import op

revision = "ph341uploadsessions"
down_revision = "ph338boardsummary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachment_upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=True),
        sa.Column("phase", sa.String(length=40), nullable=True),
        sa.Column("staging_key", sa.String(length=255), nullable=False),
        sa.Column("bytes_received", sa.BigInteger(), nullable=False),
        sa.Column("next_seq", sa.Integer(), nullable=False),
        sa.Column("declared_size", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["actors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_attachment_upload_sessions_ticket_id",
        "attachment_upload_sessions",
        ["ticket_id"],
    )
    op.create_index(
        "ix_attachment_upload_sessions_expires_at",
        "attachment_upload_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("attachment_upload_sessions")
