"""PH-193 — SonarQube native board-health ingestion.

Polls a self-hosted SonarQube Community Build for each board's main-branch quality
metrics and caches the latest snapshot in ``sonarqube_metrics`` (upsert-latest, one
row per board). On a successful poll it publishes a ``sonarqube_synced`` event on
the ``board:{id}`` Redis channel so the frontend (PH-196) can live-update.

Design contract (mirrors git_poll_cron / stale_claim_cron):
  - ``sonarqube_poll_cron`` is created by the FastAPI lifespan ONLY when
    ``settings.sonarqube_enabled and settings.sonarqube_polling_interval_seconds > 0``.
    The cron itself does NOT re-check the flag — lifespan gates task creation.
  - Error isolation is layered: the httpx client returns ``None`` on any error
    (SonarQube down / 401 / malformed JSON / project not yet scanned), and the
    per-tick ``except Exception`` keeps the loop alive across bad boards/ticks.
  - A board with no resolvable project key is skipped silently (debug log only) —
    no row, no event, no warning spam.

Single-process scope: the cron runs in one uvicorn worker (same caveat as
git_poll_cron). Acceptable for the current single-worker deploy.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import Conflict
from app.core.logging import get_logger
from app.db.models import Board, Repository, SonarQubeMetric, SonarScanJob
from app.db.session import SessionLocal
from app.events.bus import EventBus, EventEnvelope
from app.services.repo_paths import RepoPathError, to_container_path, to_host_path

if TYPE_CHECKING:
    # Import-only for the ``sync_repo_now`` return annotation — the runtime serializer
    # import is function-local to break the serializers → sonarqube module cycle.
    from app.schemas import RepoHealth

logger = get_logger(__name__)

# Metrics requested from /api/measures/component. Adding a key here also flows into
# raw_measures verbatim; the denormalized columns below are the hot fields the API reads.
MEASURE_KEYS = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc"

# httpx total timeout per call (connect+read). SonarQube down → fast fail → skip.
_TIMEOUT = httpx.Timeout(10.0)


@dataclass
class SonarSnapshot:
    """Parsed SonarQube board-health snapshot (what gets upserted + published)."""

    quality_gate_status: str | None
    bugs: int | None
    vulnerabilities: int | None
    code_smells: int | None
    coverage: Decimal | None
    duplicated_lines_density: Decimal | None
    ncloc: int | None
    raw_measures: dict[str, str] = field(default_factory=dict)


@dataclass
class SonarIssue:
    """One normalized SonarQube issue (component mapped to a relative file path)."""

    key: str
    rule: str
    severity: str | None
    type: str | None
    component: str  # relative file path (the "<projectKey>:" prefix stripped)
    line: int | None
    message: str
    hash: str | None


@dataclass
class SonarIssuesResult:
    """Result of an issue-search proxy call.

    ``status`` is one of "ok" (live fetch succeeded) or "unreachable" (SonarQube
    down / 401 / malformed JSON — degraded gracefully). The endpoint layer adds the
    "no_project_key" / "not_configured" statuses before ever calling this.
    """

    status: str
    total: int
    issues: list[SonarIssue]
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Type coercion helpers — a malformed measure degrades to None, never crashes.
# ---------------------------------------------------------------------------


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))  # SonarQube int metrics arrive as "12" / occasionally "12.0"
    except (TypeError, ValueError):
        return None


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None


# ---------------------------------------------------------------------------
# Project key resolution — Board.sonarqube_project_key first, then the map.
# ---------------------------------------------------------------------------


def resolve_project_key(board: Board) -> str | None:
    """Resolve a board → SonarQube projectKey, or None if the board has no mapping.

    Precedence: the explicit ``board.sonarqube_project_key`` column, then the
    ``settings.sonarqube_project_key_map`` JSON object keyed by board key. None →
    the board is skipped (not an error).
    """
    if board.sonarqube_project_key:
        return board.sonarqube_project_key

    raw_map = get_settings().sonarqube_project_key_map
    if not raw_map:
        return None
    try:
        mapping = json.loads(raw_map)
    except (ValueError, TypeError):
        logger.warning("sonarqube_project_key_map is not valid JSON; ignoring")
        return None
    if not isinstance(mapping, dict):
        return None
    key = mapping.get(board.key)
    return key if isinstance(key, str) and key else None


# ---------------------------------------------------------------------------
# httpx client — two SonarQube API calls, full error isolation.
# ---------------------------------------------------------------------------


async def fetch_project_status(client: httpx.AsyncClient, project_key: str) -> str | None:
    """GET /api/qualitygates/project_status → projectStatus.status (or None)."""
    resp = await client.get(
        "/api/qualitygates/project_status", params={"projectKey": project_key}
    )
    resp.raise_for_status()
    data = resp.json()
    status = data["projectStatus"]["status"]
    return status if isinstance(status, str) else None


async def fetch_measures(client: httpx.AsyncClient, project_key: str) -> dict[str, str]:
    """GET /api/measures/component → flat {metricKey: value} map."""
    resp = await client.get(
        "/api/measures/component",
        params={"component": project_key, "metricKeys": MEASURE_KEYS},
    )
    resp.raise_for_status()
    data = resp.json()
    measures = data["component"]["measures"]
    out: dict[str, str] = {}
    for m in measures:
        metric = m.get("metric")
        value = m.get("value")
        if isinstance(metric, str) and value is not None:
            out[metric] = str(value)
    return out


async def fetch_board_metrics(project_key: str) -> SonarSnapshot | None:
    """Fetch the full snapshot for a projectKey. Returns None on ANY error.

    Error isolation: SonarQube unreachable / 401 / malformed JSON / project not
    yet scanned all degrade to None (logged at warning) — never propagate, so the
    poll loop survives to the next board and the next tick.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            base_url=settings.sonarqube_url,
            # SonarQube user tokens authenticate as HTTP Basic with the token as
            # the username and an empty password (portable across Community builds).
            auth=httpx.BasicAuth(settings.sonarqube_token, ""),
            timeout=_TIMEOUT,
        ) as client:
            status = await fetch_project_status(client, project_key)
            measures = await fetch_measures(client, project_key)
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "sonarqube fetch failed project_key=%s err=%s", project_key, exc
        )
        return None

    return SonarSnapshot(
        quality_gate_status=status,
        bugs=_as_int(measures.get("bugs")),
        vulnerabilities=_as_int(measures.get("vulnerabilities")),
        code_smells=_as_int(measures.get("code_smells")),
        coverage=_as_decimal(measures.get("coverage")),
        duplicated_lines_density=_as_decimal(measures.get("duplicated_lines_density")),
        ncloc=_as_int(measures.get("ncloc")),
        raw_measures=measures,
    )


# ---------------------------------------------------------------------------
# Issue search (PH-203) — proxy /api/issues/search, normalize components.
# ---------------------------------------------------------------------------


def _strip_project_prefix(component: str, project_key: str) -> str:
    """SonarQube ``component`` is ``"<projectKey>:<relpath>"`` for file-level issues.

    Strip the leading ``"<project_key>:"`` to yield the relative file path. A
    project-level component (no ``":"``) is returned unchanged. Splitting on the
    first ``":"`` only keeps any (unlikely) colon inside the path intact.
    """
    prefix = f"{project_key}:"
    if component.startswith(prefix):
        return component[len(prefix):]
    # Fallback: a bare colon (component belongs to a different/odd key) — split once.
    if ":" in component:
        return component.split(":", 1)[1]
    return component


def _opt_str(value: object) -> str | None:
    """Narrow an arbitrary value to ``str | None`` (non-str → None)."""
    return value if isinstance(value, str) else None


def _parse_issue(raw: dict[str, object], project_key: str) -> SonarIssue:
    """Map one raw SonarQube issue dict → SonarIssue (defensive about absent keys)."""
    component = raw.get("component")
    component_str = component if isinstance(component, str) else ""
    line = raw.get("line")
    return SonarIssue(
        key=str(raw.get("key", "")),
        rule=str(raw.get("rule", "")),
        severity=_opt_str(raw.get("severity")),
        type=_opt_str(raw.get("type")),
        component=_strip_project_prefix(component_str, project_key),
        line=line if isinstance(line, int) else None,
        message=str(raw.get("message", "")),
        hash=_opt_str(raw.get("hash")),
    )


