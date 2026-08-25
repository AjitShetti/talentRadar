"""company intel directory: company profile fields + company_contacts

Revision ID: 007_company_intel_directory
Revises: 006_sync_missing_columns
Create Date: 2026-08-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '007_company_intel_directory'
down_revision: Union[str, None] = '006_sync_missing_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Directory fields on companies. IF NOT EXISTS keeps this re-runnable
    # against databases that were created from models.py metadata directly.
    op.execute("""
        ALTER TABLE companies
        ADD COLUMN IF NOT EXISTS description TEXT,
        ADD COLUMN IF NOT EXISTS tier VARCHAR(32),
        ADD COLUMN IF NOT EXISTS github_org VARCHAR(128),
        ADD COLUMN IF NOT EXISTS careers_url VARCHAR(512),
        ADD COLUMN IF NOT EXISTS office_cities TEXT[];
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_tier ON companies(tier);")
    # Directory filtering is "which companies have an office in <city>",
    # which is a containment test against office_cities.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_office_cities "
        "ON companies USING GIN (office_cities);"
    )

    op.create_table(
        'company_contacts',
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True,
                  comment='NULL = curated public contact; set = private to that user'),
        sa.Column('kind', sa.String(length=24), nullable=False,
                  server_default='careers_inbox',
                  comment='careers_inbox | careers_page | recruiter | referral | other'),
        sa.Column('name', sa.String(length=128), nullable=True),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('email', sa.String(length=256), nullable=True),
        sa.Column('linkedin_url', sa.String(length=512), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=512), nullable=True,
                  comment='Public page this contact was read from'),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default='false',
                  comment='True only when a human confirmed the contact still works'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        if_not_exists=True,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_contacts_company_id "
        "ON company_contacts(company_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_contacts_user_id "
        "ON company_contacts(user_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_contacts_company_user "
        "ON company_contacts(company_id, user_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS company_contacts;")
    op.execute("DROP INDEX IF EXISTS ix_companies_office_cities;")
    op.execute("DROP INDEX IF EXISTS ix_companies_tier;")
    op.execute("""
        ALTER TABLE companies
        DROP COLUMN IF EXISTS description,
        DROP COLUMN IF EXISTS tier,
        DROP COLUMN IF EXISTS github_org,
        DROP COLUMN IF EXISTS careers_url,
        DROP COLUMN IF EXISTS office_cities;
    """)
