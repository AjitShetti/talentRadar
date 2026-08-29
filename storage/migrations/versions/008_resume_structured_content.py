"""resumes: structured_content JSONB column for the Resume Studio LaTeX editor

Revision ID: 008_resume_structured_content
Revises: 007_company_intel_directory
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '008_resume_structured_content'
down_revision: Union[str, None] = '007_company_intel_directory'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE resumes
        ADD COLUMN IF NOT EXISTS structured_content JSONB;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE resumes DROP COLUMN IF EXISTS structured_content;")
