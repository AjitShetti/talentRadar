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
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.sql.functions import Function
from sqlalchemy.types import UserDefinedType

from config.settings import get_settings
from storage.database import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@compiles(Function, "sqlite")
def _compile_function_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
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

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(ARRAY(String))


class TextArray(StringArray):
    """PostgreSQL ``text[]`` with the same SQLite JSON fallback.

    For a column a migration creates as ``text[]``. ``varchar[]`` and ``text[]`` are different
    types to PostgreSQL, with no operator between them, so ``varchar[] @> text[]`` is an error:
    a model that says ``varchar[]`` over a ``text[]`` column (or the reverse, on a schema built
    by ``create_all``) turns an array filter into a 500 that SQLite never shows.
    """

    impl = ARRAY(Text)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(ARRAY(Text))


class Vector(UserDefinedType):
    """pgvector's ``vector(N)`` column type.

    Hand-rolled rather than pulled from ``pgvector``'s Python package: all the
    application needs is the DDL spelling plus a value that survives a round
    trip, and the two queries that read or write a vector do so through
    explicit SQL in ``ingestion/embeddings/pgvector_store.py`` (which binds
    ``double precision[]`` and casts, so no driver-side type codec has to be
    registered on either asyncpg or psycopg2). Declaring the column here is
    what lets Alembic autogenerate see the table.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: object) -> str:
        return f"vector({self.dim})"


@compiles(Vector, "sqlite")
def _compile_vector_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    # The test suite creates the schema on SQLite, which has no vector type.
    # Semantic search is never exercised there — only the table's existence.
    return "TEXT"




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

    # Company Intelligence directory fields (see services/companies.py)
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Plain-language summary of what the company does"
    )
    tier: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="big_tech | gcc | unicorn | scaleup | startup | services",
    )
    github_org: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="GitHub organisation slug, e.g. 'razorpay'"
    )
    careers_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    office_cities: Mapped[list[str] | None] = mapped_column(
        StringArray, nullable=True, comment="Indian cities with an engineering office"
    )

    # Company map (migration 015). Which source wrote each field is recorded
    # in extra_metadata["provenance"]; see domain/company_identity.py.
    slug: Mapped[str | None] = mapped_column(String(96), nullable=True, unique=True)
    summary: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="One line: what they sell, to whom"
    )
    summary_source: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="curated | site | wikidata | yc | github_org | llm"
    )
    stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(24), nullable=True)
    area: Mapped[str | None] = mapped_column(
        String(48), nullable=True, comment="Canonical Bengaluru area (domain/areas.py)"
    )
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_precision: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="exact | street | area | city"
    )
    geo_source: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="curated | wikidata | osm | nominatim | pincode | area",
    )
    wikidata_id: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    x_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_funding_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last website / Wikidata pass"
    )
    # Migration 016. The map lists tech employers only: TRUE is listed, FALSE
    # is a classified non-tech row, NULL has not been classified yet. A catalogue
    # tier is tech; an importer's row is decided by domain.company_taxonomy.is_tech.
    is_tech: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, index=True, comment="Tech employer (the map lists TRUE only)"
    )

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
    profile: Mapped["CompanyProfile | None"] = relationship(
        "CompanyProfile", back_populates="company", uselist=False
    )
    contacts: Mapped[list["CompanyContact"]] = relationship(
        "CompanyContact", back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("domain", name="uq_companies_domain"),
        Index("ix_companies_industry", "industry"),
        Index("ix_companies_hq_country", "hq_country"),
        Index("ix_companies_tier", "tier"),
        Index("ix_companies_area", "area"),
        Index("ix_companies_stage", "stage"),
        Index("ix_companies_sector", "sector"),
        Index("ix_companies_geo_precision", "geo_precision"),
        Index("ix_companies_last_funding_at", "last_funding_at"),
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
    * `embedding_id` holds the vector-store row id (job_embeddings.id).
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
        comment="Vector-store row id (job_embeddings.id) for semantic search lookups",
    )

    # ---- Enrichment state ---------------------------------------------- #
    # Live-scraped rows are written structurally, with no LLM call, so that a
    # 60-result search costs zero Groq quota. They start "raw" and are parsed
    # later - on demand, or by the budgeted scheduled pass. See
    # services/job_persistence.py and migration 011.
    enrichment_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="raw", server_default="enriched",
        comment="raw = structural scrape only; enriched = LLM-parsed; failed = unparseable",
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

    # ---- Liveness evidence --------------------------------------------- #
    # The facts domain/liveness.py turns into a verdict. All nullable: a fact
    # we do not hold stays absent rather than being guessed. Migration 013.
    source_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Publish time reported by the employer's ATS",
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Last edit time reported by the employer's ATS",
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now(),
        comment="When this posting first reached our index",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Most recent scrape that returned this posting",
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="Most recent re-check that found the posting still served",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="When a re-check found the source no longer serving it",
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

class RoleSighting(Base):
    """
    Running record of one role - a (company, normalised title) pair.

    This is the history that has to outlive retention. ``jobs`` rows are
    pruned to keep a 0.5 GB database writable, and a re-listed role arrives
    under a new external id, so neither the row nor its id can remember that
    the same role has been advertised before. One small row here can.
    ``services/job_retention.py`` never touches this table.
    """
    __tablename__ = "role_sightings"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    title_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    times_seen: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    times_reposted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)


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

    # Relationships
    profile: Mapped["Profile | None"] = relationship(
        "Profile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"


class ApplicationStatus(str, PyEnum):
    """Full job-seeker funnel: saved → applied → online_assessment → interview → offer/rejected."""
    SAVED             = "saved"
    APPLIED           = "applied"
    ONLINE_ASSESSMENT = "online_assessment"
    SCREENING         = "screening"
    INTERVIEW         = "interview"
    OFFER             = "offer"
    REJECTED          = "rejected"
    WITHDRAWN         = "withdrawn"

    @staticmethod
    def funnel_order() -> dict[str, int]:
        """Position of each stage in the application funnel (for analytics)."""
        return {
            ApplicationStatus.SAVED.value: 0,
            ApplicationStatus.APPLIED.value: 1,
            ApplicationStatus.ONLINE_ASSESSMENT.value: 2,
            ApplicationStatus.SCREENING.value: 3,
            ApplicationStatus.INTERVIEW.value: 4,
            ApplicationStatus.OFFER.value: 5,
            ApplicationStatus.WITHDRAWN.value: 5,
            ApplicationStatus.REJECTED.value: 5,
        }


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status_enum", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=ApplicationStatus.SAVED,
        server_default="saved",
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Document tracking — which resume version / cover letter was used
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
    )
    cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cover_letters.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Dates for the funnel stages (for time-in-stage analytics)
    applied_at_explicit: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    oa_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    interview_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    # The posting this application is for. One-way (no back_populates) because
    # a Job has no reason to carry every user's application. Callers that
    # render the job MUST selectinload() it: without a relationship the
    # tracker endpoint issued a separate SELECT per row, and with one, a lazy
    # access inside async code raises MissingGreenlet.
    job: Mapped["Job | None"] = relationship("Job", lazy="raise")
    resume_version: Mapped["Resume | None"] = relationship(
        "Resume", back_populates="applications"
    )
    cover_letter: Mapped["CoverLetter | None"] = relationship(
        "CoverLetter", back_populates="applications"
    )
    events: Mapped[list["ApplicationEvent"]] = relationship(
        "ApplicationEvent",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.created_at",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_job_applications_user_job"),
    )

    def __repr__(self) -> str:
        return f"<JobApplication id={self.id} status={self.status.value}>"


# ---------------------------------------------------------------------------
# interview_sessions / interview_answer_scores
# ---------------------------------------------------------------------------

class InterviewTrack(str, PyEnum):
    """Available mock interview catalog tracks (round types)."""
    CODING         = "coding"
    TECHNICAL      = "technical"
    BEHAVIORAL     = "behavioral"
    SYSTEM_DESIGN  = "system_design"
    # Backward-compatible aliases for the old catalogue
    PYTHON_DSA     = "python_dsa"
    PYTHON_BACKEND = "python_backend"
    SQL            = "sql"


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
    topic: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        comment="Free-text subject the candidate chose; NULL for catalogue tracks",
    )
    difficulty: Mapped[InterviewDifficulty] = mapped_column(
        Enum(
            InterviewDifficulty,
            name="interview_difficulty_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    # Adaptive difficulty — when True the difficulty is derived from live scores
    adaptive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # The posting this session prepares for ("Prepare for this role"), and the
    # bounded description of it handed to the interviewer. The text is stored
    # rather than rebuilt per turn so every turn sees the same role even if
    # the job row is later pruned, and so it never has to come back from the
    # browser. See agents/interview/role_context.py and migration 014.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional link to the job/application this session prepares the user for
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    prep_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interview_prep_plans.id", ondelete="SET NULL"),
        nullable=True,
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
    prep_plan: Mapped["InterviewPrepPlan | None"] = relationship(
        "InterviewPrepPlan", back_populates="sessions"
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


# ---------------------------------------------------------------------------
# profiles — career onboarding profile (1:1 with users)
# ---------------------------------------------------------------------------

class Profile(Base):
    """
    Career profile created during onboarding. Drives job matching, salary
    filtering, resume tailoring, and the personal agent.
    """
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Target roles / career goals
    target_roles: Mapped[list[str] | None] = mapped_column(
        StringArray, nullable=True,
        comment="e.g. ['Senior Python Engineer', 'Backend Engineer']",
    )
    target_locations: Mapped[list[str] | None] = mapped_column(
        StringArray, nullable=True, comment="e.g. ['Bangalore', 'Remote']",
    )
    is_remote_preferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    target_salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    years_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_role: Mapped[str | None] = mapped_column(String(256), nullable=True)
    career_goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Active resume reference
    active_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="profile")
    resumes: Mapped[list["Resume"]] = relationship(
        "Resume", back_populates="profile", foreign_keys="Resume.profile_id"
    )

    def __repr__(self) -> str:
        return f"<Profile id={self.id} user={self.user_id} complete={self.onboarding_completed}>"


# ---------------------------------------------------------------------------
# resumes — resume versions with ATS analysis
# ---------------------------------------------------------------------------

class Resume(Base):
    """
    A resume version owned by a user's profile.

    ``is_tailored`` + ``target_job_id`` distinguish original uploads from
    job-specific tailored versions produced by Resume Studio.
    """
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="Original", server_default="Original"
    )
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ATS analysis (from Resume Studio)
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ats_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Structured resume document (Resume Studio's LaTeX editor). NULL until
    # the user opens the editor for the first time; see agents/latex_templates.py.
    structured_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_tailored: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    target_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="resumes", foreign_keys=[profile_id]
    )
    target_job: Mapped["Job | None"] = relationship("Job")
    applications: Mapped[list["JobApplication"]] = relationship(
        "JobApplication", back_populates="resume_version"
    )

    def __repr__(self) -> str:
        return f"<Resume id={self.id} v={self.version_name!r} ats={self.ats_score}>"


# ---------------------------------------------------------------------------
# cover_letters
# ---------------------------------------------------------------------------

class CoverLetter(Base):
    """AI-generated cover letter for a user + target job."""
    __tablename__ = "cover_letters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(
        String(256), nullable=False, default="Cover Letter", server_default="Cover Letter"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    applications: Mapped[list["JobApplication"]] = relationship(
        "JobApplication", back_populates="cover_letter"
    )

    def __repr__(self) -> str:
        return f"<CoverLetter id={self.id} title={self.title!r}>"


# ---------------------------------------------------------------------------
# skills registry + user_skills
# ---------------------------------------------------------------------------

class Skill(Base):
    """Canonical skill registry (normalised names)."""
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aliases: Mapped[list[str] | None] = mapped_column(StringArray, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Skill id={self.id} name={self.name!r}>"


class UserSkill(Base):
    """A user's proficiency in a skill (used by career coach + matching)."""
    __tablename__ = "user_skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # 1 = beginner … 5 = expert
    proficiency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="resume", server_default="resume"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "skill_name", name="uq_user_skills_user_skill"),
    )

    def __repr__(self) -> str:
        return f"<UserSkill user={self.user_id} skill={self.skill_name!r} p={self.proficiency}>"


