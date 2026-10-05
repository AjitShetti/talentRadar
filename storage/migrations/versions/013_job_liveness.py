"""job liveness: honest dates on jobs, and a repost history that survives retention

Revision ID: 013_job_liveness
Revises: 012_interview_topic
Create Date: 2026-10-05 00:00:00.000000

Why this exists
---------------
v2 tells a job seeker whether a posting is still open. Before this migration
the index could not answer: the live ATS scraper stamped every row with the
current time as its publish date, and there was no record of when we first or
last saw a posting, or of whether the source still served it.

Six nullable timestamps go on ``jobs``. Nullable is the point - a fact we do
not hold stays absent, and ``domain/liveness.py`` reads absence as absence.
``first_seen_at`` and ``last_seen_at`` are backfilled from the row's own
timestamps, which are true. ``source_posted_at`` is deliberately *not*
backfilled from ``posted_at``: the existing values are the ones this change
exists to stop trusting.

``role_sightings`` holds one small row per (company, normalised title). A
re-listed role arrives under a new external id and retention deletes old job
rows, so this is the only place a repost can be remembered.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "013_job_liveness"
down_revision: str | None = "012_interview_topic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_posted_at", "Publish time reported by the employer's ATS"),
    ("source_updated_at", "Last edit time reported by the employer's ATS"),
    ("first_seen_at", "When this posting first reached our index"),
    ("last_seen_at", "Most recent scrape that returned this posting"),
    ("last_verified_at", "Most recent re-check that found the posting still served"),
    ("closed_at", "When a re-check found the source no longer serving it"),
)


def upgrade() -> None:
    for name, comment in _JOB_COLUMNS:
        op.add_column(
            "jobs",
            sa.Column(
                name,
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now() if name == "first_seen_at" else None,
                comment=comment,
            ),
        )
    op.execute(
        "UPDATE jobs SET first_seen_at = created_at, last_seen_at = updated_at"
    )
    op.create_index("ix_jobs_last_verified_at", "jobs", ["last_verified_at"])

    op.create_table(
        "role_sightings",
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title_key", sa.String(length=200), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("times_reposted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_external_id", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("company_id", "title_key", name="pk_role_sightings"),
    )


def downgrade() -> None:
    op.drop_table("role_sightings")
    op.drop_index("ix_jobs_last_verified_at", table_name="jobs")
    for name, _ in reversed(_JOB_COLUMNS):
        op.drop_column("jobs", name)
