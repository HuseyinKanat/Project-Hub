"""PH-272 concept tags — global concept-tag data layer (foundation).

STRICTLY ADDITIVE, SQLite-safe migration (three plain ``create_table`` calls,
no ``batch_alter_table``, no ALTER on any existing table). Foundation child of
epic PH-271; the API/permissions/serializers land in PH-273.

What it does (all brand-new tables → online-safe on a live DB):
  1. ``concept_tags`` — GLOBAL taxonomy entity (NO ``board_id``, mirrors
     Actor/Workflow). ``slug`` is globally unique (``uq_concept_tag_slug``).
  2. ``ticket_concept_tags`` — M2M junction tickets↔concept_tags (mirrors
     ``git_commit_tickets``). ``uq_ticket_concept_tag (ticket_id,
     concept_tag_id)`` dedupes double-attach; both FKs CASCADE.
  3. ``concept_tag_links`` — DIRECTED self-referential tag↔tag edge
     (``source_tag_id`` → ``target_tag_id`` + nullable ``relation`` label).
     ``uq_concept_tag_link_source_target`` dedupes a single direction; the
     reverse pair is a distinct row by design. Both FKs CASCADE.

The autogen JSON-variant ``alter_column`` noise on git_commits/sonarqube_metrics
and the spurious ``ix_repositories_board`` drop are pre-existing model-vs-DB
representation artifacts (same as PH-239/PH-246) — deliberately OMITTED so this
migration is STRICTLY additive (dropping that index would be destructive).

Downgrade drops the three tables in reverse-dependency order (children before
parent); all FKs CASCADE so order is mostly cosmetic.

Revision ID: 2722c9066c32
Revises: b1468dc15870
Create Date: 2026-06-24
"""

import sqlalchemy as sa
from alembic import op

revision = '2722c9066c32'
down_revision = 'b1468dc15870'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'concept_tags',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('slug', sa.String(length=120), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_concept_tag_slug'),
    )
    op.create_table(
        'ticket_concept_tags',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('ticket_id', sa.Uuid(), nullable=False),
        sa.Column('concept_tag_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['concept_tag_id'], ['concept_tags.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['ticket_id'], ['tickets.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'ticket_id', 'concept_tag_id', name='uq_ticket_concept_tag'
        ),
    )
    op.create_index(
        'ix_ticket_concept_tags_ticket_id',
        'ticket_concept_tags',
        ['ticket_id'],
        unique=False,
    )
    op.create_index(
        'ix_ticket_concept_tags_concept_tag_id',
        'ticket_concept_tags',
        ['concept_tag_id'],
        unique=False,
    )
    op.create_table(
        'concept_tag_links',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('source_tag_id', sa.Uuid(), nullable=False),
        sa.Column('target_tag_id', sa.Uuid(), nullable=False),
        sa.Column('relation', sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ['source_tag_id'], ['concept_tags.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['target_tag_id'], ['concept_tags.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'source_tag_id',
            'target_tag_id',
            name='uq_concept_tag_link_source_target',
        ),
    )
    op.create_index(
        'ix_concept_tag_links_source_tag_id',
        'concept_tag_links',
        ['source_tag_id'],
        unique=False,
    )
    op.create_index(
        'ix_concept_tag_links_target_tag_id',
        'concept_tag_links',
        ['target_tag_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_concept_tag_links_target_tag_id', table_name='concept_tag_links'
    )
    op.drop_index(
        'ix_concept_tag_links_source_tag_id', table_name='concept_tag_links'
    )
    op.drop_table('concept_tag_links')
    op.drop_index(
        'ix_ticket_concept_tags_concept_tag_id', table_name='ticket_concept_tags'
    )
    op.drop_index(
        'ix_ticket_concept_tags_ticket_id', table_name='ticket_concept_tags'
    )
    op.drop_table('ticket_concept_tags')
    op.drop_table('concept_tags')
