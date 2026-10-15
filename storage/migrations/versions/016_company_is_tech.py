"""company map: only tech employers are listed

Revision ID: 016_company_is_tech
Revises: 015_company_map
Create Date: 2026-10-15 00:00:00.000000

Why this exists
---------------
The company map shows tech employers only (startups, scale-ups, unicorns, Big
Tech, GCCs and IT services); the first Wikidata import also brought in a
university, a hospital, a brewery and a burrito chain. ``companies.is_tech``
holds the verdict (``domain.company_taxonomy.is_tech``): TRUE is listed,
FALSE is a classified non-tech row, NULL has not been classified yet and is
not listed either.

Backfill, in SQL where SQL can decide: a row with a curated catalogue tier is
tech. An importer also writes ``tier`` ("startup" for a young unlisted company,
"vc"), but it records its source in ``metadata.provenance.tier``, so only a
tier with no provenance, or a curated one, counts. Every other row stays NULL
for the ``tech`` refresh step (``services/company_map/tech.py``), which reads
the stored descriptions.

Every statement is IF NOT EXISTS so the migration is safe on a database that
was created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "016_company_is_tech"
down_revision: str | None = "015_company_map"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_tech BOOLEAN;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_is_tech ON companies(is_tech);")
    op.execute("""
        UPDATE companies
        SET is_tech = TRUE
        WHERE is_tech IS NULL
          AND tier IS NOT NULL
          AND COALESCE(metadata -> 'provenance' ->> 'tier', 'curated') = 'curated';
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_is_tech;")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS is_tech;")
