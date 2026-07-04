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

from api.dependencies import get_unit_of_work
from api.schemas.query_schemas import IngestRequestSchema, IngestResponseSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["Ingestion"])


@router.post("/trigger", response_model=IngestResponseSchema)
async def trigger_ingestion(
    request: IngestRequestSchema,
    uow: Any = Depends(get_unit_of_work),
):
    """
    Trigger the ingestion pipeline via Celery background tasks.

    This starts the fetch -> parse -> save -> embed pipeline
    with the specified roles and locations.
    """
    try:
        from ingestion.tasks import run_crawler
        
        task = run_crawler.delay(
            roles=request.roles,
            locations=request.locations,
            max_results_per_query=request.max_results_per_query,
        )

        return IngestResponseSchema(
            success=True,
            message="Ingestion pipeline triggered successfully",
            dag_run_id=task.id,
            estimated_time="Background task running",
        )

    except Exception as exc:
        logger.error("Failed to trigger ingestion: %s", exc, exc_info=True)
        return IngestResponseSchema(
            success=False,
            message=f"Failed to trigger ingestion: {str(exc)}",
        )


@router.get("/runs")
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


@router.get("/runs/{run_id}")
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
