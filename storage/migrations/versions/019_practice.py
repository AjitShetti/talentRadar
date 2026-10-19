"""practice: daily picks, weekly take-home attempts, judgements and switches

Revision ID: 019_practice
Revises: 016_company_is_tech
Create Date: 2026-10-16 00:00:00.000000

Why this exists
---------------
Practice (docs/superpowers/specs/2026-10-15-practice-design.md, section 5)
needs four tables and nothing else:

* ``practice_days``: one row per IST day a user acted on (a swap or a "done"),
  with the question drawn and the one-tap rating;
* ``practice_challenges``: one row per edition of the weekly take-home, with
  its brief copied in at creation so later edits to the file change nothing,
  and one edition per opening (a unique ``opens_at``) so two scheduled runs
  cannot make two;
* ``practice_attempts``: one row per user per challenge, holding the start,
  the submission and the judgement, so an attempt is never split across tables;
* ``practice_settings``: per-user switches, kept out of ``profiles`` so this
  migration stays self-contained.

Only these four tables are touched. The one other table named is ``users``,
which the foreign keys reference: deleting a user removes their Practice rows,
and deleting a challenge removes its attempts.

Every statement is IF NOT EXISTS / IF EXISTS, so the migration is safe on a
database created from ``models.py`` metadata directly. Each foreign-key column
already leads a unique constraint or an index, so no extra index is needed for
the cascades.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "019_practice"
down_revision: str | None = "016_company_is_tech"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS practice_days (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            question_slug VARCHAR(128) NOT NULL,
            topic VARCHAR(48) NOT NULL,
            difficulty VARCHAR(8) NOT NULL,
            swaps SMALLINT NOT NULL DEFAULT 0,
            done_at TIMESTAMPTZ,
            feel VARCHAR(8),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_practice_days_user_day UNIQUE (user_id, day)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_days_user_done ON practice_days (user_id, done_at);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS practice_challenges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug VARCHAR(96) NOT NULL,
            brief_slug VARCHAR(80) NOT NULL,
            title VARCHAR(160) NOT NULL,
            track VARCHAR(16) NOT NULL,
            hours SMALLINT NOT NULL,
            brief JSONB NOT NULL,
            opens_at TIMESTAMPTZ NOT NULL,
            closes_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_practice_challenges_slug UNIQUE (slug)
        );
    """)
    # One edition per opening. The slug names the brief as well, so two runs that pick
    # different briefs for the same week would not collide on it.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_challenges_opens_at "
        "ON practice_challenges (opens_at);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS practice_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            challenge_id UUID NOT NULL
                REFERENCES practice_challenges(id) ON DELETE CASCADE,
            status VARCHAR(16) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            deadline_at TIMESTAMPTZ NOT NULL,
            proof_code CHAR(10) NOT NULL,
            repo_owner VARCHAR(39),
            repo_name VARCHAR(100),
            writeup JSONB,
            video_url VARCHAR(300),
            submitted_at TIMESTAMPTZ,
            repo_created_at TIMESTAMPTZ,
            snapshot_sha CHAR(40),
            snapshot_pushed_at TIMESTAMPTZ,
            signals JSONB,
            scores JSONB,
            total NUMERIC(4, 1),
            notes JSONB,
            judge_runs JSONB,
            judge_input_sha256 CHAR(64),
            judge_attempts SMALLINT NOT NULL DEFAULT 0,
            next_judge_at TIMESTAMPTZ,
            reject_reason VARCHAR(64),
            flags TEXT[] NOT NULL DEFAULT '{}',
            held BOOLEAN NOT NULL DEFAULT FALSE,
            appeal_reason VARCHAR(500),
            appealed_at TIMESTAMPTZ,
            judged_at TIMESTAMPTZ,
            override_note VARCHAR(500),
            CONSTRAINT uq_practice_attempts_user_challenge UNIQUE (user_id, challenge_id),
            CONSTRAINT uq_practice_attempts_proof_code UNIQUE (proof_code)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_attempts_board "
        "ON practice_attempts (challenge_id, status, total);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_attempts_due "
        "ON practice_attempts (status, next_judge_at);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS practice_settings (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            display_name VARCHAR(32),
            show_repo BOOLEAN NOT NULL DEFAULT FALSE,
            contest_reminder BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    # Children before parents: attempts reference challenges, so they go first.
    op.execute("DROP TABLE IF EXISTS practice_attempts;")
    op.execute("DROP TABLE IF EXISTS practice_challenges;")
    op.execute("DROP TABLE IF EXISTS practice_days;")
    op.execute("DROP TABLE IF EXISTS practice_settings;")
