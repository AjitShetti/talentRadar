"""
services/companies.py
~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for Company Intelligence.

    get_company()      — company record + open jobs + aggregate salary data
    company_intel()    — full intelligence report (tech stack, hiring trends,
                         interview patterns, salaries) from company_profiles
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from storage.database import AsyncSessionLocal
from storage.models import Company, CompanyProfile, Job, JobStatus
from storage.repository import UnitOfWork
from services.base import parse_uuid

logger = logging.getLogger(__name__)


async def get_company(
    company_id: str | None = None,
    name: str | None = None,
    *,
    with_jobs: bool = True,
    job_limit: int = 20,
) -> dict[str, Any] | None:
    """
    Fetch a company by id or (fuzzy) name, optionally with its open jobs
    and a salary aggregate computed from those jobs.

    Returns ``None`` when not found.
    """
    session = AsyncSessionLocal()
    try:
        uow = UnitOfWork(session)
        company: Company | None = None
        if company_id and parse_uuid(company_id):
            company = await uow.companies.get(parse_uuid(company_id))
        elif name:
            company, _ = await uow.companies.search(name=name, limit=1)

        if company is None:
            return None

        jobs: list[Job] = []
        if with_jobs:
            jobs, _ = await uow.jobs.list_by_company(
                company.id, status=JobStatus.ACTIVE, limit=job_limit
            )

        # Salary aggregate from active jobs
        stmt = select(
            func.min(Job.salary_min),
            func.max(Job.salary_max),
            func.avg(Job.salary_min),
            func.count(Job.id),
        ).where(
            Job.company_id == company.id,
            Job.status == JobStatus.ACTIVE,
            Job.salary_min.is_not(None),
        )
        row = (await session.execute(stmt)).one()
        await session.commit()

        return {
            "id": str(company.id),
            "name": company.name,
            "domain": company.domain,
            "website_url": company.website_url,
            "industry": company.industry,
            "hq_country": company.hq_country,
            "hq_city": company.hq_city,
            "employee_count_range": company.employee_count_range,
            "founded_year": company.founded_year,
            "open_jobs_count": len(jobs),
            "salary_stats": {
                "min": row[0],
                "max": row[1],
                "avg": float(row[2]) if row[2] else None,
                "count_with_salary": row[3] or 0,
            },
            "jobs": [
                {
                    "id": str(j.id),
                    "title": j.title,
                    "location": j.location_raw,
                    "is_remote": j.is_remote,
                    "seniority": j.seniority.value if j.seniority else None,
                    "salary_raw": j.salary_raw,
                    "skills": list(j.skills or []),
                    "source_url": j.source_url,
                    "posted_at": j.posted_at.isoformat() if j.posted_at else None,
                }
                for j in jobs
            ],
        }
    finally:
        await session.close()


async def company_intel(
    company_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    """
    Full company intelligence report from the ``company_profiles`` table.

    Includes tech stack, salary ranges, interview patterns, hiring trends,
    plus a fallback to computed job aggregates when no profile exists yet.
    """
    session = AsyncSessionLocal()
    try:
        uow = UnitOfWork(session)
        company: Company | None = None
        if company_id and parse_uuid(company_id):
            company = await uow.companies.get(parse_uuid(company_id))
        elif name:
            company, _ = await uow.companies.search(name=name, limit=1)
        if company is None:
            return None

        profile = await _get_profile(session, company.id)
        base = {
            "company_id": str(company.id),
            "name": company.name,
            "industry": company.industry,
            "hq_city": company.hq_city,
            "hq_country": company.hq_country,
        }

        if profile:
            await session.commit()
            return {
                **base,
                "tech_stack": list(profile.tech_stack or []),
                "salary_ranges": profile.salary_ranges or {},
                "interview_patterns": profile.interview_patterns or {},
                "hiring_trends": profile.hiring_trends or {},
                "culture_summary": profile.culture_summary,
                "source": profile.source,
                "has_profile": True,
            }

        # Fallback: compute from live job data
        jobs, _ = await uow.jobs.list_by_company(
            company.id, status=JobStatus.ACTIVE, limit=100
        )
        await session.commit()
        skills: dict[str, int] = {}
        for job in jobs:
            for skill in job.skills or []:
                skills[skill] = skills.get(skill, 0) + 1
        top_skills = sorted(skills.items(), key=lambda kv: kv[1], reverse=True)[:15]

        return {
            **base,
            "tech_stack": [s for s, _ in top_skills],
            "salary_ranges": {
                "min": min((j.salary_min for j in jobs if j.salary_min), default=None),
                "max": max((j.salary_max for j in jobs if j.salary_max), default=None),
            },
            "interview_patterns": {},
            "hiring_trends": {"open_jobs": len(jobs)},
            "culture_summary": None,
            "source": "computed",
            "has_profile": False,
        }
    finally:
        await session.close()


async def _get_profile(session, company_id: Any) -> CompanyProfile | None:
    stmt = (
        select(CompanyProfile)
        .where(CompanyProfile.company_id == company_id)
        .options(selectinload(CompanyProfile.company))
    )
    return (await session.execute(stmt)).scalar_one_or_none()