# ---------------------------------------------------------------------------
# application_events — status-change timeline for the tracker
# ---------------------------------------------------------------------------

class ApplicationEvent(Base):
    """A single status change / note on an application (audit + analytics)."""
    __tablename__ = "application_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    application: Mapped["JobApplication"] = relationship(
        "JobApplication", back_populates="events"
    )

    def __repr__(self) -> str:
        return f"<ApplicationEvent app={self.application_id} {self.from_status}→{self.to_status}>"


# ---------------------------------------------------------------------------
# interview_prep_plans — personalised prep for a scheduled interview
# ---------------------------------------------------------------------------

class InterviewPrepPlan(Base):
    """Personalised interview preparation plan (job + company + resume)."""
    __tablename__ = "interview_prep_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # e.g. generated from a company_intel report + resume + job description
    interview_rounds: Mapped[list[str] | None] = mapped_column(
        StringArray, nullable=True, comment="e.g. ['coding', 'behavioral', 'system_design']"
    )
    focus_areas: Mapped[list[str] | None] = mapped_column(StringArray, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sessions: Mapped[list["InterviewSession"]] = relationship(
        "InterviewSession", back_populates="prep_plan"
    )

    def __repr__(self) -> str:
        return f"<InterviewPrepPlan id={self.id} app={self.application_id}>"


# ---------------------------------------------------------------------------
# company_profiles — company intelligence
# ---------------------------------------------------------------------------

class CompanyProfile(Base):
    """Company intelligence: tech stack, salaries, interview patterns, trends."""
    __tablename__ = "company_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    tech_stack: Mapped[list[str] | None] = mapped_column(StringArray, nullable=True)
    salary_ranges: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    interview_patterns: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hiring_trends: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    culture_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="aggregated", server_default="aggregated"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped["Company"] = relationship("Company", back_populates="profile")

    def __repr__(self) -> str:
        return f"<CompanyProfile company={self.company_id}>"


# ---------------------------------------------------------------------------
# company_contacts — talent/recruiting contacts surfaced in Company Intel
# ---------------------------------------------------------------------------

class CompanyContact(Base):
    """
    A way to reach a company's talent team.

    Two flavours live in the same table, separated by ``user_id``:

    * ``user_id IS NULL``  — a *curated* contact seeded from a public source
      (the careers inbox printed on a careers page, the company's jobs page).
      Visible to everyone. ``source_url`` records where it came from.
    * ``user_id`` set      — a contact the signed-in user saved themselves
      (a recruiter who reached out, a referral). Private to that user.

    Named individuals are only ever stored when a user adds them or a curated
    entry cites a public source — nothing here is inferred or generated.
    """
    __tablename__ = "company_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="NULL = curated public contact; set = private to that user",
    )
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="careers_inbox",
        server_default="careers_inbox",
        comment=(
            "careers_inbox | careers_page | recruiter | referral | other"
            " | founder | leader | engineer"
        ),
    )
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="Public page this contact was read from"
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="True only when a human confirmed the contact still works",
    )
    # People (migration 015): public profile links and the kind of source
    # the person was read from. A curated person always has source_url.
    x_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="Personal site"
    )
    source_kind: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="curated | wikidata | site | news | github | tavily | user",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped["Company"] = relationship("Company", back_populates="contacts")

    __table_args__ = (
        Index("ix_company_contacts_company_user", "company_id", "user_id"),
        Index(
            "uq_company_contacts_curated_person",
            "company_id",
            "kind",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("user_id IS NULL AND name IS NOT NULL"),
            sqlite_where=text("user_id IS NULL AND name IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<CompanyContact company={self.company_id} kind={self.kind!r}>"


# ---------------------------------------------------------------------------
# company_news — startup news from public RSS feeds (migration 015)
# ---------------------------------------------------------------------------

class CompanyNews(Base):
    """
    One article from an Indian startup-news feed, classified.

    ``company_id`` is set only when the subject matched a company by
    normalised name; it is SET NULL on delete so the article outlives a
    pruned company. Funding rows feed ``companies.last_funding_at`` and
    ``stage``. Retention: ``services/job_retention.py``.
    """
    __tablename__ = "company_news"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="inc42 | yourstory | ettech | mint"
    )
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    snippet: Mapped[str | None] = mapped_column(String(600), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    category: Mapped[str] = mapped_column(
        String(16), nullable=False, default="other", server_default="other",
        comment="funding | acquisition | launch | other",
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    company_name_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)
    round: Mapped[str | None] = mapped_column(String(24), nullable=True)
    amount_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_usd: Mapped[float | None] = mapped_column(
        Numeric(asdecimal=False), nullable=True,
        comment="Approximate; INR converted at domain.news.INR_PER_USD",
    )
    investors: Mapped[list[str] | None] = mapped_column(StringArray, nullable=True)
    extracted_by: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="rules | llm"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    company: Mapped["Company | None"] = relationship("Company")

    __table_args__ = (
        Index("ix_company_news_company_id", "company_id"),
        Index("ix_company_news_published_at", "published_at"),
    )

    def __repr__(self) -> str:
        return f"<CompanyNews {self.source} {self.title[:40]!r}>"


# ---------------------------------------------------------------------------
# events — the Bengaluru tech-events tracker (migration 018)
# ---------------------------------------------------------------------------


class Event(Base):
    """
    One event: read from a source (Luma, GDG, Eventbrite) or added by a user.

    ``(source, external_id)`` is the identity, so a re-read updates the row.
    ``listed`` and ``reasons`` are the relevance verdict
    (``domain.event_relevance``). A cross-source duplicate sets ``duplicate_of``
    to its primary and is never listed; the primary carries the listing.
    ``owner_user_id`` is set only for an event a user added by hand, and is
    private to that user. Retention: ``services/job_retention.py``.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="luma | gdg | eventbrite | user"
    )
    external_id: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        comment="Luma evt-…; GDG slug; Eventbrite numeric id; for user, the row id",
    )
    url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="The public event page (https only); NULL for a manual event with no link",
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    snippet: Mapped[str | None] = mapped_column(
        String(600), nullable=True, comment="Description, de-boilerplated and bounded"
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="NULL = start + 2 h"
    )
    venue_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(
        String(300), nullable=True, comment="As printed by the source"
    )
    area: Mapped[str | None] = mapped_column(
        String(48), nullable=True, comment="Canonical area, from domain.areas"
    )
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_precision: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="exact | area | none"
    )
    is_online: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_free: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL = the source did not say"
    )
    organiser_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    organiser_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    hosts: Mapped[list[str] | None] = mapped_column(
        TextArray, nullable=True, comment="'Hosted by' names, when the source states them"
    )
    format: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="meetup | talk | workshop | hackathon | conference | mixer | demo | hiring | other",
    )
    topics: Mapped[list[str] | None] = mapped_column(
        TextArray, nullable=True, comment="Closed vocabulary (domain.event_relevance)"
    )
    listed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="The relevance verdict",
    )
    reasons: Mapped[list[str] | None] = mapped_column(
        TextArray, nullable=True, comment="The evidence behind listed, in display order"
    )
    is_cancelled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Only from an explicit source signal, never from a missing listing",
    )
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        comment="Set only for a manual event; private to that user",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Moves on every sighting",
    )
    detail_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last detail-page read (GDG and Eventbrite)",
    )

    # NOTE: 'metadata' is reserved by SQLAlchemy Declarative API; the Python
    # attribute is 'extra_metadata' but maps to the 'metadata' Postgres column.
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Raw taxonomy ids, source scope and link reasons",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_events_source_external_id"),
        Index("ix_events_starts_at", "starts_at"),
        Index("ix_events_listed_starts_at", "listed", "starts_at"),
        Index("ix_events_duplicate_of", "duplicate_of"),
        Index("ix_events_owner_user_id", "owner_user_id"),
    )

    def __repr__(self) -> str:
        return f"<Event {self.source}:{self.external_id} {self.title[:40]!r}>"


class EventCompany(Base):
    """
    An event linked to a company on the map (spec §5.2).

    A join table rather than a column, because one event can name two
    companies ("BitGo x Giottus"). An event never creates a company; the link
    is made only to a company with ``is_tech IS TRUE``.
    """

    __tablename__ = "event_companies"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(12), nullable=False, comment="organiser | host")
    matched_on: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        comment="organiser_name | organiser_domain | host_name | title_pair | title_at",
    )

    __table_args__ = (Index("ix_event_companies_company_id", "company_id"),)

    def __repr__(self) -> str:
        return f"<EventCompany event={self.event_id} company={self.company_id} {self.role}>"


class EventSave(Base):
    """
    A user's own side of an event: saved, or going, with an optional note.

    Unique per ``(user_id, event_id)``. Private to the user: every read is
    filtered by ``user_id``.
    """

    __tablename__ = "event_saves"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False, comment="saved | going")
    note: Mapped[str | None] = mapped_column(
        String(2000), nullable=True, comment="The user's own note on the event"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_event_saves_user_event"),
        Index("ix_event_saves_event_id", "event_id"),
    )

    def __repr__(self) -> str:
        return f"<EventSave user={self.user_id} event={self.event_id} {self.status}>"


class EventContact(Base):
    """
    A person the user met at an event: the user's private notes (spec §5.4).

    Never enriched, never looked up, never in a public payload. ``email`` is
    only what the person gave the user. ``company_id`` is resolved by exact
    normalised name at write time; if that company is deleted, the link is
    cleared and the person's row is kept.
    """

    __tablename__ = "event_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="As typed")
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    x_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email: Mapped[str | None] = mapped_column(
        String(254), nullable=True, comment="Only what the person gave the user"
    )
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_event_contacts_user_created", "user_id", "created_at"),
        Index("ix_event_contacts_event_id", "event_id"),
        Index("ix_event_contacts_company_id", "company_id"),
    )

    def __repr__(self) -> str:
        return f"<EventContact user={self.user_id} event={self.event_id} {self.name!r}>"


# ---------------------------------------------------------------------------
# market_snapshots — historical trends for the trends dashboard
# ---------------------------------------------------------------------------

class MarketSnapshot(Base):
    """Periodic aggregate of market trends (skills/salary/location demand)."""
    __tablename__ = "market_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    snapshot_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(
        String(128), nullable=False, default="all", server_default="all", index=True
    )
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("snapshot_date", "scope", name="uq_market_snapshots_date_scope"),
    )

    def __repr__(self) -> str:
        return f"<MarketSnapshot date={self.snapshot_date} scope={self.scope!r}>"


# ---------------------------------------------------------------------------
# learning_tasks — career coach recommendations
# ---------------------------------------------------------------------------

class LearningTask(Base):
    """A concrete learning/practice task recommended by the career coach."""
    __tablename__ = "learning_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    resources: Mapped[list[str] | None] = mapped_column(StringArray, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="career_coach", server_default="career_coach"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_learning_tasks_user_status", "user_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<LearningTask id={self.id} skill={self.skill_name!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# agent_memory — persistent memory for the personal AI agent
# ---------------------------------------------------------------------------

class AgentMemory(Base):
    """
    Persistent memory store for the personal agent: remembers the user's
    profile, weaknesses, goals, and emits the next-best-action recommendation.
    """
    __tablename__ = "agent_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    memory_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="e.g. 'weakness', 'goal', 'achievement', 'preference'",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_agent_memory_user_type", "user_id", "memory_type"),
    )

    def __repr__(self) -> str:
        return f"<AgentMemory user={self.user_id} type={self.memory_type!r}>"


# ---------------------------------------------------------------------------
# analytics — per-user aggregated funnel metrics (periodic snapshots)
# ---------------------------------------------------------------------------

class AnalyticsSnapshot(Base):
    """Periodic per-user dashboard analytics (funnel, conversion, skills)."""
    __tablename__ = "analytics_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AnalyticsSnapshot user={self.user_id} date={self.snapshot_date}>"


# ---------------------------------------------------------------------------
# daily job matches — cached top-3 openings per user per day, matched
# against Profile.target_roles by the daily APScheduler job
# ---------------------------------------------------------------------------

class DailyJobMatch(Base):
    """One row of a user's daily top-3 job matches for their target roles."""
    __tablename__ = "daily_job_matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    matched_role: Mapped[str] = mapped_column(
        String(256), nullable=False,
        comment="Which Profile.target_roles[] entry produced this match",
    )
    match_date: Mapped[datetime] = mapped_column(
        Date, nullable=False, index=True,
        comment="Server date this batch was computed for",
    )
    rank: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="0..2, ordering within that day's top-3"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["Job"] = relationship("Job")

    __table_args__ = (
        UniqueConstraint("user_id", "match_date", "rank", name="uq_daily_job_matches_user_date_rank"),
        Index("ix_daily_job_matches_user_date", "user_id", "match_date"),
    )

    def __repr__(self) -> str:
        return f"<DailyJobMatch user={self.user_id} date={self.match_date} rank={self.rank}>"


# ---------------------------------------------------------------------------
# practice — the daily LeetCode pick, the weekly take-home and its judgement,
# and the per-user switches (docs/superpowers/specs/2026-10-15-practice-design.md §5)
# ---------------------------------------------------------------------------

# Every state an attempt can be in. A judged or rejected attempt keeps its row;
# "missed" is shown only to its owner.
ATTEMPT_STATUSES: tuple[str, ...] = (
    "started",
    "submitted",
    "queued",
    "judged",
    "rejected",
    "judge_failed",
    "missed",
)


class PracticeDay(Base):
    """One IST day the user acted on in Practice: a swap or a "done"."""

    __tablename__ = "practice_days"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, comment="IST calendar date")
    question_slug: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="Catalogue slug"
    )
    topic: Mapped[str] = mapped_column(String(48), nullable=False, comment="The topic drawn")
    difficulty: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="easy | medium | hard"
    )
    swaps: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="0 or 1: whether the user asked for a different question this day",
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feel: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="easy | right | hard, the one-tap rating"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_practice_days_user_day"),
        Index("ix_practice_days_user_done", "user_id", "done_at"),
    )

    def __repr__(self) -> str:
        return f"<PracticeDay user={self.user_id} day={self.day}>"


