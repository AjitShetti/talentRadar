"""
api/schemas/job_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for job-related API requests and responses.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class JobFilterSchema(BaseModel):
    """Filters for job search."""
    query: str | None = Field(None, description="Free-text search query")
    skills: list[str] | None = Field(None, description="Required skills")
    location: str | None = Field(None, description="Job location")
    country: str | None = Field(None, description="Country filter")
    city: str | None = Field(None, description="City filter")
    is_remote: bool | None = Field(None, description="Remote-only filter")
    seniority: str | None = Field(None, description="Seniority level")
    employment_type: str | None = Field(None, description="Employment type")
    company_id: str | None = Field(None, description="Company UUID filter")
    company_name: str | None = Field(None, description="Company name filter")
    salary_min: float | None = Field(None, ge=0, description="Minimum salary")
    salary_max: float | None = Field(None, ge=0, description="Maximum salary")
    posted_after: datetime | None = Field(None, description="Posted after date")
    status: str | None = Field("active", description="Job status filter")
    limit: int = Field(20, ge=1, le=100, description="Number of results")
    offset: int = Field(0, ge=0, description="Pagination offset")


class JobResponseSchema(BaseModel):
    """Single job response payload."""
    id: str
    title: str
    company_id: str
    company_name: str | None = None
    source: str
    source_url: str | None = None
    location_raw: str | None = None
    country: str | None = None
    city: str | None = None
    is_remote: bool
    seniority: str | None = None
    employment_type: str | None = None
    salary_raw: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    skills: list[str] = []
    tags: list[str] = []
    description_clean: str | None = None
    posted_at: datetime | None = None
    created_at: datetime
    embedding_id: str | None = None
    match_score: float | None = Field(None, description="Match score if from search")

    class Config:
        from_attributes = True


class JobListResponseSchema(BaseModel):
    """Paginated job list response."""
    jobs: list[JobResponseSchema]
    total: int
    limit: int
    offset: int
    has_more: bool


class JobDetailResponseSchema(BaseModel):
    """Detailed job response with full description."""
    job: JobResponseSchema
    similar_jobs: list[JobResponseSchema] = []
    company_info: dict | None = None


class SearchRequestSchema(BaseModel):
    """Natural language search request."""
    query: str = Field(..., min_length=1, max_length=1000, description="Natural language search query")
    limit: int = Field(20, ge=1, le=100, description="Number of results")
    offset: int = Field(0, ge=0, description="Pagination offset")


class SearchResponseSchema(BaseModel):
    """Search response with AI summary."""
    results: list[JobResponseSchema]
    total_found: int
    summary: str | None = Field(None, description="AI-generated summary")
    filters_applied: dict | None = None
