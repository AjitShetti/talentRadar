"""
services/job_matching.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Daily job matches: search the user's target roles (Profile.target_roles)
against already-ingested postings and cache up to 3 picks per day.

    compute_daily_matches_for_user()   — search + persist today's top 3 for one user
    get_daily_matches_for_user()       — read today's cached top 3 (None if not run yet)
    run_daily_matching_for_all_users() — batch entry point for the daily scheduler
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.base import parse_uuid
from services.jobs import search_jobs
from services.profiles import get_profile
from storage.database import AsyncSessionLocal
from storage.models import DailyJobMatch, Job, Profile

logger = logging.getLogger(__name__)

#: How many candidates to pull per target role before ranking/deduping.
_SEARCH_LIMIT_PER_ROLE = 5
#: How many matches surface on the dashboard.
_TOP_N = 3
#: Cap on how many of a user's target roles get searched — keeps the daily
#: batch bounded even if someone lists many roles.
_MAX_ROLES_SEARCHED = 3


def _serialise(job: Job, matched_role: str) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "title": job.title,
        "company": job.company.name if job.company else "Unknown",
        "location": job.location_raw or f"{job.city or ''}, {job.country or ''}".strip(", "),
        "is_remote": job.is_remote,
        "skills": list(job.skills or []),
        "salary_raw": job.salary_raw,
        "source_url": job.source_url,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "matched_role": matched_role,
    }


async def compute_daily_matches_for_user(
    *, user_id: str, session: AsyncSession | None = None
) -> list[dict[str, Any]]:
    """
    Search the user's target roles against already-ingested jobs, cache the
    top 3 as today's ``daily_job_matches`` rows, and return them serialised.

    Returns ``[]`` (and writes nothing) when the profile has no target roles.
    """
    profile = await get_profile(user_id=user_id)
    target_roles = [r.strip() for r in (profile or {}).get("target_roles", []) if r and r.strip()]
    if not target_roles:
        return []

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return []

    pooled: dict[str, dict[str, Any]] = {}
    for role in target_roles[:_MAX_ROLES_SEARCHED]:
        try:
            result = await search_jobs(query=role, limit=_SEARCH_LIMIT_PER_ROLE)
        except Exception:
            logger.warning(
                "Daily match search failed for role %r (user %s)", role, user_id, exc_info=True
            )
            continue
        for job in result.get("jobs", []):
            job_id = job.get("id")
            if job_id and job_id not in pooled:
                pooled[job_id] = {**job, "matched_role": role}

    ranked = sorted(pooled.values(), key=lambda j: j.get("posted_at") or "", reverse=True)[:_TOP_N]

    own_session = session is None
    session = session or AsyncSessionLocal()
    try:
        today = date.today()
        await session.execute(
            delete(DailyJobMatch).where(
                DailyJobMatch.user_id == user_uuid,
                DailyJobMatch.match_date == today,
            )
        )
        for rank, job in enumerate(ranked):
            job_uuid = parse_uuid(job["id"])
            if not job_uuid:
                continue
            session.add(
                DailyJobMatch(
                    user_id=user_uuid,
                    job_id=job_uuid,
                    matched_role=job["matched_role"],
                    match_date=today,
                    rank=rank,
                )
            )
        await session.commit()
    finally:
        if own_session:
            await session.close()

    return ranked


async def get_daily_matches_for_user(
    *, user_id: str, session: AsyncSession | None = None
) -> list[dict[str, Any]] | None:
    """
    Read today's cached matches for a user.

    Returns ``None`` when nothing has been computed for today yet (lets the
    caller distinguish "not run" from "ran, found zero"), or ``[]`` when a
    prior run found no matches.
    """
    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return None

    own_session = session is None
    session = session or AsyncSessionLocal()
    try:
        today = date.today()
        rows = (
            (
                await session.execute(
                    select(DailyJobMatch)
                    .where(DailyJobMatch.user_id == user_uuid, DailyJobMatch.match_date == today)
                    .options(selectinload(DailyJobMatch.job).selectinload(Job.company))
                    .order_by(DailyJobMatch.rank)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        return [_serialise(row.job, row.matched_role) for row in rows if row.job]
    finally:
        if own_session:
            await session.close()


async def run_daily_matching_for_all_users() -> None:
    """
    Scheduler entry point: compute today's matches for every user with a
    completed profile. One user's failure never stops the batch.
    """
    session = AsyncSessionLocal()
    try:
        user_ids = (
            (
                await session.execute(
                    select(Profile.user_id).where(Profile.onboarding_completed.is_(True))
                )
            )
            .scalars()
            .all()
        )
    finally:
        await session.close()

    logger.info("Daily job matching: computing for %d users", len(user_ids))
    matched = 0
    for user_id in user_ids:
        try:
            results = await compute_daily_matches_for_user(user_id=str(user_id))
            if results:
                matched += 1
        except Exception:
            logger.warning("Daily job matching failed for user %s", user_id, exc_info=True)
    logger.info("Daily job matching: %d/%d users got matches", matched, len(user_ids))
