"""outreach pack: the daily pack's two tables and where an email address was found

Revision ID: 020_outreach_pack
Revises: 019_practice
Create Date: 2026-10-26 00:00:00.000000

Why this exists
---------------
The daily outreach pack (docs/superpowers/specs/2026-10-26-daily-outreach-pack-design.md,
section 4) needs:

* ``outreach_packs``: one row per user per IST day, built on the day's first read
  (a unique ``(user_id, day)`` so two concurrent first reads make one pack);
* ``outreach_pack_items``: one row per recipient (``first``) or due follow-up
  (``follow_up``), holding the written message and what the user did with it;
* ``company_contacts.email_source_url``: the page an address was published on,
  which may differ from the page the person was named on.

No email address is stored in either new table: a found one lives on the contact, a
guessed one is computed at read time and never stored.

Every statement is IF NOT EXISTS / IF EXISTS, so the migration is safe on a database
created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "020_outreach_pack"
down_revision: str | None = "019_practice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS email_source_url VARCHAR(512)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS outreach_packs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_outreach_packs_user_day UNIQUE (user_id, day)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS outreach_pack_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pack_id UUID NOT NULL REFERENCES outreach_packs(id) ON DELETE CASCADE,
            kind VARCHAR(12) NOT NULL DEFAULT 'first',
            company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
            company_name VARCHAR(256) NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            contact_id UUID REFERENCES company_contacts(id) ON DELETE SET NULL,
            outreach_person_id UUID REFERENCES outreach_people(id) ON DELETE SET NULL,
            person_name VARCHAR(128),
            person_title VARCHAR(128),
            person_kind VARCHAR(24),
            channel VARCHAR(12) NOT NULL,
            format VARCHAR(24) NOT NULL,
            subject VARCHAR(80),
            message TEXT,
            generated BOOLEAN,
            reason VARCHAR(24),
            status VARCHAR(12) NOT NULL DEFAULT 'new',
            thread_id UUID REFERENCES outreach_threads(id) ON DELETE SET NULL,
            reasons TEXT[] DEFAULT '{}',
            seen_at TIMESTAMPTZ,
            applied_at TIMESTAMPTZ,
            acted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_pack_items_pack_position "
        "ON outreach_pack_items (pack_id, position)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_pack_items_company_id "
        "ON outreach_pack_items (company_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outreach_pack_items")
    op.execute("DROP TABLE IF EXISTS outreach_packs")
    op.execute("ALTER TABLE company_contacts DROP COLUMN IF EXISTS email_source_url")
