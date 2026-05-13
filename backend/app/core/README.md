# core/

Cross-cutting concerns:

- `config.py` — pydantic-settings configuration loaded from `.env`.
- `auth.py` — bearer token + session auth helpers.
- `permissions.py` — permission grammar parser + `require_permission()` helper.
- `exceptions.py` — `ProjectHubError` base + typed exceptions.

See `skills.md` § 14 for exception patterns.
