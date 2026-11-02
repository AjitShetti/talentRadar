"""applications: the interview's own time, as the user entered it

Revision ID: 022_application_interview_at
Revises: 021_practice_design
Create Date: 2026-11-02 00:00:00.000000

Why this exists
---------------
``job_applications.interview_scheduled_at`` is stamped with now() when a status moves
to interview, so it says when the row moved and nothing about when the interview is.
The agenda and the briefing still read it as the interview's time.

``interview_at`` holds the time the user typed and is the only thing those surfaces
read. It starts empty for every row: no stamp is copied into it, because none of them
is a time anybody entered. The statements are IF NOT EXISTS / IF EXISTS, so the
migration is safe on a database created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "022_application_interview_at"
down_revision: str | None = "021_practice_design"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS interview_at TIMESTAMPTZ;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE job_applications DROP COLUMN IF EXISTS interview_at;")
