"""interview topic: lets a candidate name what they want to be interviewed on

Revision ID: 012_interview_topic
Revises: 011_job_enrichment_status
Create Date: 2026-09-13 00:00:00.000000

Why this exists
---------------
The interview lab offered four fixed tracks (Python DSA, Python backend, SQL,
system design). Candidates interviewing for anything else - React, product
management, data engineering - had nothing to practise.

``interview_track_enum`` already carries the four round *styles* (technical,
coding, system_design, behavioral; added in 004). What was missing is the
subject, and a subject is open-ended text, not an enum value - adding one enum
value per technology would mean a migration per request. So the style stays in
the enum and the subject goes in a short nullable column. Existing sessions
keep ``topic = NULL`` and continue to render by their track.

80 characters matches ``agents.interview.topics.TOPIC_MAX_LENGTH``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "012_interview_topic"
down_revision: str | None = "011_job_enrichment_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column(
            "topic",
            sa.String(length=80),
            nullable=True,
            comment="Free-text subject the candidate chose; NULL for catalogue tracks",
        ),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "topic")
