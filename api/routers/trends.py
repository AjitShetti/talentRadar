"""
api/routers/trends.py
~~~~~~~~~~~~~~~~~~~~~
Market trend analysis endpoints.

Provides:
- Skill demand trends
- Salary insights
- Geographic distribution
- Seniority trends
- AI-generated market summaries
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from agents.orchestrator import Orchestrator
from agents.trend_agent import TrendAgent
from api.schemas.query_schemas import TrendRequestSchema, TrendResponseSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trends", tags=["Trends"])


@router.post("/", response_model=TrendResponseSchema)
async def get_market_trends(request: TrendRequestSchema):
    """
    Get market trend analysis.

    Analyzes current job market data to provide insights on:
    - Most in-demand skills
    - Salary ranges by role/location
    - Geographic distribution of opportunities
    - Seniority level trends

    Parameters
    ----------
    request : TrendRequestSchema
        Query and lookback window (7-365 days).
    """
    trend_agent = TrendAgent()
    response = await trend_agent.get_market_trends(
        query=request.query,
        days=request.days,
    )

    if not response.success:
        return TrendResponseSchema(
            summary=response.error or "Failed to generate trends",
        )

    return TrendResponseSchema(
        summary=response.summary,
        total_jobs=response.metadata.get("total_jobs", 0),
        top_skills=response.metadata.get("top_skills", []),
        salary_data=response.metadata.get("salary_data"),
        location_data=response.metadata.get("location_data", []),
        seniority_data=response.metadata.get("seniority_data", []),
        period_days=request.days,
    )


@router.get("/skills")
async def get_top_skills(days: int = 30, limit: int = 20):
    """Get the most in-demand skills in the current market."""
    trend_agent = TrendAgent()
    response = await trend_agent.get_market_trends("top skills", days=days)

    if not response.success:
        return {"skills": []}

    return {
        "skills": response.metadata.get("top_skills", [])[:limit],
        "period_days": days,
    }


@router.get("/salaries")
async def get_salary_insights(days: int = 30):
    """Get salary insights and market rates."""
    trend_agent = TrendAgent()
    response = await trend_agent.get_market_trends("salary data", days=days)

    if not response.success:
        return {"available": False}

    return response.metadata.get("salary_data", {"available": False})


@router.get("/locations")
async def get_location_trends(days: int = 30):
    """Get geographic distribution of job opportunities."""
    trend_agent = TrendAgent()
    response = await trend_agent.get_market_trends("job locations", days=days)

    if not response.success:
        return {"locations": []}

    return {
        "locations": response.metadata.get("location_data", []),
        "period_days": days,
    }
