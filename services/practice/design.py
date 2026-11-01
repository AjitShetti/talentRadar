"""
services/practice/design.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Opening, saving and reading a system design answer (spec 4.5). No model call happens here.

* **The open answer.** A person can hold several answers to one problem, one per day. A save only
  ever addresses the open one: today's when the problem is today's, else yesterday's before
  06:00 IST when it was yesterday's. An answer swapped away is an answer whose problem is not its
  day's, so it is never open.
* **The first save pins the day.** It inserts the ``practice_days`` row, not done, with the answer
  in one transaction, so the pick cannot move under a drawing. Losing that insert to another
  request finds the row and goes on.
* **Retention.** The drawing and the reasoning go after 180 days and the row after 400.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, null, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.practice_design import MAX_REASONING_CHARS, DesignProblem, scene_refusal, without_nul
from domain.practice_schedule import answer_open, ist_day
from services.cache_backend import CacheBackend
from services.practice import daily
from services.practice.challenges import SAVES_PER_MINUTE, clean_text
from services.practice.errors import (
    PracticeConflict,
    PracticeInvalid,
    PracticeNotFound,
    PracticeRateLimited,
)
from storage.database import AsyncSessionLocal
from storage.models import PracticeDay, PracticeDesignAnswer

SCENE_RETENTION = timedelta(days=180)
ANSWER_RETENTION = timedelta(days=400)
_SAVE_COUNTER_TTL = 120
_SAVE_TRIES = 3

_NOT_FOUND = "We could not find that problem."
_CLOSED = "This answer can no longer be changed."
_TOO_FAST = "That is a lot of saves in a minute. Wait a moment and try again."
_TOO_LONG = f"Keep your reasoning to {MAX_REASONING_CHARS:,} characters."
_UNREADABLE = "That reasoning could not be read. Reload the page and try again."
_TRY_AGAIN = "That save did not go through. Try again."


def _moment(now: datetime | None) -> datetime:
    moment = now if now is not None else datetime.now(UTC)
    ist_day(moment)  # raises ValueError for a naive datetime
    return moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    """SQLite hands timezone-aware columns back naive; every stored instant is UTC."""
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return aware.isoformat()


async def _find_day(session: AsyncSession, user: uuid.UUID, day: date) -> PracticeDay | None:
    return (
        await session.execute(
            select(PracticeDay).where(PracticeDay.user_id == user, PracticeDay.day == day)
        )
    ).scalar_one_or_none()


async def _state_of(session: AsyncSession, state: daily.DayState, day: date) -> daily.DayState:
    """The person's day ``day``, as it stood: what its pick was, ``state`` itself for today."""
    if day == state.day:
        return state
    return await daily.load_day(session, str(state.user), state.moment - (state.day - day))


async def _open_day(session: AsyncSession, state: daily.DayState, slug: str) -> date | None:
    shown = daily.shown_problem(state)
    if shown is not None and shown.kind == "design" and shown.slug == slug:
        return state.day
    yesterday = state.day - timedelta(days=1)
    if not answer_open(state.moment, yesterday):
        return None
    row = await _find_day(session, state.user, yesterday)
    if row is not None:
        return yesterday if row.kind == "design" and row.question_slug == slug else None
    # No save yet: a page opened before midnight can save for the first time after it.
    earlier = daily.shown_problem(await _state_of(session, state, yesterday))
    return (
        yesterday
        if earlier is not None and earlier.kind == "design" and earlier.slug == slug
        else None
    )


async def open_day(
    session: AsyncSession, user: uuid.UUID, slug: str, moment: datetime
) -> date | None:
    """The day whose answer to ``slug`` can be changed at ``moment``, or ``None``."""
    return await _open_day(session, await daily.load_day(session, str(user), moment), slug)


def _problem(problem: DesignProblem) -> dict[str, Any]:
    return {
        "slug": problem.slug,
        "title": problem.title,
        "minutes": problem.minutes,
        "topics": list(problem.topics),
        "summary": problem.summary,
        "context": list(problem.context),
        "must_handle": list(problem.must_handle),
        "show_in_answer": list(problem.show_in_answer),
    }


def _answer(answer: PracticeDesignAnswer) -> dict[str, Any]:
    return {
        "elements": answer.scene,
        "reasoning": answer.reasoning or "",
        "saved_at": _iso(answer.updated_at),
    }


def _feedback(answer: PracticeDesignAnswer) -> dict[str, Any] | None:
    """The stored feedback. ``work_on_next`` is kept in ``notes`` beside the criteria's notes."""
    if answer.scores is None or answer.total is None or answer.feedback_at is None:
        return None
    notes = dict(answer.notes or {})
    work_on_next = str(notes.pop("work_on_next", ""))
    return {
        "scores": answer.scores,
        "notes": notes,
        "work_on_next": work_on_next,
        "total": answer.total,
        "at": _iso(answer.feedback_at),
    }


