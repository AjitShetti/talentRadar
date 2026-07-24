"""
api/schemas/application_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for job application tracking.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ApplicationCreateSchema(BaseModel):
    job_id: str = Field(..., description="UUID of the job")
    status: str = Field("saved")
    notes: str | None = Field(None)


class ApplicationUpdateSchema(BaseModel):
    status: str | None = Field(None)
    notes: str | None = Field(None)
    applied_at: datetime | None = Field(None)


class ApplicationJobSchema(BaseModel):
    id: str
    title: str
    company_name: str | None = None
    location_raw: str | None = None
    is_remote: bool = False
    source_url: str | None = None
    salary_raw: str | None = None
    skills: list[str] = []


class ApplicationResponseSchema(BaseModel):
    id: str
    job_id: str | None
    status: str
    notes: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime
    job: ApplicationJobSchema | None = None


class ApplicationListResponseSchema(BaseModel):
    applications: list[ApplicationResponseSchema]
    total: int
