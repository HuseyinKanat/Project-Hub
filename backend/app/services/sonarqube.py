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
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Board, SonarQubeMetric
from app.db.session import SessionLocal
from app.events.bus import EventBus, EventEnvelope

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
    - CancelledError breaks the loop cleanly on shutdown.
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
            logger.info("sonarqube_poll_cron stopped")
            break
        except Exception as exc:
            logger.warning("sonarqube_poll_cron error=%s", exc)
