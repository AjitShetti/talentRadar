"""
services/market.py
~~~~~~~~~~~~~~~~~~
Deterministic tools for Market Trends.

    get_market_trends() — aggregate skill demand / salary / location /
                          seniority distribution + optional LLM summary
    record_market_snapshot() — persist a snapshot to market_snapshots
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from storage.database import AsyncSessionLocal
from storage.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def get_market_trends(
    query: str = "Market trends",
    days: int = 30,
    *,
    with_summary: bool = True,
) -> dict[str, Any]:
    """
    Aggregate the job market over the last ``days``:

        - total active jobs
        - top skills (count)
        - salary stats
        - location distribution
        - seniority distribution

    Returns a serialisable payload plus an optional LLM-generated summary.
    """
    from services.llm import generate_market_summary  # lazy import

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    session = AsyncSessionLocal()
    try:
        total_jobs = await _count_active_jobs(session, cutoff)
        top_skills = await _get_top_skills(session, cutoff, limit=15)
        salary_data = await _get_salary_data(session, cutoff)
        location_data = await _get_location_distribution(session, cutoff)
        seniority_data = await _get_seniority_distribution(session, cutoff)

        summary: str | None = None
        if with_summary:
            summary = await generate_market_summary(
                query=query,
                total_jobs=total_jobs,
                top_skills=top_skills,
                salary_data=salary_data,
                location_data=location_data,
                seniority_data=seniority_data,
            )

        return {
            "success": True,
            "summary": summary,
            "period_days": days,
            "data": {
                "total_jobs": total_jobs,
                "top_skills": top_skills,
                "salary": salary_data,
                "locations": location_data,
                "seniority": seniority_data,
            },
        }
    except Exception as exc:  # noqa: BLE001 — service boundary
        logger.error("Market trend aggregation failed: %s", exc, exc_info=True)
        return {"success": False, "error": str(exc), "data": {}}
    finally:
        await session.close()


async def record_market_snapshot(
    *, scope: str = "all", data: dict[str, Any] | None = None
) -> MarketSnapshot | None:
    """Persist the current market aggregate to ``market_snapshots``."""
    from storage.repository import UnitOfWork

    trends = await get_market_trends(query="snapshot", days=30, with_summary=False)
    if not trends.get("success"):
        return None

    session = AsyncSessionLocal()
    try:
        async with UnitOfWork(session):
            snap = MarketSnapshot(
                snapshot_date=datetime.now(tz=timezone.utc),
                scope=scope,
                data=data or trends.get("data"),
            )
            session.add(snap)
            await session.flush()
            return snap
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Internal SQL aggregations
# ---------------------------------------------------------------------------

async def _count_active_jobs(session, cutoff: datetime) -> int:
    stmt = text(
        """
        SELECT COUNT(id) FROM jobs
        WHERE status = 'active' AND COALESCE(posted_at, created_at) >= :cutoff
        """
    )
    result = await session.execute(stmt, {"cutoff": cutoff})
    return result.scalar() or 0


async def _get_top_skills(session, cutoff: datetime, limit: int = 15) -> list[dict[str, Any]]:
    stmt = text(
        """
        SELECT unnest(jobs.skills) AS skill, COUNT(*) AS count
        FROM jobs
        WHERE jobs.status = 'active'
          AND COALESCE(jobs.posted_at, jobs.created_at) >= :cutoff
          AND jobs.skills IS NOT NULL
        GROUP BY skill
        ORDER BY count DESC
        LIMIT :limit
        """
    )
    result = await session.execute(stmt, {"cutoff": cutoff, "limit": limit})
    return [{"skill": row[0], "count": row[1]} for row in result.fetchall()]


async def _get_salary_data(session, cutoff: datetime) -> dict[str, Any]:
    stmt = text(
        """
        SELECT
            AVG(salary_min) AS avg_min,
            AVG(salary_max) AS avg_max,
            MIN(salary_min) AS min,
            MAX(salary_max) AS max,
            COUNT(id) AS count_with_salary
        FROM jobs
        WHERE status = 'active'
          AND COALESCE(posted_at, created_at) >= :cutoff
          AND salary_min IS NOT NULL
        """
    )
    row = (await session.execute(stmt, {"cutoff": cutoff})).first()
    if not row or not row.count_with_salary:
        return {"available": False}
    return {
        "available": True,
        "avg_min": float(row.avg_min) if row.avg_min else None,
        "avg_max": float(row.avg_max) if row.avg_max else None,
        "min": float(row.min) if row.min else None,
        "max": float(row.max) if row.max else None,
        "count": row.count_with_salary,
    }


async def _get_location_distribution(session, cutoff: datetime) -> list[dict[str, Any]]:
    stmt = text(
        """
        SELECT
            CASE WHEN is_remote THEN 'Remote' ELSE COALESCE(city, country, 'Unknown') END AS location,
            COUNT(*) AS count
        FROM jobs
        WHERE status = 'active'
          AND COALESCE(posted_at, created_at) >= :cutoff
        GROUP BY location
        ORDER BY count DESC
        LIMIT 10
        """
    )
    result = await session.execute(stmt, {"cutoff": cutoff})
    return [{"location": row[0], "count": row[1]} for row in result.fetchall()]


async def _get_seniority_distribution(session, cutoff: datetime) -> list[dict[str, Any]]:
    stmt = text(
        """
        SELECT seniority, COUNT(id) AS cnt
        FROM jobs
        WHERE status = 'active'
          AND COALESCE(posted_at, created_at) >= :cutoff
        GROUP BY seniority
        ORDER BY cnt DESC
        """
    )
    result = await session.execute(stmt, {"cutoff": cutoff})
    return [{"seniority": row[0] or "unspecified", "count": row[1]} for row in result.fetchall()]
