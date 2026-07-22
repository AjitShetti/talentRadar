"""
storage/models.py
~~~~~~~~~~~~~~~~~
SQLAlchemy ORM models for the TalentRadar PostgreSQL schema.

Tables
------
  companies        - Canonical employer/company records
  ingestion_runs   - Audit log for every crawl / ingestion pipeline run
  jobs             - Individual job-posting records tied to a company
                     and the ingestion run that discovered them
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from storage.database import Base


from sqlalchemy.sql.functions import Function


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(Function, "sqlite")
def _compile_function_sqlite(element, compiler, **kw):
    if element.name.lower() == "array_to_string":
        args = list(element.clauses)
        if args:
            return f"coalesce({compiler.process(args[0], **kw)}, '')"
        return "''"
    return compiler.visit_function(element, **kw)






class StringArray(TypeDecorator):
    """PostgreSQL ARRAY(String) with SQLite JSON fallback for testing."""
    impl = ARRAY(String)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(ARRAY(String))




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """UTC timestamp — used as server-side default."""
    return func.now()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class IngestionStatus(str, PyEnum):
    """Lifecycle stages of a single ingestion run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"       # completed with some errors
    FAILED  = "failed"


class JobStatus(str, PyEnum):
    """Current state of a job posting."""
    ACTIVE    = "active"
    EXPIRED   = "expired"
    FILLED    = "filled"
    DUPLICATE = "duplicate"
    ARCHIVED  = "archived"


class EmploymentType(str, PyEnum):
    FULL_TIME  = "full_time"
    PART_TIME  = "part_time"
    CONTRACT   = "contract"
    INTERNSHIP = "internship"
    FREELANCE  = "freelance"


class SeniorityLevel(str, PyEnum):
    INTERN     = "intern"
    JUNIOR     = "junior"
    MID        = "mid"
    SENIOR     = "senior"
    LEAD       = "lead"
    PRINCIPAL  = "principal"
    STAFF      = "staff"
    DIRECTOR   = "director"
    VP         = "vp"
    C_LEVEL    = "c_level"


# ---------------------------------------------------------------------------
# companies
# ---------------------------------------------------------------------------

