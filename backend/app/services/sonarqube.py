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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Board, SonarQubeMetric
from app.db.session import SessionLocal
from app.events.bus import EventBus, EventEnvelope
from app.services.repo_paths import RepoPathError, to_container_path

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
    session: AsyncSession, board: Board, project_key: str, snapshot: SonarSnapshot
) -> SonarQubeMetric:
    """Insert-or-update the single SonarQubeMetric row for a board (unique board_id)."""
    metric = (
        await session.execute(
            select(SonarQubeMetric).where(SonarQubeMetric.board_id == board.id)
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if metric is None:
        metric = SonarQubeMetric(board_id=board.id)
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


async def poll_board(session: AsyncSession, board: Board) -> bool:
    """Poll one board: resolve key → fetch → upsert → publish.

    Returns True if a metric row was written + event published, False if the board
    was skipped (no key) or the fetch returned None. Never raises for the SonarQube
    error paths (the client already isolates those).
    """
    project_key = resolve_project_key(board)
    if project_key is None:
        logger.debug("sonarqube poll: board=%s has no project key, skipping", board.key)
        return False

    snapshot = await fetch_board_metrics(project_key)
    if snapshot is None:
        # Client already logged the cause; skip — no row, no event.
        return False

    metric = await _upsert_metric(session, board, project_key, snapshot)
    await session.commit()
    await _publish_synced(board, metric)
    logger.info(
        "sonarqube poll: synced board=%s gate=%s bugs=%s ncloc=%s",
        board.key,
        snapshot.quality_gate_status,
        snapshot.bugs,
        snapshot.ncloc,
    )
    return True


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

    metric = (
        await session.execute(
            select(SonarQubeMetric).where(SonarQubeMetric.board_id == board.id)
        )
    ).scalar_one_or_none()
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


async def _poll_all_boards() -> None:
    """One poll tick: iterate all boards, poll each (resolve_project_key skips unlinked).

    Fresh session per tick (never held across asyncio.sleep — matches refresh.py /
    stale_claims.py).
    """
    async with SessionLocal() as session:
        boards = (await session.execute(select(Board))).scalars().all()
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
