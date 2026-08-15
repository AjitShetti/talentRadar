"""sync missing columns

Revision ID: 006_sync_missing_columns
Revises: 005_purge_bad_seeded_data
Create Date: 2026-08-15 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '006_sync_missing_columns'
down_revision: Union[str, None] = '005_purge_bad_seeded_data'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safely add any missing columns to job_applications
    op.execute("""
        ALTER TABLE job_applications
        ADD COLUMN IF NOT EXISTS resume_version_id UUID REFERENCES resumes(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS cover_letter_id UUID REFERENCES cover_letters(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS applied_at_explicit TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS oa_completed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS interview_scheduled_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS outcome_at TIMESTAMPTZ;
    """)

    # Safely add any missing columns to interview_sessions
    op.execute("""
        ALTER TABLE interview_sessions
        ADD COLUMN IF NOT EXISTS adaptive BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS application_id UUID REFERENCES job_applications(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS prep_plan_id UUID REFERENCES interview_prep_plans(id) ON DELETE SET NULL;
    """)

    # Create index if not exists
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_interview_sessions_application_id ON interview_sessions(application_id);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE interview_sessions
        DROP COLUMN IF EXISTS prep_plan_id,
        DROP COLUMN IF EXISTS application_id,
        DROP COLUMN IF EXISTS adaptive;
    """)
    op.execute("""
        ALTER TABLE job_applications
        DROP COLUMN IF EXISTS outcome_at,
        DROP COLUMN IF EXISTS interview_scheduled_at,
        DROP COLUMN IF EXISTS oa_completed_at,
        DROP COLUMN IF EXISTS applied_at_explicit,
        DROP COLUMN IF EXISTS cover_letter_id,
        DROP COLUMN IF EXISTS resume_version_id;
    """)
