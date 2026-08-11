"""
services/jobs.py
~~~~~~~~~~~~~~~~
Deterministic tools for the Job Engine.

Provides the tools that agents and the API BFF call:
    search_jobs()        — full-text + vector search over the jobs table
    calculate_match()    — resume↔job match score with breakdown
    rank_jobs_for_profile() — rank jobs by match to a candidate profile

These functions contain NO LLM orchestration — they are pure service
functions with clear inputs/outputs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agents.state import QueryContext
from storage.database import AsyncSessionLocal
from storage.models import JobStatus, Job
from storage.repository import UnitOfWork
from services.base import as_list, parse_uuid

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# search_jobs
# ---------------------------------------------------------------------------

async def search_jobs(
    *,
    query: str | None = None,
    skills: list[str] | None = None,
    location: str | None = None,
    is_remote: bool | None = None,
    seniority: str | None = None,
    employment_type: str | None = None,
    company: str | None = None,
    salary_min: float | None = None,
    salary_max: float | None = None,
    country: str | None = None,
    city: str | None = None,
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Deterministic job search across PostgreSQL (title/description/skills).

    Returns a serialisable payload::

        {
          "jobs": [ {id, title, company, location, is_remote, seniority,
                      skills, salary_raw, salary_min, salary_max,
                      salary_currency, source_url, posted_at, match_reason} ],
          "total": int,
          "limit": int,
          "offset": int,
        }
    """
    own_session = session is None
    session = session or AsyncSessionLocal()

    def _serialise(job: Job) -> dict[str, Any]:
        return {
            "id": str(job.id),
            "external_id": job.external_id,
            "title": job.title,
            "company": job.company.name if job.company else "Unknown",
            "company_id": str(job.company_id) if job.company_id else None,
            "location": job.location_raw or f"{job.city or ''}, {job.country or ''}".strip(", "),
            "city": job.city,
            "country": job.country,
            "is_remote": job.is_remote,
            "seniority": job.seniority.value if job.seniority else None,
            "employment_type": job.employment_type.value if job.employment_type else None,
            "skills": list(job.skills or []),
            "salary_raw": job.salary_raw,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "salary_currency": job.salary_currency,
            "source_url": job.source_url,
            "source": job.source,
            "posted_at": job.posted_at.isoformat() if job.posted_at else None,
            "match_reason": "Database query match",
        }

    try:
        async with UnitOfWork(session):
            jobs, total = await session_safe_search(
                session, query=query, skills=skills, location=location,
                is_remote=is_remote, seniority=seniority,
                employment_type=employment_type, company=company,
                salary_min=salary_min, salary_max=salary_max,
                country=country, city=city, limit=limit, offset=offset,
            )
        return {
            "jobs": [_serialise(j) for j in jobs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        if own_session:
            await session.close()


async def session_safe_search(
    session: AsyncSession,
    *,
    query: str | None = None,
    skills: list[str] | None = None,
    location: str | None = None,
    is_remote: bool | None = None,
    seniority: str | None = None,
    employment_type: str | None = None,
    company: str | None = None,
    salary_min: float | None = None,
    salary_max: float | None = None,
    country: str | None = None,
    city: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Job], int]:
    """Internal — wraps JobRepository.search with safe conversions."""
    from storage.models import EmploymentType, SeniorityLevel

    def _enum(val: str | None, enum_cls: type) -> Any | None:
        if not val:
            return None
        try:
            return enum_cls(val)
        except ValueError:
            return None

    uow = UnitOfWork(session)
    jobs, total = await uow.jobs.search(
        query=query,
        skills=skills or None,
        is_remote=is_remote,
        seniority=_enum(seniority, SeniorityLevel),
        employment_type=_enum(employment_type, EmploymentType),
        country=country,
        city=city,
        salary_min_gte=salary_min,
        salary_max_lte=salary_max,
        limit=limit,
        offset=offset,
        status=JobStatus.ACTIVE,
    )
    return list(jobs), total


# ---------------------------------------------------------------------------
# calculate_match — resume ↔ job description
# ---------------------------------------------------------------------------

async def calculate_match(
    resume_text: str,
    job_description: str,
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Compute a deterministic match score between a resume and a job
    description using the ML pipeline (skills / experience / education /
    semantic similarity).

    Returns::

        {
          "match_percentage": float,
          "breakdown": {skills, experience, education, semantic},
          "matched_skills": [...],
          "missing_skills": [...],
          "extra_skills": [...],
          "confidence": float,
          "processing_time_ms": int,
        }
    """
    import time

    from fastapi.concurrency import run_in_threadpool
    from ml.config import ScoringWeights
    from ml.resume_matcher import ResumeMatcher

    start = time.perf_counter()
    matcher = ResumeMatcher()
    if weights:
        matcher.config.weights = ScoringWeights(**weights)
    result = await run_in_threadpool(
        matcher.match,
        resume_text=resume_text,
        job_description=job_description,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    return {
        "match_percentage": result.overall_score,
        "breakdown": {
            "skills": result.breakdown.skills,
            "experience": result.breakdown.experience,
            "education": result.breakdown.education,
            "semantic": result.breakdown.semantic,
        },
        "matched_skills": list(result.matched_skills),
        "missing_skills": list(result.missing_skills),
        "extra_skills": list(result.extra_skills),
        "confidence": result.confidence,
        "processing_time_ms": elapsed_ms,
        "warnings": list(getattr(result, "warnings", []) or []),
    }


# ---------------------------------------------------------------------------
# rank_jobs_for_profile — match a candidate profile against many jobs
# ---------------------------------------------------------------------------

async def rank_jobs_for_profile(
    *,
    resume_text: str,
    job_ids: list[str],
    limit: int = 10,
) -> dict[str, Any]:
    """
    Rank a set of jobs by match to the candidate's resume text.

    Uses the ML matcher against each job's description and returns the
    best matches with their missing skills and match reasons — this is
    the "Discover" step of the job-seeker journey.
    """
    own = None
    session = AsyncSessionLocal()
    try:
        uow = UnitOfWork(session)
        ids = [u for u in (parse_uuid(j) for j in job_ids) if u]
        jobs = await uow.jobs.get_by_ids(ids)
        await session.commit()
    finally:
        await session.close()

    results: list[dict[str, Any]] = []
    for job in jobs:
        jd_text = job.description_clean or job.description_raw or ""
        combined = f"Job Title: {job.title}\n\n{jd_text}"
        if not combined.strip():
            continue
        match = await calculate_match(resume_text, combined)
        results.append({
            "job": {
                "id": str(job.id),
                "title": job.title,
                "company": job.company.name if job.company else "Unknown",
                "location": job.location_raw,
                "is_remote": job.is_remote,
                "salary_raw": job.salary_raw,
                "skills": list(job.skills or []),
                "source_url": job.source_url,
            },
            **match,
        })

    results.sort(key=lambda r: r["match_percentage"], reverse=True)
    return {"results": results[:limit], "total": len(results)}
