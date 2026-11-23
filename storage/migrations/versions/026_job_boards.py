"""job boards: the roster of public applicant-tracking-system boards

Revision ID: 026_job_boards
Revises: 025_profile_routine
Create Date: 2026-11-23 00:00:00.000000

Why this exists
---------------
The job-board roster (docs/superpowers/specs/2026-11-23-job-board-roster-design.md, section
"The table") keeps one row per public board: an applicant-tracking system (Greenhouse, Ashby,
Lever and so on) and the id that system gives a company. A board is found four ways (an open
list, YC, a careers page, a name probe) and then read on a paced schedule. The row holds that
schedule and what the last read found; the roles read from it are ordinary ``jobs`` rows, so
nothing about a role lives here.

``(ats, board, coalesce(region, ''))`` is unique. ``region`` is set only for Personio, where
one board id can sit on the ``.de`` and the ``.com`` host, so the same board is never two rows.
``coalesce`` is what makes two missing regions collide: a plain unique constraint treats each
NULL as different and would let the same board in twice.

No existing table changes. Every statement is IF NOT EXISTS / IF EXISTS, so the migration is
safe on a database created from ``models.py`` metadata directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "026_job_boards"
down_revision: str | None = "025_profile_routine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_boards (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ats TEXT NOT NULL,
            board TEXT NOT NULL,
            region TEXT,
            company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
            company_name TEXT,
            discovered_via TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            india_jobs INTEGER,
            total_jobs INTEGER,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_read_at TIMESTAMPTZ,
            last_india_at TIMESTAMPTZ,
            next_read_at TIMESTAMPTZ,
            failures INTEGER NOT NULL DEFAULT 0,
            zero_india_reads INTEGER NOT NULL DEFAULT 0,
            not_found_reads INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_boards_ats_board_region "
        "ON job_boards (ats, board, coalesce(region, ''))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_boards_status_next_read "
        "ON job_boards (status, next_read_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_boards_company_id ON job_boards (company_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_boards")
