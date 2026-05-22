"""PH-97 clone shared default workflow per board — data migration

For each workflow that is actively shared by more than one board, all boards
except the first (ordered by board_workflows.created_at) receive a private
deep-copy clone.  The original workflow row is kept intact (template for new
boards).  Both Board.workflow_id (legacy FK) and BoardWorkflow.workflow_id
(authoritative junction) are updated atomically inside a single CTE chain.

Idempotency guarantee:
  Running upgrade() a second time after it has already run is a no-op:
  the "shared" CTE filters on HAVING count > 1, which will return zero rows
  once each board already has its own workflow.

Downgrade: intentional no-op (pass). Reverting to shared UUIDs is destructive
(would discard any per-board edits made after the initial migration).

Revision ID: 20260522_0005
Revises: 0ff08d26f630
Create Date: 2026-05-22
"""

import sqlalchemy as sa
from alembic import op

revision = "20260522_0005"
down_revision = "0ff08d26f630"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL so we stay in a single transaction and avoid ORM overhead.
    #
    # Strategy:
    #   1. Find workflows used by >1 active board ("shared").
    #   2. For each such workflow, rank the boards by bw.created_at so the
    #      earliest board keeps the original; later boards get clones.
    #   3. INSERT new Workflow rows for rn > 1 boards.
    #   4. UPDATE BoardWorkflow and Board.workflow_id for those boards.
    #
    # Postgres-specific: gen_random_uuid(), JSONB cast, CTE with INSERT.
    op.execute(sa.text("""
        WITH shared_workflows AS (
            -- Workflows that are currently active on more than one board
            SELECT bw.workflow_id
            FROM board_workflows bw
            WHERE bw.is_active = true
            GROUP BY bw.workflow_id
            HAVING count(DISTINCT bw.board_id) > 1
        ),
        ranked_boards AS (
            -- Rank boards sharing each workflow by junction row creation time.
            -- rn = 1 keeps the original; rn > 1 gets a clone.
            SELECT
                bw.id           AS bw_id,
                bw.board_id,
                bw.workflow_id  AS original_wf_id,
                b.key           AS board_key,
                row_number() OVER (
                    PARTITION BY bw.workflow_id
                    ORDER BY bw.created_at ASC, bw.id ASC
                )               AS rn
            FROM board_workflows bw
            JOIN shared_workflows sw ON sw.workflow_id = bw.workflow_id
            JOIN boards b ON b.id = bw.board_id
            WHERE bw.is_active = true
        ),
        boards_to_clone AS (
            -- Only boards ranked rn > 1 need a clone
            SELECT
                rb.bw_id,
                rb.board_id,
                rb.original_wf_id,
                rb.board_key,
                gen_random_uuid() AS new_wf_id
            FROM ranked_boards rb
            WHERE rb.rn > 1
        ),
        insert_clones AS (
            -- Create new Workflow rows (deep-copy states + transitions)
            INSERT INTO workflows
                (id, name, states, transitions, is_default, created_at, updated_at)
            SELECT
                btc.new_wf_id,
                btc.board_key || ' Workflow',
                w.states,
                w.transitions,
                false,
                now(),
                now()
            FROM boards_to_clone btc
            JOIN workflows w ON w.id = btc.original_wf_id
            RETURNING id AS inserted_id
        ),
        update_junction AS (
            -- Point BoardWorkflow rows to the new workflow UUID
            UPDATE board_workflows bw
            SET workflow_id = btc.new_wf_id,
                updated_at  = now()
            FROM boards_to_clone btc
            WHERE bw.id = btc.bw_id
            RETURNING bw.board_id, btc.new_wf_id
        )
        -- Align Board.workflow_id (legacy FK) with junction
        UPDATE boards b
        SET workflow_id = uj.new_wf_id,
            updated_at  = now()
        FROM update_junction uj
        WHERE b.id = uj.board_id
    """))


def downgrade() -> None:
    # Intentional no-op.
    # Reverting to a shared workflow is destructive (would discard per-board
    # edits) and is not safe to automate.  Manual SQL required if ever needed.
    pass
