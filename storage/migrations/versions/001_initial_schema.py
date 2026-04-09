"""Initial schema — companies, ingestion_runs, jobs

Revision ID: 001_initial_schema
Revises     : (none — first migration)
Create Date : 2026-04-08 00:00:00 UTC

Tables created
--------------
  companies        — canonical employer records
  ingestion_runs   — crawl / pipeline audit log
  jobs             — individual job postings

Postgres-specific features used
--------------------------------
  * UUID primary keys (gen_random_uuid())
  * JSONB columns for flexible metadata payloads
  * ARRAY(VARCHAR) columns for skills / tags with GIN indexes
  * Native ENUM types (status enums)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Pure SQL migration to avoid SQLAlchemy enum/type creation issues    #
    # ------------------------------------------------------------------ #
    
    # Create ENUM types
    op.execute("""
        CREATE TYPE ingestion_status_enum AS ENUM ('pending', 'running', 'success', 'partial', 'failed')
    """)
    op.execute("""
        CREATE TYPE job_status_enum AS ENUM ('active', 'expired', 'filled', 'duplicate', 'archived')
    """)
    op.execute("""
        CREATE TYPE employment_type_enum AS ENUM ('full_time', 'part_time', 'contract', 'internship', 'freelance')
    """)
    op.execute("""
        CREATE TYPE seniority_level_enum AS ENUM ('intern', 'junior', 'mid', 'senior', 'lead', 'principal', 'staff', 'director', 'vp', 'c_level')
    """)

    # Create companies table
    op.execute("""
        CREATE TABLE companies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(256) NOT NULL,
            domain VARCHAR(256),
            linkedin_url VARCHAR(512),
            website_url VARCHAR(512),
            logo_url VARCHAR(1024),
            industry VARCHAR(128),
            hq_country VARCHAR(64),
            hq_city VARCHAR(128),
            employee_count_range VARCHAR(64),
            founded_year INTEGER,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_companies_name ON companies (name)")
    op.execute("CREATE INDEX ix_companies_industry ON companies (industry)")
    op.execute("CREATE INDEX ix_companies_hq_country ON companies (hq_country)")
    op.execute("ALTER TABLE companies ADD CONSTRAINT uq_companies_domain UNIQUE (domain)")

    # Create ingestion_runs table
    op.execute("""
        CREATE TABLE ingestion_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
            source VARCHAR(128) NOT NULL,
            status ingestion_status_enum NOT NULL DEFAULT 'pending',
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            jobs_discovered INTEGER NOT NULL DEFAULT 0,
            jobs_inserted INTEGER NOT NULL DEFAULT 0,
            jobs_updated INTEGER NOT NULL DEFAULT 0,
            jobs_skipped INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            error_trace JSONB,
            run_config JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_ingestion_runs_company_id ON ingestion_runs (company_id)")
    op.execute("CREATE INDEX ix_ingestion_runs_source ON ingestion_runs (source)")
    op.execute("CREATE INDEX ix_ingestion_runs_status ON ingestion_runs (status)")
    op.execute("CREATE INDEX ix_ingestion_runs_started_at ON ingestion_runs (started_at)")
    op.execute("CREATE INDEX ix_ingestion_runs_source_status ON ingestion_runs (source, status)")

    # Create jobs table
    op.execute("""
        CREATE TABLE jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            ingestion_run_id UUID REFERENCES ingestion_runs(id) ON DELETE SET NULL,
            external_id VARCHAR(256),
            source VARCHAR(128) NOT NULL,
            source_url VARCHAR(2048),
            title VARCHAR(512) NOT NULL,
            description_raw TEXT,
            description_clean TEXT,
            status job_status_enum NOT NULL DEFAULT 'active',
            employment_type employment_type_enum,
            seniority seniority_level_enum,
            location_raw VARCHAR(256),
            country VARCHAR(64),
            city VARCHAR(128),
            is_remote BOOLEAN NOT NULL DEFAULT false,
            salary_raw VARCHAR(256),
            salary_min DOUBLE PRECISION,
            salary_max DOUBLE PRECISION,
            salary_currency VARCHAR(8),
            skills VARCHAR[],
            tags VARCHAR[],
            embedding_id VARCHAR(256),
            view_count BIGINT NOT NULL DEFAULT 0,
            apply_count BIGINT NOT NULL DEFAULT 0,
            posted_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_jobs_external_id_source UNIQUE (external_id, source)
        )
    """)
    # Standard B-tree indexes
    op.execute("CREATE INDEX ix_jobs_company_id ON jobs (company_id)")
    op.execute("CREATE INDEX ix_jobs_ingestion_run_id ON jobs (ingestion_run_id)")
    op.execute("CREATE INDEX ix_jobs_title ON jobs (title)")
    op.execute("CREATE INDEX ix_jobs_source ON jobs (source)")
    op.execute("CREATE INDEX ix_jobs_status ON jobs (status)")
    op.execute("CREATE INDEX ix_jobs_seniority ON jobs (seniority)")
    op.execute("CREATE INDEX ix_jobs_country ON jobs (country)")
    op.execute("CREATE INDEX ix_jobs_posted_at ON jobs (posted_at)")
    op.execute("CREATE INDEX ix_jobs_company_status ON jobs (company_id, status)")
    op.execute("CREATE INDEX ix_jobs_posted_at_status ON jobs (posted_at, status)")
    op.execute("CREATE INDEX ix_jobs_is_remote_seniority ON jobs (is_remote, seniority)")

    # GIN indexes for ARRAY containment queries
    op.execute("CREATE INDEX ix_jobs_skills_gin ON jobs USING gin (skills)")
    op.execute("CREATE INDEX ix_jobs_tags_gin ON jobs USING gin (tags)")


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.execute("DROP INDEX IF EXISTS ix_jobs_tags_gin;")
    op.execute("DROP INDEX IF EXISTS ix_jobs_skills_gin;")
    op.drop_table("jobs")
    op.drop_table("ingestion_runs")
    op.drop_table("companies")

    # Drop ENUM types
    op.execute("DROP TYPE IF EXISTS seniority_level_enum;")
    op.execute("DROP TYPE IF EXISTS employment_type_enum;")
    op.execute("DROP TYPE IF EXISTS job_status_enum;")
    op.execute("DROP TYPE IF EXISTS ingestion_status_enum;")
