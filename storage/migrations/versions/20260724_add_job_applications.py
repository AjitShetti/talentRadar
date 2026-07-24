"""add job_applications table

Revision ID: add_job_applications
Revises: 003_add_interview_tables
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'add_job_applications'
down_revision = '003_add_interview_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'application_status_enum') THEN
                CREATE TYPE application_status_enum AS ENUM ('saved', 'applied', 'screening', 'interview', 'offer', 'rejected', 'withdrawn');
            END IF;
        END$$;
    """)
    op.create_table(
        'job_applications',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', postgresql.ENUM('saved', 'applied', 'screening', 'interview', 'offer', 'rejected', 'withdrawn', name='application_status_enum', create_type=False), nullable=False, server_default='saved'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'job_id', name='uq_job_applications_user_job'),
    )
    op.create_index('ix_job_applications_user_id', 'job_applications', ['user_id'])
    op.create_index('ix_job_applications_job_id', 'job_applications', ['job_id'])
    op.create_index('ix_job_applications_user_status', 'job_applications', ['user_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_job_applications_user_status', table_name='job_applications')
    op.drop_index('ix_job_applications_job_id', table_name='job_applications')
    op.drop_index('ix_job_applications_user_id', table_name='job_applications')
    op.drop_table('job_applications')
    op.execute("DROP TYPE IF EXISTS application_status_enum")
