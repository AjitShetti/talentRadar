"""applications: a role the user found somewhere we never indexed

Revision ID: 023_application_manual_role
Revises: 022_application_interview_at
Create Date: 2026-11-03 00:00:00.000000

Why this exists
---------------
The tracker could only hold a posting already in ``jobs``, so a role found on LinkedIn or
an employer's own site could not be tracked at all. ``job_id`` was already nullable; a row
without one now carries what the user typed: the role, the company and, optionally, the
posting's link. Nothing is fetched from that link. Readers prefer the posting's own title
and company when the row has a ``job_id``.

The statements are IF NOT EXISTS / IF EXISTS, so the migration is safe on a database
created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "023_application_manual_role"
down_revision: str | None = "022_application_interview_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE job_applications "
        "ADD COLUMN IF NOT EXISTS role_title VARCHAR(300), "
        "ADD COLUMN IF NOT EXISTS company_name VARCHAR(200), "
        "ADD COLUMN IF NOT EXISTS posting_url TEXT;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE job_applications "
        "DROP COLUMN IF EXISTS posting_url, "
        "DROP COLUMN IF EXISTS company_name, "
        "DROP COLUMN IF EXISTS role_title;"
    )
