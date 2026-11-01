"""
services/practice/design_feedback.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Get feedback on a design answer (spec 4.6). The second place Practice calls a model, and the first
on a person's request.

* **The practice counts whatever the model does.** The day is marked done and committed before any
  call, and no database session stays open across one.
* **Never a fallback score.** A model error, an unreadable reply or a spent budget stores nothing:
  the page gets ``feedback_problem`` (``busy`` or ``limit``) and offers to try again. There is no
  background retry.
* **The same input is not read twice.** A stored result with the same input hash, model and prompt
  version is returned with no call and no budget.
* **The contest judge comes first.** Feedback uses the judge's model, and a rate-limited judge backs
  off for hours, so while the judge holds ``JUDGING_KEY`` feedback is ``busy``, and everyone together
  gets ``design_feedback_per_minute`` calls a minute. That claim comes first, so ``busy`` costs the
  person nothing from their three.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from domain.practice_design import (
    MIN_LABELLED_SHAPES,
    MIN_REASONING_CHARS,
    describe_scene,
)
from domain.practice_design_feedback import (
    DESIGN_PROMPT_VERSION,
    MAX_FEEDBACK_TOKENS,
    Feedback,
    FeedbackError,
    build_feedback_prompt,
    parse_feedback,
)
from domain.practice_judge import input_sha256
from domain.practice_schedule import ist_day
from services import llm_budget
from services.cache_backend import CacheBackend
from services.llm import _chat, is_request_too_large
from services.practice import daily, design
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from services.practice.judge import JUDGING_KEY
from storage.database import AsyncSessionLocal
from storage.models import PracticeDay, PracticeDesignAnswer

logger = logging.getLogger(__name__)

PER_USER_DAILY_DESIGN_FEEDBACK = 3
DAILY_DESIGN_FEEDBACK_BUDGET = 150
_MINUTE_KEY_TTL = 120
#: Longer than any model call, so that a crash cannot leave an answer locked for long.
_LOCK_SECONDS = 180

Problem = Literal["busy", "limit"]

_NOT_FOUND = "We could not find that problem."
_CLOSED = "This answer can no longer be changed, so it cannot get feedback."
_ON_ITS_WAY = "Your feedback is on its way."
_NEED_SHAPES = f"label at least {MIN_LABELLED_SHAPES} shapes in your drawing"
_NEED_REASONING = f"write at least {MIN_REASONING_CHARS} characters of reasoning"


@dataclass(frozen=True)
class _Open:
    """What the steps after the first need, read before the session closed."""

    user: UUID
    day: date
    answer_id: UUID
    system: str
    prompt: str
    input_hash: str


def _moment(now: datetime | None) -> datetime:
    moment = now if now is not None else datetime.now(UTC)
    ist_day(moment)  # raises ValueError for a naive datetime
    return moment.astimezone(UTC)


def _missing(labelled_shapes: int, reasoning: str) -> str | None:
    """Which of the two minimums is short, as a sentence, or ``None``."""
    wants = []
    if labelled_shapes < MIN_LABELLED_SHAPES:
        wants.append(_NEED_SHAPES)
    if len(reasoning) < MIN_REASONING_CHARS:
        wants.append(_NEED_REASONING)
    if not wants:
        return None
    return f"To get feedback, {' and '.join(wants)}."


async def _load_answer(
    session: AsyncSession, user: UUID, day: date, slug: str
) -> PracticeDesignAnswer | None:
    return (
        await session.execute(
            select(PracticeDesignAnswer).where(
                PracticeDesignAnswer.user_id == user,
                PracticeDesignAnswer.day == day,
                PracticeDesignAnswer.question_slug == slug,
            )
        )
    ).scalar_one_or_none()


async def _open_and_mark_done(user_id: str, slug: str, moment: datetime) -> _Open:
    """Steps 1 to 3: find the open answer, check there is something to read, mark the day done."""
    async with AsyncSessionLocal() as session:
        state = await daily.load_day(session, user_id, moment)
        day = await design.open_day(session, state.user, slug, moment)
        if day is None:
            held = (
                await session.execute(
                    select(func.count())
                    .select_from(PracticeDesignAnswer)
                    .where(
                        PracticeDesignAnswer.user_id == state.user,
                        PracticeDesignAnswer.question_slug == slug,
                    )
                )
            ).scalar_one()
            raise PracticeConflict(_CLOSED) if held else PracticeNotFound(_NOT_FOUND)
        problem = next((p for p in state.problems if p.slug == slug), None)
        if problem is None:
            raise PracticeNotFound(_NOT_FOUND)
        answer = await _load_answer(session, state.user, day, slug)
        reading = describe_scene(answer.scene or [] if answer is not None else [])
        reasoning = (answer.reasoning or "") if answer is not None else ""
        missing = _missing(reading.labelled_shapes, reasoning)
        if missing is not None or answer is None:
            raise PracticeInvalid(missing or _NOT_FOUND)
        system, prompt = build_feedback_prompt(problem, reading, reasoning)
        opened = _Open(state.user, day, answer.id, system, prompt, input_sha256(prompt))
        await session.execute(
            update(PracticeDay)
            .where(
                PracticeDay.user_id == state.user,
                PracticeDay.day == day,
                PracticeDay.done_at.is_(None),
            )
            .values(done_at=moment)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return opened


async def _has_stored(opened: _Open, model: str) -> bool:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(
                    PracticeDesignAnswer.scores,
                    PracticeDesignAnswer.input_hash,
                    PracticeDesignAnswer.model,
                    PracticeDesignAnswer.prompt_version,
                ).where(PracticeDesignAnswer.id == opened.answer_id)
            )
        ).first()
    return (
        row is not None
        and row.scores is not None
        and row.input_hash == opened.input_hash
        and row.model == model
        and row.prompt_version == DESIGN_PROMPT_VERSION
    )


async def _claim_lock(key: str) -> bool:
    """Whether this request owns the answer: the first ``incr`` sees 1. No cache grants it."""
    try:
        return await CacheBackend.incr(key, _LOCK_SECONDS) == 1
    except Exception as exc:
        logger.warning("practice: the feedback lock is unavailable (%s)", type(exc).__name__)
        return True


async def _release_lock(key: str) -> None:
    try:
        await CacheBackend.delete(key)
    except Exception as exc:
        logger.warning("practice: the feedback lock was not released (%s)", type(exc).__name__)


async def _judge_is_running() -> bool:
    """A cache that cannot answer says no: the budgets fail open the same way."""
    try:
        return await CacheBackend.get(JUDGING_KEY) is not None
    except Exception as exc:
        logger.debug("practice: could not read the judge's key (%s)", type(exc).__name__)
        return False


def _user_key(user: UUID, moment: datetime) -> str:
    return f"tr:budget:design-feedback:user:{user}:{ist_day(moment).isoformat()}"


async def _claim_budgets(user: UUID, moment: datetime, per_minute: int) -> Problem | None:
    """Step 6. The person's three are only read here: ``_store`` is what uses one, so neither a
    ``busy`` nor a failed call costs them anything, and someone over them takes no one's minute."""
    if await _judge_is_running():
        return "busy"
    if await llm_budget.used(_user_key(user, moment)) >= PER_USER_DAILY_DESIGN_FEEDBACK:
        return "limit"
    minute = int(moment.timestamp() // 60)
    if not await llm_budget.claim(
        f"tr:budget:design-feedback-minute:{minute}", per_minute, _MINUTE_KEY_TTL
    ):
        return "busy"
    if not await llm_budget.claim(
        f"tr:budget:design-feedback:{moment.date().isoformat()}", DAILY_DESIGN_FEEDBACK_BUDGET
    ):
        return "limit"
    return None


async def _ask(opened: _Open, model: str) -> Feedback | None:
    """One model call, read strictly. ``None`` for any error or unreadable reply: never a score."""
    try:
        raw = await _chat(
            opened.system,
            opened.prompt,
            model=model,
            temperature=0,
            max_tokens=MAX_FEEDBACK_TOKENS,
            json_mode=True,
            reasoning_effort="medium",
        )
        return parse_feedback(raw)
    except FeedbackError:
        logger.warning("practice: the design feedback reply could not be read")
    except Exception as exc:
        reason = "too large" if is_request_too_large(exc) else "failed"
        logger.warning("practice: the design feedback call %s (%s)", reason, type(exc).__name__)
    return None


async def _store(opened: _Open, feedback: Feedback, model: str, moment: datetime) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(PracticeDesignAnswer)
            .where(PracticeDesignAnswer.id == opened.answer_id)
            .values(
                scores=feedback.scores,
                notes={**feedback.notes, "work_on_next": feedback.work_on_next},
                total=feedback.total,
                input_hash=opened.input_hash,
                model=model,
                prompt_version=DESIGN_PROMPT_VERSION,
                submitted_at=func.coalesce(PracticeDesignAnswer.submitted_at, moment),
                feedback_at=moment,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def request_feedback(
    user_id: str, slug: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Read the open answer to ``slug``. Returns the page, with ``feedback_problem`` when no result
    was made. 404 with no answer, 409 when closed or already being read, 422 when too little."""
    moment = _moment(now)
    opened = await _open_and_mark_done(user_id, slug, moment)
    settings = get_settings()
    model = settings.groq_model
    problem: Problem | None = None
    if not await _has_stored(opened, model):
        lock = f"tr:lock:design-feedback:{opened.user}:{opened.answer_id}"
        if not await _claim_lock(lock):
            raise PracticeConflict(_ON_ITS_WAY)
        try:
            # Another press may have finished between the read above and this lock.
            if not await _has_stored(opened, model):
                problem = await _claim_budgets(
                    opened.user, moment, settings.design_feedback_per_minute
                )
                if problem is None:
                    feedback = await _ask(opened, model)
                    if feedback is None:
                        problem = "busy"
                    else:
                        await _store(opened, feedback, model, moment)
                        await llm_budget.claim(
                            _user_key(opened.user, moment), PER_USER_DAILY_DESIGN_FEEDBACK
                        )
        finally:
            await _release_lock(lock)
    page = await design.read_page(user_id, slug, opened.day, now=moment)
    return {**page, "feedback_problem": problem}
