"""
services/activity.py
~~~~~~~~~~~~~~~~~~~~
The Overview's year: one square per IST day, counting the three things a job
search is made of, plus the two meters beside it.

    year_activity() — the days, their totals and streaks, what is solved, and
                      how the mock interviews went

* A problem is a Practice day marked done (one a day at most).
* An application is a tracker row past "saved", on the day it moved to
  applied (``applied_at_explicit``), else ``applied_at``, else the day the row
  was created.
* A mock interview is a session that ended gracefully (``completed``), on the
  day it started. An abandoned one counts for nothing.

Read only, no outside call and no model.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.practice_schedule import ist_day
from services.base import parse_uuid
from services.practice.catalogue import load_catalogue
from services.practice.daily import _available, _solved
from services.practice.design_catalogue import load_problems
from storage.database import AsyncSessionLocal
from storage.models import ApplicationStatus, InterviewSession, JobApplication, PracticeDay

#: The window, in weeks, at most: a year and a week, like Practice's graph.
MAX_WEEKS = 53
#: A mock interview scored at or above this is strong; below WEAK_BELOW needs work.
STRONG_FROM = 75.0
WEAK_BELOW = 50.0

KINDS = ("problems", "applications", "interviews")


def _aware(moment: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; every stored instant is UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _band(score: float) -> str:
    if score >= STRONG_FROM:
        return "strong"
    return "fair" if score >= WEAK_BELOW else "weak"


def _current_streak(days: set[date], today: date) -> int:
    """Consecutive active days ending today, or yesterday while today is still open."""
    cursor = today if today in days else today - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _longest_run(days: set[date]) -> int:
    longest = run = 0
    previous: date | None = None
    for current in sorted(days):
        run = run + 1 if previous is not None and (current - previous).days == 1 else 1
        longest = max(longest, run)
        previous = current
    return longest


async def year_activity(
    user_id: str,
    *,
    weeks: int = MAX_WEEKS,
    now: datetime | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """
    ``days`` has one entry per IST day for ``weeks`` weeks ending today, oldest
    first, each with a count per kind. ``totals`` and ``streak`` read the window;
    ``solved`` and ``interviews`` read everything, as the meters do.

    Returns ``None`` for an id that is not a user id.
    """
    user = parse_uuid(user_id)
    if user is None:
        return None
    weeks = max(1, min(weeks, MAX_WEEKS))
    moment = _aware(now) if now is not None else datetime.now(UTC)
    today = ist_day(moment)
    first = today - timedelta(days=weeks * 7 - 1)

    own = session is None
    session = session or AsyncSessionLocal()
    try:
        practice = (
            await session.execute(
                select(
                    PracticeDay.day,
                    PracticeDay.difficulty,
                    PracticeDay.kind,
                    PracticeDay.question_slug,
                ).where(
                    PracticeDay.user_id == user,
                    PracticeDay.done_at.is_not(None),
                    PracticeDay.day <= today,
                )
            )
        ).all()
        applications = (
            await session.execute(
                select(
                    JobApplication.applied_at_explicit,
                    JobApplication.applied_at,
                    JobApplication.created_at,
                ).where(
                    JobApplication.user_id == user,
                    JobApplication.status != ApplicationStatus.SAVED,
                )
            )
        ).all()
        interviews = (
            await session.execute(
                select(InterviewSession.created_at, InterviewSession.total_score).where(
                    InterviewSession.user_id == user,
                    InterviewSession.completed.is_(True),
                )
            )
        ).all()
    finally:
        if own:
            await session.close()

    counts: dict[str, Counter[date]] = {kind: Counter() for kind in KINDS}
    for row in practice:
        counts["problems"][row.day] += 1
    for explicit, applied, created in applications:
        when = explicit or applied or created
        if when is not None:
            counts["applications"][ist_day(_aware(when))] += 1
    for created, _score in interviews:
        counts["interviews"][ist_day(_aware(created))] += 1

    window = [first + timedelta(days=offset) for offset in range(weeks * 7)]
    in_window = set(window)
    active = {
        day for kind in KINDS for day, count in counts[kind].items() if count and day in in_window
    }
    scored = [float(score) for _created, score in interviews if score is not None]
    bands = Counter(_band(score) for score in scored)

    return {
        "days": [
            {"day": day.isoformat(), **{kind: counts[kind][day] for kind in KINDS}}
            for day in window
        ],
        "totals": {
            kind: sum(count for day, count in counts[kind].items() if day in in_window)
            for kind in KINDS
        },
        "streak": {"current": _current_streak(active, today), "longest": _longest_run(active)},
        "solved": _solved(practice),
        "available": _available(load_catalogue(), load_problems()),
        "interviews": {
            "completed": len(interviews),
            "strong": bands["strong"],
            "fair": bands["fair"],
            "weak": bands["weak"],
            "average": round(sum(scored) / len(scored)) if scored else None,
        },
    }
