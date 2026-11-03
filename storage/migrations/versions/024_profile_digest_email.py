"""profiles: the opt-in morning email

Revision ID: 024_profile_digest_email
Revises: 023_application_manual_role
Create Date: 2026-11-03 00:00:01.000000

Why this exists
---------------
Nothing reached a user who was not on the site: a follow-up due, an interview tomorrow or a
take-home deadline showed only on the Overview. ``digest_email`` is the user's choice to get
that day plan by mail each morning (``services/digest.py``). It is false for every row,
existing ones included: nobody is mailed who did not ask.

The statements are IF NOT EXISTS / IF EXISTS, so the migration is safe on a database
created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "024_profile_digest_email"
down_revision: str | None = "023_application_manual_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS digest_email BOOLEAN NOT NULL DEFAULT false;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS digest_email;")
