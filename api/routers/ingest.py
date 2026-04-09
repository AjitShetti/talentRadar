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
    Trigger the ingestion pipeline via Airflow REST API.

    This starts the fetch -> parse -> save -> embed pipeline
    with the specified roles and locations.

    Note: Requires Airflow webserver to be running and accessible.
    """
    try:
        # Trigger Airflow DAG via its REST API
        import httpx

        airflow_url = "http://airflow-webserver:8080/api/v1/dags/talentradar_fetch_and_parse/dagRuns"
        auth = ("admin", "admin")  # Should be configurable

        payload = {
            "conf": {
                "roles": request.roles,
                "locations": request.locations,
                "max_results_per_query": request.max_results_per_query,
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                airflow_url,
                json=payload,
                auth=auth,
                timeout=10.0,
            )

        if response.status_code in (200, 201):
            dag_run = response.json()
            return IngestResponseSchema(
                success=True,
                message="Ingestion pipeline triggered successfully",
                dag_run_id=dag_run.get("dag_run_id"),
                estimated_time="5-15 minutes depending on result count",
            )
        else:
            logger.error("Airflow trigger failed: %s", response.text)
            return IngestResponseSchema(
                success=False,
                message=f"Airflow trigger failed: {response.status_code}",
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
