"""
api/schemas/application_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas for job application tracking.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

#: A posting link the user typed: http(s), visible ASCII, no spaces. It is stored and shown
#: as a link that opens in a new tab, and never fetched.
_POSTING_URL = re.compile(r"https?://[!-~]+")


class ApplicationCreateSchema(BaseModel):
    """A posting we hold (``job_id``), or a role found elsewhere: its title and company."""

    job_id: str | None = Field(None, description="UUID of the job")
    role_title: str | None = Field(None, max_length=300)
    company_name: str | None = Field(None, max_length=200)
    posting_url: str | None = Field(None, max_length=512)
    status: str = Field("saved")
    notes: str | None = Field(None)

    @field_validator("role_title", "company_name", "posting_url", mode="before")
    @classmethod
    def _flatten(cls, value: object) -> object:
        """One line, trimmed; blank is unset."""
        if isinstance(value, str):
            return " ".join(value.split()) or None
        return value

    @field_validator("posting_url")
    @classmethod
    def _http_link(cls, value: str | None) -> str | None:
        if value is not None and not _POSTING_URL.fullmatch(value):
            raise ValueError("The posting link must start with https://")
        return value

    @model_validator(mode="after")
    def _a_job_or_a_role(self) -> ApplicationCreateSchema:
        if self.job_id is None and not (self.role_title and self.company_name):
            raise ValueError("Add the role and the company.")
        return self


class ApplicationUpdateSchema(BaseModel):
    status: str | None = Field(None)
    notes: str | None = Field(None)
    applied_at: datetime | None = Field(None)
    # An absent key is left alone and null clears it. A time without an offset is
    # refused: read as any one zone, it could be hours off what the user typed.
    interview_at: AwareDatetime | None = Field(None)


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
    interview_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    job: ApplicationJobSchema | None = None
    # What the user typed for a role found elsewhere; empty for a posting we hold.
    role_title: str | None = None
    company_name: str | None = None
    posting_url: str | None = None


class ApplicationListResponseSchema(BaseModel):
    applications: list[ApplicationResponseSchema]
    total: int
