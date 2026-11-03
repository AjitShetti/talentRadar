"""profiles: the daily routine and whether the first-run tour has been seen

Revision ID: 023_profile_routine
Revises: 022_application_interview_at
Create Date: 2026-11-03 00:00:00.000000

Why this exists
---------------
``routine`` holds the blocks a user sets for their day and today's ticks on them (the
shape is in ``domain/routine.py``). It starts empty for every row: a routine is something
the user sets, so none is invented for anyone.

``tour_seen_at`` is when the user finished or skipped the first-run tour. The tour is for
new users only. Anyone who has already completed onboarding has found their way around,
and a tour that opened on the next sign-in would be a tour of an app they already use,
so they are marked as having seen it now. The backfill runs once, on the rows that exist
when this migration does; a user who onboards afterwards has no stamp and gets the tour.

The statements are IF NOT EXISTS / IF EXISTS, so the migration is safe on a database
created from ``models.py`` metadata directly, and the backfill only touches rows with no
stamp, so running it again changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "023_profile_routine"
down_revision: str | None = "022_application_interview_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS routine JSONB;")
    op.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS tour_seen_at TIMESTAMPTZ;")
    op.execute(
        "UPDATE profiles SET tour_seen_at = now() "
        "WHERE onboarding_completed IS TRUE AND tour_seen_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS tour_seen_at;")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS routine;")
