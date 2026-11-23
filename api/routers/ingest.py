"""
api/routers/ingest.py
~~~~~~~~~~~~~~~~~~~~~
Data ingestion management endpoints.

Provides:
- Trigger ingestion pipeline manually
- Check ingestion status
- View ingestion run history
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials

from api.auth import (
    get_current_user,
    optional_security,
    require_admin,
    require_cron_or_admin,
    require_role,
)
from api.dependencies import get_unit_of_work
from api.schemas.query_schemas import IngestRequestSchema, IngestResponseSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["Ingestion"])


@router.post(
    "/trigger",
    response_model=IngestResponseSchema,
    dependencies=[Depends(require_role("admin"))],
)
async def trigger_ingestion(request: IngestRequestSchema):
    """
    Run multi-source job ingestion and persist the results.

    Discovers postings across the enabled sources, parses each one with the LLM
    JD parser, then upserts into Postgres and embeds into the vector store —
    the same path as ``scripts/run_ingestion.py``. This is what populates the
    tables the search endpoints read, so it is the way to fill a fresh database.

    Admin-only. It runs LLM parsing over everything the sources return, so an
    open endpoint was a free lever for anyone to drain the project's Groq
    quota, pin the container, and get the deployment's IP blocked by the
    boards being scraped.

    Runs synchronously and can take minutes on a large role list; prefer the
    script for a first bulk load of an empty deployment.
    """
    try:
        # ``RealtimeScraperEngine.search_all`` is the *live search* path: it fans
        # scrapers out and caches the result in Redis for the search UI, and
        # never writes to Postgres. Pointing the trigger at it meant an admin
        # got "discovered N jobs" while the ``jobs`` table stayed empty, which
        # is what left the deployed Find Roles page blank. ``dispatch_ingestion``
        # is the parse → persist → embed path, and the only one that populates
        # the database the search endpoints read from.
        from ingestion.dispatcher import dispatch_ingestion

        result = await dispatch_ingestion(
            roles=request.roles,
            locations=request.locations,
            max_results_per_query=request.max_results_per_query,
        )

        inserted = result.get("inserted", 0)
        updated = result.get("updated", 0)
        embedded = result.get("embedded", 0)
        fetched = result.get("total_fetched", 0)

        return IngestResponseSchema(
            success=True,
            message=(
                f"Ingestion complete: fetched {fetched}, "
                f"inserted {inserted}, updated {updated}, embedded {embedded}."
            ),
            dag_run_id=str(result.get("run_id", "")),
            estimated_time=None,
        )

    except Exception as exc:
        logger.error("Failed to trigger ingestion: %s", exc, exc_info=True)
        return IngestResponseSchema(
            success=False,
            message="Ingestion could not be started. The error has been logged.",
        )


@router.get("/runs", dependencies=[Depends(get_current_user)])
async def get_ingestion_runs(
    limit: int = 20,
    offset: int = 0,
    uow: Any = Depends(get_unit_of_work),
):
    """Get recent ingestion runs with status."""
    runs = await uow.ingestion_runs.list(
        limit=limit,
        offset=offset,
    )

    return {
        "runs": [
            {
                "id": str(run.id),
                "source": run.source,
                "status": run.status.value if run.status else "unknown",
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "jobs_discovered": run.jobs_discovered,
                "jobs_inserted": run.jobs_inserted,
                "jobs_updated": run.jobs_updated,
                "jobs_skipped": run.jobs_skipped,
            }
            for run in runs
        ],
        "total": len(runs),
    }


@router.get("/runs/{run_id}", dependencies=[Depends(get_current_user)])
async def get_ingestion_run_detail(
    run_id: str,
    uow: Any = Depends(get_unit_of_work),
):
    """Get details for a specific ingestion run."""
    run = await uow.ingestion_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Ingestion run not found")

    return {
        "id": str(run.id),
        "source": run.source,
        "status": run.status.value if run.status else "unknown",
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "jobs_discovered": run.jobs_discovered,
        "jobs_inserted": run.jobs_inserted,
        "jobs_updated": run.jobs_updated,
        "jobs_skipped": run.jobs_skipped,
        "error_message": run.error_message,
        "run_config": run.run_config,
    }


async def _job_board_counts() -> dict[str, Any]:
    """
    Job boards by status and by ATS, from two GROUP BYs on ``job_boards``.

    A failure answers ``{}`` and is logged by class name: the rest of the health report must
    stand without it.
    """
    from sqlalchemy import func, select

    from storage.database import AsyncSessionLocal
    from storage.models import JobBoard

    try:
        async with AsyncSessionLocal() as session:
            by_status = await session.execute(
                select(JobBoard.status, func.count()).group_by(JobBoard.status)
            )
            by_ats = await session.execute(
                select(JobBoard.ats, func.count()).group_by(JobBoard.ats)
            )
            return {
                "by_status": {str(status): int(count) for status, count in by_status.all()},
                "by_ats": {str(ats): int(count) for ats, count in by_ats.all()},
            }
    except Exception as exc:
        logger.warning("Job board counts unavailable (%s)", type(exc).__name__)
        return {}


@router.get("/sources/health", dependencies=[Depends(require_role("admin"))])
async def get_source_health() -> dict[str, Any]:
    """
    Live health of every registered job source.

    This is the half of the verification system that fixture tests cannot
    provide. A contract test replays a response captured before a site
    changed, so it keeps passing; only real traffic reveals that a board now
    returns a 200 that parses to nothing. Three consecutive zero-yield runs
    show up here as ``degraded``, repeated failures as ``failing`` with the
    circuit breaker open.

    Admin-only: it names the sources being scraped and their failure modes.
    """
    from config.settings import get_settings
    from ingestion.sources.registry import default_live_sources, live_source_registry
    from services.source_health import SourceHealthService

    registered = live_source_registry()
    active = {s.name for s in default_live_sources(
        enable_stealth=get_settings().enable_stealth_scrapers
    )}

    report = await SourceHealthService.report([s.name for s in registered])
    by_name = {entry["name"]: entry for entry in report}

    sources = []
    for source in registered:
        entry = dict(by_name.get(source.name, {"name": source.name, "status": "unknown"}))
        entry["tier"] = source.tier
        entry["timeout_seconds"] = source.timeout_seconds
        entry["requires_browser"] = source.requires_browser
        # A browser-only source on this deployment is excluded by design, not
        # broken - worth distinguishing, or every report looks half-failed.
        entry["in_active_roster"] = source.name in active
        sources.append(entry)

    degraded = [s["name"] for s in sources if s["in_active_roster"] and s.get("status") == "degraded"]
    failing = [s["name"] for s in sources if s["in_active_roster"] and s.get("status") == "failing"]

    return {
        "sources": sources,
        "active_count": len(active),
        "registered_count": len(registered),
        "degraded": degraded,
        "failing": failing,
        "healthy": not degraded and not failing,
        "job_boards": await _job_board_counts(),
    }


@router.post("/sources/{source_name}/reset", dependencies=[Depends(require_role("admin"))])
async def reset_source_health(source_name: str) -> dict[str, Any]:
    """
    Clear a source's counters and close its circuit breaker.

    The recovery action for a source that was blocked or broken and has since
    been fixed: without it, a tripped breaker only clears on its own cooldown.
    """
    from ingestion.sources.registry import get_live_source
    from services.source_health import SourceHealthService

    try:
        get_live_source(source_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await SourceHealthService.reset(source_name)
    return {"success": True, "source": source_name, "message": "Health counters cleared."}


@router.post("/reverify", dependencies=[Depends(require_cron_or_admin)])
async def reverify_postings(limit: int = Query(default=150, ge=1, le=500)) -> dict[str, int]:
    """
    Re-check stored ATS postings against the employer's careers API.

    Called on a schedule by ``.github/workflows/reverify.yml`` - there is no
    worker process. Each run refreshes ``last_verified_at`` on postings still
    served and sets ``closed_at`` on those that are gone, which is the
    evidence the liveness verdict is built from.
    """
    from services import liveness

    return await liveness.reverify_batch(limit=limit)


RefreshStep = Literal[
    "wikidata", "osm", "yc", "hiring_list", "github_orgs", "sites", "geocode", "summaries", "github",
    "tech",
]


@router.post("/company-map/refresh", dependencies=[Depends(require_cron_or_admin)])
async def refresh_company_map(
    step: RefreshStep, limit: int = Query(default=50, ge=1, le=5000)
) -> dict[str, int | str]:
    """
    Run one bounded company-map refresh step.

    Called by ``.github/workflows/company-map.yml`` - there is no worker.
    ``run_step`` caps ``limit`` per step (Nominatim's 1 req/s, the daily
    summary budget) and never raises: a failed step comes back as
    ``{"error": ...}`` and the rows keep their last good data.
    """
    from services.company_map import refresh

    return await refresh.run_step(step, limit)


RefreshEventsStep = Literal["luma", "gdg", "eventbrite", "link", "relevance", "prune", "purge"]


async def _purge_needs_an_admin(
    step: RefreshEventsStep,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
) -> None:
    """``purge`` deletes stored rows (spec 10: admin only), so the cron secret is not enough."""
    if step == "purge":
        require_admin(credentials)


@router.post(
    "/events/refresh",
    dependencies=[Depends(require_cron_or_admin), Depends(_purge_needs_an_admin)],
)
async def refresh_events(
    step: RefreshEventsStep,
    limit: int = Query(default=30, ge=1, le=2000),
    source: str | None = Query(default=None, max_length=32),
) -> dict[str, int | str]:
    """
    Run one bounded Events refresh step (Luma, GDG, Eventbrite, link, relevance, prune).

    Called twice a day by ``.github/workflows/events.yml`` - there is no worker.
    ``run_step`` caps ``limit`` per step (each source's polite rate) and never raises:
    a failed step comes back as ``{"error": ...}``. ``source`` is read by ``purge`` only,
    which needs ``source=eventbrite`` and a signed-in admin: the cron token is refused.
    """
    from services.events import refresh

    return await refresh.run_step(step, limit, source=source)


@router.post("/news", dependencies=[Depends(require_cron_or_admin)])
async def ingest_startup_news() -> dict[str, int | str]:
    """
    Fetch the startup-news feeds and let funding move the company map.

    Called every 3 h by ``.github/workflows/news.yml``. Never raises.
    """
    from services.company_map import news_ingest

    return await news_ingest.ingest_news()


@router.post("/outreach-issues", dependencies=[Depends(require_cron_or_admin)])
async def refresh_outreach_issues(
    limit: int = Query(default=20, ge=1, le=60),
) -> dict[str, int | str | None]:
    """
    Check a bounded number of companies for good-first-issues on GitHub.

    Called daily by ``.github/workflows/outreach.yml``. This is the only thing
    that searches GitHub for issues: a page load reads the stored snapshots. The step
    spaces its requests to GitHub's limits, caps ``limit`` at 20 without a
    ``GITHUB_TOKEN``, never raises (a failure comes back as ``{"error": ...}``)
    and says in ``stopped`` why a run ended early: ``budget``, ``rate_limited``,
    ``auth`` (a rejected token) or ``errors`` (GitHub unreachable).
    """
    from services.outreach import oss

    return await oss.refresh_issues(limit=limit)


PracticeStep = Literal["challenges", "judge"]


@router.post("/practice", dependencies=[Depends(require_cron_or_admin)])
async def run_practice_step(
    step: PracticeStep, limit: int = Query(default=4, ge=1, le=10)
) -> dict[str, Any]:
    """
    Run one Practice step: ``challenges`` (editions, statuses, retention) or ``judge``.

    Called every two hours by ``.github/workflows/practice.yml`` - there is no worker.
    ``limit`` is how many attempts ``judge`` may take (it applies its own, lower ceiling);
    ``challenges`` ignores it. ``run_step`` never raises: a failure comes back as
    ``{"error": ...}`` and whatever the step finished stays committed.
    """
    from services.practice import steps

    return await steps.run_step(step, limit)


JobBoardStep = Literal["open_list", "yc", "probe", "sweep", "prune"]


@router.post("/job-boards", dependencies=[Depends(require_cron_or_admin)])
async def run_job_board_step(
    step: JobBoardStep, limit: int = Query(default=300, ge=1, le=20000)
) -> dict[str, int | str]:
    """
    Run one job-board roster step: ``open_list``, ``yc``, ``probe``, ``sweep`` or ``prune``.

    Called by ``.github/workflows/job-boards.yml`` - there is no worker. ``limit`` is how many
    boards or rows the step may take; ``run_step`` clamps it to the step's own ceiling (the sweep
    to 600, the probe to 60, and so on) and never raises: a failure comes back as
    ``{"step": ..., "error": ...}`` and the rows the step finished stay committed.
    """
    from services.job_boards import steps

    return await steps.run_step(step, limit)
