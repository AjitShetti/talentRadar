"""
api/schemas/query_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for query, trends, and recommendation endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequestSchema(BaseModel):
    """Natural language query request."""
    query: str = Field(..., min_length=1, max_length=2000, description="User query")
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class QueryResponseSchema(BaseModel):
    """Query response with AI agent output."""
    intent: str
    summary: str | None = None
    results: list[dict] = []
    metadata: dict = {}
    error: str | None = None


class TrendRequestSchema(BaseModel):
    """Market trend query request."""
    query: str = Field("General market trends", max_length=500)
    days: int = Field(30, ge=7, le=365, description="Lookback window in days")


class TrendResponseSchema(BaseModel):
    """Market trend analysis response."""
    summary: str | None = None
    total_jobs: int = 0
    top_skills: list[dict] = []
    salary_data: dict | None = None
    location_data: list[dict] = []
    seniority_data: list[dict] = []
    period_days: int = 30


class CandidateProfileSchema(BaseModel):
    """Candidate profile for matching."""
    name: str | None = None
    skills: list[str] = []
    experience_years: int | None = None
    current_title: str | None = None
    desired_title: str | None = None
    location: str | None = None
    is_remote: bool = False
    seniority: str | None = None
    resume_text: str | None = Field(None, max_length=10000)


class MatchRequestSchema(BaseModel):
    """Candidate-job matching request."""
    candidate: CandidateProfileSchema
    limit: int = Field(10, ge=1, le=50)


class MatchResponseSchema(BaseModel):
    """Candidate-job matching response."""
    matches: list[dict] = []
    summary: str | None = None
    top_score: float | None = None


class IngestRequestSchema(BaseModel):
    """Manual ingestion trigger request."""
    roles: list[str] = Field(..., min_length=1, description="Job roles to search for")
    locations: list[str] = Field(["Remote"], description="Location filters")
    max_results_per_query: int = Field(5, ge=1, le=50)


class IngestResponseSchema(BaseModel):
    """Ingestion trigger response."""
    success: bool
    message: str
    dag_run_id: str | None = None
    estimated_time: str | None = None


class SearchSuggestionSchema(BaseModel):
    """Search suggestion response."""
    query: str
    intent: str
    suggested_filters: dict = {}


class HealthResponseSchema(BaseModel):
    """Health check response."""
    status: str
    version: str
    services: dict[str, bool] = {}
