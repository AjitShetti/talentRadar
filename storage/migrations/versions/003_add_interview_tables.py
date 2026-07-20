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
    bind = op.get_bind()

    # -- Create ENUM types (idempotent via pg_type check) ------------------
    bind.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'interview_track_enum') THEN
                CREATE TYPE interview_track_enum
                    AS ENUM ('python_dsa', 'python_backend', 'sql', 'system_design');
            END IF;
        END $$
    """))
    bind.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'interview_difficulty_enum') THEN
                CREATE TYPE interview_difficulty_enum
                    AS ENUM ('beginner', 'mid', 'senior');
            END IF;
        END $$
    """))

    # -- interview_sessions ------------------------------------------------
    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id               UUID        NOT NULL DEFAULT gen_random_uuid(),
            user_id          UUID        NOT NULL,
            track            interview_track_enum       NOT NULL,
            difficulty       interview_difficulty_enum  NOT NULL,
            duration_seconds INTEGER,
            total_score      DOUBLE PRECISION,
            completed        BOOLEAN     NOT NULL DEFAULT false,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_interview_sessions_user_id "
        "ON interview_sessions (user_id)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_interview_sessions_track "
        "ON interview_sessions (track)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_interview_sessions_user_created "
        "ON interview_sessions (user_id, created_at)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_interview_sessions_track_difficulty "
        "ON interview_sessions (track, difficulty)"
    ))

    # -- interview_answer_scores -------------------------------------------
    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS interview_answer_scores (
            id               UUID        NOT NULL DEFAULT gen_random_uuid(),
            session_id       UUID        NOT NULL,
            question_index   INTEGER     NOT NULL,
            question_text    TEXT        NOT NULL,
            answer_summary   TEXT,
            score_correctness DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            score_clarity     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            score_depth       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            was_followup      BOOLEAN    NOT NULL DEFAULT false,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            FOREIGN KEY (session_id) REFERENCES interview_sessions(id) ON DELETE CASCADE
        )
    """))

    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_interview_answer_scores_session_id "
        "ON interview_answer_scores (session_id)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_interview_answer_scores_session_idx "
        "ON interview_answer_scores (session_id, question_index)"
    ))


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
