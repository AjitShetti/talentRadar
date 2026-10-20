"""
services/practice/daily.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The daily practice question: what to show, and the few things a person can do with it.

The pick is *computed on read* from rules in ``domain/practice_pick.py``, so ``today`` writes
nothing. A ``practice_days`` row exists only after the person swaps the question or marks it
done; it stores the slug they were looking at so the page stays put for the rest of the day
even if the catalogue is rebuilt in between.

* **The day is the IST calendar date** (``domain.practice_schedule.ist_day``, a fixed
  +05:30 offset). A "done" at 00:10 IST lands on the new day; UTC midnight changes nothing.
* **Writes are safe to repeat.** ``practice_days`` is unique on ``(user_id, day)``. Marking
  done inserts the row or, when a double click got there first, finds it. Swapping is a
  conditional ``UPDATE ... WHERE swaps < 1``, so two clicks can never make two swaps; the
  second gets :class:`PracticeConflict`.
* **No streak.** The history has a calendar and three neutral counts; there is no "current
  streak" to lose.
* **Nothing here requests leetcode.com.** A question's link is built from its catalogue slug
  by :attr:`Question.url`; nothing a person types ever reaches a URL.

Every function takes the signed-in user's id as a string and an optional ``now`` (aware, UTC
by default). Another user's rows are never read: every query is scoped to ``user_id``.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.practice_pick import (
    DIFFICULTIES,
    REPEAT_AFTER_DAYS,
    Difficulty,
    Feel,
    HistoryItem,
    Pick,
    Question,
    pick_question,
    topic_label,
)
from domain.practice_schedule import (
    CONTEST_URL,
    LEETCODE_PROBLEMSET_URL,
    contest_window,
    ist_day,
)
from domain.practice_topics import TRACK_LABELS, Track, track_for_roles
from services.base import parse_uuid
from services.practice.catalogue import load_catalogue
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from storage.database import AsyncSessionLocal
from storage.models import PracticeDay, PracticeSettings, Profile

logger = logging.getLogger(__name__)

#: One swap a day. ``pick_question`` replays ``swaps + 1`` draws, so the stored count is
#: capped at this before it is passed on: an out-of-range value costs nothing.
MAX_SWAPS = 1
#: The calendar's longest window, in weeks.
MAX_WEEKS = 26
#: Rows older than this are deleted by retention (spec 5.1), so no run is longer than this.
RETENTION_DAYS = 400
#: "Practised in the last 14 days" is what makes the contest card worth showing.
RECENT_PRACTICE_DAYS = 14

FEELS: tuple[Feel, ...] = ("easy", "right", "hard")

_NO_QUESTION = "There is no question for today: the question list is not loaded yet."
_SWAPPED_ALREADY = "You have already swapped today's question."
_DONE_ALREADY = "Today's question is marked done. Undo that first to swap it."
_NOT_DONE = "Mark today's question done before rating it."
_NO_ACCOUNT = "We could not find your account."


# ---------------------------------------------------------------------------
# One user's day, loaded
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Day:
    """Everything the rules and the payload need about one user on one IST day."""

    moment: datetime
    day: date
    user: uuid.UUID
    catalogue: tuple[Question, ...]
    track: Track
    years: float | None
    history: tuple[HistoryItem, ...]
    row: PracticeDay | None
    #: The person's Practice setting for the weekly contest card; on until they turn it off.
    contest_reminder: bool


@dataclass(frozen=True)
class _Shown:
    """The question on the page, and the line that says why it is there."""

    question: Question
    topic: str
    reason: str
    relaxed: tuple[str, ...]


def _moment(now: datetime | None) -> datetime:
    """``now`` as an aware UTC instant; a naive one is refused by :func:`ist_day`."""
    moment = now if now is not None else datetime.now(UTC)
    ist_day(moment)  # raises ValueError for a naive datetime
    return moment.astimezone(UTC)


def _user(user_id: str) -> uuid.UUID:
    user = parse_uuid(user_id)
    if user is None:
        raise PracticeNotFound(_NO_ACCOUNT)
    return user


def _aware(moment: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; every stored instant is UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _check_feel(feel: str | None, *, required: bool = False) -> None:
    if feel is None and not required:
        return
    if feel not in FEELS:
        raise PracticeInvalid("Rate it easy, right or hard.")


def _difficulty(value: str) -> Difficulty:
    """A stored difficulty as the domain type; an unreadable one is treated as medium."""
    for known in DIFFICULTIES:
        if known == value:
            return known
    return "medium"


def _feel(value: str | None) -> Feel | None:
    for known in FEELS:
        if known == value:
            return known
    return None


async def _find(session: AsyncSession, user: uuid.UUID, day: date) -> PracticeDay | None:
    return (
        await session.execute(
            select(PracticeDay).where(PracticeDay.user_id == user, PracticeDay.day == day)
        )
    ).scalar_one_or_none()


async def _load(session: AsyncSession, user_id: str, moment: datetime) -> _Day:
    """The user's profile, the last 180 days of their practice, and today's row if any."""
    user = _user(user_id)
    day = ist_day(moment)
    profile = (
        await session.execute(
            select(Profile.target_roles, Profile.years_experience).where(Profile.user_id == user)
        )
    ).first()
    reminder = (
        await session.execute(
            select(PracticeSettings.contest_reminder).where(PracticeSettings.user_id == user)
        )
    ).scalar_one_or_none()
    rows = (
        (
            await session.execute(
                select(PracticeDay).where(
                    PracticeDay.user_id == user,
                    PracticeDay.day >= day - timedelta(days=REPEAT_AFTER_DAYS),
                    PracticeDay.day <= day,
                )
            )
        )
        .scalars()
        .all()
    )
    return _Day(
        moment=moment,
        day=day,
        user=user,
        catalogue=load_catalogue(),
        track=track_for_roles(profile.target_roles if profile else None),
        years=profile.years_experience if profile else None,
        # Strictly before today: what the person does today must not move today's pick.
        history=tuple(
            HistoryItem(
                day=row.day,
                slug=row.question_slug,
                topic=row.topic,
                difficulty=_difficulty(row.difficulty),
                done=row.done_at is not None,
                feel=_feel(row.feel),
            )
            for row in rows
            if row.day < day
        ),
        row=next((row for row in rows if row.day == day), None),
        contest_reminder=True if reminder is None else bool(reminder),
    )


def _swaps_used(row: PracticeDay | None) -> int:
    return 0 if row is None else max(0, min(row.swaps, MAX_SWAPS))


def _pick(state: _Day, swaps: int) -> Pick | None:
    if not state.catalogue:
        return None
    return pick_question(
        user_id=str(state.user),
        day=state.day,
        swaps=swaps,
        track=state.track,
        years=state.years,
        history=state.history,
        catalogue=state.catalogue,
    )


def _shown(state: _Day) -> _Shown | None:
    """The question the page shows today, or ``None`` when there is nothing to show.

    Normally the pick for the stored swap count. But once a row exists it remembers the slug
    the person saw, and that wins if the catalogue has been rebuilt since, so a "done" never
    ends up beside a question that was not the one done.
    """
    pick = _pick(state, _swaps_used(state.row))
    row = state.row
    if row is not None and (pick is None or pick.question.slug != row.question_slug):
        kept = next((q for q in state.catalogue if q.slug == row.question_slug), None)
        if kept is not None:
            return _Shown(
                kept, row.topic, f"{topic_label(row.topic)} — your question for today.", ()
            )
    if pick is None:
        return None
    return _Shown(pick.question, pick.topic, pick.reason, pick.relaxed)


def _payload(state: _Day) -> dict[str, Any]:
    shown = _shown(state)
    row = state.row
    done = row is not None and row.done_at is not None
    window = contest_window(state.moment)
    return {
        "day": state.day.isoformat(),
        "catalogue": "ok" if shown is not None else "empty",
        "catalogue_size": len(state.catalogue),
        "track": state.track,
        "track_label": TRACK_LABELS[state.track],
        "question": _question(shown.question) if shown is not None else None,
        "reason": shown.reason if shown is not None else "",
        "relaxed": list(shown.relaxed) if shown is not None else [],
        "done_at": _aware(row.done_at).isoformat() if row is not None and row.done_at else None,
        "feel": _feel(row.feel) if row is not None and done else None,
        # Swapping a question that is marked done would move the mark to another question.
        "swaps_left": 0 if done else MAX_SWAPS - _swaps_used(row),
        "leetcode_daily_url": LEETCODE_PROBLEMSET_URL,
        "contest": {
            "starts_at": window.starts_at.isoformat(),
            "ends_at": window.ends_at.isoformat(),
            "show": window.show and state.contest_reminder,
            "live": window.live,
            "url": CONTEST_URL,
        },
    }


def _question(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "slug": question.slug,
        "title": question.title,
        "difficulty": question.difficulty,
        "topics": list(question.topics),
        "url": question.url,
    }


def _matched(result: Result[Any]) -> bool:
    """Whether an UPDATE changed a row. A conditional update that matched none lost the race."""
    return cast("CursorResult[Any]", result).rowcount > 0


def _unknown_account(error: IntegrityError) -> PracticeNotFound:
    """An insert failed and no row for today exists: not a double click, a missing user."""
    logger.warning("practice_days insert failed without a competing row: %s", error.orig)
    return PracticeNotFound(_NO_ACCOUNT)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def today(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Today's question for the user. Performs no write."""
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        state = await _load(session, user_id, moment)
    return _payload(state)


