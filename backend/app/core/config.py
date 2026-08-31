"""Application configuration."""

from functools import lru_cache

from pydantic import AliasChoices, Field
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

    # PH-228 — host_home is the HOST machine's $HOME that the docker bind mount
    # (`${HOME}:/repos:ro`) maps onto `repos_root` (/repos). Boards store HOST paths
    # (Board.repos_path); services.repo_paths translates HOST↔container by swapping
    # this prefix for repos_root. Default is the verified live dev value; override
    # per-machine via the HOST_HOME env var so it equals whatever $HOME resolved to
    # at mount time. (Do NOT derive from the container's own $HOME — that is /root,
    # not the host home the mount source resolves to.)
    host_home: str = "/Users/huseyinkanat"

    # Git sync (G3) — max commits walked on first sync when last_synced_sha is None.
    # Protects against OOM on very large repos; G6 manual refresh may revisit.
    git_backfill_limit: int = 2000

    # Git diff (G5) — byte cap on per-request unified-diff response.  Default 1 MiB.
    # Set higher for power users; lower for tight environments.
    git_diff_max_bytes: int = 1_048_576

    # --- Ticket evidence attachments (PH-296) ---
    # attachments_root: named-volume mount where attachment blobs are stored under
    # a UUID shard key ({id[:2]}/{id}). Distinct from the read-only /repos mount.
    attachments_root: str = "/data/attachments"
    # attachment_max_bytes: hard per-file cap enforced while streaming (default 25 MiB).
    # Oversize uploads are rejected mid-stream (413) and the partial file removed.
    attachment_max_bytes: int = 26_214_400
    # attachment_mcp_max_bytes: cap for the INLINE-base64 MCP path (add_attachment_content,
    # PH-318) — DISTINCT from and smaller than the 25 MiB disk cap above. A remote agent ships
    # bytes inline over JSON-RPC and request.json() buffers the WHOLE body into RAM before the
    # dispatcher can reject it (no reverse proxy / no uvicorn body limit), so peak transient
    # memory is several multiples of the payload. 8 MiB bounds that worst case (~37 MiB/request)
    # while comfortably covering real remote evidence (PNG/JPEG/text/JSON/markdown, short clips);
    # large mp4 device recordings stay on REST multipart (spooled UploadFile, no full-RAM buffer).
    # PayloadTooLarge.limit echoes whichever cap was hit. Tune per-machine via env.
    attachment_mcp_max_bytes: int = 8_388_608  # 8 MiB
    # attachment_allowed_types: comma-separated content-type allowlist. A stored
    # attachment's normalized (parameter-stripped, lower-cased) content type must
    # be a member or the write is rejected (415). Override per-machine via env.
    attachment_allowed_types: str = "image/png,image/jpeg,video/mp4,text/plain,application/json,text/markdown"
    # attachment_upload_ttl_seconds: lifetime of a CHUNKED upload session (PH-341,
    # add_attachment_begin/chunk/commit). A session (row + staging blob) that is
    # never committed becomes eligible for GC once created_at + this TTL is in the
    # past; the upload_session_gc_cron sweeps it (row + staging blob), and a
    # chunk/commit against an already-expired session is a 404 that also aborts it.
    # 1 h comfortably covers a slow multi-chunk mp4 upload while bounding abandoned
    # staging to (active sessions x 25 MiB) until the next sweep. Tune per-machine.
    attachment_upload_ttl_seconds: int = 3600

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

    # --- SonarQube integration (PH-191 epic; consumers land in PH-193) ---
    # Master kill switch — False → poller/client no-op, stack boots without sonar.
    sonarqube_enabled: bool = False
    # Base URL of the self-hosted SonarQube Community Build (compose service name).
    sonarqube_url: str = "http://sonarqube:9000"
    # HOST-facing base for browser deep links (PH-203). sonarqube_url is the
    # compose-internal service name (http://sonarqube:9000) and is NOT browser-usable;
    # this is the host-reachable base the frontend (PH-204) appends issue params to.
    # Honors the existing .env var SONAR_SCAN_URL (and the preferred SONARQUBE_SCAN_URL).
    sonarqube_scan_url: str = Field(
        default="http://localhost:9000",
        validation_alias=AliasChoices("SONARQUBE_SCAN_URL", "SONAR_SCAN_URL"),
    )
    # User token generated via the one-time bootstrap (see runbook). Secret — keep in .env.
    sonarqube_token: str = ""
    # Board-health poll cadence (seconds); consumed by the poll cron in PH-193.
    sonarqube_polling_interval_seconds: int = 300
    # Maps a project-hub board key (e.g. "PH") → SonarQube projectKey. JSON object string,
    # parsed by the consumer in PH-193 (declaration only here). Empty → no mapping yet.
    sonarqube_project_key_map: str = ""
    # PH-257 — opt-in flag for real C# (Unity) analysis via the HOST-side .NET pipeline
    # (Unity SyncSolution → SonarScanner.MSBuild begin → dotnet build → end). Default
    # False: a csharp board plans to the HONEST scan_status "needs_dotnet_setup" (no job
    # enqueued — fake-done is impossible). Set SONAR_DOTNET_ENABLED=true (in .env) AFTER
    # the host prerequisite is installed (dotnet SDK + dotnet-sonarscanner) to flip csharp
    # plans to "queued" so the host watcher runs the .NET scanner. The container backend
    # cannot SEE the host toolchain, so this flag is the operator's "prerequisite ready"
    # signal; the host runner still independently guards (honest skip) if a tool is missing.
    sonar_dotnet_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("SONAR_DOTNET_ENABLED", "SONARQUBE_DOTNET_ENABLED"),
    )

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

    @property
    def attachment_allowed_types_set(self) -> set[str]:
        """Normalized (lower-cased) content-type allowlist for attachments."""
        return {
            item.strip().lower()
            for item in self.attachment_allowed_types.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
