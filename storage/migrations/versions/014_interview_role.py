"""interview role: lets a mock interview be anchored to one real posting

Revision ID: 014_interview_role
Revises: 013_job_liveness
Create Date: 2026-10-05 00:00:01.000000

Why this exists
---------------
"Prepare for this role" starts an Interview Lab session that knows the job it
is for. Two nullable columns on ``interview_sessions`` carry that:

``job_id`` links the session to the posting, so a role's page can show the
last prep score for it. It is SET NULL on delete because job rows are pruned
by retention and a finished session must outlive the posting it was for.

``role_context`` is the bounded, sanitised description of the role that is
handed to the interviewer (``agents/interview/role_context.py``). It is stored
rather than rebuilt each turn so that every turn of a session sees the same
role, and so the text never has to round-trip through the browser - the
client-held agent state is untrusted, and this text goes into a system prompt.

Existing sessions keep both NULL and behave exactly as before.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "014_interview_role"
down_revision: str | None = "013_job_liveness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_interview_sessions_job_id",
        "interview_sessions",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_interview_sessions_job_id", "interview_sessions", ["job_id"])
    op.add_column("interview_sessions", sa.Column("role_context", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_sessions", "role_context")
    op.drop_index("ix_interview_sessions_job_id", table_name="interview_sessions")
    op.drop_constraint(
        "fk_interview_sessions_job_id", "interview_sessions", type_="foreignkey"
    )
    op.drop_column("interview_sessions", "job_id")