class PracticeChallenge(Base):
    """One edition of the weekly take-home. Its brief is copied in at creation."""

    __tablename__ = "practice_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    slug: Mapped[str] = mapped_column(
        String(96), nullable=False, comment="<brief-slug>-<YYYY-MM-DD of opens_at>"
    )
    brief_slug: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="The brief file's slug"
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    track: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="backend | frontend | fullstack | data | ai"
    )
    hours: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="24 or 48")
    brief: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, comment="The brief file's content, copied at creation (<= 8 KB)"
    )
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("slug", name="uq_practice_challenges_slug"),
        # One edition per opening: two scheduled runs that name different briefs get different
        # slugs, so the slug alone cannot stop a second edition for the same week.
        Index("uq_practice_challenges_opens_at", "opens_at", unique=True),
    )

    def __repr__(self) -> str:
        return f"<PracticeChallenge slug={self.slug!r}>"


class PracticeAttempt(Base):
    """One user's take-home: the start, the submission and the judgement, in one row."""

    __tablename__ = "practice_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("practice_challenges.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="One of ATTEMPT_STATUSES"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Server time of Start"
    )
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="started_at plus the brief's hours"
    )
    proof_code: Mapped[str] = mapped_column(
        CHAR(10), nullable=False, comment="[A-Z2-9]{10}; the repo's .talentradar file holds it"
    )
    repo_owner: Mapped[str | None] = mapped_column(
        String(39), nullable=True, comment="Captured from the strict GitHub URL"
    )
    repo_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Captured from the strict GitHub URL"
    )
    writeup: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="decisions, tradeoffs, ai_usage, next_steps; each bounded"
    )
    video_url: Mapped[str | None] = mapped_column(
        String(300), nullable=True, comment="Shown to its owner and to an admin, never judged"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last save"
    )
    repo_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="From GitHub's own timestamp"
    )
    snapshot_sha: Mapped[str | None] = mapped_column(
        CHAR(40), nullable=True, comment="The last push at or before the deadline"
    )
    snapshot_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="From the GitHub Activity API"
    )
    signals: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Deterministic evidence, not points"
    )
    scores: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="{criterion: number}, the final scores"
    )
    total: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), nullable=True, comment="Weighted total, 0 to 100, one decimal"
    )
    notes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Per-criterion notes, strengths, improvements, summary"
    )
    judge_runs: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True, comment="At most two runs: scores, model, prompt version, time"
    )
    judge_input_sha256: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, comment="SHA-256 of the assembled judge input"
    )
    judge_attempts: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Failed judge calls so far; judge_failed after six",
    )
    next_judge_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Backoff: when the judge may try again"
    )
    reject_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="Shown to the owner when status is rejected"
    )
    flags: Mapped[list[str]] = mapped_column(
        StringArray,
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        comment="e.g. injection_text",
    )
    held: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Off the leaderboard until an admin clears it",
    )
    appeal_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="For an admin only; never sent to the model"
    )
    appealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    override_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Admin note on an override"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "challenge_id", name="uq_practice_attempts_user_challenge"),
        UniqueConstraint("proof_code", name="uq_practice_attempts_proof_code"),
        Index("ix_practice_attempts_board", "challenge_id", "status", "total"),
        Index("ix_practice_attempts_due", "status", "next_judge_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<PracticeAttempt user={self.user_id} challenge={self.challenge_id} "
            f"status={self.status!r}>"
        )