async def history(user_id: str, *, weeks: int = 12, now: datetime | None = None) -> dict[str, Any]:
    """The calendar and its three counts.

    ``days`` has one entry per IST day for ``weeks`` weeks (1-26) ending today, oldest first;
    ``difficulty`` and ``topic`` are set only on a practised day. ``counts`` and the longest
    run read every retained day, not just the window; ``topics`` counts the window's days.
    """
    weeks = max(1, min(weeks, MAX_WEEKS))
    moment = _moment(now)
    day = ist_day(moment)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(PracticeDay.day, PracticeDay.topic, PracticeDay.difficulty).where(
                    PracticeDay.user_id == user,
                    PracticeDay.done_at.is_not(None),
                    PracticeDay.day >= day - timedelta(days=RETENTION_DAYS),
                    PracticeDay.day <= day,
                )
            )
        ).all()

    done = {row.day: (row.topic, row.difficulty) for row in rows}
    first = day - timedelta(days=weeks * 7 - 1)
    window = [first + timedelta(days=offset) for offset in range(weeks * 7)]
    return {
        "days": [
            {
                "day": cell.isoformat(),
                "done": cell in done,
                "difficulty": done[cell][1] if cell in done else None,
                "topic": done[cell][0] if cell in done else None,
            }
            for cell in window
        ],
        "counts": {
            "this_month": sum(1 for d in done if (d.year, d.month) == (day.year, day.month)),
            "last_30": sum(1 for d in done if 0 <= (day - d).days < 30),
            "longest_run": _longest_run(done),
        },
        "topics": [
            {"topic": topic, "count": count}
            for topic, count in sorted(
                Counter(done[cell][0] for cell in window if cell in done).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    }


def _longest_run(days: Iterable[date]) -> int:
    """The most consecutive calendar days in ``days``."""
    longest = run = 0
    previous: date | None = None
    for current in sorted(set(days)):
        run = run + 1 if previous is not None and (current - previous).days == 1 else 1
        longest = max(longest, run)
        previous = current
    return longest


async def practised_recently(
    user_id: str, *, days: int = RECENT_PRACTICE_DAYS, now: datetime | None = None
) -> bool:
    """Whether the user marked a question done within the last ``days`` IST days (inclusive)."""
    user = parse_uuid(user_id)
    if user is None:
        return False
    day = ist_day(_moment(now))
    async with AsyncSessionLocal() as session:
        found = await session.execute(
            select(PracticeDay.id)
            .where(
                PracticeDay.user_id == user,
                PracticeDay.done_at.is_not(None),
                PracticeDay.day >= day - timedelta(days=days),
                PracticeDay.day <= day,
            )
            .limit(1)
        )
        return found.first() is not None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def mark_done(
    user_id: str, feel: Feel | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Mark today's question done, optionally with a rating. Safe to repeat.

    The first call stores ``done_at``; later calls keep that moment and only apply a rating
    if one is given. Two requests at once end in one row: whoever inserts second finds it.
    """
    _check_feel(feel)
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        state = await _load(session, user_id, moment)
        shown = _shown(state)
        if shown is None:
            raise PracticeNotFound(_NO_QUESTION)
        inserted = state.row is None and await _insert_done(session, state, shown, feel)
        if not inserted:
            mine = (PracticeDay.user_id == state.user, PracticeDay.day == state.day)
            await session.execute(
                update(PracticeDay)
                .where(*mine, PracticeDay.done_at.is_(None))
                .values(
                    done_at=moment,
                    question_slug=shown.question.slug,
                    topic=shown.topic,
                    difficulty=shown.question.difficulty,
                )
                .execution_options(synchronize_session=False)
            )
            if feel is not None:
                await session.execute(
                    update(PracticeDay)
                    .where(*mine, PracticeDay.done_at.is_not(None))
                    .values(feel=feel)
                    .execution_options(synchronize_session=False)
                )
            await session.commit()
    return await today(user_id, now=moment)


async def _insert_done(
    session: AsyncSession, state: _Day, shown: _Shown, feel: Feel | None
) -> bool:
    """Insert today's row as done. ``False`` means a concurrent request inserted it first."""
    session.add(
        PracticeDay(
            user_id=state.user,
            day=state.day,
            question_slug=shown.question.slug,
            topic=shown.topic,
            difficulty=shown.question.difficulty,
            swaps=0,
            done_at=state.moment,
            feel=feel,
        )
    )
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if await _find(session, state.user, state.day) is None:
            raise _unknown_account(error) from error
        return False
    return True


async def set_feel(user_id: str, feel: Feel, *, now: datetime | None = None) -> dict[str, Any]:
    """Rate the question marked done today (the one-tap "How was it?")."""
    _check_feel(feel, required=True)
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        changed = await session.execute(
            update(PracticeDay)
            .where(
                PracticeDay.user_id == user,
                PracticeDay.day == ist_day(moment),
                PracticeDay.done_at.is_not(None),
            )
            .values(feel=feel)
            .execution_options(synchronize_session=False)
        )
        if not _matched(changed):
            await session.rollback()
            raise PracticeConflict(_NOT_DONE)
        await session.commit()
    return await today(user_id, now=moment)


async def undo_done(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Take back a misclicked "done". Only today's row (IST) is touched; older days stay."""
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(PracticeDay)
            .where(
                PracticeDay.user_id == user,
                PracticeDay.day == ist_day(moment),
                PracticeDay.done_at.is_not(None),
            )
            .values(done_at=None, feel=None)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return await today(user_id, now=moment)


async def swap(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Ask for a different question. One a day; a second raises :class:`PracticeConflict`."""
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        state = await _load(session, user_id, moment)
        refusal = _swap_refusal(state.row) if state.row is not None else None
        if refusal is not None:
            raise PracticeConflict(refusal)
        picked = _pick(state, MAX_SWAPS)
        if picked is None:
            raise PracticeNotFound(_NO_QUESTION)
        await _claim_swap(session, state, picked)
    return await today(user_id, now=moment)


def _swap_refusal(row: PracticeDay) -> str | None:
    """Why today's row cannot be swapped, or ``None`` when it can."""
    if row.done_at is not None:
        return _DONE_ALREADY
    if row.swaps >= MAX_SWAPS:
        return _SWAPPED_ALREADY
    return None


async def _claim_swap(session: AsyncSession, state: _Day, picked: Pick) -> None:
    """Record the swap exactly once, whoever else is writing today's row.

    The update only matches a row that is not yet swapped and not done, so of two clicks one
    changes it and the other matches nothing and is refused. If there is no row, insert
    one; losing that insert to a concurrent request means the row now exists, so go round
    again and let the update decide.
    """
    values: dict[str, Any] = {
        "question_slug": picked.question.slug,
        "topic": picked.topic,
        "difficulty": picked.question.difficulty,
        "swaps": MAX_SWAPS,
    }
    for _attempt in range(2):
        changed = await session.execute(
            update(PracticeDay)
            .where(
                PracticeDay.user_id == state.user,
                PracticeDay.day == state.day,
                PracticeDay.swaps < MAX_SWAPS,
                PracticeDay.done_at.is_(None),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if _matched(changed):
            await session.commit()
            return
        current = await _find(session, state.user, state.day)
        if current is not None:
            # Read the row before the rollback: rolling back expires it.
            refusal = _swap_refusal(current) or _SWAPPED_ALREADY
            await session.rollback()
            raise PracticeConflict(refusal)
        session.add(PracticeDay(user_id=state.user, day=state.day, done_at=None, **values))
        try:
            await session.commit()
            return
        except IntegrityError as error:
            await session.rollback()
            if await _find(session, state.user, state.day) is None:
                raise _unknown_account(error) from error
    raise PracticeConflict(_SWAPPED_ALREADY)
