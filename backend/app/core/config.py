"""Application configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    postgres_user: str = "projecthub"
    postgres_password: str = "changeme"
    postgres_db: str = "projecthub"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_host: str = "redis"
    redis_port: int = 6379

    admin_username: str = "admin"
    admin_password: str = "change-me-on-first-login"
    admin_display_name: str = "Admin"

    cors_allowed_origins: str = "http://localhost:5173"
    secret_key: str = "dev-only"
    token_hash_rounds: int = 12

    # Git integration (G2) — allowlist root for read-only bind mounts
    repos_root: str = "/repos"

    # Git sync (G3) — max commits walked on first sync when last_synced_sha is None.
    # Protects against OOM on very large repos; G6 manual refresh may revisit.
    git_backfill_limit: int = 2000

    # Git diff (G5) — byte cap on per-request unified-diff response.  Default 1 MiB.
    # Set higher for power users; lower for tight environments.
    git_diff_max_bytes: int = 1_048_576

    # Git refresh + poller (G6)
    # git_poll_interval_seconds: background poller cadence; <=0 disables poller.
    git_poll_interval_seconds: int = 30
    # git_refresh_debounce_seconds: burst coalesce window (monotonic).
    git_refresh_debounce_seconds: float = 2.0
    # git_refresh_enabled: master kill switch — False → endpoint 503 + no poller.
    git_refresh_enabled: bool = True

    # SMTP Configuration for email notifications
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_use_ssl: bool = False
    email_from: str = "noreply@project-hub.local"
    email_enabled: bool = False

    @property
    def database_url(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
