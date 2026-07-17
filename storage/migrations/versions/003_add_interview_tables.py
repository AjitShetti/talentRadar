"""add interview tables

Revision ID: 003
Revises: 002
Create Date: 2026-07-17 10:56:00.000000

Adds:
    - interview_track_enum        PostgreSQL ENUM type
    - interview_difficulty_enum   PostgreSQL ENUM type
    - interview_sessions          One row per mock interview session
    - interview_answer_scores     One row per question answered in a session
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision identifiers — used by Alembic.
# ---------------------------------------------------------------------------
revision: str = "003_add_interview_tables"
down_revision: Union[str, None] = "002_add_user_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TRACK_VALUES = ["python_dsa", "python_backend", "sql", "system_design"]
DIFFICULTY_VALUES = ["beginner", "mid", "senior"]

interview_track_enum = sa.Enum(
    *TRACK_VALUES,
    name="interview_track_enum",
)
interview_difficulty_enum = sa.Enum(
    *DIFFICULTY_VALUES,
    name="interview_difficulty_enum",
)


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------
def upgrade() -> None:
    # -- Create ENUM types first (must exist before the column is added) ----
    interview_track_enum.create(op.get_bind(), checkfirst=True)
    interview_difficulty_enum.create(op.get_bind(), checkfirst=True)

    # -- interview_sessions -------------------------------------------------
    op.create_table(
        "interview_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "track",
            sa.Enum(*TRACK_VALUES, name="interview_track_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "difficulty",
            sa.Enum(*DIFFICULTY_VALUES, name="interview_difficulty_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "duration_seconds",
            sa.Integer(),
            nullable=True,
            comment="Wall-clock seconds from start to end/abort",
        ),
        sa.Column(
            "total_score",
            sa.Float(),
            nullable=True,
            comment="Aggregate score 0-100, NULL until session is completed",
        ),
        sa.Column(
            "completed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="True = gracefully ended; False = abandoned mid-session",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Indexes for common query patterns
    op.create_index(
        "ix_interview_sessions_user_id",
        "interview_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_interview_sessions_track",
        "interview_sessions",
        ["track"],
    )
    op.create_index(
        "ix_interview_sessions_user_created",
        "interview_sessions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_interview_sessions_track_difficulty",
        "interview_sessions",
        ["track", "difficulty"],
    )

    # -- interview_answer_scores --------------------------------------------
    op.create_table(
        "interview_answer_scores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "question_index",
            sa.Integer(),
            nullable=False,
            comment="0-based position in the session (including follow-ups)",
        ),
        sa.Column(
            "question_text",
            sa.Text(),
            nullable=False,
            comment="The exact question text posed to the user",
        ),
        sa.Column(
            "answer_summary",
            sa.Text(),
            nullable=True,
            comment="Brief LLM-generated summary of the answer (not full transcript)",
        ),
        # Sub-scores: 0.0 – 10.0 each
        sa.Column(
            "score_correctness",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
            comment="Factual accuracy of the answer",
        ),
        sa.Column(
            "score_clarity",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
            comment="How clearly and concisely the answer was communicated",
        ),
        sa.Column(
            "score_depth",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
            comment="Technical depth and nuance demonstrated",
        ),
        sa.Column(
            "was_followup",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="True if this row is a follow-up probe, not the original question",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Composite index for fetching all scores of a session in order
    op.create_index(
        "ix_interview_answer_scores_session_id",
        "interview_answer_scores",
        ["session_id"],
    )
    op.create_index(
        "ix_interview_answer_scores_session_idx",
        "interview_answer_scores",
        ["session_id", "question_index"],
    )


# ---------------------------------------------------------------------------
# downgrade  — drops in reverse dependency order
# ---------------------------------------------------------------------------
def downgrade() -> None:
    # Drop tables first (children before parent)
    op.drop_index("ix_interview_answer_scores_session_idx", table_name="interview_answer_scores")
    op.drop_index("ix_interview_answer_scores_session_id", table_name="interview_answer_scores")
    op.drop_table("interview_answer_scores")

    op.drop_index("ix_interview_sessions_track_difficulty", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_user_created", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_track", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_user_id", table_name="interview_sessions")
    op.drop_table("interview_sessions")

    # Drop ENUM types last (they can't be dropped while columns reference them)
    interview_difficulty_enum.drop(op.get_bind(), checkfirst=True)
    interview_track_enum.drop(op.get_bind(), checkfirst=True)
