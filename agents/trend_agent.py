"""
agents/trend_agent.py
~~~~~~~~~~~~~~~~~~~~~
Market trend analysis agent.

Queries PostgreSQL for aggregated job market data and uses
Groq LLM to generate human-readable trend reports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from groq import AsyncGroq
from sqlalchemy import func, select, text

from agents.prompts.trend_prompt import TREND_ANALYSIS_PROMPT
from agents.state import AgentResponse, IntentType
from config.settings import get_settings
from storage.database import AsyncSessionLocal
from storage.models import Job, JobStatus

logger = logging.getLogger(__name__)


class TrendAgent:
    """
    Market trend analysis agent.

    Provides:
    - Skill demand trends (most requested skills)
    - Salary insights by role/location
    - Geographic distribution of jobs
    - Seniority level distribution
    - Overall market summary
    """

    def __init__(self):
        settings = get_settings()
        self._groq = AsyncGroq(api_key=settings.groq_api_key)

    async def get_market_trends(self, query: str, days: int = 30) -> AgentResponse:
        """
        Generate market trend report.

        Parameters
        ----------
        query : str
            User's trend query (e.g., "What skills are in demand for ML engineers?")
        days : int
            Lookback window in days.

        Returns
        -------
        AgentResponse
            Trend analysis with LLM-generated insights.
        """
        try:
            async with AsyncSessionLocal() as session:
                # 1. Total active jobs
                total_jobs = await self._count_active_jobs(session, days)

                # 2. Top skills
                top_skills = await self._get_top_skills(session, days, limit=15)

                # 3. Salary insights
                salary_data = await self._get_salary_data(session, days)

                # 4. Location distribution
                location_data = await self._get_location_distribution(session, days)

                # 5. Seniority distribution
                seniority_data = await self._get_seniority_distribution(session, days)

                # 6. Generate LLM summary
                summary = await self._generate_trend_summary(
                    query, total_jobs, top_skills, salary_data, location_data, seniority_data
                )

                return AgentResponse(
                    success=True,
                    intent=IntentType.MARKET_TRENDS,
                    summary=summary,
                    metadata={
                        "total_jobs": total_jobs,
                        "top_skills": top_skills,
                        "salary_data": salary_data,
                        "location_data": location_data,
                        "seniority_data": seniority_data,
                        "period_days": days,
                    },
                )

        except Exception as exc:
            logger.error("Trend analysis failed: %s", exc, exc_info=True)
            return AgentResponse(
                success=False,
                intent=IntentType.MARKET_TRENDS,
                error=str(exc),
            )

    @staticmethod
    async def _count_active_jobs(session, days: int) -> int:
        """Count active jobs in the last N days."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        stmt = (
            select(func.count(Job.id))
            .where(Job.status == JobStatus.ACTIVE)
            .where(Job.posted_at >= cutoff)
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

    @staticmethod
    async def _get_top_skills(session, days: int, limit: int = 10) -> list[dict[str, Any]]:
        """Get most frequently mentioned skills."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

        # Unnest the skills array and count
        stmt = text("""
            SELECT unnest(jobs.skills) AS skill, COUNT(*) AS count
            FROM jobs
            WHERE jobs.status = 'active'
              AND jobs.posted_at >= :cutoff
              AND jobs.skills IS NOT NULL
            GROUP BY skill
            ORDER BY count DESC
            LIMIT :limit
        """)
        result = await session.execute(stmt, {"cutoff": cutoff, "limit": limit})
        rows = result.fetchall()
        return [{"skill": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def _get_salary_data(session, days: int) -> dict[str, Any]:
        """Get aggregate salary statistics."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

        stmt = (
            select(
                func.avg(Job.salary_min).label("avg_min"),
                func.avg(Job.salary_max).label("avg_max"),
                func.min(Job.salary_min).label("min"),
                func.max(Job.salary_max).label("max"),
                func.count(Job.id).label("count_with_salary"),
            )
            .where(Job.status == JobStatus.ACTIVE)
            .where(Job.posted_at >= cutoff)
            .where(Job.salary_min.isnot(None))
        )
        result = await session.execute(stmt)
        row = result.first()

        if not row or row.count_with_salary == 0:
            return {"available": False}

        return {
            "available": True,
            "avg_min": float(row.avg_min) if row.avg_min else None,
            "avg_max": float(row.avg_max) if row.avg_max else None,
            "min": float(row.min) if row.min else None,
            "max": float(row.max) if row.max else None,
            "count": row.count_with_salary,
        }

    @staticmethod
    async def _get_location_distribution(session, days: int) -> list[dict[str, Any]]:
        """Get job distribution by location."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

        stmt = text("""
            SELECT
                CASE WHEN is_remote THEN 'Remote' ELSE COALESCE(country, 'Unknown') END AS location,
                COUNT(*) AS count
            FROM jobs
            WHERE status = 'active'
              AND posted_at >= :cutoff
            GROUP BY location
            ORDER BY count DESC
            LIMIT 10
        """)
        result = await session.execute(stmt, {"cutoff": cutoff})
        rows = result.fetchall()
        return [{"location": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def _get_seniority_distribution(session, days: int) -> list[dict[str, Any]]:
        """Get job distribution by seniority level."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

        stmt = (
            select(Job.seniority, func.count(Job.id))
            .where(Job.status == JobStatus.ACTIVE)
            .where(Job.posted_at >= cutoff)
            .group_by(Job.seniority)
            .order_by(func.count(Job.id).desc())
        )
        result = await session.execute(stmt)
        rows = result.fetchall()
        return [{"seniority": row[0] or "unspecified", "count": row[1]} for row in rows]

    async def _generate_trend_summary(
        self,
        query: str,
        total_jobs: int,
        top_skills: list[dict],
        salary_data: dict,
        location_data: list[dict],
        seniority_data: list[dict],
    ) -> str:
        """Generate LLM-powered trend summary."""
        top_skills_text = ", ".join(f"{s['skill']} ({s['count']})" for s in top_skills[:10])
        avg_salary = "N/A"
        if salary_data.get("available"):
            avg_salary = f"{salary_data['avg_min']:.0f} - {salary_data['avg_max']:.0f}"

        prompt = f"""\
Query: {query}

Market Data (last 30 days):
- Total Active Jobs: {total_jobs}
- Top Skills: {top_skills_text}
- Average Salary Range: {avg_salary}
- Top Locations: {', '.join(f"{l['location']} ({l['count']})" for l in location_data[:5])}
- Seniority Distribution: {', '.join(f"{s['seniority']} ({s['count']})" for s in seniority_data[:5])}

Provide a comprehensive market analysis.
"""

        try:
            response = await self._groq.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[
                    {"role": "system", "content": TREND_ANALYSIS_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=800,
            )
            return response.choices[0].message.content
        except Exception as exc:
            logger.warning("Trend summary generation failed: %s", exc)
            return self._fallback_summary(total_jobs, top_skills, salary_data)

    @staticmethod
    def _fallback_summary(total_jobs, top_skills, salary_data):
        """Generate a basic summary if LLM fails."""
        skills = ", ".join(s["skill"] for s in top_skills[:5])
        summary = f"\ud83d\udcca Market Overview\n\n"
        summary += f"- **{total_jobs}** active jobs in the database\n"
        summary += f"- **Most in-demand skills**: {skills}\n"
        if salary_data.get("available"):
            summary += f"- **Average salary range**: {salary_data['avg_min']:.0f} - {salary_data['avg_max']:.0f}\n"
        summary += "\nFor more detailed insights, try asking about specific roles or locations."
        return summary
