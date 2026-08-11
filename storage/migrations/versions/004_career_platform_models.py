"""career platform models

Revision ID: 004_career_platform_models
Revises: add_job_applications
Create Date: 2026-08-11

Adds the full job-seeker career platform schema:
  profiles, resumes, cover_letters, skills, user_skills,
  application_events, interview_prep_plans, company_profiles,
  market_snapshots, learning_tasks, agent_memory, analytics_snapshots
and extends existing tables:
  - job_applications: funnel stage timestamps + documents used + events
  - interview_sessions: new track values, adaptive flag, app/prep links

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_career_platform_models"
down_revision = "add_job_applications"
branch_labels = None
depends_on = None

# Helper: create an ARRAY-of-string column in a Postgres-compatible way.
# SQLite fallback (used by tests) renders as JSON — see storage/models.py
# for the matching TypeDecorator.
ARRAY_STR = postgresql.ARRAY(sa.String())


def _create_string_array_column(*args, **kwargs):
    """Alias kept for readability; Postgres-only migrations may use ARRAY_STR directly."""
    return postgresql.ARRAY(sa.String())


def upgrade() -> None:
    bind = op.get_bind()
    # ─────────────────────────────────────────────────────────────────────
    # Extend enums
    # ─────────────────────────────────────────────────────────────────────
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE application_status_enum ADD VALUE IF NOT EXISTS 'online_assessment'")
        op.execute(
            "ALTER TYPE interview_track_enum ADD VALUE IF NOT EXISTS 'coding'"
        )
        op.execute(
            "ALTER TYPE interview_track_enum ADD VALUE IF NOT EXISTS 'technical'"
        )
        op.execute(
            "ALTER TYPE interview_track_enum ADD VALUE IF NOT EXISTS 'behavioral'"
        )

    # ─────────────────────────────────────────────────────────────────────
    # profiles
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=256), nullable=True),
        sa.Column("headline", sa.String(length=512), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("target_roles", ARRAY_STR, nullable=True),
        sa.Column("target_locations", ARRAY_STR, nullable=True),
        sa.Column("is_remote_preferred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("target_salary_min", sa.Float(), nullable=True),
        sa.Column("target_salary_max", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(length=8), nullable=True),
        sa.Column("years_experience", sa.Float(), nullable=True),
        sa.Column("current_role", sa.String(length=256), nullable=True),
        sa.Column("career_goals", sa.Text(), nullable=True),
        sa.Column("onboarding_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("active_resume_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    # ─────────────────────────────────────────────────────────────────────
    # resumes
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_name", sa.String(length=256), server_default="Original", nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=True),
        sa.Column("file_type", sa.String(length=16), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("ats_score", sa.Float(), nullable=True),
        sa.Column("ats_analysis", postgresql.JSONB(), nullable=True),
        sa.Column("is_tailored", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("target_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resumes_profile_id", "resumes", ["profile_id"])
    op.create_index("ix_resumes_target_job_id", "resumes", ["target_job_id"])

    # Deferred FK — profiles.active_resume_id -> resumes.id (circular dep)
    op.create_foreign_key(
        "fk_profiles_active_resume",
        "profiles", "resumes",
        ["active_resume_id"], ["id"],
        ondelete="SET NULL",
    )

    # ─────────────────────────────────────────────────────────────────────
    # cover_letters
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "cover_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=256), server_default="Cover Letter", nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cover_letters_profile_id", "cover_letters", ["profile_id"])

    # ─────────────────────────────────────────────────────────────────────
    # skills
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("aliases", ARRAY_STR, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_skills_name", "skills", ["name"])

    # ─────────────────────────────────────────────────────────────────────
    # user_skills
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "user_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("proficiency", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=64), server_default="resume", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_name", name="uq_user_skills_user_skill"),
    )
    op.create_index("ix_user_skills_user_id", "user_skills", ["user_id"])
    op.create_index("ix_user_skills_skill_name", "user_skills", ["skill_name"])

    # ─────────────────────────────────────────────────────────────────────
    # application_events
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "application_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_events_application_id", "application_events", ["application_id"])

    # ─────────────────────────────────────────────────────────────────────
    # interview_prep_plans
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "interview_prep_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content", postgresql.JSONB(), nullable=True),
        sa.Column("interview_rounds", ARRAY_STR, nullable=True),
        sa.Column("focus_areas", ARRAY_STR, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interview_prep_plans_application_id", "interview_prep_plans", ["application_id"])
    op.create_index("ix_interview_prep_plans_user_id", "interview_prep_plans", ["user_id"])

    # ─────────────────────────────────────────────────────────────────────
    # company_profiles
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "company_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tech_stack", ARRAY_STR, nullable=True),
        sa.Column("salary_ranges", postgresql.JSONB(), nullable=True),
        sa.Column("interview_patterns", postgresql.JSONB(), nullable=True),
        sa.Column("hiring_trends", postgresql.JSONB(), nullable=True),
        sa.Column("culture_summary", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), server_default="aggregated", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index("ix_company_profiles_company_id", "company_profiles", ["company_id"])

    # ─────────────────────────────────────────────────────────────────────
    # market_snapshots
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "market_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=128), server_default="all", nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_date", "scope", name="uq_market_snapshots_date_scope"),
    )
    op.create_index("ix_market_snapshots_snapshot_date", "market_snapshots", ["snapshot_date"])
    op.create_index("ix_market_snapshots_scope", "market_snapshots", ["scope"])

    # ─────────────────────────────────────────────────────────────────────
    # learning_tasks
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "learning_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("resources", ARRAY_STR, nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("source", sa.String(length=64), server_default="career_coach", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_tasks_user_id", "learning_tasks", ["user_id"])
    op.create_index("ix_learning_tasks_skill_name", "learning_tasks", ["skill_name"])
    op.create_index("ix_learning_tasks_user_status", "learning_tasks", ["user_id", "status"])

    # ─────────────────────────────────────────────────────────────────────
    # agent_memory
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_memory_user_id", "agent_memory", ["user_id"])
    op.create_index("ix_agent_memory_memory_type", "agent_memory", ["memory_type"])
    op.create_index("ix_agent_memory_user_type", "agent_memory", ["user_id", "memory_type"])

    # ─────────────────────────────────────────────────────────────────────
    # analytics_snapshots
    # ─────────────────────────────────────────────────────────────────────
    op.create_table(
        "analytics_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_snapshots_user_id", "analytics_snapshots", ["user_id"])
    op.create_index("ix_analytics_snapshots_snapshot_date", "analytics_snapshots", ["snapshot_date"])

    # ─────────────────────────────────────────────────────────────────────
    # Extend job_applications
    # ─────────────────────────────────────────────────────────────────────
    op.add_column(
        "job_applications",
        sa.Column("resume_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("cover_letter_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("applied_at_explicit", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("oa_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("interview_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_applications_resume_version",
        "job_applications", "resumes",
        ["resume_version_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_job_applications_cover_letter",
        "job_applications", "cover_letters",
        ["cover_letter_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_job_applications_status", "job_applications", ["status"])

    # ─────────────────────────────────────────────────────────────────────
    # Extend interview_sessions
    # ─────────────────────────────────────────────────────────────────────
    op.add_column(
        "interview_sessions",
        sa.Column("adaptive", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("prep_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_interview_sessions_application",
        "interview_sessions", "job_applications",
        ["application_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interview_sessions_prep_plan",
        "interview_sessions", "interview_prep_plans",
        ["prep_plan_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_interview_sessions_application_id", "interview_sessions", ["application_id"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_interview_sessions_application_id", table_name="interview_sessions")
    op.drop_constraint("fk_interview_sessions_prep_plan", "interview_sessions", type_="foreignkey")
    op.drop_constraint("fk_interview_sessions_application", "interview_sessions", type_="foreignkey")
    op.drop_column("interview_sessions", "prep_plan_id")
    op.drop_column("interview_sessions", "application_id")
    op.drop_column("interview_sessions", "adaptive")

    op.drop_index("ix_job_applications_status", table_name="job_applications")
    op.drop_constraint("fk_job_applications_cover_letter", "job_applications", type_="foreignkey")
    op.drop_constraint("fk_job_applications_resume_version", "job_applications", type_="foreignkey")
    op.drop_column("job_applications", "outcome_at")
    op.drop_column("job_applications", "interview_scheduled_at")
    op.drop_column("job_applications", "oa_completed_at")
    op.drop_column("job_applications", "applied_at_explicit")
    op.drop_column("job_applications", "cover_letter_id")
    op.drop_column("job_applications", "resume_version_id")

    op.drop_index("ix_analytics_snapshots_snapshot_date", table_name="analytics_snapshots")
    op.drop_index("ix_analytics_snapshots_user_id", table_name="analytics_snapshots")
    op.drop_table("analytics_snapshots")
    op.drop_index("ix_agent_memory_user_type", table_name="agent_memory")
    op.drop_index("ix_agent_memory_memory_type", table_name="agent_memory")
    op.drop_index("ix_agent_memory_user_id", table_name="agent_memory")
    op.drop_table("agent_memory")
    op.drop_index("ix_learning_tasks_user_status", table_name="learning_tasks")
    op.drop_index("ix_learning_tasks_skill_name", table_name="learning_tasks")
    op.drop_index("ix_learning_tasks_user_id", table_name="learning_tasks")
    op.drop_table("learning_tasks")
    op.drop_index("ix_market_snapshots_scope", table_name="market_snapshots")
    op.drop_index("ix_market_snapshots_snapshot_date", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_company_profiles_company_id", table_name="company_profiles")
    op.drop_table("company_profiles")
    op.drop_index("ix_interview_prep_plans_user_id", table_name="interview_prep_plans")
    op.drop_index("ix_interview_prep_plans_application_id", table_name="interview_prep_plans")
    op.drop_table("interview_prep_plans")
    op.drop_index("ix_application_events_application_id", table_name="application_events")
    op.drop_table("application_events")
    op.drop_index("ix_user_skills_skill_name", table_name="user_skills")
    op.drop_index("ix_user_skills_user_id", table_name="user_skills")
    op.drop_table("user_skills")
    op.drop_index("ix_skills_name", table_name="skills")
    op.drop_table("skills")
    op.drop_index("ix_cover_letters_profile_id", table_name="cover_letters")
    op.drop_table("cover_letters")
    op.drop_index("ix_resumes_target_job_id", table_name="resumes")
    op.drop_index("ix_resumes_profile_id", table_name="resumes")
    op.drop_table("resumes")
    op.drop_constraint("fk_profiles_active_resume", "profiles", type_="foreignkey")
    op.drop_index("ix_profiles_user_id", table_name="profiles")
    op.drop_table("profiles")

    if bind.dialect.name == "postgresql":
        # NOTE: enum value removal is not supported by ALTER TYPE in older PG;
        # the added values are left in place on downgrade for safety.
        pass