async def fetch_issues(
    project_key: str,
    *,
    types: str | None = None,
    severities: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> SonarIssuesResult:
    """Proxy ``GET /api/issues/search`` for a projectKey. Never raises.

    Mirrors fetch_board_metrics' client + error isolation: SonarQube unreachable /
    401 / malformed JSON degrade to a ``status="unreachable"`` empty result (logged
    at warning) — never propagate. ``types`` / ``severities`` are CSV filters that
    are OMITTED entirely when None (sending ``types=`` empty makes SonarQube treat it
    as a malformed filter).
    """
    settings = get_settings()
    params: dict[str, str | int] = {
        "componentKeys": project_key,
        "resolved": "false",
        "p": page,
        "ps": page_size,
    }
    if types:
        params["types"] = types
    if severities:
        params["severities"] = severities

    try:
        async with httpx.AsyncClient(
            base_url=settings.sonarqube_url,
            # Same auth model as fetch_board_metrics: token as HTTP Basic username,
            # empty password (portable across Community builds).
            auth=httpx.BasicAuth(settings.sonarqube_token, ""),
            timeout=_TIMEOUT,
        ) as client:
            resp = await client.get("/api/issues/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_issues = data["issues"]
            # `total` lives at the top level on older builds, under `paging` on newer.
            total = data.get("total")
            if total is None:
                paging = data.get("paging") or {}
                total = paging.get("total", 0)
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "sonarqube issue fetch failed project_key=%s err=%s", project_key, exc
        )
        return SonarIssuesResult(
            status="unreachable", total=0, issues=[], page=page, page_size=page_size
        )

    issues = [
        _parse_issue(raw, project_key)
        for raw in raw_issues
        if isinstance(raw, dict)
    ]
    return SonarIssuesResult(
        status="ok",
        total=int(total) if isinstance(total, (int, float)) else 0,
        issues=issues,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Persistence + event
# ---------------------------------------------------------------------------


async def _publish_synced(board: Board, metric: SonarQubeMetric) -> None:
    """Publish a board-level ``sonarqube_synced`` event on board:{id}.

    EventEnvelope is ticket-shaped; for a board-level event we use empty
    ticket_id/ticket_key sentinels (no WS client subscribes to the ticket channel
    for this event). Payload mirrors BoardHealth so PH-196 can patch its cached
    board.health from the WS message without a refetch.
    """
    now_iso = datetime.now(UTC).isoformat()
    envelope = EventEnvelope(
        event_id=str(uuid4()),
        type="sonarqube_synced",
        board_id=str(board.id),
        ticket_id="",
        ticket_key="",
        actor_id=None,
        payload={
            "quality_gate_status": metric.quality_gate_status,
            "bugs": metric.bugs,
            "vulnerabilities": metric.vulnerabilities,
            "code_smells": metric.code_smells,
            "coverage": float(metric.coverage) if metric.coverage is not None else None,
            "duplicated_lines_density": (
                float(metric.duplicated_lines_density)
                if metric.duplicated_lines_density is not None
                else None
            ),
            "ncloc": metric.ncloc,
            "fetched_at": metric.fetched_at.isoformat(),
        },
        occurred_at=now_iso,
    )
    await EventBus.publish(envelope)


async def _upsert_metric(
    session: AsyncSession,
    board: Board,
    project_key: str,
    snapshot: SonarSnapshot,
    repo_id: uuid.UUID | None = None,
) -> SonarQubeMetric:
    """Insert-or-update the SonarQubeMetric row for ``(board_id, repo_id)`` (PH-246).

    Was keyed on ``board_id`` alone (1:1); now upsert-latest per ``(board_id, repo_id)``
    so a multi-repo board carries one row per repo. ``repo_id=None`` targets the legacy
    board-level row (back-compat for the board-level ``poll_board`` fallback path).
    """
    metric = (
        await session.execute(
            select(SonarQubeMetric).where(
                SonarQubeMetric.board_id == board.id,
                SonarQubeMetric.repo_id == repo_id,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if metric is None:
        metric = SonarQubeMetric(board_id=board.id, repo_id=repo_id)
        session.add(metric)

    metric.project_key = project_key
    metric.quality_gate_status = snapshot.quality_gate_status
    metric.bugs = snapshot.bugs
    metric.vulnerabilities = snapshot.vulnerabilities
    metric.code_smells = snapshot.code_smells
    metric.coverage = snapshot.coverage
    metric.duplicated_lines_density = snapshot.duplicated_lines_density
    metric.ncloc = snapshot.ncloc
    metric.raw_measures = snapshot.raw_measures
    metric.fetched_at = now
    return metric


# ---------------------------------------------------------------------------
# Poll tick + cron
# ---------------------------------------------------------------------------


async def poll_repo(
    session: AsyncSession,
    board: Board,
    repo: Repository | None,
    project_key: str,
) -> bool:
    """Poll ONE repo's SonarQube project: fetch → upsert ``(board_id, repo_id)`` → publish.

    PH-246: the per-repo unit of polling. ``repo=None`` targets the legacy board-level
    metric row (the board-level fallback path). Returns True if a metric row was written
    + event published, False if the fetch returned None (project not yet scanned /
    unreachable). Never raises for the SonarQube error paths (the client isolates those).
    """
    snapshot = await fetch_board_metrics(project_key)
    if snapshot is None:
        # Client already logged the cause; skip — no row, no event.
        return False

    repo_id = repo.id if repo is not None else None
    metric = await _upsert_metric(session, board, project_key, snapshot, repo_id=repo_id)
    await session.commit()
    await _publish_synced(board, metric)
    logger.info(
        "sonarqube poll: synced board=%s repo=%s key=%s gate=%s bugs=%s ncloc=%s",
        board.key,
        repo.slug if repo is not None else None,
        project_key,
        snapshot.quality_gate_status,
        snapshot.bugs,
        snapshot.ncloc,
    )
    return True


async def poll_board(session: AsyncSession, board: Board) -> bool:
    """Poll a board across ALL its repos: per-repo resolve key → fetch → upsert → publish.

    PH-246: was per-board single poll; now loops ``build_scan_plans`` (one plan per repo,
    each carrying its derived ``project_key``) and calls ``poll_repo`` for every repo
    whose key resolves. A board with NO linked repos falls back to the legacy board-level
    poll (``resolve_project_key`` → board-level metric row) so repo-less boards stay
    byte-compatible. One bad repo never aborts the others (best-effort fan-out).

    Returns True if AT LEAST ONE repo's metric was written, False if every repo was
    skipped (no key / fetch None). Never raises for the SonarQube error paths.
    """
    # Repo-less board (or unloaded collection) → legacy board-level poll, byte-compatible.
    if "repositories" in inspect(board).unloaded or not board.repositories:
        project_key = resolve_project_key(board)
        if project_key is None:
            logger.debug(
                "sonarqube poll: board=%s has no project key, skipping", board.key
            )
            return False
        return await poll_repo(session, board, None, project_key)

    any_synced = False
    for repo in board.repositories:
        project_key = derive_repo_project_key(board, repo)
        if not project_key:
            continue
        if await poll_repo(session, board, repo, project_key):
            any_synced = True
    return any_synced


# ---------------------------------------------------------------------------
# Setup / sync / status (PH-223) — one-click board↔project linkage.
#
# All three are graceful-200: a disabled / unreachable SonarQube degrades to
# status FLAGS, never an exception out (mirrors the PH-203 issues proxy). NO
# secret (token / compose-internal sonarqube_url) ever appears in the returned
# data or any log line.
# ---------------------------------------------------------------------------


# PH-235: the honest setup-status discriminator. The frontend keys its messaging
# off this enum, NOT off the boolean trio (which conflated "no analysis" with
# "unreachable"). Exhaustive values:
#   disabled      — sonarqube_enabled=false (server kill switch)
#   unconfigured  — enabled but no resolvable project key
#   no_analysis   — configured, no cached metric, server NOT known to be down
#                   (the formerly-false "unreachable" case)
#   ok            — configured + a cached metric exists (or a live poll succeeded)
#   unreachable   — a REAL live attempt (sync path) actually failed
SONAR_STATUS_DISABLED = "disabled"
SONAR_STATUS_UNCONFIGURED = "unconfigured"
SONAR_STATUS_NO_ANALYSIS = "no_analysis"
SONAR_STATUS_OK = "ok"
SONAR_STATUS_UNREACHABLE = "unreachable"


@dataclass
class SonarSetupStatusData:
    """Plain assembly of a board's SonarQube setup state (secret-free).

    The API layer maps this verbatim onto the ``SonarSetupStatus`` schema. Kept a
    dataclass so the service stays Pydantic-free and reusable from the cron / tests.

    PH-235 — two new fields make the signal HONEST:
      ``status``        an explicit discriminator (see SONAR_STATUS_* above). The
                        load-bearing field; ``reachable``'s old "metric exists ⇒
                        reachable" overload is gone.
      ``has_analysis``  ``metric is not None`` — the truthful "a cached metric row
                        exists" signal that used to be smuggled inside ``reachable``.
    ``reachable`` is KEPT (backward compat) but now means ONLY "the last REAL live
    attempt succeeded"; on the pure-read path it is a best-effort optimistic mirror
    of ``has_analysis`` and is NEVER emitted as a false ``False`` for a configured
    but unscanned board.
    """

    status: str
    has_analysis: bool
    enabled: bool
    reachable: bool
    configured: bool
    project_key: str | None
    last_metric_fetched_at: datetime | None
    quality_gate_status: str | None
    dashboard_url: str | None
    message: str


def derive_default_project_key(board: Board) -> str:
    """Default SonarQube projectKey for a board when setup supplies none.

    Precedence (PH-229):
      1. **PH literal** — the PH board ALWAYS resolves to ``project-hub`` so the
         derived key matches ``sonar-project.properties`` (the post-merge
         ``sonar-scan.sh`` scanner WRITE) and the poller READ agree on one key.
         This branch is FIRST and is never basename-derived (gotcha: even though
         ``basename(/repos/Documents/project-hub)`` happens to equal
         ``project-hub``, we must not depend on that coincidence).
      2. **Path basename** — a NON-PH board WITH a resolvable ``repos_path`` derives
         the default from the path basename (the natural scanner project identity,
         e.g. ``/Users/.../kims`` → ``kims``). The HOST basename is used directly
         (translation is unnecessary for a string basename, and a non-mounted/typo
         path still yields a sensible string default).
      3. **Board key** — a board with no path, an empty/``..`` basename, or a
         ``RepoPathError`` falls back to ``board.key.lower()`` (graceful, no 500).
    """
    if board.key.upper() == "PH":
        return "project-hub"

    basename = _path_basename_key(board.repos_path)
    if basename:
        return basename
    return board.key.lower()


def derive_repo_project_key(board: Board, repo: Repository) -> str:
    """Per-repo SonarQube projectKey for a linked repository (PH-246).

    The SOURCE OF TRUTH for scanning a multi-repo board. Precedence:
      1. **Explicit override** — ``repo.sonarqube_project_key`` already set ⇒ use it
         verbatim (idempotent; a setup/scan that derived a key once is stable, and an
         operator override always wins).
      2. **Primary repo** — INHERIT the board's existing key
         (``board.sonarqube_project_key or derive_default_project_key(board)``). This
         guarantees KIM/PH/GXA-primary keep their EXACT current Sonar project — no
         rename, no orphaned dashboard. PH stays special-cased via
         ``derive_default_project_key`` (PH-literal → ``project-hub``).
      3. **Non-primary (sibling) repo** — ``<base>-<repo.slug>`` where ``<base>`` is the
         board's resolved primary key (step 2's value). ``repo.slug`` (not name) is the
         stable per-board identity (``uq_repository_board_slug``). Slugified + lowercased
         for Sonar-key safety, then joined to the base.

    Total / never-raises — a slug is always present on a persisted ``Repository`` row.
    """
    if repo.sonarqube_project_key:
        return repo.sonarqube_project_key

    base = board.sonarqube_project_key or derive_default_project_key(board)
    if repo.is_primary:
        return base
    return f"{base}-{_slugify_key_segment(repo.slug)}"


def _slugify_key_segment(value: str) -> str:
    """Lowercase + Sonar-key-safe a slug segment (``[a-zA-Z0-9_.\\-]`` only).

    SonarQube projectKeys allow letters/digits/``-``/``_``/``.``; everything else
    (whitespace, ``/``) collapses to ``-``. ``Repository.slug`` is already a URL-safe
    slug (``[a-z0-9_-]`` per repositories._slugify), so this is a cheap, deterministic
    belt-and-suspenders normalisation that never produces an empty segment.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_.\-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "repo"


def _path_basename_key(host_path: str | None) -> str | None:
    """Slugified basename of a board's HOST ``repos_path``, or None when unusable.

    Returns None for a null/empty path, a path whose translation raises
    ``RepoPathError`` (outside ``HOST_HOME`` / contains ``..``), or a path with no
    final component — so the caller falls back to the board-key default. Never
    raises (mirrors the never-500 contract).
    """
    if not host_path:
        return None
    try:
        # Validate the path is well-formed + under HOST_HOME (consistency with
        # detect); we only need the basename, but reusing the guard keeps a typo'd
        # or escaping path from yielding a misleading key.
        to_container_path(host_path)
    except RepoPathError:
        return None
    name = PurePosixPath(host_path).name
    return name or None


def _dashboard_url(project_key: str | None) -> str | None:
    """HOST-facing SonarQube dashboard deep link, or None when no key.

    Built from ``sonarqube_scan_url`` (browser-reachable), NEVER the
    compose-internal ``sonarqube_url`` and NEVER the token (PH-203 contract).
    """
    if not project_key:
        return None
    base = get_settings().sonarqube_scan_url.rstrip("/")
    return f"{base}/dashboard?id={project_key}"


async def setup_board_project(
    session: AsyncSession, board: Board, project_key: str | None = None
) -> str:
    """One-click setup: persist the board's SonarQube projectKey. Idempotent.

    Resolves the effective key (supplied ``project_key`` overrides, else the
    derived default) and writes it to ``board.sonarqube_project_key`` only when it
    actually changes — re-running with the same effective key is a clean no-op (no
    duplicate state, no unique violation, no history spam). Returns the key in use.

    Provisioning model = scan-time auto-create (architect decision): this does NOT
    call the SonarQube admin ``projects/create`` API. Persisting the key is enough —
    ``sonar-scanner`` (post-merge) auto-creates the Community project on first run,
    and the poller / issues proxy then resolve the same key.

    PH-229: the derived default is now path-aware (``derive_default_project_key``
    uses the board ``repos_path`` basename for non-PH boards, PH literal kept
    first). That helper is total — a null path / ``RepoPathError`` degrades to the
    ``board.key.lower()`` default, so this function still never raises for a bad
    path. The scanner working dir itself stays out of this module (post-merge
    ``sonar-scan.sh``); PH-229's sonar change is the DEFAULT-KEY derivation only.
    """
    key = project_key or derive_default_project_key(board)
    if board.sonarqube_project_key != key:
        board.sonarqube_project_key = key
        await session.commit()
    return key


async def setup_repo_project(
    session: AsyncSession,
    board: Board,
    repo: Repository,
    project_key: str | None = None,
) -> str:
    """Per-repo setup: persist ONE repo's SonarQube projectKey. Idempotent (PH-254).

    Mirrors ``setup_board_project`` exactly — persist-only, scan-time auto-create,
    never-500, secret-free — but targets ``Repository.sonarqube_project_key`` for a
    single (typically secondary) repo. Resolves the effective key (supplied
    ``project_key`` overrides; else ``derive_repo_project_key(board, repo)`` —
    primary inherits the board key, a sibling gets ``<primaryKey>-<slug>``), then
    writes it ONLY when it actually changes (a re-run with the same effective key is
    a clean no-op: no commit, no history spam — same idempotency contract as the
    board-level fn). Returns the key now in use.

    Decision (a) — persist-only, NO validation poll: like ``setup_board_project``
    this does NOT probe SonarQube. Provisioning is scan-time auto-create; a live
    poll on the write path would reintroduce the never-500/never-hang risk the whole
    module avoids, and the project legitimately does not exist until the first scan.
    Sync (PH-255) confirms the project later.

    Decision (b) — intra-board conflict ⇒ 409, cross-board allowed: a key already
    held by ANOTHER repo of the SAME board is rejected (``Conflict``). The
    ``(board_id, repo_id)`` metric model means two repos pointing at one Sonar
    project would yield two cards backed by one project_key. The check spans BOTH a
    sibling's STORED key AND its DERIVED key (a sibling with no explicit key yet
    still occupies its derived ``<base>-<slug>`` / inherited-primary slot), so an
    operator cannot pin a key that silently collides with a sibling's effective key.
    The board repo set is small (no N+1 concern). Cross-board reuse is NOT checked:
    boards are independent Sonar surfaces and there is no cheap authoritative
    cross-board check without a network call (violating decision a).

    Validation: an explicit whitespace-only key strips to empty ⇒ ``ValueError``
    (the route maps it to 422); an omitted key always derives a non-empty default
    (a persisted ``Repository`` always has a slug, so ``derive_repo_project_key`` is
    total). Persists even when ``sonarqube_enabled=false`` (config allowed offline).
    """
    if project_key is not None and not project_key.strip():
        raise ValueError("project_key must not be blank")
    key = (project_key or "").strip() or derive_repo_project_key(board, repo)

    # Intra-board conflict (decision b): another repo of THIS board whose EFFECTIVE
    # key (stored override OR derived default) equals `key`. Compare derived keys so
    # an unset sibling still defends its slot. Cross-board reuse is intentionally
    # not checked (boards are independent Sonar surfaces).
    for sibling in board.repositories:
        if sibling.id == repo.id:
            continue
        if derive_repo_project_key(board, sibling) == key:
            raise Conflict(
                f"project_key '{key}' already in use by repo '{sibling.slug}' "
                "on this board",
                conflicting_repo=sibling.slug,
            )

    if repo.sonarqube_project_key != key:
        repo.sonarqube_project_key = key
        await session.commit()
    return key


async def build_setup_status(
    session: AsyncSession, board: Board, *, reachable: bool | None = None
) -> SonarSetupStatusData:
    """Assemble a board's ``SonarSetupStatus`` — pure, NO network call.

    Cheap + member-readable: reads ``settings.sonarqube_enabled`` and the cached
    ``SonarQubeMetric`` row only. ``reachable`` is NOT probed here (a read endpoint
    must never block on a down server).

    PH-235 — HONEST status classification. The old code derived ``reachable`` from
    metric presence on the read path, so a configured-but-never-scanned board (no
    metric) rendered as a FALSE "unreachable". The fix separates the two concepts:

      * ``has_analysis = metric is not None`` — the truthful "we have data" signal.
      * ``status`` (the discriminator the UI keys off):
          - not enabled                         → ``disabled``
          - not configured                      → ``unconfigured``
          - ``reachable is False`` (REAL failed
            live sync only)                     → ``unreachable``
          - read path (``reachable is None``) or
            successful sync (``reachable True``)→ ``ok`` if has_analysis else
                                                  ``no_analysis``

    Absence of a metric on the pure-read path becomes ``no_analysis`` — NEVER a
    false ``unreachable``. ``reachable=False`` is only ever reachable via the
    ``sync`` path passing a genuinely failed live attempt.
    """
    settings = get_settings()
    enabled = settings.sonarqube_enabled
    project_key = resolve_project_key(board)
    configured = project_key is not None

    # PH-246: a board may now carry N metric rows (one per repo). The setup-status view
    # is board-level back-compat → pick the freshest row (``fetched_at`` desc) as the
    # representative "has analysis" signal. ``.first()`` (not ``scalar_one_or_none``)
    # because >1 row is now legal.
    metric = (
        await session.execute(
            select(SonarQubeMetric)
            .where(SonarQubeMetric.board_id == board.id)
            .order_by(SonarQubeMetric.fetched_at.desc())
        )
    ).scalars().first()
    last_fetched = metric.fetched_at if metric is not None else None
    gate = metric.quality_gate_status if metric is not None else None
    has_analysis = metric is not None

    # --- status derivation (PH-235) --------------------------------------------
    if not enabled:
        status = SONAR_STATUS_DISABLED
    elif not configured:
        status = SONAR_STATUS_UNCONFIGURED
    elif reachable is False:
        # ONLY a real failed live sync lands here (read path passes None).
        status = SONAR_STATUS_UNREACHABLE
    else:
        # Read path (reachable is None) or a succeeded sync (reachable is True):
        # metric presence — NOT a probe — distinguishes ok from no_analysis.
        status = SONAR_STATUS_OK if has_analysis else SONAR_STATUS_NO_ANALYSIS

    # ``reachable`` wire value: verbatim when a live attempt was made; on the
    # read path, an optimistic mirror of has_analysis — but NEVER the source of a
    # false "unreachable" (the UI ignores it when status==no_analysis).
    reachable_flag = reachable if reachable is not None else has_analysis

    message = _setup_status_message(
        status=status,
        project_key=project_key,
        has_analysis=has_analysis,
    )

    return SonarSetupStatusData(
        status=status,
        has_analysis=has_analysis,
        enabled=enabled,
        reachable=reachable_flag,
        configured=configured,
        project_key=project_key,
        last_metric_fetched_at=last_fetched,
        quality_gate_status=gate,
        dashboard_url=_dashboard_url(project_key),
        message=message,
    )


def _setup_status_message(
    *,
    status: str,
    project_key: str | None,
    has_analysis: bool,
) -> str:
    """Short human-readable status line (secret-free), driven by the PH-235 enum.

    The HONEST wording: ``no_analysis`` says "no analysis yet (run a scan)" — it is
    NOT phrased as unreachable. ``unreachable`` is reserved for a genuine outage
    (a failed live sync); a real outage is never masked.
    """
    if status == SONAR_STATUS_DISABLED:
        return "SonarQube is disabled on this server"
    if status == SONAR_STATUS_UNCONFIGURED:
        return "no SonarQube project key configured"
    if status == SONAR_STATUS_NO_ANALYSIS:
        return f"linked to {project_key} — no analysis yet (run a scan)"
    if status == SONAR_STATUS_UNREACHABLE:
        if has_analysis:
            return f"linked to {project_key} — unreachable, showing cached"
        return f"linked to {project_key} — unreachable, no analysis yet"
    return f"linked to {project_key}"


async def sync_board_now(
    session: AsyncSession, board: Board
) -> SonarSetupStatusData:
    """On-demand re-poll: read SonarQube's *existing* analysis + refresh the cache.

    Re-poll, NOT re-scan: ``poll_board`` reads the latest analysis (fast, bounded by
    ``_TIMEOUT=10s``, already fully error-isolated) — it does NOT trigger a scanner
    run (those stay post-merge in ``sonar-scan.sh``). Never raises for the SonarQube
    error paths; on a disabled / unconfigured / unreachable board the fresh status
    simply reports ``reachable=false`` with a message. Returns the fresh status.
    """
    settings = get_settings()
    if not settings.sonarqube_enabled:
        # Kill switch: no live attempt, no probe — degrade to status flags.
        return await build_setup_status(session, board, reachable=False)

    polled = await poll_board(session, board)
    return await build_setup_status(session, board, reachable=polled)


async def sync_repo_now(
    session: AsyncSession, board: Board, repo: Repository
) -> RepoHealth:
    """On-demand re-poll of ONE repo's existing analysis → fresh ``RepoHealth`` (PH-255).

    The single-repo slice of ``sync_board_now``: re-poll (NOT re-scan) the repo's EXISTING
    SonarQube analysis and refresh its single ``(board_id, repo_id)`` ``SonarQubeMetric``
    row, then serialize the now-current state. Composes the SAME PH-246/252 helpers
    (``derive_repo_project_key`` → ``poll_repo`` → ``repo_health``/``unscanned_repo_health``)
    — NO new resolution logic.

    Decision B — a NEVER-scanned repo (Sonar has no analysis for the derived key) returns
    the HONEST ``unscanned_repo_health`` card (null gate / null 7 metrics / null
    ``fetched_at`` / derived non-null ``project_key`` / ``dashboard_url=None``), NOT a 404 /
    "nothing to sync". ``poll_repo`` returns False (no row written, no raise) for the
    fetch-None case (never-scanned / unreachable); we fall through to the unscanned card.
    When Sonar IS reachable AND has analysis, ``poll_repo`` upserts the row + publishes a
    per-repo ``sonarqube_synced`` event, and ``repo_health(metric)`` returns the LIVE values
    — sync is the bridge that flips a card out of "No analysis yet".

    Kill switch: ``sonarqube_enabled=false`` → NO live attempt (no probe, no hang), return
    the CURRENT state (cached metric row if present, else the unscanned card). Mirrors
    ``sync_board_now``'s disabled short-circuit. Never raises (never-500), never blocks
    (10s-bounded client). Secret-free — only ORM identity + derived key are surfaced.

    The serializer import is function-local to break the ``serializers → sonarqube``
    module cycle (serializers imports ``derive_repo_project_key``/``_dashboard_url`` from
    here at import time).
    """
    from app.services.serializers import repo_health, unscanned_repo_health

    async def _current_health() -> RepoHealth:
        # Re-query THIS repo's metric with ``repository`` eager-loaded so ``repo_health``
        # never lazy-loads ``metric.repository`` (MissingGreenlet in async). A fresh
        # upsert by ``poll_repo`` means the route's in-memory ``board.sonarqube_metrics``
        # snapshot is stale — this explicit select picks up the new/updated row.
        metric = (
            await session.execute(
                select(SonarQubeMetric)
                .where(
                    SonarQubeMetric.board_id == board.id,
                    SonarQubeMetric.repo_id == repo.id,
                )
                .options(selectinload(SonarQubeMetric.repository))
            )
        ).scalar_one_or_none()
        if metric is not None:
            return repo_health(metric)
        return unscanned_repo_health(board, repo)

    if not get_settings().sonarqube_enabled:
        # Kill switch: no live attempt, no probe — degrade to the current card.
        return await _current_health()

    key = derive_repo_project_key(board, repo)
    # poll_repo upserts the (board_id, repo_id) metric row on success (and commits +
    # publishes the per-repo event); returns False on fetch-None (never-scanned /
    # unreachable) without writing a row or raising. Either way we serialize the
    # now-current state (live metric on success, honest unscanned card otherwise).
    await poll_repo(session, board, repo, key)
    return await _current_health()


# ---------------------------------------------------------------------------
# Per-board scan (PH-236, C2) — "Scan now" enqueue + scan-plan for the host runner.
#
# The backend runs INSIDE a container and CANNOT `docker compose run`, so it never
# launches the scanner itself. The `scan` endpoint is a CHEAP, NON-blocking, never-500
# planner: it resolves project_key + container source path + language support and
# returns a `scan_status` enum (the actual analysis runs HOST-side via
# scripts/sonar-scan-board.sh, which curls `scan-plan`). This is DISTINCT from `sync`
# (sync only re-polls the EXISTING analysis; scan plans a NEW analysis run).
#
# Secret-free (PH-203/PH-223 contract): neither the token nor the compose-internal
# `sonarqube_url` ever appears in a SonarScanResult / SonarScanPlan or any log line.
# ---------------------------------------------------------------------------


# scan_status enum (the load-bearing field the host runner + frontend C3 key off):
#   queued              scannable — intent recorded, run the host runner to analyze
#   running             reserved (a watcher actively scanning); not emitted by v1 `scan`
#   unsupported         language not analyzable in SonarQube Community Edition at all
#   needs_dotnet_setup  csharp (Unity) board, but SONAR_DOTNET_ENABLED is not set — the
#                       host .NET prerequisite (dotnet SDK + dotnet-sonarscanner) is not
#                       declared ready, so we HONESTLY skip (no job) instead of fake-done
#                       (PH-257). Flip SONAR_DOTNET_ENABLED=true once installed → queued.
#   disabled            settings.sonarqube_enabled is False (server kill switch)
#   unconfigured        no resolvable project key
#   error               no repos_path, or a path outside HOST_HOME / with '..' (RepoPathError)
SCAN_STATUS_QUEUED = "queued"
SCAN_STATUS_RUNNING = "running"
SCAN_STATUS_UNSUPPORTED = "unsupported"
SCAN_STATUS_NEEDS_DOTNET_SETUP = "needs_dotnet_setup"
SCAN_STATUS_DISABLED = "disabled"
SCAN_STATUS_UNCONFIGURED = "unconfigured"
SCAN_STATUS_ERROR = "error"

# PH-239 — SonarScanJob lifecycle states (the `state` column on sonar_scan_jobs).
# Distinct from the scan_status enum above: scan_status is the request OUTCOME the
# UI sees at click time (may be unsupported/disabled/...); JOB_STATE is the lifecycle
# of an actually-enqueued job that the host watcher drives.
JOB_STATE_QUEUED = "queued"
JOB_STATE_RUNNING = "running"
JOB_STATE_DONE = "done"
JOB_STATE_FAILED = "failed"


class ScanJobConflict(Exception):
    """A scan-job lifecycle transition is illegal (e.g. claiming a non-queued job).

    Raised by ``claim_scan_job`` / ``complete_scan_job`` so the API layer can map it
    to an HTTP 409 (the double-run guard, AC: "a second claim ... is rejected"). NOT
    a never-500 violation — it is a deliberate, documented conflict signal.
    """


class ScanJobNotFound(Exception):
    """The referenced scan job id does not exist (API maps to 404)."""

# Languages SonarQube Community Build CAN analyze with the IN-CONTAINER sonar-scanner-cli
# (sensor present in CE, no special toolchain). Used only to gate the HONEST unsupported
# path; the scanner's own sensors still pick the final language set.
# ``detect_board_language`` returns these labels.
_CE_SUPPORTED_LANGUAGES = frozenset(
    {"kotlin", "java", "python", "javascript", "typescript", "go", "php", "ruby"}
)
# Languages we explicitly KNOW are unsupported in CE at all (gated up front for an honest
# message even before the scanner runs). EMPTY since PH-257 — csharp moved to
# ``_CE_DOTNET_LANGUAGES`` (it IS analyzable, just via the host .NET pipeline, not the
# container CLI). Kept as the seam for any future genuinely-unsupported language.
_CE_UNSUPPORTED_LANGUAGES: frozenset[str] = frozenset()
# PH-257 — languages CE analyzes ONLY via the SonarScanner for .NET (begin → MSBuild/
# dotnet build → end) run on the HOST, NOT the container sonar-scanner-cli (an empirical
# probe proved the CLI indexes .cs files but reports 0 ncloc / 0 issues — a stub). These
# are ``supported=True`` (a scan PATH exists) but route through the host runner's C#
# branch and are gated by ``SONAR_DOTNET_ENABLED`` at plan time (honest
# ``needs_dotnet_setup`` until the operator declares the host prerequisite ready).
_CE_DOTNET_LANGUAGES = frozenset({"csharp"})

# Extension → language label. Order matters only for the marker-file shortcuts below;
# extension counting is otherwise a plain tally. Kept small + dependency-free.
_EXT_LANGUAGE = {
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".php": "php",
    ".rb": "ruby",
    ".cs": "csharp",
}

# Directories never worth walking for language detection (build output / VCS / vendor /
# Unity-generated). Keeps detection bounded + avoids classifying generated trees.
_DETECT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".gradle",
        ".idea",
        "build",
        "node_modules",
        "Library",  # Unity-generated
        "Temp",  # Unity-generated
        "obj",
        "bin",
        "dist",
        "target",
        ".venv",
        "venv",
        "__pycache__",
    }
)

# Bound the walk so a huge tree (Unity asset libraries, monorepos) can't stall the
# never-500/cheap `scan` endpoint. Detection only needs a representative sample.
_DETECT_MAX_FILES = 4000


def detect_board_language(container_path: str) -> str | None:
    """Infer a board's primary source language from its code tree (best-effort, pure-ish).

    ``container_path`` is the IN-CONTAINER path (``/repos/<rel>``) produced by
    ``to_container_path`` — the scanner sees the same tree at the same path. Returns a
    lowercase language label (``"kotlin"``, ``"csharp"``, ...) or ``None`` when the tree
    is empty / unreadable / has no recognized source. NEVER raises (mirrors the
    never-500 contract): a missing dir / permission error degrades to ``None``.

    Strategy (cheap, bounded by ``_DETECT_MAX_FILES``):
      1. **Unity marker shortcut** — an ``Assets/`` + (``ProjectSettings/`` or
         ``Packages/``) layout is a Unity project ⇒ ``csharp`` (the honest gate; CE
         can't analyze it). This wins before extension counting so a Unity tree with a
         few stray ``.py`` editor scripts is still correctly flagged C#.
      2. **Gradle/Kotlin marker shortcut** (PH-243) — a root ``build.gradle.kts`` or
         ``settings.gradle.kts`` is the canonical Kotlin-DSL Gradle signal. An
         Android/Kotlin project's deep ``src/main/kotlin`` tree (or stray ``.py``
         tooling/scripts) can out-tally or exhaust the bounded walk before the real
         ``.kt`` files are reached, misclassifying it as ``python``. A root marker-file
         check is deterministic and won't misfire on a real python repo. ``kotlin`` is
         CE-supported, so this corrects the ``supported`` gate for real Kotlin boards.
      3. **Extension tally** — walk the tree (skipping build/VCS/vendor/Unity-generated
         dirs), count recognized source extensions, return the most common language.
      Ties / no source ⇒ ``None`` (caller treats unknown as a generic sources scan, but
      a KNOWN-unsupported language gates to ``unsupported`` first).
    """
    if not container_path or not os.path.isdir(container_path):
        return None

    # 1) Unity layout → csharp (the C# honesty gate). Unity projects don't ship a
    # .sln/.csproj in VCS (they're generated), so detect by the canonical dir layout.
    try:
        has_assets = os.path.isdir(os.path.join(container_path, "Assets"))
        has_unity_meta = os.path.isdir(
            os.path.join(container_path, "ProjectSettings")
        ) or os.path.isdir(os.path.join(container_path, "Packages"))
        if has_assets and has_unity_meta:
            return "csharp"
    except OSError:
        return None

    # 2) Gradle/Kotlin marker → kotlin (PH-243). A root *.gradle.kts is the canonical
    # Kotlin-DSL Gradle signal; bias to kotlin BEFORE the tally so a deep src/main/kotlin
    # tree (or stray .py tooling) can't misclassify a real Android/Kotlin board as python.
    # Conservative marker-file presence check — won't misfire on a real python repo.
    try:
        for marker in ("build.gradle.kts", "settings.gradle.kts"):
            if os.path.isfile(os.path.join(container_path, marker)):
                return "kotlin"
    except OSError:
        return None

    # 3) Extension tally (bounded walk; skip generated/vendor dirs).
    counts: dict[str, int] = {}
    seen = 0
    try:
        for _root, dirs, files in os.walk(container_path):
            # Prune skip-dirs in place so os.walk never descends into them.
            dirs[:] = [d for d in dirs if d not in _DETECT_SKIP_DIRS]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                lang = _EXT_LANGUAGE.get(ext)
                if lang is not None:
                    counts[lang] = counts.get(lang, 0) + 1
                seen += 1
                if seen >= _DETECT_MAX_FILES:
                    break
            if seen >= _DETECT_MAX_FILES:
                break
    except OSError:
        return None

    if not counts:
        return None
    # Most-common language wins; deterministic tie-break by label for stability.
    return max(sorted(counts), key=lambda lang: counts[lang])


def _language_supported(language: str | None) -> bool:
    """Whether SonarQube Community Edition has ANY scan path for ``language``.

    Semantics (PH-257): ``supported`` means "a scan path exists", NOT "the container CLI
    can do it". A KNOWN-unsupported language → False (none currently). Everything else,
    including ``csharp`` (analyzable via the host .NET pipeline) and an UNKNOWN language
    (``None`` / unrecognized — optimistic: let the scanner's own sensors decide) → True.

    NOTE: csharp returning True does NOT mean it queues immediately — ``_plan_scan_status``
    applies the ``_CE_DOTNET_LANGUAGES`` + ``SONAR_DOTNET_ENABLED`` gate so a csharp board
    HONESTLY reports ``needs_dotnet_setup`` until the host prerequisite is declared ready.
    """
    if language in _CE_UNSUPPORTED_LANGUAGES:
        return False
    return True


@dataclass
class SonarScanPlan:
    """The per-REPO scan plan the HOST runner (scripts/sonar-scan-board.sh) consumes.

    The 7 FROZEN fields (PH-236) — the host script + frontend C3 depend on these names
    and they are NEVER renamed/removed. Secret-free: NO token, NO compose-internal
    ``sonarqube_url``.

      project_key       the resolved SonarQube projectKey (or None when unconfigured)
      container_source  the IN-CONTAINER sources path the scanner reads
                        (``/repos/<rel>``); None when no/invalid repos_path
      host_source       the HOST path — informational only
      language          detected primary language label, or None
      supported         True ⇒ CE can analyze it ⇒ the host runner should scan
      reason            human-readable why (esp. when ``supported`` is False)
      exclusions        sonar.exclusions glob the runner passes (lang-aware), or None

    PH-246 ADDITIVE fields (each plan element is self-describing for per-repo job +
    health keying — the multi-repo ``build_scan_plans`` sets them; the legacy
    board-level fallback leaves them None):
      repo_id           the linked Repository this plan targets (or None — board-level)
      repo_slug         the repo's stable per-board slug (or None — board-level)
    """

    project_key: str | None
    container_source: str | None
    host_source: str | None
    language: str | None
    supported: bool
    reason: str
    exclusions: str | None
    repo_id: uuid.UUID | None = None
    repo_slug: str | None = None


@dataclass
class SonarRepoScanOutcome:
    """Per-repo outcome inside a ``SonarScanResult`` (PH-246, additive). Secret-free.

    One per linked repo the board scan considered — lets the FE (PH-249) show which
    repos queued vs were skipped (unsupported/error) without a separate call.
    """

    repo_slug: str | None
    project_key: str | None
    scan_status: str
    language: str | None


@dataclass
class SonarScanResult:
    """Result of a ``request_board_scan`` (POST .../sonarqube/scan). Secret-free.

    ``scan_status`` is the load-bearing enum (SCAN_STATUS_* above). PH-246: it is now a
    per-board AGGREGATE — ``queued`` if ≥1 repo queued, else the all-repos status
    (unsupported/unconfigured/disabled/error). The top-level ``project_key`` /
    ``language`` / ``container_source`` stay the PRIMARY repo's values for back-compat;
    the additive ``repos`` list carries each repo's outcome. ``message`` tells the user
    what happened. NO token, NO compose-internal ``sonarqube_url`` ever appears here.
    """

    scan_status: str
    project_key: str | None
    language: str | None
    container_source: str | None
    message: str
    repos: list[SonarRepoScanOutcome] = field(default_factory=list)


# sonar.exclusions appended to a board scan to keep generated/vendor noise out of the
# analysis (board scans are sources-only for v1; no deep per-language tuning).
#
# PH-244: broadened to cover vendored/native/generated trees. The GXA board's
# ``GameXCore/src`` is 6.1 GB and bundles a vendored native tree
# (``src/main/cpp/LiteRT/bazel-LiteRT/external/androidndk/toolchains/...``) whose stray
# ``.java`` made the JavaSensor hard-abort. None of the added globs match legitimate app
# source (``src/main/{kotlin,java}``, ``app/``, ``frontend/src``, ``backend/app``) — each
# targets a path SEGMENT that is native/vendored/generated by convention, or a binary
# extension. Note: ``**/*.java`` is NOT in this base set — it is appended per-plan by
# ``build_scan_plan`` only when the board's primary language is NOT java (see Fix B1).
_SCAN_EXCLUSIONS = (
    "**/build/**,**/.gradle/**,**/node_modules/**,**/dist/**,"
    "**/Library/**,**/Temp/**,**/obj/**,**/bin/**,**/.venv/**,**/venv/**,"
    "**/cpp/**,**/.cxx/**,**/*.so,**/*.a,**/*.o,**/external/**,"
    "**/bazel-*/**,**/third_party/**,**/vendor/**,**/androidndk/**,"
    "**/ndk/**,**/toolchains/**"
)


def _loaded_primary_repository(board: Board) -> Repository | None:
    """The board's primary repo IFF ``repositories`` is already loaded, else None.

    PH-242 async-safety guard: ``Board.primary_repository`` iterates
    ``board.repositories``, which in async would trigger a lazy-load (raising
    ``MissingGreenlet``) if the collection was NOT eager-loaded. The two production
    call sites (``api_board_sonarqube_scan`` / ``scan-plan`` via ``get_board``)
    always ``selectinload(Board.repositories)``, but a defensive check keeps the
    never-500 contract bullet-proof for any future bare-loaded caller: when the
    relationship is unloaded we treat the board as having no primary repo → the
    ``repos_path`` fallback runs (safe, no query). Uses the SQLAlchemy instance
    inspector's ``unloaded`` set — a pure in-memory check, no IO.
    """
    if "repositories" in inspect(board).unloaded:
        return None
    return board.primary_repository


def _resolve_repo_container_source(repo: Repository) -> tuple[str | None, str | None]:
    """Resolve ONE repository's scan source → (container_source, error_reason).

    PH-246: the per-repo extraction of the PH-242 primary-repo branch — identical
    logic, just keyed on an arbitrary repo (not only the board's primary). The precise,
    git-synced code root is ``Repository.local_path``, which the column invariant
    guarantees is already container-form (``/repos/...``); it must NOT be re-translated.

      1. Truthy ``local_path`` already container-form (starts with ``repos_root``) → use
         as-is (common/expected case).
      2. Legacy host-form ``local_path`` → defensively normalize via
         ``to_container_path``; on ``RepoPathError`` degrade to an error reason.
      3. No ``local_path`` → an error reason (an UNSCANNABLE plan element — the runner
         skips it, the other repos still scan; never-500).

    Returns ``(container_path, None)`` on success, ``(None, reason)`` otherwise. NEVER
    raises — the caller maps the reason onto an unsupported/error plan element.
    """
    if not repo.local_path:
        return None, f"repo {repo.slug} has no local_path configured"
    repos_root = get_settings().repos_root.rstrip("/")
    local_path = repo.local_path
    # local_path is documented container-form (/repos/...) — use as-is. Only the
    # repos_root prefix gates the cheap path; a trailing-slash variant ("/repos")
    # still matches because we compare against the normalized root + "/".
    if local_path == repos_root or local_path.startswith(repos_root + "/"):
        return local_path, None
    # Defensive: a legacy/host-form local_path row → normalize, error on failure.
    try:
        return to_container_path(local_path), None
    except RepoPathError as exc:
        return None, f"repo {repo.slug} local_path is not scannable: {exc}"


def _resolve_container_source(board: Board) -> tuple[str | None, str | None]:
    """Resolve a board's PRIMARY scan source → (container_source, error_reason).

    PH-242: PREFER the board's primary linked repository's ``local_path`` over the
    coarse ``board.repos_path``. ``repos_path`` is often the PARENT directory of the
    real code root (e.g. GXA: repos_path=``/Users/.../GameX`` but the code lives in
    ``/repos/.../GameX/GameXCore``), so scanning ``repos_path`` binds SonarQube to the
    wrong tree (0 LoC, junk dirs). The precise, git-synced code root is
    ``Repository.local_path``, which the column invariant guarantees is already
    container-form (``/repos/...``); it must NOT be re-translated.

    Resolution:
      1. Primary repo present with a usable ``local_path`` → use it (delegates to
         ``_resolve_repo_container_source``).
      2. Otherwise → existing behavior: ``board.repos_path`` → ``to_container_path``.
         Keeps boards whose ``repos_path`` already equals the code root (PH/KIM) and
         repo-less boards byte-for-byte unchanged.

    Reading the primary repo goes through ``_loaded_primary_repository`` so an
    unloaded ``repositories`` collection degrades to the fallback instead of a lazy
    async load — preserving never-500.

    Returns ``(container_path, None)`` on success, ``(None, reason)`` when no scannable
    source exists. NEVER raises — the caller maps the reason onto an error status.
    """
    repo = _loaded_primary_repository(board)
    if repo is not None and repo.local_path:
        container, _reason = _resolve_repo_container_source(repo)
        if container is not None:
            return container, None
        # A bad primary local_path falls through to the repos_path fallback (PH-242
        # behavior preserved byte-for-byte: never-500, repos_path is the safety net).

    host_path = board.repos_path
    if not host_path:
        return None, "board has no repos_path configured"
    try:
        return to_container_path(host_path), None
    except RepoPathError as exc:
        return None, f"repos_path is not scannable: {exc}"


def _host_source_for(board: Board, container_source: str | None) -> str | None:
    """Informational HOST path matching the scanned ``container_source`` (PH-242).

    When the primary-repo branch drove ``container_source`` (i.e. it equals the
    primary repo's container-form ``local_path``), the host equivalent is
    ``to_host_path(local_path)`` (best-effort; on ``RepoPathError`` fall back to
    ``board.repos_path``). Otherwise the fallback repos_path was used → keep
    ``board.repos_path``. Cosmetic only — ``container_source`` is the load-bearing
    field the scanner reads.
    """
    repo = _loaded_primary_repository(board)
    if (
        container_source is not None
        and repo is not None
        and repo.local_path == container_source
    ):
        try:
            return to_host_path(repo.local_path)
        except RepoPathError:
            return board.repos_path
    return board.repos_path


def _repo_host_source(repo: Repository, container_source: str | None) -> str | None:
    """Informational HOST path for a per-repo plan element (PH-246, cosmetic).

    When ``container_source`` equals the repo's container-form ``local_path`` (the
    common case), the host equivalent is ``to_host_path(local_path)`` (best-effort; on
    ``RepoPathError`` fall back to ``local_path`` itself). Load-bearing field is
    ``container_source`` — this is display only.
    """
    if container_source is not None and repo.local_path == container_source:
        try:
            return to_host_path(repo.local_path)
        except RepoPathError:
            return repo.local_path
    return repo.local_path or None


def _language_plan_fields(container_source: str) -> tuple[str | None, bool, str, str]:
    """Resolve (language, supported, reason, exclusions) for a scannable source.

    PH-246: the shared language/CE-support/exclusions resolution extracted so both the
    board-level ``build_scan_plan`` and the per-repo ``build_scan_plans`` element path
    apply IDENTICAL logic (detect → support gate → PH-244 lang-aware ``**/*.java`` rule).
    Pure (filesystem read only); NEVER raises.
    """
    language = detect_board_language(container_source)
    supported = _language_supported(language)
    if not supported:
        reason = (
            f"{language or 'this language'} is not analyzable in SonarQube "
            "Community Edition"
        )
    elif language in _CE_DOTNET_LANGUAGES:
        # PH-257 — csharp IS supported (a scan path exists) but only via the HOST .NET
        # pipeline, not the container CLI. Honest reason regardless of the flag; the
        # final scan_status (queued vs needs_dotnet_setup) is decided in _plan_scan_status.
        reason = (
            f"{language} (Unity/.NET) is analyzed via the host SonarScanner for .NET "
            "pipeline — requires dotnet SDK + dotnet-sonarscanner on the host and "
            "SONAR_DOTNET_ENABLED=true"
        )
    elif language is None:
        reason = "no recognized source language detected — a generic sources scan"
    else:
        reason = f"{language} is analyzable in SonarQube Community Edition"

    # PH-244 Fix B1: language-aware **/*.java exclusion. When the primary language is
    # NOT java, drop incidental .java (e.g. the GXA vendored tree's stray files) so
    # SonarQube's JavaSensor has nothing to analyze and cannot hard-abort with
    # AnalysisException. A java-primary source keeps .java analyzable. Value-only
    # broadening of the EXISTING frozen ``exclusions`` field — no shape change (PH-236).
    exclusions = _SCAN_EXCLUSIONS
    if language != "java":
        exclusions = f"{_SCAN_EXCLUSIONS},**/*.java"

    return language, supported, reason, exclusions


def build_scan_plan(board: Board) -> SonarScanPlan:
    """Compute a board's PRIMARY scan plan — the single-object back-compat resolution.

    PH-246: KEPT as a thin wrapper returning the PRIMARY repo's plan (or the board-level
    fallback when no repos). All existing single-plan callers (``request_board_scan``'s
    summary fields, the kept single-object ``/scan-plan`` endpoint, the host script)
    keep working BYTE-COMPATIBLY — the 7 frozen fields are unchanged; the additive
    ``repo_id``/``repo_slug`` are populated when a primary repo drove the plan.

    Pure (settings + filesystem read only, NO network). NEVER raises (never-500).
    """
    plans = build_scan_plans(board)
    # ``build_scan_plans`` always returns ≥1 element (a board-level fallback element
    # when there are no repos); the first is the primary (repos are primary-first).
    return plans[0]


def _board_level_plan(board: Board) -> SonarScanPlan:
    """The board-level (no-linked-repo) scan plan — PH-242 ``repos_path`` fallback path.

    PH-246: factored out so ``build_scan_plans`` can emit exactly this when a board has
    no linked repositories (byte-identical to pre-PH-246 ``build_scan_plan`` for a
    repo-less board). ``repo_id``/``repo_slug`` stay None (no repo to key on).
    """
    project_key = resolve_project_key(board)
    container_source, path_reason = _resolve_container_source(board)

    if project_key is None:
        return SonarScanPlan(
            project_key=None,
            container_source=container_source,
            host_source=board.repos_path,
            language=None,
            supported=False,
            reason="no SonarQube project key configured (run setup first)",
            exclusions=None,
        )
    if container_source is None:
        return SonarScanPlan(
            project_key=project_key,
            container_source=None,
            host_source=board.repos_path,
            language=None,
            supported=False,
            reason=path_reason or "board has no scannable repos_path",
            exclusions=None,
        )

    language, supported, reason, exclusions = _language_plan_fields(container_source)
    return SonarScanPlan(
        project_key=project_key,
        container_source=container_source,
        host_source=_host_source_for(board, container_source),
        language=language,
        supported=supported,
        reason=reason,
        exclusions=exclusions,
    )


def _build_repo_plan(board: Board, repo: Repository) -> SonarScanPlan:
    """One repository's scan plan element (PH-246). NEVER raises (never-500).

    ``project_key`` derives from ``derive_repo_project_key`` (primary inherits the
    board key; siblings get ``<primaryKey>-<slug>``). A repo with no usable
    ``local_path`` degrades to an UNSCANNABLE element (``supported=False`` + reason)
    rather than being dropped — the runner skips it, the others still scan. Both
    ``repo_id`` and ``repo_slug`` are always set so the element is self-describing for
    per-repo job + health keying.
    """
    project_key = derive_repo_project_key(board, repo)
    container_source, path_reason = _resolve_repo_container_source(repo)

    if container_source is None:
        return SonarScanPlan(
            project_key=project_key,
            container_source=None,
            host_source=_repo_host_source(repo, None),
            language=None,
            supported=False,
            reason=path_reason or f"repo {repo.slug} has no scannable source",
            exclusions=None,
            repo_id=repo.id,
            repo_slug=repo.slug,
        )

    language, supported, reason, exclusions = _language_plan_fields(container_source)
    return SonarScanPlan(
        project_key=project_key,
        container_source=container_source,
        host_source=_repo_host_source(repo, container_source),
        language=language,
        supported=supported,
        reason=reason,
        exclusions=exclusions,
        repo_id=repo.id,
        repo_slug=repo.slug,
    )


def build_scan_plans(board: Board) -> list[SonarScanPlan]:
    """Compute one scan plan PER linked repository (PH-246 — the multi-repo source of truth).

    Iterates ``board.repositories`` (primary first, then by slug) — eager-loaded; the
    ``inspect(board).unloaded`` async-safety guard degrades an unloaded collection to a
    single board-level plan instead of a lazy async load (never-500). For EACH repo:
      * ``project_key`` = ``derive_repo_project_key`` (primary inherits board key; siblings
        ``<primaryKey>-<slug>``),
      * ``container_source`` resolved from THAT repo's ``local_path`` (not the primary's),
      * ``language``/``supported``/``exclusions`` resolved per-repo identically to today.
    A repo with no usable ``local_path`` → an UNSCANNABLE element (kept, not dropped — the
    runner skips it, the others still scan).

    A board with NO linked repositories (or an unloaded collection) → exactly ONE
    board-level plan (``_board_level_plan``) = today's ``build_scan_plan`` output, so a
    single-repo / repo-less board is byte-identical. ALWAYS returns ≥1 element.

    Pure (settings + filesystem read only, NO network). NEVER raises (never-500).
    """
    # Async-safety: only iterate repositories when the collection is eager-loaded.
    if "repositories" in inspect(board).unloaded or not board.repositories:
        return [_board_level_plan(board)]

    # Primary first, then by slug — stable, deterministic ordering for the runner + UI.
    repos = sorted(board.repositories, key=lambda r: (not r.is_primary, r.slug))
    return [_build_repo_plan(board, repo) for repo in repos]


async def _find_or_create_queued_job(
    session: AsyncSession,
    board: Board,
    plan: SonarScanPlan,
    requested_by: uuid.UUID | None,
) -> SonarScanJob:
    """Return the existing ``queued`` job for ``(board, plan.repo_id)``, or create one.

    PH-246 — idempotency moved from per-BOARD to per-``(board_id, repo_id, queued)``: a
    multi-repo board enqueues N jobs (one per scannable repo) and re-clicking "Scan now"
    while GXA's 3 jobs wait re-uses each rather than stacking 6 (R5). A board/repo whose
    latest job is ``running``/``done``/``failed`` still gets a NEW queued job — a fresh
    request after the prior one started is legitimate. The ``project_key`` (+ ``repo_slug``)
    snapshot on a re-used row is refreshed to the currently-resolved values.

    ``plan.repo_id`` is None for the board-level fallback (repo-less board) — that path
    keys idempotency on ``(board_id, repo_id IS NULL, queued)``, i.e. the legacy single
    board-level queued job, so a repo-less board is byte-compatible with PH-239.
    """
    project_key = plan.project_key
    assert project_key is not None  # caller only enqueues a resolved (queued) plan
    repo_id = plan.repo_id
    existing = (
        await session.execute(
            select(SonarScanJob)
            .where(
                SonarScanJob.board_id == board.id,
                SonarScanJob.repo_id == repo_id,
                SonarScanJob.state == JOB_STATE_QUEUED,
            )
            .order_by(SonarScanJob.requested_at.desc())
        )
    ).scalars().first()
    if existing is not None:
        existing.project_key = project_key  # refresh snapshot to the live key
        existing.repo_slug = plan.repo_slug
        await session.commit()
        return existing

    job = SonarScanJob(
        board_id=board.id,
        repo_id=repo_id,
        repo_slug=plan.repo_slug,
        project_key=project_key,
        state=JOB_STATE_QUEUED,
        requested_by=requested_by,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


def _plan_scan_status(
    plan: SonarScanPlan, *, sonar_enabled: bool, dotnet_enabled: bool
) -> str:
    """Classify ONE plan element into a ``scan_status`` (no enqueue). PH-246 / PH-257.

    Same honest gating order as PH-235/236 — only ``queued`` is scannable:
      1. no project key                 → unconfigured
      2. sonar disabled                 → disabled
      3. no/invalid path                → error
      4. genuinely unsupported language → unsupported
      5. csharp (.NET) + flag OFF       → needs_dotnet_setup (PH-257 honest skip, NO job)
      6. otherwise                      → queued

    PH-257 — csharp is ``supported=True`` (a host .NET scan path exists) so it passes step
    4, but it can only be analyzed by the host runner's C# branch which needs the dotnet
    SDK + dotnet-sonarscanner the CONTAINER backend cannot see. ``SONAR_DOTNET_ENABLED``
    (``dotnet_enabled``) is the operator's "host prerequisite ready" signal: OFF →
    ``needs_dotnet_setup`` (honest, the caller enqueues NO job — fake-done is impossible);
    ON → ``queued`` (the host runner still independently guards + honest-skips if a tool is
    actually missing). Non-dotnet languages are completely unaffected (no regression).
    """
    if plan.project_key is None:
        return SCAN_STATUS_UNCONFIGURED
    if not sonar_enabled:
        return SCAN_STATUS_DISABLED
    if plan.container_source is None:
        return SCAN_STATUS_ERROR
    if not plan.supported:
        return SCAN_STATUS_UNSUPPORTED
    if plan.language in _CE_DOTNET_LANGUAGES and not dotnet_enabled:
        return SCAN_STATUS_NEEDS_DOTNET_SETUP
    return SCAN_STATUS_QUEUED


async def request_board_scan(
    session: AsyncSession, board: Board, requested_by: uuid.UUID | None = None
) -> SonarScanResult:
    """Enqueue an on-demand per-board scan (admin "Scan now"). Cheap, NON-blocking.

    PH-246 — PER-REPO: ``build_scan_plans`` yields one plan per linked repo; this
    enqueues one ``SonarScanJob(state=queued)`` PER SCANNABLE repo (idempotent per
    ``(board_id, repo_id)`` — see ``_find_or_create_queued_job``). The honest
    non-scannable outcomes (unconfigured/disabled/error/unsupported) do NOT enqueue a
    job for that repo, preserving the PH-235/236 contract; one bad repo never aborts the
    others (never-500). The host-side watcher long-polls ``GET /api/scans/pending``,
    claims each job, runs the scanner for that repo's ``project_key``/``container_source``
    (via the ``/scan-plans`` list endpoint, PH-248), then POSTs ``/complete`` — on success
    the backend immediately ``poll_repo``-ingests that repo's fresh measures.

    The returned ``scan_status`` is the per-board AGGREGATE: ``queued`` if ≥1 repo
    queued; else, when every repo shares one non-queued status, that status (so a
    single-repo board is byte-compatible with PH-239); else ``error`` (mixed
    non-scannable). The top-level ``project_key``/``language``/``container_source`` stay
    the PRIMARY plan's values (back-compat); ``repos`` carries each repo's outcome.

    DISTINCT from ``sync_board_now`` (which only re-polls the EXISTING analysis). NEVER
    raises (never-500), NEVER blocks (no scanner here — the backend can't ``docker
    compose run``).
    """
    settings = get_settings()
    sonar_enabled = settings.sonarqube_enabled
    dotnet_enabled = settings.sonar_dotnet_enabled
    plans = build_scan_plans(board)
    primary = plans[0]  # primary-first ordering — drives the back-compat top-level fields

    outcomes: list[SonarRepoScanOutcome] = []
    queued_count = 0
    for plan in plans:
        plan_status = _plan_scan_status(
            plan, sonar_enabled=sonar_enabled, dotnet_enabled=dotnet_enabled
        )
        outcomes.append(
            SonarRepoScanOutcome(
                repo_slug=plan.repo_slug,
                project_key=plan.project_key,
                scan_status=plan_status,
                language=plan.language,
            )
        )
        if plan_status == SCAN_STATUS_QUEUED:
            queued_count += 1
            job = await _find_or_create_queued_job(session, board, plan, requested_by)
            logger.info(
                "sonarqube scan queued board=%s repo=%s project_key=%s language=%s "
                "source=%s job=%s",
                board.key,
                plan.repo_slug,
                plan.project_key,
                plan.language,
                plan.container_source,
                job.id,
            )

    # Aggregate scan_status: queued if any queued; else collapse a uniform non-queued
    # status (single-repo / repo-less boards stay byte-compatible); else error (mixed).
    if queued_count > 0:
        aggregate = SCAN_STATUS_QUEUED
        message = (
            f"scan queued for {queued_count} repo(s). The host watcher "
            "(scripts/sonar-scan-watcher.sh) will run it automatically."
        )
    else:
        statuses = {o.scan_status for o in outcomes}
        uniform = len(statuses) == 1
        aggregate = next(iter(statuses)) if uniform else SCAN_STATUS_ERROR
        # Honest, status-appropriate message (keeps the PH-235/236 wording for a
        # uniform single-status board; ``disabled`` is reported even when a key + path
        # resolved). The primary plan's reason carries the path/lang detail; a MIXED
        # non-scannable board reports the aggregate-error summary.
        if aggregate == SCAN_STATUS_DISABLED:
            message = "SonarQube is disabled on this server"
        elif not uniform:
            message = "no repo has a scannable source"
        else:
            message = primary.reason

    return SonarScanResult(
        scan_status=aggregate,
        project_key=primary.project_key,
        language=primary.language,
        container_source=primary.container_source,
        message=message,
        repos=outcomes,
    )


@dataclass
class SonarRepoScanResult:
    """Result of a ``request_repo_scan`` (POST .../repositories/{selector}/sonarqube/scan).

    PH-255 — the per-REPO sibling of ``SonarScanResult``: it describes a SINGLE repo's
    scan request, so there is no ``repos[]`` aggregate. ``scan_status`` is the same
    load-bearing enum (SCAN_STATUS_*); ``job_id`` is the enqueued ``SonarScanJob`` id when
    (and only when) ``scan_status == queued``, else None (a non-scannable repo enqueues
    NO job — decision A: honest status, not a 409/422). Secret-free: NO token, NO
    compose-internal ``sonarqube_url`` ever appears here.
    """

    repo_slug: str | None
    project_key: str | None
    language: str | None
    scan_status: str
    job_id: uuid.UUID | None
    message: str


async def request_repo_scan(
    session: AsyncSession,
    board: Board,
    repo: Repository,
    requested_by: uuid.UUID | None = None,
) -> SonarRepoScanResult:
    """Enqueue an on-demand scan for ONE repo (per-repo "Scan now", PH-255).

    The single-repo slice of ``request_board_scan`` — it composes the SAME PH-246
    helpers (``_build_repo_plan`` → ``_plan_scan_status`` → ``_find_or_create_queued_job``)
    against the already-resolved ``repo`` instead of looping ``build_scan_plans``. NO new
    resolution / key / path logic: ``_build_repo_plan`` derives the project key
    (``derive_repo_project_key``) + resolves THAT repo's container source + language.

    On ``queued`` ONLY: ``_find_or_create_queued_job`` enqueues (idempotent per
    ``(board_id, repo_id, queued)`` — a 2nd scan while a queued job waits re-uses it,
    returning the SAME ``job_id``, no stacking) and the result carries ``job.id``. Every
    non-queued status (unconfigured / disabled / error / unsupported — decision A) returns
    the HONEST ``scan_status`` with ``job_id=None`` and NO job created (200, never 409/422,
    never 500). The host watcher (``GET /api/scans/pending`` → claim → ``/complete`` →
    ``poll_board`` ingest) drains the job unchanged. Cheap, NON-blocking (no scanner here —
    the backend can't ``docker compose run``).
    """
    settings = get_settings()
    sonar_enabled = settings.sonarqube_enabled
    dotnet_enabled = settings.sonar_dotnet_enabled
    plan = _build_repo_plan(board, repo)  # single-element, never-raises
    scan_status = _plan_scan_status(
        plan, sonar_enabled=sonar_enabled, dotnet_enabled=dotnet_enabled
    )

    job_id: uuid.UUID | None = None
    if scan_status == SCAN_STATUS_QUEUED:
        job = await _find_or_create_queued_job(session, board, plan, requested_by)
        job_id = job.id
        logger.info(
            "sonarqube repo scan queued board=%s repo=%s project_key=%s language=%s "
            "source=%s job=%s",
            board.key,
            plan.repo_slug,
            plan.project_key,
            plan.language,
            plan.container_source,
            job.id,
        )
        message = (
            f"scan queued for repo {plan.repo_slug}. The host watcher "
            "(scripts/sonar-scan-watcher.sh) will run it automatically."
        )
    elif scan_status == SCAN_STATUS_DISABLED:
        message = "SonarQube is disabled on this server"
    else:
        # unconfigured / error / unsupported / needs_dotnet_setup — the plan's reason
        # carries the path/lang/key/dotnet-setup detail (secret-free; no token / url).
        message = plan.reason

    return SonarRepoScanResult(
        repo_slug=plan.repo_slug,
        project_key=plan.project_key,
        language=plan.language,
        scan_status=scan_status,
        job_id=job_id,
        message=message,
    )


# ---------------------------------------------------------------------------
# PH-239 — scan-job lifecycle: the watcher seam (pending → claim → complete).
#
# These power GET /api/scans/pending, POST /api/scans/{id}/claim,
# POST /api/scans/{id}/complete. Secret-free: NO token, NO compose-internal
# sonarqube_url ever crosses these. The complete-success path triggers an IMMEDIATE
# poll_board ingest so metrics appear within seconds, not after the 300s poll cron.
# ---------------------------------------------------------------------------


@dataclass
class PendingScanJob:
    """One queued scan job, projected for the host watcher. Secret-free.

    PH-246: ``repo_slug`` tells the watcher (PH-248) which repo each job targets so the
    ``/complete`` ingest re-polls the right project. Nullable for a legacy board-level
    job (``repo_id IS NULL``).
    """

    job_id: uuid.UUID
    board_key: str
    project_key: str
    repo_slug: str | None


async def list_pending_scans(session: AsyncSession) -> list[PendingScanJob]:
    """All ``queued`` scan jobs (oldest first), joined to their board key.

    The watcher long-polls this; returns ``[]`` fast when idle (cheap, indexed on
    ``state``). Secret-free — only job id + board key + project key + repo slug.
    """
    rows = (
        await session.execute(
            select(SonarScanJob, Board.key)
            .join(Board, SonarScanJob.board_id == Board.id)
            .where(SonarScanJob.state == JOB_STATE_QUEUED)
            .order_by(SonarScanJob.requested_at.asc())
        )
    ).all()
    return [
        PendingScanJob(
            job_id=job.id,
            board_key=board_key,
            project_key=job.project_key,
            repo_slug=job.repo_slug,
        )
        for job, board_key in rows
    ]


async def _get_job(
    session: AsyncSession, job_id: uuid.UUID, *, for_update: bool = False
) -> SonarScanJob:
    """Load a job by id or raise ``ScanJobNotFound``.

    ``for_update=True`` takes a ``SELECT ... FOR UPDATE`` row lock so the
    read-then-check-then-write in ``claim_scan_job`` is serialized: two
    watchers racing on the same job can't both observe ``queued`` and both
    claim it (TOCTOU). The lock is released by the surrounding ``commit``.
    """
    stmt = select(SonarScanJob).where(SonarScanJob.id == job_id)
    if for_update:
        stmt = stmt.with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise ScanJobNotFound(str(job_id))
    return job


async def claim_scan_job(session: AsyncSession, job_id: uuid.UUID) -> SonarScanJob:
    """Atomically transition a job ``queued`` → ``running`` (sets ``started_at``).

    Double-run guard (R2): claiming a job NOT in ``queued`` raises ``ScanJobConflict``
    (API → 409), so a second watcher instance can't re-run an already-claimed job.
    Raises ``ScanJobNotFound`` for an unknown id.
    """
    job = await _get_job(session, job_id, for_update=True)
    if job.state != JOB_STATE_QUEUED:
        raise ScanJobConflict(
            f"job {job_id} is {job.state}, not claimable (expected queued)"
        )
    job.state = JOB_STATE_RUNNING
    job.started_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(job)
    logger.info("sonarqube scan job claimed job=%s board_id=%s", job.id, job.board_id)
    return job


async def complete_scan_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    success: bool,
    detail: str | None = None,
) -> SonarScanJob:
    """Report a running job's outcome: ``running`` → ``done`` | ``failed``.

    On ``success=True`` the job goes ``done`` and the backend IMMEDIATELY ingests the
    fresh analysis (no 300s cron wait); the periodic cron remains the backstop for the
    SonarQube async-indexing race (R3 — an immediate poll that races ahead of indexing
    returns None, and the cron catches it). On ``success=False`` the job goes ``failed``
    with ``detail`` persisted and NO ingest.

    PH-246: the ingest now targets the job's SPECIFIC repo (``job.repo_id`` → that repo's
    ``project_key``/metric row), not the whole board, so completing GXA's gamexsdk job
    upserts only the gamexsdk metric. A legacy job with ``repo_id IS NULL`` falls back to
    a board-level ``poll_board`` (byte-compatible with PH-239).

    Completing a job NOT in ``running`` raises ``ScanJobConflict`` (API → 409). Raises
    ``ScanJobNotFound`` for an unknown id. The ingest call is already fully
    error-isolated (never raises for SonarQube error paths), so a complete-success
    against a still-indexing project still records ``done`` cleanly.
    """
    job = await _get_job(session, job_id)
    if job.state != JOB_STATE_RUNNING:
        raise ScanJobConflict(
            f"job {job_id} is {job.state}, not completable (expected running)"
        )

    job.finished_at = datetime.now(UTC)
    if not success:
        job.state = JOB_STATE_FAILED
        job.detail = detail or "scanner reported failure"
        await session.commit()
        await session.refresh(job)
        logger.warning(
            "sonarqube scan job failed job=%s board_id=%s detail=%s",
            job.id,
            job.board_id,
            job.detail,
        )
        return job

    job.state = JOB_STATE_DONE
    await session.commit()

    # Immediate ingest: pull the fresh analysis now so metrics appear within seconds.
    # PH-246: eager-load repositories so we can ingest the job's SPECIFIC repo (and so
    # poll_board's repo loop is async-safe if we fall back to it).
    board = (
        await session.execute(
            select(Board)
            .where(Board.id == job.board_id)
            .options(selectinload(Board.repositories))
        )
    ).scalar_one_or_none()
    ingested = False
    if board is not None:
        if job.repo_id is not None:
            repo = next(
                (r for r in board.repositories if r.id == job.repo_id), None
            )
            if repo is not None:
                ingested = await poll_repo(
                    session, board, repo, derive_repo_project_key(board, repo)
                )
            else:
                # Repo was removed between enqueue + complete — fall back to job key.
                ingested = await poll_repo(session, board, None, job.project_key)
        else:
            # Legacy board-level job (repo_id IS NULL) — board-level poll (PH-239 compat).
            ingested = await poll_board(session, board)
    job.detail = detail or ("ingested" if ingested else "done; ingest pending (cron backstop)")
    await session.commit()
    await session.refresh(job)
    logger.info(
        "sonarqube scan job done job=%s board_id=%s ingested=%s",
        job.id,
        job.board_id,
        ingested,
    )
    return job


async def _poll_all_boards() -> None:
    """One poll tick: iterate all boards, poll each (resolve_project_key skips unlinked).

    Fresh session per tick (never held across asyncio.sleep — matches refresh.py /
    stale_claims.py).
    """
    async with SessionLocal() as session:
        # PH-246: eager-load repositories so poll_board's per-repo loop is async-safe
        # (no lazy MissingGreenlet in the cron's per-board fan-out).
        boards = (
            await session.execute(
                select(Board).options(selectinload(Board.repositories))
            )
        ).scalars().all()
        for board in boards:
            await poll_board(session, board)


async def sonarqube_poll_cron() -> None:
    """Lifespan background task: periodically poll SonarQube board health.

    Mirrors git_poll_cron shape exactly:
    - CancelledError is re-raised so cancellation propagates (graceful shutdown).
    - Other exceptions are swallowed per-tick so one bad board/tick can't kill it.
    - Enable gating (sonarqube_enabled + interval>0) is done by lifespan before
      creating the task — the cron itself does not re-check.
    """
    settings = get_settings()
    interval = settings.sonarqube_polling_interval_seconds
    logger.info("sonarqube_poll_cron started interval=%ds", interval)

    while True:
        try:
            await asyncio.sleep(interval)
            await _poll_all_boards()
        except asyncio.CancelledError:
            # Cooperative cancellation: log, then re-raise so the framework
            # sees the task acknowledged the cancel (graceful shutdown).
            logger.info("sonarqube_poll_cron stopped")
            raise
        except Exception as exc:
            logger.warning("sonarqube_poll_cron error=%s", exc)
