"""
domain/entities.py
~~~~~~~~~~~~~~~~~~
Pure Pydantic domain entities — no ORM, no framework coupling.

These models represent the core business objects in a form that can be
passed between services, agents, and API layers without dragging in
SQLAlchemy or database concerns.

The storage layer maps these to/from ORM models; the API layer maps
these to/from request/response schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from domain.enums import (
    ApplicationStatus,
    EmploymentType,
    IngestionStatus,
    InterviewDifficulty,
    InterviewTrack,
    JobStatus,
    SeniorityLevel,
)

# ── Company ────────────────────────────────────────────────────────────────

class Company(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    domain: str | None = None
    linkedin_url: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    industry: str | None = None
    hq_country: str | None = None
    hq_city: str | None = None
    employee_count_range: str | None = None
    founded_year: int | None = None
    extra_metadata: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Job posting ────────────────────────────────────────────────────────────

class Job(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    company_id: uuid.UUID
    ingestion_run_id: uuid.UUID | None = None
    external_id: str | None = None
    source: str
    source_url: str | None = None
    title: str
    description_raw: str | None = None
    description_clean: str | None = None
    status: JobStatus = JobStatus.ACTIVE
    employment_type: EmploymentType | None = None
    seniority: SeniorityLevel | None = None
    location_raw: str | None = None
    country: str | None = None
    city: str | None = None
    is_remote: bool = False
    salary_raw: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    skills: list[str] | None = None
    tags: list[str] | None = None
    embedding_id: str | None = None
    view_count: int = 0
    apply_count: int = 0
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    extra_metadata: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Ingestion run ──────────────────────────────────────────────────────────

class IngestionRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    company_id: uuid.UUID | None = None
    source: str
    status: IngestionStatus = IngestionStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    jobs_discovered: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_skipped: int = 0
    error_message: str | None = None
    error_trace: dict | None = None
    run_config: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── User ───────────────────────────────────────────────────────────────────

class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    email: str
    role: str = "user"
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Profile (career onboarding) ────────────────────────────────────────────

class Profile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    target_roles: list[str] | None = None
    target_locations: list[str] | None = None
    is_remote_preferred: bool = False
    target_salary_min: float | None = None
    target_salary_max: float | None = None
    salary_currency: str | None = None
    years_experience: float | None = None
    current_role: str | None = None
    career_goals: str | None = None
    onboarding_completed: bool = False
    active_resume_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Resume ─────────────────────────────────────────────────────────────────

class Resume(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    profile_id: uuid.UUID
    version_name: str = "Original"
    file_path: str | None = None
    file_type: str | None = None
    extracted_text: str | None = None
    ats_score: float | None = None
    ats_analysis: dict | None = None
    is_tailored: bool = False
    target_job_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Cover letter ───────────────────────────────────────────────────────────

class CoverLetter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    profile_id: uuid.UUID
    job_id: uuid.UUID | None = None
    title: str = "Cover Letter"
    content: str
    tone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Skill + UserSkill ─────────────────────────────────────────────────────

class Skill(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    category: str | None = None
    aliases: list[str] | None = None
    created_at: datetime | None = None


class UserSkill(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    skill_id: uuid.UUID | None = None
    skill_name: str
    proficiency: int | None = None   # 1–5
    source: str = "resume"
    created_at: datetime | None = None


# ── Job application ────────────────────────────────────────────────────────

class JobApplication(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    job_id: uuid.UUID | None = None
    status: ApplicationStatus = ApplicationStatus.SAVED
    notes: str | None = None
    resume_version_id: uuid.UUID | None = None
    cover_letter_id: uuid.UUID | None = None
    applied_at: datetime | None = None
    oa_completed_at: datetime | None = None
    interview_scheduled_at: datetime | None = None
    outcome_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Application event ─────────────────────────────────────────────────────

class ApplicationEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    application_id: uuid.UUID
    from_status: str | None = None
    to_status: str
    note: str | None = None
    created_at: datetime | None = None


# ── Interview ──────────────────────────────────────────────────────────────

class InterviewSession(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    track: InterviewTrack
    difficulty: InterviewDifficulty
    adaptive: bool = False
    application_id: uuid.UUID | None = None
    prep_plan_id: uuid.UUID | None = None
    duration_seconds: int | None = None
    total_score: float | None = None
    completed: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InterviewAnswerScore(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    session_id: uuid.UUID
    question_index: int
    question_text: str
    answer_summary: str | None = None
    score_correctness: float = 0.0
    score_clarity: float = 0.0
    score_depth: float = 0.0
    was_followup: bool = False
    created_at: datetime | None = None


# ── Company profile (intelligence) ─────────────────────────────────────────

class CompanyProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    company_id: uuid.UUID
    tech_stack: list[str] | None = None
    salary_ranges: dict | None = None
    interview_patterns: dict | None = None
    hiring_trends: dict | None = None
    culture_summary: str | None = None
    source: str = "aggregated"
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Market snapshot ────────────────────────────────────────────────────────

class MarketSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    snapshot_date: datetime
    scope: str = "all"
    data: dict | None = None
    created_at: datetime | None = None


# ── Learning task ──────────────────────────────────────────────────────────

class LearningTask(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    skill_name: str
    title: str
    description: str | None = None
    resources: list[str] | None = None
    priority: int | None = None
    status: str = "pending"
    source: str = "career_coach"
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Agent memory ───────────────────────────────────────────────────────────

class AgentMemory(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    memory_type: str
    content: str
    extra_metadata: dict | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None


# ── Analytics snapshot ─────────────────────────────────────────────────────

class AnalyticsSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    snapshot_date: datetime
    data: dict | None = None
    created_at: datetime | None = None


# ── Interview prep plan ───────────────────────────────────────────────────

class InterviewPrepPlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    application_id: uuid.UUID | None = None
    user_id: uuid.UUID
    job_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    content: dict | None = None
    interview_rounds: list[str] | None = None
    focus_areas: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
