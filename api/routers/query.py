"""
api/routers/query.py
~~~~~~~~~~~~~~~~~~~~
Natural language query endpoint powered by AI agents.

Provides:
- Unified query endpoint for jobs, trends, and candidates
- Intent classification and automatic routing
- AI-generated summaries and insights
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from agents.orchestrator import Orchestrator
from api.schemas.query_schemas import QueryRequestSchema, QueryResponseSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["Query"])


@router.post("/", response_model=QueryResponseSchema)
async def process_query(request: QueryRequestSchema):
    """
    Process a natural language query using AI agents.

    The orchestrator automatically:
    1. Classifies the intent (search, trends, candidates)
    2. Extracts filters and keywords
    3. Routes to the appropriate agent
    4. Returns a unified response

    Examples:
    - "Find remote software engineer jobs"
    - "What skills are in demand for data scientists?"
    - "Show me ML engineer salaries in the US"
    """
    orchestrator = Orchestrator()
    response = await orchestrator.process_query(
        query=request.query,
        limit=request.limit,
        offset=request.offset,
    )

    return QueryResponseSchema(
        intent=response.intent.value,
        summary=response.summary,
        results=[
            {
                "job_id": r.job_id,
                "title": r.title,
                "company": r.company,
                "location": r.location,
                "is_remote": r.is_remote,
                "skills": r.skills,
                "score": r.score,
                "match_reason": r.match_reason,
            }
            for r in response.results
        ],
        metadata=response.metadata,
        error=response.error,
    )
