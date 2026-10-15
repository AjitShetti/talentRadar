"""outreach: threads, people, touches, good-first-issue snapshots and settings

Revision ID: 017_outreach
Revises: 016_company_is_tech
Create Date: 2026-10-15 00:00:00.000000

Why this exists
---------------
Outreach is an outreach-first job search
(``docs/superpowers/specs/2026-10-15-outreach-design.md``): a per-user thread
per company, the people in it, every touch, and a follow-up clock per person.

* ``outreach_threads``  one per user per company. ``company_id`` is SET NULL on
  delete and ``company_name`` is a snapshot, so a thread outlives its company
  row and a typed company we hold no row for (``company_id`` NULL) is possible.
  The partial unique index is what makes re-adding a company return the
  existing thread.
* ``outreach_people``   the people in a thread. ``contact_id`` points at the
  ``company_contacts`` row that is the source of truth for links and email.
* ``outreach_touches``  what happened (a message the user sent, a reply).
  ``user_id`` is denormalised for the per-day count.
* ``oss_issue_snapshots``  a company's open good-first-issues, shared across
  users because it is public data.
* ``outreach_settings``  one row per user holding the daily target.

The daily target is its own table rather than a ``profiles`` column on
purpose: a ``profiles`` column would put the Profile model in the diff of every
parallel feature branch (events and practice also add tables), whereas a
one-row-per-user table keeps this migration self-contained. For the same reason
the migration creates its own five tables and alters none of the existing ones,
so it stacks with the migrations beside it by re-pointing one
``down_revision`` line.

Every thread's people and touches are removed by the database (``ON DELETE
CASCADE``), which is what the retention prune relies on.

Every statement is IF NOT EXISTS (and the downgrade IF EXISTS) so the
migration is safe on a database that was created from ``models.py`` metadata
directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "017_outreach"
down_revision: str | None = "016_company_is_tech"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS outreach_threads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
            company_name VARCHAR(256) NOT NULL,
            job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
            job_title VARCHAR(256),
            application_id UUID REFERENCES job_applications(id) ON DELETE SET NULL,
            source VARCHAR(12) NOT NULL,
            note VARCHAR(500),
            next_step VARCHAR(140),
            next_step_at DATE,
            closed_at TIMESTAMPTZ,
            outcome VARCHAR(16),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_outreach_threads_user ON outreach_threads(user_id);")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_threads_user_company
        ON outreach_threads (user_id, company_id)
        WHERE company_id IS NOT NULL;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS outreach_people (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id UUID NOT NULL REFERENCES outreach_threads(id) ON DELETE CASCADE,
            contact_id UUID REFERENCES company_contacts(id) ON DELETE SET NULL,
            name VARCHAR(128) NOT NULL,
            title VARCHAR(128),
            kind VARCHAR(24) NOT NULL,
            state VARCHAR(10) NOT NULL DEFAULT 'planned',
            first_sent_at TIMESTAMPTZ,
            last_outbound_at TIMESTAMPTZ,
            follow_ups_sent SMALLINT NOT NULL DEFAULT 0,
            next_follow_up_at TIMESTAMPTZ,
            replied_at TIMESTAMPTZ,
            email VARCHAR(256),
            linkedin_url VARCHAR(512),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    # The partial unique index below only covers people with a contact row, so
    # it cannot serve "the people of a thread" or the ON DELETE CASCADE lookup.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_people_thread ON outreach_people(thread_id);"
    )
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_people_thread_contact
        ON outreach_people (thread_id, contact_id)
        WHERE contact_id IS NOT NULL;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS outreach_touches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id UUID NOT NULL REFERENCES outreach_threads(id) ON DELETE CASCADE,
            person_id UUID REFERENCES outreach_people(id) ON DELETE SET NULL,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            channel VARCHAR(10) NOT NULL,
            direction VARCHAR(3) NOT NULL,
            kind VARCHAR(10) NOT NULL,
            note VARCHAR(500),
            body VARCHAR(2000),
            happened_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_touches_thread ON outreach_touches(thread_id);"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_outreach_touches_user_happened
        ON outreach_touches (user_id, happened_at);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS oss_issue_snapshots (
            company_id UUID PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
            org VARCHAR(128) NOT NULL,
            status VARCHAR(12) NOT NULL,
            total_count INTEGER NOT NULL DEFAULT 0,
            issues JSONB NOT NULL DEFAULT '[]',
            fetched_at TIMESTAMPTZ NOT NULL
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS outreach_settings (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            daily_target SMALLINT NOT NULL DEFAULT 3,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    # Dropping a table drops its indexes. Children first: touches reference
    # people and threads, people reference threads.
    op.execute("DROP TABLE IF EXISTS outreach_touches;")
    op.execute("DROP TABLE IF EXISTS outreach_people;")
    op.execute("DROP TABLE IF EXISTS outreach_threads;")
    op.execute("DROP TABLE IF EXISTS oss_issue_snapshots;")
    op.execute("DROP TABLE IF EXISTS outreach_settings;")
