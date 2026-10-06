"""
services/dossier.py
~~~~~~~~~~~~~~~~~~~
The Role Dossier: everything TalentRadar knows about one role, for one person.

A dossier answers four questions, each from a part of the system that already
existed before this module:

    Is it open?        the liveness verdict            services/liveness.py
    Is it for me?      resume against the role's skills services/resumes.py
    What's my way in?  the company's known contacts    services/company_contacts.py
    Am I ready?        prep sessions run for this role interview_sessions

Nothing here calls a model. The page a job seeker opens most often must not
spend quota to render, and must not be slow because a provider is.

Each section is loaded on its own and may be absent. The four answers come
from four unrelated subsystems; one of them being down, or simply having
nothing to say, is ordinary and must never cost the reader the other three.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from domain.platforms import platform_of
from services import liveness
from services.base import parse_uuid
from services.company_contacts import list_contacts
from services.resumes import _extract_skills, get_active_resume

logger = logging.getLogger(__name__)

# How long an on-demand re-check may hold up the page before it is abandoned.
MAX_DESCRIPTION_CHARS = 6000


# ── the role ─────────────────────────────────────────────────────────────────


async def _load_role(job_id: uuid.UUID) -> dict[str, Any] | None:
    """
    The role and its liveness verdict, or ``None`` when there is no such job.

    Opening a dossier is the moment someone is deciding whether to apply, so
    if the last confirmation is stale the posting is re-checked now - once,
    bounded - rather than showing a verdict that may no longer be true.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from storage.database import AsyncSessionLocal
    from storage.models import Job

    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(
                select(Job).where(Job.id == job_id).options(selectinload(Job.company))
            )
        ).scalar_one_or_none()
        if job is None:
            return None

        await liveness.refresh_if_due(session, job)

        sightings = await liveness.sightings_for(session, [(job.company_id, job.title)])
        sighting = next(iter(sightings.values()), None)
        verdict = liveness.verdict_for(job, sighting, now=datetime.now(UTC))
        platform = platform_of(job.source, job.source_url)
        company = job.company

        return {
            "id": str(job.id),
            "title": job.title,
            "company_id": str(job.company_id) if job.company_id else None,
            "company": company.name if company else None,
            "location": job.location_raw or job.city,
            "is_remote": bool(job.is_remote),
            "seniority": job.seniority.value if job.seniority else None,
            "employment_type": job.employment_type.value if job.employment_type else None,
            "salary_raw": job.salary_raw,
            "skills": list(job.skills or []),
            "description": (job.description_clean or "")[:MAX_DESCRIPTION_CHARS],
            "source_url": job.source_url,
            "platform": platform.key if platform else None,
            "liveness": liveness.verdict_to_dict(verdict),
        }


# ── is it for me? ────────────────────────────────────────────────────────────


async def _fit(user_id: str, role: dict[str, Any]) -> dict[str, Any]:
    """
    The saved resume against the skills this role asks for.

    A set comparison, not a score from a model: the reader can see exactly
    which skills were found and which were not, and disagree with it.
    """
    resume = await get_active_resume(user_id=user_id)
    if not resume or not resume.get("extracted_text"):
        return {"available": False, "reason": "no_resume"}

    role_skills = {s.lower().strip() for s in role.get("skills") or [] if s and s.strip()}
    if not role_skills and role.get("description"):
        role_skills = _extract_skills(role["description"])
    if not role_skills:
        return {"available": False, "reason": "no_role_skills"}

    resume_skills = _extract_skills(resume["extracted_text"])
    matched = sorted(role_skills & resume_skills)
    return {
        "available": True,
        "matched_skills": matched,
        "missing_skills": sorted(role_skills - resume_skills),
        "coverage_percentage": round(len(matched) / len(role_skills) * 100, 1),
    }


# ── what's my way in? ────────────────────────────────────────────────────────


async def _way_in(user_id: str, role: dict[str, Any]) -> dict[str, Any]:
    """
    People and channels already on record for this company.

    Only what Company Intel holds - published contacts and the ones this user
    logged themselves. Nothing is inferred, and an empty list is a real
    answer: it tells the reader to go and find someone.
    """
    company_id = role.get("company_id")
    rows = await list_contacts(company_id, user_id) if company_id else []
    careers = next(
        (c.get("source_url") for c in rows if c.get("kind") == "careers_page" and c.get("source_url")),
        None,
    )
    return {
        "company_id": company_id,
        "careers_url": careers,
        "contacts": [c for c in rows if c.get("kind") != "careers_page"],
    }


# ── am I ready? ──────────────────────────────────────────────────────────────


async def _readiness(user_id: str, job_id: uuid.UUID) -> dict[str, Any]:
    """Prep sessions run for this role, and where it sits in the tracker."""
    from sqlalchemy import select

    from storage.database import AsyncSessionLocal
    from storage.models import InterviewSession, JobApplication

    user_uuid = parse_uuid(user_id)
    if user_uuid is None:
        return {"sessions": 0, "last_score": None, "last_at": None, "application": None}

    async with AsyncSessionLocal() as session:
        sessions = list(
            (
                await session.execute(
                    select(InterviewSession)
                    .where(
                        InterviewSession.user_id == user_uuid,
                        InterviewSession.job_id == job_id,
                    )
                    .order_by(InterviewSession.created_at.desc())
                    .limit(20)
                )
            ).scalars()
        )
        application = (
            await session.execute(
                select(JobApplication)
                .where(JobApplication.user_id == user_uuid, JobApplication.job_id == job_id)
                .order_by(JobApplication.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    scored = [s for s in sessions if s.total_score is not None]
    last = scored[0] if scored else None
    return {
        "sessions": len(sessions),
        "last_score": round(float(last.total_score), 1) if last and last.total_score is not None else None,
        "last_at": last.created_at.isoformat() if last and last.created_at else None,
        "application": (
            {"id": str(application.id), "status": application.status.value}
            if application is not None
            else None
        ),
    }


# ── composition ──────────────────────────────────────────────────────────────


async def _section(name: str, work: Any) -> Any:
    """Run one section; a failure is logged and becomes ``None``."""
    try:
        return await work
    except Exception as exc:
        logger.warning("Dossier section %r unavailable: %s", name, exc)
        return None


async def build_dossier(*, job_id: str, user_id: str) -> dict[str, Any] | None:
    """The dossier for one role, or ``None`` when the role does not exist."""
    job_uuid = parse_uuid(job_id)
    if job_uuid is None:
        return None

    role = await _load_role(job_uuid)
    if role is None:
        return None
    verdict = role.pop("liveness", None)

    fit, way_in, readiness = await asyncio.gather(
        _section("fit", _fit(user_id, role)),
        _section("way_in", _way_in(user_id, role)),
        _section("readiness", _readiness(user_id, job_uuid)),
    )
    return {
        "role": role,
        "liveness": verdict,
        "fit": fit,
        "way_in": way_in,
        "readiness": readiness,
    }
