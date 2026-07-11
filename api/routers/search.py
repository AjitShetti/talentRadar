"""
api/routers/search.py
~~~~~~~~~~~~~~~~~~~~~
Job search endpoints.

Provides:
- Structured search with filters
- Natural language search via RAG agent
- Individual job detail retrieval
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from agents.orchestrator import Orchestrator
from api.dependencies import get_unit_of_work, get_job_repository
from api.schemas.job_schemas import (
    JobDetailResponseSchema,
    JobFilterSchema,
    JobListResponseSchema,
    JobResponseSchema,
    SearchRequestSchema,
    SearchResponseSchema,
)
from storage.repository import JobRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])


@router.post("/structured", response_model=JobListResponseSchema)
async def search_jobs_structured(
    filters: JobFilterSchema,
    uow: Any = Depends(get_unit_of_work),
):
    """
    Structured job search with filters.

    Supports filtering by:
    - Skills (array containment)
    - Location (country, city, remote)
    - Seniority and employment type
    - Salary range
    - Date range
    """
    from storage.models import JobStatus, SeniorityLevel, EmploymentType
    
    status_enum = JobStatus.ACTIVE
    if filters.status:
        try:
            status_enum = JobStatus(filters.status.upper())
        except ValueError:
            pass
            
    seniority_enum = None
    if filters.seniority:
        try:
            seniority_enum = SeniorityLevel(filters.seniority.upper().replace("-", "_"))
        except ValueError:
            pass

    employment_type_enum = None
    if filters.employment_type:
        try:
            employment_type_enum = EmploymentType(filters.employment_type.upper().replace("-", "_"))
        except ValueError:
            pass

    jobs, total = await uow.jobs.search(
        title=filters.query,
        skills=filters.skills,
        country=filters.country,
        city=filters.city,
        is_remote=filters.is_remote,
        seniority=seniority_enum,
        employment_type=employment_type_enum,
        salary_min_gte=filters.salary_min,
        salary_max_lte=filters.salary_max,
        posted_after=filters.posted_after,
        status=status_enum,
        limit=filters.limit,
        offset=filters.offset,
    )

    job_responses = [
        JobResponseSchema(
            id=str(job.id),
            title=job.title,
            company_id=str(job.company_id),
            source=job.source,
            source_url=job.source_url,
            location_raw=job.location_raw,
            country=job.country,
            city=job.city,
            is_remote=job.is_remote,
            seniority=job.seniority.value if job.seniority else None,
            employment_type=job.employment_type.value if job.employment_type else None,
            salary_raw=job.salary_raw,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            skills=job.skills or [],
            tags=job.tags or [],
            description_clean=job.description_clean,
            posted_at=job.posted_at,
            created_at=job.created_at,
            embedding_id=job.embedding_id,
        )
        for job in jobs
    ]

    return JobListResponseSchema(
        jobs=job_responses,
        total=total,
        limit=filters.limit,
        offset=filters.offset,
        has_more=filters.offset + len(jobs) < total,
    )


@router.post("/semantic", response_model=SearchResponseSchema)
async def search_jobs_semantic(request: SearchRequestSchema):
    """
    Natural language semantic search using the RAG agent.

    Examples:
    - "Find remote Python engineer jobs"
    - "Senior ML engineer positions in San Francisco"
    - "Entry-level data science roles"
    """
    orchestrator = Orchestrator()
    response = await orchestrator.process_query(
        query=request.query,
        limit=request.limit,
        offset=request.offset,
    )

    # Convert agent results to response schema.
    # RetrievalResult only carries the fields returned from ChromaDB metadata +
    # DB lookup — fields not available there (company_id, created_at) are left
    # as None via the now-optional schema defaults.
    job_results = [
        JobResponseSchema(
            id=result.job_id,
            title=result.title,
            company=result.company,
            location_raw=result.location,
            is_remote=result.is_remote,
            seniority=result.seniority,
            skills=result.skills,
            source_url=result.source_url,
            match_score=result.score,
        )
        for result in response.results
    ]

    return SearchResponseSchema(
        results=job_results,
        total_found=response.metadata.get("total_found", len(response.results)),
        summary=response.summary,
        filters_applied={"query": request.query},
    )


@router.get("/{job_id}", response_model=JobDetailResponseSchema)
async def get_job_detail(
    job_id: str,
    job_repo: JobRepository = Depends(get_job_repository),
):
    """
    Get detailed information about a specific job.

    Includes:
    - Full job description
    - Company information
    - Similar jobs (via embedding similarity)
    """
    job = await job_repo.get(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")

    # Fetch company info
    company_name = None
    if job.company:
        company_name = job.company.name

    job_response = JobResponseSchema(
        id=str(job.id),
        title=job.title,
        company_id=str(job.company_id),
        company_name=company_name,
        source=job.source,
        source_url=job.source_url,
        location_raw=job.location_raw,
        country=job.country,
        city=job.city,
        is_remote=job.is_remote,
        seniority=job.seniority.value if job.seniority else None,
        employment_type=job.employment_type.value if job.employment_type else None,
        salary_raw=job.salary_raw,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        skills=job.skills or [],
        tags=job.tags or [],
        description_clean=job.description_clean,
        posted_at=job.posted_at,
        created_at=job.created_at,
        embedding_id=job.embedding_id,
    )

    return JobDetailResponseSchema(job=job_response)


@router.post("/{job_id}/view")
async def increment_job_view(
    job_id: str,
    job_repo: JobRepository = Depends(get_job_repository),
):
    """Increment the view counter for a job."""
    job = await job_repo.get(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")

    await job_repo.increment_view(job.id)
    return {"success": True, "views": job.view_count + 1}
