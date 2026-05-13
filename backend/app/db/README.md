# db/

- `models/` — SQLAlchemy 2.0 ORM models.
- `migrations/` — Alembic migrations. **All schema changes must have a migration.**
- `session.py` — async engine + session factory.
- `base.py` — declarative base.

See `skills.md` § 10 for migration patterns.