async def read_page(
    user_id: str, slug: str, day: date | None = None, *, now: datetime | None = None
) -> dict[str, Any]:
    """A problem and one of the person's answers to it. Writes nothing.

    Without ``day``: the open answer, else the newest one, read-only. With ``day``: that day's.
    Anything else is a 404, as is another person's answer.
    """
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        state = await daily.load_day(session, user_id, moment)
        problem = next((p for p in state.problems if p.slug == slug), None)
        if problem is None:
            raise PracticeNotFound(_NOT_FOUND)
        opened = await _open_day(session, state, slug)
        answers = (
            (
                await session.execute(
                    select(PracticeDesignAnswer)
                    .where(
                        PracticeDesignAnswer.user_id == state.user,
                        PracticeDesignAnswer.question_slug == slug,
                    )
                    .order_by(PracticeDesignAnswer.day.desc())
                )
            )
            .scalars()
            .all()
        )
        by_day = {answer.day: answer for answer in answers}
        target = day if day is not None else opened
        if target is None and answers:
            target = answers[0].day
        if target is None or (target not in by_day and target != opened):
            raise PracticeNotFound(_NOT_FOUND)
        answer = by_day.get(target)
        row = await _find_day(session, state.user, target)
    return {
        "problem": _problem(problem),
        "day": target.isoformat(),
        "answer": _answer(answer) if answer is not None else None,
        "editable": target == opened,
        "done": row is not None and row.done_at is not None and row.question_slug == slug,
        "last_answer_day": next(
            (a.day.isoformat() for a in answers if a.day < target and a.scene is not None), None
        ),
        "feedback": _feedback(answer) if answer is not None else None,
        "feedback_problem": None,
    }


async def _count_save(user: uuid.UUID, moment: datetime) -> None:
    minute = int(moment.timestamp() // 60)
    used = await CacheBackend.incr(f"tr:practice:design-save:{user}:{minute}", _SAVE_COUNTER_TTL)
    if used > SAVES_PER_MINUTE:
        raise PracticeRateLimited(_TOO_FAST)


def _checked_reasoning(reasoning: object) -> str:
    if not isinstance(reasoning, str):
        raise PracticeInvalid(_UNREADABLE)
    text = clean_text(reasoning)
    if len(text) > MAX_REASONING_CHARS:
        raise PracticeInvalid(_TOO_LONG)
    return text


async def _write(
    session: AsyncSession,
    state: daily.DayState,
    target: date,
    slug: str,
    elements: object,
    text: str,
) -> None:
    """Stage the answer, and the day's row when this is the day's first save."""
    if await _find_day(session, state.user, target) is None:
        shown = daily.shown_problem(await _state_of(session, state, target))
        if shown is not None:
            session.add(
                PracticeDay(
                    user_id=state.user,
                    day=target,
                    question_slug=shown.slug,
                    topic=shown.topic,
                    difficulty=shown.difficulty,
                    kind=shown.kind,
                    swaps=0,
                    done_at=None,
                )
            )
    answer = (
        await session.execute(
            select(PracticeDesignAnswer).where(
                PracticeDesignAnswer.user_id == state.user,
                PracticeDesignAnswer.day == target,
                PracticeDesignAnswer.question_slug == slug,
            )
        )
    ).scalar_one_or_none()
    if answer is None:
        answer = PracticeDesignAnswer(user_id=state.user, day=target, question_slug=slug)
        session.add(answer)
    answer.scene = elements  # type: ignore[assignment]  # scene_refusal: it is a list
    answer.reasoning = text
    answer.updated_at = state.moment


async def save_answer(
    user_id: str, slug: str, elements: object, reasoning: object, *, now: datetime | None = None
) -> dict[str, Any]:
    """Save the open answer to ``slug``: 422 for a bad one, 409 when none is open, 429 if too fast."""
    refusal = scene_refusal(elements)
    if refusal is not None:
        raise PracticeInvalid(refusal)
    text = _checked_reasoning(reasoning)
    scene = without_nul(elements)
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        state = await daily.load_day(session, user_id, moment)
        target = await _open_day(session, state, slug)
        if target is None:
            raise PracticeConflict(_CLOSED)
        await _count_save(state.user, moment)
        for _attempt in range(_SAVE_TRIES):
            try:
                await _write(session, state, target, slug, scene, text)
                await session.commit()
            except IntegrityError as error:
                # A first save at the same moment took the day's row, or the account has gone.
                await session.rollback()
                if await _find_day(session, state.user, target) is None:
                    raise PracticeNotFound(daily.NO_ACCOUNT) from error
                continue
            return {"day": target.isoformat(), "saved_at": moment.isoformat()}
    # Not a plain conflict: that says the answer has closed, and the page stops saving on it.
    raise PracticeRateLimited(_TRY_AGAIN)


async def prune_answers(moment: datetime) -> int:
    """Clear drawings and reasoning past 180 days; delete rows past 400. Returns rows deleted."""
    today = ist_day(moment)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(PracticeDesignAnswer)
            .where(
                PracticeDesignAnswer.day < today - SCENE_RETENTION,
                or_(
                    PracticeDesignAnswer.scene.is_not(None),
                    PracticeDesignAnswer.reasoning.is_not(None),
                ),
            )
            .values(scene=null(), reasoning=None)
            .execution_options(synchronize_session=False)
        )
        deleted = await session.execute(
            delete(PracticeDesignAnswer)
            .where(PracticeDesignAnswer.day < today - ANSWER_RETENTION)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return int(getattr(deleted, "rowcount", 0) or 0)
