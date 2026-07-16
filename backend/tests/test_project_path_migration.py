"""PH-322: migration up/down round-trip + ORM ↔ migration schema parity.

Driven on a THROWAWAY in-memory sqlite (never the live DB): the migration's
upgrade()/downgrade() are run via Alembic's MigrationContext/Operations, and the
ORM ``Base.metadata.create_all`` schema is asserted to carry the SAME named unique
index/constraint the migration emits (1:1 parity — the reason they are declared in
``__table_args__``).
"""

from __future__ import annotations

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.db.base import Base

_MIGRATION_FILE = (
    "app/db/migrations/versions/20260716_0017_ph_322_user_profile_project_paths.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("ph322_mig", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revises_verified_head() -> None:
    mig = _load_migration()
    assert mig.revision == "ph322userprofile"
    assert mig.down_revision == "ph320tokenlookup"


def test_migration_up_down_round_trips() -> None:
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
        # Pre-322 schema slice the migration touches.
        conn.execute(sa.text("CREATE TABLE actors (id TEXT PRIMARY KEY, display_name TEXT)"))
        conn.execute(sa.text("CREATE TABLE boards (id TEXT PRIMARY KEY, key TEXT)"))
        conn.commit()

        ctx = MigrationContext.configure(conn)
        assert type(ctx.impl).__name__ == "SQLiteImpl"
        with Operations.context(ctx):
            mig.upgrade()
        conn.commit()

        insp = sa.inspect(conn)
        assert "owner_slug" in {c["name"] for c in insp.get_columns("actors")}
        assert "project_paths" in insp.get_table_names()
        pp_cols = {c["name"] for c in insp.get_columns("project_paths")}
        assert {"id", "owner_slug", "board_id", "local_path", "created_at", "updated_at"} <= pp_cols

        # UNIQUE (owner_slug, board_id) enforced (upsert key).
        conn.execute(sa.text("INSERT INTO boards VALUES ('b1','PH')"))
        conn.execute(
            sa.text(
                "INSERT INTO project_paths (id, owner_slug, board_id, local_path, "
                "created_at, updated_at) VALUES "
                "('p1','alice','b1','/x',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        conn.commit()
        try:
            conn.execute(
                sa.text(
                    "INSERT INTO project_paths (id, owner_slug, board_id, local_path, "
                    "created_at, updated_at) VALUES "
                    "('p2','alice','b1','/y',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            raise AssertionError("duplicate (owner_slug, board_id) must violate UNIQUE")
        except IntegrityError:
            conn.rollback()

        # actors.owner_slug UNIQUE for two humans, but NULLs coexist (distinct).
        conn.execute(sa.text("INSERT INTO actors VALUES ('a1','Alice','alice')"))
        conn.commit()
        try:
            conn.execute(sa.text("INSERT INTO actors VALUES ('a2','Al2','alice')"))
            raise AssertionError("duplicate owner_slug must violate UNIQUE")
        except IntegrityError:
            conn.rollback()
        ins_null = "INSERT INTO actors (id, display_name, owner_slug) VALUES (:i,:n,NULL)"
        conn.execute(sa.text(ins_null), {"i": "n1", "n": "N1"})
        conn.execute(sa.text(ins_null), {"i": "n2", "n": "N2"})
        conn.commit()  # two NULLs coexist — no violation

        # Downgrade removes both the table and the column.
        with Operations.context(ctx):
            mig.downgrade()
        conn.commit()
        insp2 = sa.inspect(conn)
        assert "project_paths" not in insp2.get_table_names()
        assert "owner_slug" not in {c["name"] for c in insp2.get_columns("actors")}


def test_create_all_matches_migration_named_indexes() -> None:
    """The ORM test schema (Base.metadata.create_all) carries the SAME named unique
    index/constraint the migration emits — the __table_args__ 1:1 parity guarantee."""
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    insp = sa.inspect(eng)

    assert "project_paths" in insp.get_table_names()
    actor_idx = {i["name"] for i in insp.get_indexes("actors")}
    assert "uq_actors_owner_slug" in actor_idx
    pp_uniques = {u["name"] for u in insp.get_unique_constraints("project_paths")}
    assert "uq_project_path_owner_board" in pp_uniques
