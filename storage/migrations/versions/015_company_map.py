"""company map: location, stage/sector, people links and startup news

Revision ID: 015_company_map
Revises: 014_interview_role
Create Date: 2026-10-14 00:00:00.000000

Why this exists
---------------
Company Intel becomes a map of Bengaluru employers
(``docs/superpowers/specs/2026-10-14-company-map-design.md``).

``companies`` gains what the map draws and filters on: a stable ``slug``, a
one-line ``summary`` with the source it came from, ``stage``/``sector``, a
canonical ``area`` plus the printed ``address``/``pincode``, coordinates with
an honest ``geo_precision`` (exact | street | area | city) and ``geo_source``,
the ``wikidata_id`` second identity key, a counted ``employee_count``, the
company's own ``x_url``, ``last_funding_at`` (denormalised from news for the
"raised recently" filter) and ``enriched_at``. Which source wrote each field
lives in the existing ``metadata`` JSONB under ``provenance``.

People join ``company_contacts`` as kinds founder / leader / engineer with
public profile links and a ``source_kind``. The partial unique index dedupes
curated people per company; a user's private contacts are untouched by it.

``company_news`` holds classified articles from public startup-news feeds.
Its ``company_id`` is SET NULL on delete so an article outlives a pruned
company.

Every statement is IF NOT EXISTS so the migration is safe on a database that
was created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "015_company_map"
down_revision: str | None = "014_interview_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMPANY_INDEXES = ("area", "stage", "sector", "geo_precision", "last_funding_at")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE companies
        ADD COLUMN IF NOT EXISTS slug VARCHAR(96),
        ADD COLUMN IF NOT EXISTS summary VARCHAR(200),
        ADD COLUMN IF NOT EXISTS summary_source VARCHAR(16),
        ADD COLUMN IF NOT EXISTS stage VARCHAR(16),
        ADD COLUMN IF NOT EXISTS sector VARCHAR(24),
        ADD COLUMN IF NOT EXISTS area VARCHAR(48),
        ADD COLUMN IF NOT EXISTS address VARCHAR(300),
        ADD COLUMN IF NOT EXISTS pincode VARCHAR(6),
        ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS lng DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS geo_precision VARCHAR(8),
        ADD COLUMN IF NOT EXISTS geo_source VARCHAR(16),
        ADD COLUMN IF NOT EXISTS wikidata_id VARCHAR(16),
        ADD COLUMN IF NOT EXISTS employee_count INTEGER,
        ADD COLUMN IF NOT EXISTS x_url VARCHAR(256),
        ADD COLUMN IF NOT EXISTS last_funding_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;
    """)
    # Named the way SQLAlchemy names a column-level unique constraint, so a
    # database built from metadata and one built from migrations agree.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'companies_slug_key'
            ) THEN
                ALTER TABLE companies ADD CONSTRAINT companies_slug_key UNIQUE (slug);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'companies_wikidata_id_key'
            ) THEN
                ALTER TABLE companies
                    ADD CONSTRAINT companies_wikidata_id_key UNIQUE (wikidata_id);
            END IF;
        END
        $$;
    """)
    for column in _COMPANY_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_companies_{column} ON companies({column});")

    op.execute("""
        ALTER TABLE company_contacts
        ADD COLUMN IF NOT EXISTS x_url VARCHAR(256),
        ADD COLUMN IF NOT EXISTS github_url VARCHAR(256),
        ADD COLUMN IF NOT EXISTS profile_url VARCHAR(512),
        ADD COLUMN IF NOT EXISTS source_kind VARCHAR(16);
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_company_contacts_curated_person
        ON company_contacts (company_id, kind, lower(name))
        WHERE user_id IS NULL AND name IS NOT NULL;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS company_news (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            url VARCHAR(1024) NOT NULL,
            source VARCHAR(24) NOT NULL,
            title VARCHAR(400) NOT NULL,
            snippet VARCHAR(600),
            published_at TIMESTAMPTZ,
            category VARCHAR(16) NOT NULL DEFAULT 'other',
            company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
            company_name_raw VARCHAR(200),
            round VARCHAR(24),
            amount_text VARCHAR(64),
            amount_usd NUMERIC,
            investors TEXT[],
            extracted_by VARCHAR(8),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT company_news_url_key UNIQUE (url)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_company_news_company_id ON company_news(company_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_news_published_at ON company_news(published_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS company_news;")

    op.execute("DROP INDEX IF EXISTS uq_company_contacts_curated_person;")
    op.execute("""
        ALTER TABLE company_contacts
        DROP COLUMN IF EXISTS source_kind,
        DROP COLUMN IF EXISTS profile_url,
        DROP COLUMN IF EXISTS github_url,
        DROP COLUMN IF EXISTS x_url;
    """)

    for column in reversed(_COMPANY_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS ix_companies_{column};")
    op.execute("ALTER TABLE companies DROP CONSTRAINT IF EXISTS companies_wikidata_id_key;")
    op.execute("ALTER TABLE companies DROP CONSTRAINT IF EXISTS companies_slug_key;")
    op.execute("""
        ALTER TABLE companies
        DROP COLUMN IF EXISTS enriched_at,
        DROP COLUMN IF EXISTS last_funding_at,
        DROP COLUMN IF EXISTS x_url,
        DROP COLUMN IF EXISTS employee_count,
        DROP COLUMN IF EXISTS wikidata_id,
        DROP COLUMN IF EXISTS geo_source,
        DROP COLUMN IF EXISTS geo_precision,
        DROP COLUMN IF EXISTS lng,
        DROP COLUMN IF EXISTS lat,
        DROP COLUMN IF EXISTS pincode,
        DROP COLUMN IF EXISTS address,
        DROP COLUMN IF EXISTS area,
        DROP COLUMN IF EXISTS sector,
        DROP COLUMN IF EXISTS stage,
        DROP COLUMN IF EXISTS summary_source,
        DROP COLUMN IF EXISTS summary,
        DROP COLUMN IF EXISTS slug;
    """)
