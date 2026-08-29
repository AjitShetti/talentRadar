"""daily job matches: cached top-3 openings per user per target role/day

Revision ID: 009_daily_job_matches
Revises: 008_resume_structured_content
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '009_daily_job_matches'
down_revision: Union[str, None] = '008_resume_structured_content'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'daily_job_matches',
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('matched_role', sa.String(length=256), nullable=False,
                  comment="Which Profile.target_roles[] entry produced this match"),
        sa.Column('match_date', sa.Date(), nullable=False,
                  comment="Server date this batch was computed for"),
        sa.Column('rank', sa.Integer(), nullable=False,
                  comment="0..2, ordering within that day's top-3"),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'match_date', 'rank', name='uq_daily_job_matches_user_date_rank'),
        if_not_exists=True,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_daily_job_matches_user_id "
        "ON daily_job_matches(user_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_daily_job_matches_user_date "
        "ON daily_job_matches(user_id, match_date);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_job_matches;")