class Company(Base):
    """
    Canonical company record.
    A company may appear across many ingestion runs and job postings.
    """
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(256), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Firmographic metadata
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hq_country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hq_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    employee_count_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Enrichment payload (arbitrary extra fields from data providers)
    # NOTE: 'metadata' is reserved by SQLAlchemy Declarative API; the Python
    # attribute is 'extra_metadata' but maps to the 'metadata' Postgres column.
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="company", cascade="all, delete-orphan"
    )
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        "IngestionRun", back_populates="company"
    )

    __table_args__ = (
        UniqueConstraint("domain", name="uq_companies_domain"),
        Index("ix_companies_industry", "industry"),
        Index("ix_companies_hq_country", "hq_country"),
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# ingestion_runs
# ---------------------------------------------------------------------------

class IngestionRun(Base):
    """
    Audit log for every executed pipeline / crawl run.
    Tracks source, timing, counters, and any error payload for observability.
    """
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # Scope — a run may be scoped to one company or be a bulk/global run
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Source identifies which crawler/scraper produced this run
    source: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True,
        comment="e.g. linkedin, lever, greenhouse, tavily, manual",
    )

    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, name="ingestion_status_enum", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=IngestionStatus.PENDING,
        server_default=IngestionStatus.PENDING.value,
        index=True,
    )

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Counters
    jobs_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Error detail — only populated on FAILED / PARTIAL runs
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_trace: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Free-form config/params snapshot used for this run
    run_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    company: Mapped["Company | None"] = relationship(
        "Company", back_populates="ingestion_runs"
    )
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="ingestion_run"
    )

    __table_args__ = (
        Index("ix_ingestion_runs_source_status", "source", "status"),
        Index("ix_ingestion_runs_started_at", "started_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<IngestionRun id={self.id} source={self.source!r} "
            f"status={self.status.value}>"
        )


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

class Job(Base):
    """
    Individual job-posting record discovered by the ingestion pipeline.

    Design decisions
    ----------------
    * `external_id` + `source` forms a unique key so the same posting
      scraped twice is de-duplicated rather than duplicated.
    * Compensation is stored as raw text AND structured min/max columns
      so we can query ranges even when parsing is imperfect.
    * Skills / tags use a Postgres ARRAY for cheap containment queries.
    * `embedding_id` holds the ChromaDB document ID for vector lookups.
    """
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ---- Foreign keys -------------------------------------------------- #
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ---- Source identity ----------------------------------------------- #
    external_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
        comment="ID assigned by the data source (LinkedIn job id, Greenhouse id, …)",
    )
    source: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True,
        comment="Which crawler/board this posting came from",
    )
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # ---- Core content -------------------------------------------------- #
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    description_raw: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Original HTML / markdown from source"
    )
    description_clean: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Cleaned plain-text after HTML stripping"
    )

    # ---- Classification ----------------------------------------------- #
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status_enum", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=JobStatus.ACTIVE,
        server_default=JobStatus.ACTIVE.value,
        index=True,
    )
    employment_type: Mapped[EmploymentType | None] = mapped_column(
        Enum(EmploymentType, name="employment_type_enum", values_callable=lambda obj: [e.value for e in obj]), nullable=True
    )
    seniority: Mapped[SeniorityLevel | None] = mapped_column(
        Enum(SeniorityLevel, name="seniority_level_enum", values_callable=lambda obj: [e.value for e in obj]), nullable=True, index=True
    )

    # ---- Location ------------------------------------------------------ #
    location_raw: Mapped[str | None] = mapped_column(String(256), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_remote: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ---- Compensation -------------------------------------------------- #
    salary_raw: Mapped[str | None] = mapped_column(String(256), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ---- Skills / tags ------------------------------------------------- #
    skills: Mapped[list[str] | None] = mapped_column(
        StringArray, nullable=True,
        comment="Parsed skill tokens (Python, SQL, …)",
    )
    tags: Mapped[list[str] | None] = mapped_column(
        StringArray, nullable=True,
        comment="Free-form taxonomy tags added during enrichment",
    )

    # ---- Vector store reference ---------------------------------------- #
    embedding_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
        comment="ChromaDB document ID for semantic search lookups",
    )

    # ---- Analytics counters -------------------------------------------- #
    view_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    apply_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    # ---- Dates --------------------------------------------------------- #
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="When the posting first appeared on the source board",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Extra enrichment payload
    # NOTE: 'metadata' is reserved by SQLAlchemy Declarative API; the Python
    # attribute is 'extra_metadata' but maps to the 'metadata' Postgres column.
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="jobs")
    ingestion_run: Mapped["IngestionRun | None"] = relationship(
        "IngestionRun", back_populates="jobs"
    )

    __table_args__ = (
        # Deduplication: same external job from the same source only once
        UniqueConstraint("external_id", "source", name="uq_jobs_external_id_source"),
        Index("ix_jobs_company_status", "company_id", "status"),
        Index("ix_jobs_posted_at_status", "posted_at", "status"),
        Index("ix_jobs_is_remote_seniority", "is_remote", "seniority"),
        # GIN index for efficient ARRAY containment queries on skills
        Index("ix_jobs_skills_gin", "skills", postgresql_using="gin"),
        Index("ix_jobs_tags_gin", "tags", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title!r} status={self.status.value}>"


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

class User(Base):
    """
    User account for the application.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="user", server_default="user", nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"


# ---------------------------------------------------------------------------
# interview_sessions / interview_answer_scores
# ---------------------------------------------------------------------------

class InterviewTrack(str, PyEnum):
    """Available mock interview catalog tracks."""
    PYTHON_DSA     = "python_dsa"
    PYTHON_BACKEND = "python_backend"
    SQL            = "sql"
    SYSTEM_DESIGN  = "system_design"


class InterviewDifficulty(str, PyEnum):
    """Difficulty level chosen by the user per session."""
    BEGINNER = "beginner"
    MID      = "mid"
    SENIOR   = "senior"


class InterviewSession(Base):
    """
    One mock interview session for a user.

    Design decisions
    ----------------
    * Session metadata and final aggregate score only — raw transcripts are
      never persisted (privacy-first decision).
    * ``completed`` distinguishes a gracefully finished session from one that
      was abandoned mid-way (tab closed, network drop, etc.).
    * Scores are stored as floats 0–100 for human-readable display.
    """
    __tablename__ = "interview_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # Owner
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Session config
    track: Mapped[InterviewTrack] = mapped_column(
        Enum(
            InterviewTrack,
            name="interview_track_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        index=True,
    )
    difficulty: Mapped[InterviewDifficulty] = mapped_column(
        Enum(
            InterviewDifficulty,
            name="interview_difficulty_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )

    # Outcome
    duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Wall-clock seconds from start to end/abort",
    )
    total_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Aggregate score 0–100, NULL until session is completed",
    )
    completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="True = gracefully ended; False = abandoned mid-session",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    answer_scores: Mapped[list["InterviewAnswerScore"]] = relationship(
        "InterviewAnswerScore",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="InterviewAnswerScore.question_index",
    )

    __table_args__ = (
        Index("ix_interview_sessions_user_created", "user_id", "created_at"),
        Index("ix_interview_sessions_track_difficulty", "track", "difficulty"),
    )

    def __repr__(self) -> str:
        return (
            f"<InterviewSession id={self.id} track={self.track.value!r} "
            f"difficulty={self.difficulty.value!r} score={self.total_score}>"
        )


class InterviewAnswerScore(Base):
    """
    Per-question score record within a single interview session.

    Design decisions
    ----------------
    * Stores ``question_text`` and a brief ``answer_summary`` (NOT the full
      transcript) so the score card can be rendered without raw audio data.
    * Three sub-scores (correctness / clarity / depth) each 0–10 so the
      frontend can display a breakdown radar chart in future.
    * ``was_followup`` distinguishes a follow-up probe from an original Q.
    """
    __tablename__ = "interview_answer_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    question_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="0-based position in the session (including follow-ups)",
    )
    question_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The exact question text posed to the user",
    )
    answer_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Brief LLM-generated summary of the user's answer (not full transcript)",
    )

    # Sub-scores (0.0 – 10.0 each)
    score_correctness: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="Factual accuracy of the answer",
    )
    score_clarity: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="How clearly and concisely the answer was communicated",
    )
    score_depth: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="Technical depth and nuance demonstrated",
    )

    was_followup: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="True if this row is a follow-up probe, not the original question",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    session: Mapped["InterviewSession"] = relationship(
        "InterviewSession", back_populates="answer_scores"
    )

    __table_args__ = (
        Index(
            "ix_interview_answer_scores_session_idx",
            "session_id",
            "question_index",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<InterviewAnswerScore session={self.session_id} "
            f"q={self.question_index} followup={self.was_followup}>"
        )
