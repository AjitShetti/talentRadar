"""
storage/interview_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Data-access layer for mock interview sessions and per-answer scores.

All public methods accept an ``AsyncSession`` as the first argument so
callers can compose multiple operations inside a single transaction if
needed.  No session management (commit / rollback) is done here — that is
the responsibility of the FastAPI dependency or the calling service.

Follows the conventions in storage/repository.py:
  - async methods throughout
  - returns ORM model instances
  - raises ValueError for invalid input, lets DB errors propagate
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    InterviewAnswerScore,
    InterviewDifficulty,
    InterviewSession,
    InterviewTrack,
)

logger = logging.getLogger(__name__)


class InterviewRepository:
    """
    Repository for interview_sessions and interview_answer_scores.

    Usage::

        async with get_db() as db:
            repo = InterviewRepository()
            session = await repo.create_session(db, user_id=..., track=..., difficulty=...)
    """

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        track: InterviewTrack,
        difficulty: InterviewDifficulty,
    ) -> InterviewSession:
        """
        Persist a new interview session record and return it.

        The session starts with ``completed=False`` and ``total_score=None``
        until :meth:`complete_session` is called at the end of the interview.
        """
        session = InterviewSession(
            user_id=user_id,
            track=track,
            difficulty=difficulty,
            completed=False,
        )
        db.add(session)
        await db.flush()  # populate server-generated id without committing
        logger.info(
            "InterviewSession created: id=%s user=%s track=%s difficulty=%s",
            session.id, user_id, track.value, difficulty.value,
        )
        return session

    async def complete_session(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        total_score: float,
        duration_seconds: int | None = None,
    ) -> InterviewSession:
        """
        Mark a session as completed and persist the final aggregate score.

        Args:
            session_id:       UUID of the session to finalise.
            total_score:      Aggregate score 0–100.
            duration_seconds: Optional elapsed wall-clock time.

        Raises:
            ValueError: If the session is not found.
        """
        interview_session = await self._get_session_or_raise(db, session_id)
        interview_session.completed = True
        interview_session.total_score = round(total_score, 2)
        interview_session.duration_seconds = duration_seconds
        interview_session.updated_at = datetime.now(timezone.utc)
        db.add(interview_session)
        await db.flush()
        logger.info(
            "InterviewSession completed: id=%s score=%.2f",
            session_id, total_score,
        )
        return interview_session

    async def abandon_session(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        partial_score: float | None = None,
        duration_seconds: int | None = None,
    ) -> InterviewSession:
        """
        Mark a session as abandoned (tab closed, network drop, etc.).

        The session is left with ``completed=False`` so it appears as
        'incomplete' in the user's history.  A partial score may be
        saved if any answers were scored before the abort.
        """
        interview_session = await self._get_session_or_raise(db, session_id)
        interview_session.completed = False
        interview_session.total_score = (
            round(partial_score, 2) if partial_score is not None else None
        )
        interview_session.duration_seconds = duration_seconds
        interview_session.updated_at = datetime.now(timezone.utc)
        db.add(interview_session)
        await db.flush()
        logger.info("InterviewSession abandoned: id=%s", session_id)
        return interview_session

    async def get_session(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
    ) -> InterviewSession | None:
        """Return the session or None if not found."""
        result = await db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_session_history(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[InterviewSession]:
        """
        Return a user's past sessions, newest first.

        Returns both completed and abandoned sessions so the history
        page can show 'incomplete' badges.
        """
        result = await db.execute(
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id)
            .order_by(InterviewSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Answer scores CRUD
    # ------------------------------------------------------------------

    async def save_answer_score(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        question_index: int,
        question_text: str,
        score_correctness: float,
        score_clarity: float,
        score_depth: float,
        answer_summary: str | None = None,
        was_followup: bool = False,
    ) -> InterviewAnswerScore:
        """
        Persist the LLM-evaluated score for one question/answer turn.

        Sub-scores are clamped to [0.0, 10.0] before saving to guard
        against LLM hallucinations returning out-of-range values.
        """
        def _clamp(v: float) -> float:
            return max(0.0, min(10.0, v))

        score = InterviewAnswerScore(
            session_id=session_id,
            question_index=question_index,
            question_text=question_text[:4096],        # guard against huge text
            answer_summary=answer_summary[:2048] if answer_summary else None,
            score_correctness=_clamp(score_correctness),
            score_clarity=_clamp(score_clarity),
            score_depth=_clamp(score_depth),
            was_followup=was_followup,
        )
        db.add(score)
        await db.flush()
        logger.debug(
            "AnswerScore saved: session=%s q=%d correct=%.1f clarity=%.1f depth=%.1f",
            session_id, question_index,
            score.score_correctness, score.score_clarity, score.score_depth,
        )
        return score

    async def get_session_scores(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
    ) -> Sequence[InterviewAnswerScore]:
        """
        Return all per-question scores for a session, ordered by question index.
        """
        result = await db.execute(
            select(InterviewAnswerScore)
            .where(InterviewAnswerScore.session_id == session_id)
            .order_by(InterviewAnswerScore.question_index)
        )
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    async def compute_total_score(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
    ) -> float:
        """
        Compute the aggregate session score (0–100) from all answer scores.

        Formula:
            avg(correctness + clarity + depth) / 30 * 100

        This normalises the three 0-10 sub-scores to produce a single
        0–100 total.  Returns 0.0 if no scores exist yet.
        """
        result = await db.execute(
            select(
                func.avg(InterviewAnswerScore.score_correctness).label("avg_c"),
                func.avg(InterviewAnswerScore.score_clarity).label("avg_cl"),
                func.avg(InterviewAnswerScore.score_depth).label("avg_d"),
            ).where(InterviewAnswerScore.session_id == session_id)
        )
        row = result.one()
        avg_c  = row.avg_c  or 0.0
        avg_cl = row.avg_cl or 0.0
        avg_d  = row.avg_d  or 0.0
        total = ((avg_c + avg_cl + avg_d) / 30.0) * 100.0
        return round(total, 2)

    async def compute_score_breakdown(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
    ) -> dict[str, float]:
        """
        Return sub-score averages scaled to 0–100 for the score breakdown UI.
        """
        result = await db.execute(
            select(
                func.avg(InterviewAnswerScore.score_correctness).label("avg_c"),
                func.avg(InterviewAnswerScore.score_clarity).label("avg_cl"),
                func.avg(InterviewAnswerScore.score_depth).label("avg_d"),
            ).where(InterviewAnswerScore.session_id == session_id)
        )
        row = result.one()
        def _scale(v: float | None) -> float:
            return round((v or 0.0) / 10.0 * 100.0, 2)

        return {
            "correctness": _scale(row.avg_c),
            "clarity":     _scale(row.avg_cl),
            "depth":       _scale(row.avg_d),
        }

    async def get_answer_scores_for_sessions(
        self,
        db: AsyncSession,
        session_ids: Sequence[uuid.UUID],
    ) -> Sequence[InterviewAnswerScore]:
        """
        Return every per-question score belonging to ``session_ids``.

        Used by the dashboard insight aggregator, which needs the raw
        sub-scores across several sessions at once rather than one
        session at a time.  Returns an empty list for an empty input.
        """
        if not session_ids:
            return []
        result = await db.execute(
            select(InterviewAnswerScore)
            .where(InterviewAnswerScore.session_id.in_(list(session_ids)))
            .order_by(
                InterviewAnswerScore.session_id,
                InterviewAnswerScore.question_index,
            )
        )
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_session_or_raise(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
    ) -> InterviewSession:
        interview_session = await self.get_session(db, session_id)
        if interview_session is None:
            raise ValueError(f"InterviewSession not found: {session_id}")
        return interview_session
