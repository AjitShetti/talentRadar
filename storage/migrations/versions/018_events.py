"""events: the Bengaluru tech-events tracker

Revision ID: 018_events
Revises: 016_company_is_tech
Create Date: 2026-10-15 00:00:00.000000

Why this exists
---------------
Events (``docs/superpowers/specs/2026-10-15-events-design.md``) puts the
Bengaluru tech meetups, talks and demo nights from Luma, GDG and Eventbrite on
one list, lets a user save an event or mark that they are going, and records
who they met there. It creates four tables and alters none of the existing
ones:

* ``events`` holds one row per event. ``(source, external_id)`` is the identity
  a re-read updates. ``listed`` and ``reasons`` are the relevance verdict,
  ``duplicate_of`` points a cross-source duplicate at its primary, and
  ``owner_user_id`` is set only for an event a user added by hand. Times are
  ``timestamptz``.
* ``event_companies`` links an event to the companies on the map. Its primary
  key leads with ``event_id``, so a lookup by ``company_id`` has its own index.
* ``event_saves`` is one user's save or "going" on one event, unique on
  ``(user_id, event_id)``.
* ``event_contacts`` is the people a user met there: private notes that go
  with the user.

Foreign keys follow the spec. Deleting a user or an event cascades to the rows
that depend on it, deleting a company only clears ``event_contacts.company_id``,
and deleting a primary event clears its duplicates' ``duplicate_of``.

Every child foreign-key column is findable by an index, unless an existing
index already leads with it, so a cascade does not scan the child table.

Every statement is IF NOT EXISTS so the migration is safe on a database that
was created from ``models.py`` metadata directly. The downgrade drops children
before parents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "018_events"
down_revision: str | None = "016_company_is_tech"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source VARCHAR(16) NOT NULL,
            external_id VARCHAR(160) NOT NULL,
            url VARCHAR(1024) NOT NULL,
            title VARCHAR(300) NOT NULL,
            snippet VARCHAR(600),
            starts_at TIMESTAMPTZ NOT NULL,
            ends_at TIMESTAMPTZ,
            venue_name VARCHAR(200),
            address VARCHAR(300),
            area VARCHAR(48),
            lat DOUBLE PRECISION,
            lng DOUBLE PRECISION,
            geo_precision VARCHAR(8),
            is_online BOOLEAN NOT NULL DEFAULT false,
            is_free BOOLEAN,
            organiser_name VARCHAR(200),
            organiser_url VARCHAR(512),
            hosts TEXT[],
            format VARCHAR(16),
            topics TEXT[],
            listed BOOLEAN NOT NULL DEFAULT false,
            reasons TEXT[],
            is_cancelled BOOLEAN NOT NULL DEFAULT false,
            duplicate_of UUID REFERENCES events(id) ON DELETE SET NULL,
            owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            detail_fetched_at TIMESTAMPTZ,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_events_source_external_id UNIQUE (source, external_id)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_starts_at ON events (starts_at);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_listed_starts_at ON events (listed, starts_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_duplicate_of ON events (duplicate_of);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_owner_user_id ON events (owner_user_id);")

    op.execute("""
        CREATE TABLE IF NOT EXISTS event_companies (
            event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            role VARCHAR(12) NOT NULL,
            matched_on VARCHAR(24) NOT NULL,
            PRIMARY KEY (event_id, company_id)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_event_companies_company_id ON event_companies (company_id);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS event_saves (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            status VARCHAR(8) NOT NULL,
            note VARCHAR(2000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_event_saves_user_event UNIQUE (user_id, event_id)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_event_saves_event_id ON event_saves (event_id);")

    op.execute("""
        CREATE TABLE IF NOT EXISTS event_contacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            name VARCHAR(200) NOT NULL,
            title VARCHAR(200),
            company_name VARCHAR(200),
            company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
            linkedin_url VARCHAR(512),
            x_url VARCHAR(512),
            github_url VARCHAR(512),
            email VARCHAR(254),
            note VARCHAR(2000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_event_contacts_user_created "
        "ON event_contacts (user_id, created_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_event_contacts_event_id ON event_contacts (event_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_event_contacts_company_id ON event_contacts (company_id);"
    )


def downgrade() -> None:
    # Children first. Dropping a table takes its own indexes with it.
    op.execute("DROP TABLE IF EXISTS event_contacts;")
    op.execute("DROP TABLE IF EXISTS event_saves;")
    op.execute("DROP TABLE IF EXISTS event_companies;")
    op.execute("DROP TABLE IF EXISTS events;")
