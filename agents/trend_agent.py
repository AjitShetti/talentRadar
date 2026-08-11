"""
agents/trend_agent.py
~~~~~~~~~~~~~~~~~~~~~
Market trend analysis agent.

Thin orchestrator: delegates all data aggregation to
``services.market.get_market_trends`` and returns an ``AgentResponse``.
No DB logic lives in the agent — the deterministic tool layer owns it.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.state import AgentResponse, IntentType
from services.market import get_market_trends as market_trends

logger = logging.getLogger(__name__)


class TrendAgent:
    """
    Market trend analysis agent.

    Provides:
    - Skill demand trends (most requested skills)
    - Salary insights by role/location
    - Geographic distribution of jobs
    - Seniority level distribution
    - Overall market summary (LLM-generated via the service layer)
    """

    async def get_market_trends(self, query: str, days: int = 30) -> AgentResponse:
        """
        Generate market trend report by delegating to the market service.

        Parameters
        ----------
        query : str
            User's trend query (e.g., "What skills are in demand for ML engineers?")
        days : int
            Lookback window in days.

        Returns
        -------
        AgentResponse
            Trend analysis with LLM-generated insights in ``metadata``.
        """
        try:
            result = await market_trends(query=query, days=days, with_summary=True)
            if not result.get("success"):
                return AgentResponse(
                    success=False,
                    intent=IntentType.MARKET_TRENDS,
                    error=result.get("error") or "Market trend analysis failed",
                )

            data = result.get("data", {})
            return AgentResponse(
                success=True,
                intent=IntentType.MARKET_TRENDS,
                summary=result.get("summary"),
                metadata={
                    "total_jobs": data.get("total_jobs", 0),
                    "top_skills": data.get("top_skills", []),
                    "salary_data": data.get("salary", {"available": False}),
                    "location_data": data.get("locations", []),
                    "seniority_data": data.get("seniority", []),
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
