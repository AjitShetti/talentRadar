"""practice: say which kind of problem a day had, and keep design answers

Revision ID: 021_practice_design
Revises: 020_outreach_pack
Create Date: 2026-11-01 00:00:00.000000

Why this exists
---------------
The Practice redesign (docs/superpowers/specs/2026-11-01-practice-redesign-design.md, section 7)
gives three days a week a system design problem, solved on a whiteboard in the app:

* ``practice_days.kind`` says whether a day's problem was DSA or design. Every row
  from before this migration was a LeetCode question, so the default is ``dsa``;
* ``practice_design_answers`` holds a person's drawing, reasoning and private
  feedback, one row per problem per IST day.

Deleting a user removes their answers. Every statement is IF NOT EXISTS / IF EXISTS, so the
migration is safe on a database created from ``models.py`` metadata directly. ``user_id`` leads
the unique constraint, so no extra index is needed for the cascade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "021_practice_design"
down_revision: str | None = "020_outreach_pack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE practice_days ADD COLUMN IF NOT EXISTS kind VARCHAR(8) NOT NULL DEFAULT 'dsa';"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS practice_design_answers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            question_slug VARCHAR(128) NOT NULL,
            scene JSONB,
            reasoning TEXT,
            submitted_at TIMESTAMPTZ,
            scores JSONB,
            notes JSONB,
            total SMALLINT,
            input_hash CHAR(64),
            model VARCHAR(64),
            prompt_version VARCHAR(32),
            feedback_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_practice_design_answers UNIQUE (user_id, day, question_slug)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS practice_design_answers;")
    op.execute("ALTER TABLE practice_days DROP COLUMN IF EXISTS kind;")