class PracticeSettings(Base):
    """A user's Practice switches: leaderboard name, repo link, contest reminder."""

    __tablename__ = "practice_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="Opt-in leaderboard name"
    )
    show_repo: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Opt-in: the repo link appears on the board",
    )
    contest_reminder: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="Show the weekly contest on the Overview and on /practice",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PracticeSettings user={self.user_id}>"


# ---------------------------------------------------------------------------
# Vector store (pgvector)
# ---------------------------------------------------------------------------

class JobEmbedding(Base):
    """A job description's embedding, stored in Postgres via pgvector.

    This table is what replaced the ChromaDB server. It is deliberately
    standalone rather than a column on ``jobs``:

    * ``id`` is the same stable MD5 fingerprint of the source URL that
      ``jobs.embedding_id`` holds and that ChromaDB used as its document id,
      so the two stores are interchangeable and the existing rows keep
      resolving;
    * a job row can exist before it has been embedded (and an embedding can
      outlive a re-ingested job), so the lifetimes are separate;
    * embedding rows are wide and are never wanted in the ordinary job
      queries, which would otherwise drag 384 floats per row across the wire.

    Reads and writes go through ``ingestion/embeddings/pgvector_store.py``,
    which issues explicit SQL — the ORM is not used for the vector column.
    """

    __tablename__ = "job_embeddings"

    id: Mapped[str] = mapped_column(
        String(256), primary_key=True,
        comment="Stable MD5 fingerprint of source_url; matches jobs.embedding_id",
    )
    document: Mapped[str] = mapped_column(
        Text, nullable=False, comment="The text that was embedded",
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}",
        comment="Flat, filterable fields (title, company, is_remote, skills_str …)",
    )
    embedding: Mapped[Any] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=False,
        comment="all-MiniLM-L6-v2 embedding of `document`",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<JobEmbedding id={self.id}>"


