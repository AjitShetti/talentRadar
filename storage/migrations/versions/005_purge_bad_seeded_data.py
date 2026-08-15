"""purge bad seeded data

Revision ID: 005_purge_bad_seeded_data
Revises: 004_career_platform_models
Create Date: 2026-08-15

Deletes contaminated job records (Wikipedia, Reddit, search listing pages)
and orphaned company records generated from the stale seed corpus.
"""
from alembic import op
import sqlalchemy as sa

revision = "005_purge_bad_seeded_data"
down_revision = "004_career_platform_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Delete contaminated jobs matching non-job domains or search/listing patterns
    op.execute("""
        DELETE FROM jobs
        WHERE source_url IS NULL
           OR source_url ILIKE '%wikipedia.org%'
           OR source_url ILIKE '%reddit.com%'
           OR source_url ILIKE '%youtube.com%'
           OR source_url ILIKE '%youtu.be%'
           OR source_url ILIKE '%medium.com%'
           OR source_url ILIKE '%quora.com%'
           OR source_url ILIKE '%github.com%'
           OR source_url ILIKE '%twitter.com%'
           OR source_url ILIKE '%x.com%'
           OR source_url ILIKE '%facebook.com%'
           OR source_url ILIKE '%instagram.com%'
           OR source_url ILIKE '%/q-%'
           OR source_url ILIKE '%-jobs.html%'
           OR source_url ILIKE '%/jobs/search%'
           OR source_url ILIKE '%/jobs/collections%'
           OR source_url ILIKE '%/jobs/role/%'
           OR source_url ILIKE '%/browse-jobs%'
           OR (source_url ILIKE '%linkedin.com%' AND source_url NOT ILIKE '%/jobs/view/%')
           OR (source_url ILIKE '%indeed.com%' AND source_url NOT ILIKE '%/viewjob%' AND source_url NOT ILIKE '%/rc/clk%')
           OR (source_url ILIKE '%naukri.com%' AND source_url NOT ILIKE '%job-listings-%' AND source_url NOT ILIKE '%job-detail%');
    """)

    # 2. Delete orphaned companies that have no active jobs and are garbage names/domains
    op.execute("""
        DELETE FROM companies
        WHERE id NOT IN (SELECT DISTINCT company_id FROM jobs WHERE company_id IS NOT NULL)
          AND (
              LOWER(name) IN ('en', 'www', 'reddit', 'wikipedia', 'youtube', 'medium', 'quora', 'unknown company')
              OR LOWER(domain) IN ('en.talentradar.internal', 'www.talentradar.internal', 'reddit.talentradar.internal', 'wikipedia.talentradar.internal')
          );
    """)


def downgrade() -> None:
    # Data deletion is irreversible
    pass
