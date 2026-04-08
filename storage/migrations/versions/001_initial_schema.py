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
    # Postgres ENUM types                                                  #
    # ------------------------------------------------------------------ #
    ingestion_status_enum = postgresql.ENUM(
        "pending", "running", "success", "partial", "failed",
        name="ingestion_status_enum",
        create_type=True,
    )
    job_status_enum = postgresql.ENUM(
        "active", "expired", "filled", "duplicate", "archived",
        name="job_status_enum",
        create_type=True,
    )
    employment_type_enum = postgresql.ENUM(
        "full_time", "part_time", "contract", "internship", "freelance",
        name="employment_type_enum",
        create_type=True,
    )
    seniority_level_enum = postgresql.ENUM(
        "intern", "junior", "mid", "senior", "lead",
        "principal", "staff", "director", "vp", "c_level",
        name="seniority_level_enum",
        create_type=True,
    )

    ingestion_status_enum.create(op.get_bind(), checkfirst=True)
    job_status_enum.create(op.get_bind(), checkfirst=True)
    employment_type_enum.create(op.get_bind(), checkfirst=True)
    seniority_level_enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------ #
    # companies                                                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("domain", sa.String(256), nullable=True),
        sa.Column("linkedin_url", sa.String(512), nullable=True),
        sa.Column("website_url", sa.String(512), nullable=True),
        sa.Column("logo_url", sa.String(1024), nullable=True),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("hq_country", sa.String(64), nullable=True),
        sa.Column("hq_city", sa.String(128), nullable=True),
        sa.Column("employee_count_range", sa.String(64), nullable=True),
        sa.Column("founded_year", sa.Integer, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_companies_name", "companies", ["name"])
    op.create_index("ix_companies_industry", "companies", ["industry"])
    op.create_index("ix_companies_hq_country", "companies", ["hq_country"])
    op.create_unique_constraint("uq_companies_domain", "companies", ["domain"])

    # ------------------------------------------------------------------ #
    # ingestion_runs                                                       #
    # ------------------------------------------------------------------ #
    op.create_table(
        "ingestion_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "success", "partial", "failed",
                name="ingestion_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobs_discovered", sa.Integer, nullable=False, server_default="0"),
        sa.Column("jobs_inserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("jobs_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("jobs_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("error_trace", postgresql.JSONB, nullable=True),
        sa.Column("run_config", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_ingestion_runs_company_id", "ingestion_runs", ["company_id"])
    op.create_index("ix_ingestion_runs_source", "ingestion_runs", ["source"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])
    op.create_index("ix_ingestion_runs_started_at", "ingestion_runs", ["started_at"])
    op.create_index(
        "ix_ingestion_runs_source_status",
        "ingestion_runs",
        ["source", "status"],
    )

    # ------------------------------------------------------------------ #
    # jobs                                                                 #
    # ------------------------------------------------------------------ #
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_id", sa.String(256), nullable=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description_raw", sa.Text, nullable=True),
        sa.Column("description_clean", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active", "expired", "filled", "duplicate", "archived",
                name="job_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "employment_type",
            sa.Enum(
                "full_time", "part_time", "contract", "internship", "freelance",
                name="employment_type_enum",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "seniority",
            sa.Enum(
                "intern", "junior", "mid", "senior", "lead",
                "principal", "staff", "director", "vp", "c_level",
                name="seniority_level_enum",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("location_raw", sa.String(256), nullable=True),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column(
            "is_remote", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("salary_raw", sa.String(256), nullable=True),
        sa.Column("salary_min", sa.Float, nullable=True),
        sa.Column("salary_max", sa.Float, nullable=True),
        sa.Column("salary_currency", sa.String(8), nullable=True),
        sa.Column("skills", postgresql.ARRAY(sa.String), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String), nullable=True),
        sa.Column("embedding_id", sa.String(256), nullable=True),
        sa.Column("view_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("apply_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Standard B-tree indexes
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_ingestion_run_id", "jobs", ["ingestion_run_id"])
    op.create_index("ix_jobs_title", "jobs", ["title"])
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_seniority", "jobs", ["seniority"])
    op.create_index("ix_jobs_country", "jobs", ["country"])
    op.create_index("ix_jobs_posted_at", "jobs", ["posted_at"])
    op.create_index("ix_jobs_company_status", "jobs", ["company_id", "status"])
    op.create_index("ix_jobs_posted_at_status", "jobs", ["posted_at", "status"])
    op.create_index("ix_jobs_is_remote_seniority", "jobs", ["is_remote", "seniority"])

    # Deduplication constraint
    op.create_unique_constraint(
        "uq_jobs_external_id_source", "jobs", ["external_id", "source"]
    )

    # GIN indexes for ARRAY containment queries (skills @> '{Python}' etc.)
    op.execute(
        "CREATE INDEX ix_jobs_skills_gin ON jobs USING gin (skills);"
    )
    op.execute(
        "CREATE INDEX ix_jobs_tags_gin ON jobs USING gin (tags);"
    )


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