# ---------------------------------------------------------------------------
# outreach (migration 017)
# ---------------------------------------------------------------------------

class OutreachThread(Base):
    """
    One user's conversation with one company: who they plan to reach, who they
    have reached and what happened.

    Private to ``user_id``. ``company_id`` is NULL for a company the user typed
    that we hold no row for (a user's typing must not create rows in the shared
    ``companies`` table) and for a company later removed, so ``company_name``
    and ``job_title`` are snapshots that keep the thread readable on their own.
    A company is opened at most once per user (the partial unique index).

    ``people`` and ``touches`` are deleted by the database (``ON DELETE
    CASCADE``), which is why they are ``passive_deletes`` and ``lazy="raise"``:
    callers that need them ``selectinload`` them explicitly.
    """
    __tablename__ = "outreach_threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL = a typed company we hold no row for, or one later removed",
    )
    company_name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="Snapshot, so the thread outlives its company"
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        comment="A posting attached as context",
    )
    job_title: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="Snapshot; jobs are pruned after 45 days"
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="SET NULL"),
        nullable=True,
        comment="Link to the Applications tracker",
    )
    source: Mapped[str] = mapped_column(
        String(12), nullable=False,
        comment="suggestion | drawer | list | typed | overview",
    )
    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="The user's 'why this company'"
    )
    next_step: Mapped[str | None] = mapped_column(
        String(140), nullable=True, comment="After a reply, e.g. 'Call on Thursday'"
    )
    next_step_at: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="The day that step is due, in IST"
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Set when the user closes the thread"
    )
    outcome: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="referred | interviewing | offer | not_now | not_a_fit | other",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    people: Mapped[list["OutreachPerson"]] = relationship(
        "OutreachPerson", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    touches: Mapped[list["OutreachTouch"]] = relationship(
        "OutreachTouch", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )

    __table_args__ = (
        Index("ix_outreach_threads_user", "user_id"),
        Index(
            "uq_outreach_threads_user_company",
            "user_id",
            "company_id",
            unique=True,
            postgresql_where=text("company_id IS NOT NULL"),
            sqlite_where=text("company_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<OutreachThread id={self.id} user={self.user_id}>"


class OutreachPerson(Base):
    """
    A person in a thread, with their own follow-up clock.

    In a thread with a company, every person is a ``company_contacts`` row
    first, so a person always has a source; that row is the truth for links and
    email, and ``name``/``title`` only keep the person readable if it goes
    away. A name-only thread has no contact rows, so ``email`` and
    ``linkedin_url`` hold what the user typed and are used for nothing else.
    ``cold`` is derived from the clock at read time and is never stored.
    """
    __tablename__ = "outreach_people"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outreach_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company_contacts.id", ondelete="SET NULL"),
        nullable=True,
        comment="A curated person or the user's own saved contact",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="The contact's kind"
    )
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="planned", server_default="planned",
        comment="planned | waiting | replied | dropped",
    )
    first_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_outbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    follow_ups_sent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    next_follow_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Recomputed on every touch; the user can snooze it",
    )
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="Only for a person with no contact row"
    )
    linkedin_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="Only for a person with no contact row"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # The unique index below is partial, so it cannot serve "the people of
        # a thread" or the ON DELETE CASCADE lookup; this one can.
        Index("ix_outreach_people_thread", "thread_id"),
        Index(
            "uq_outreach_people_thread_contact",
            "thread_id",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
            sqlite_where=text("contact_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<OutreachPerson id={self.id} thread={self.thread_id} state={self.state!r}>"


class OutreachTouch(Base):
    """
    One thing that happened: a message the user sent, or a reply they got.

    Nothing here is sent by the app; a touch is the user recording what they
    did. ``kind`` is set by the service from the follow-up clock, never by the
    client. ``user_id`` is denormalised for the per-day count.
    """
    __tablename__ = "outreach_touches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outreach_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outreach_people.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL for a thread-level touch (a GitHub comment)",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="linkedin | email | x | github | call | event | other",
    )
    direction: Mapped[str] = mapped_column(String(3), nullable=False, comment="out | in")
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="first | follow_up | reply | other"
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(
        String(2000), nullable=True,
        comment="A copy of the sent message, only when the user asks to keep one",
    )
    happened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Backdated up to 30 days by the user, never in the future",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_outreach_touches_thread", "thread_id"),
        Index("ix_outreach_touches_user_happened", "user_id", "happened_at"),
    )

    def __repr__(self) -> str:
        return f"<OutreachTouch id={self.id} thread={self.thread_id} {self.direction}>"


class OssIssueSnapshot(Base):
    """
    A company's open good-first-issues on GitHub, as of ``fetched_at``.

    Public data about the company, so it is shared across users. Only the
    scheduled step writes it (a page load never calls GitHub); when a fetch
    fails the row records the status and keeps the previous ``issues``.
    """
    __tablename__ = "oss_issue_snapshots"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    org: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="The github_org this was read for"
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="ok | not_found | rate_limited | error"
    )
    total_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
        comment="GitHub's total for the query",
    )
    issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="At most 5 of {repo, number, title, url, labels, updated_at, comments}",
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<OssIssueSnapshot company={self.company_id} status={self.status!r}>"


class OutreachSettings(Base):
    """A user's outreach preferences. No row means the defaults."""
    __tablename__ = "outreach_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_target: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3",
        comment="0-10 people to reach a day; 0 turns the suggestion nudge off",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<OutreachSettings user={self.user_id} daily_target={self.daily_target}>"
