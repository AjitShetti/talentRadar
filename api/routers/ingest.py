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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user, require_role
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
