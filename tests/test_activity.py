"""
tests/test_activity.py
~~~~~~~~~~~~~~~~~~~~~~
The Overview's year: problems, applications and mock interviews per IST day, and
the meters beside it.

Runs on in-memory SQLite (``tests/outreach_support.py``). Every role and company is fictional.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.activity import year_activity
from storage.models import (
    ApplicationStatus,
    InterviewDifficulty,
    InterviewSession,
    InterviewTrack,
    JobApplication,
    PracticeDay,
)
from tests.outreach_support import make_user, outreach_db  # noqa: F401

Factory = async_sessionmaker[AsyncSession]

# 10 Nov 2026, 12:00 IST.
NOW = datetime(2026, 11, 10, 6, 30, tzinfo=UTC)


def practice(user_id: object, day: date, slug: str, difficulty: str) -> PracticeDay:
    return PracticeDay(
        user_id=user_id,
        day=day,
        kind="dsa",
        question_slug=slug,
        difficulty=difficulty,
        topic="arrays",
        done_at=datetime(day.year, day.month, day.day, 8, tzinfo=UTC),
    )


def interview(
    user_id: object, at: datetime, score: float | None, completed: bool = True
) -> InterviewSession:
    return InterviewSession(
        user_id=user_id,
        track=InterviewTrack.CODING,
        difficulty=InterviewDifficulty.MID,
        total_score=score,
        completed=completed,
        created_at=at,
    )


async def test_the_year_counts_each_kind_on_its_ist_day(outreach_db: Factory) -> None:  # noqa: F811
    async with outreach_db() as session:
        user = await make_user(session)
        session.add_all(
            [
                practice(user.id, date(2026, 11, 9), "two-sum", "easy"),
                practice(user.id, date(2026, 11, 10), "lru-cache", "medium"),
                # 9 Nov 20:00 UTC is 10 Nov 01:30 IST.
                JobApplication(
                    user_id=user.id,
                    status=ApplicationStatus.APPLIED,
                    role_title="Platform Engineer",
                    company_name="Lumen Works",
                    applied_at_explicit=datetime(2026, 11, 9, 20, tzinfo=UTC),
                ),
                # Saved is not applied.
                JobApplication(
                    user_id=user.id,
                    status=ApplicationStatus.SAVED,
                    role_title="Data Engineer",
                    company_name="Lumen Works",
                ),
                interview(user.id, datetime(2026, 11, 10, 4, tzinfo=UTC), 82.0),
                interview(user.id, datetime(2026, 11, 8, 4, tzinfo=UTC), 41.0),
                # Abandoned: counts for nothing.
                interview(user.id, datetime(2026, 11, 10, 5, tzinfo=UTC), None, completed=False),
            ]
        )
        await session.commit()

        year = await year_activity(str(user.id), weeks=1, now=NOW, session=session)

    assert year is not None
    days = {day["day"]: day for day in year["days"]}
    assert len(days) == 7 and year["days"][-1]["day"] == "2026-11-10"
    assert days["2026-11-10"] == {
        "day": "2026-11-10",
        "problems": 1,
        "applications": 1,
        "interviews": 1,
    }
    assert days["2026-11-09"]["problems"] == 1 and days["2026-11-09"]["applications"] == 0
    assert year["totals"] == {"problems": 2, "applications": 1, "interviews": 2}
    assert year["streak"] == {"current": 3, "longest": 3}
    assert year["solved"]["easy"] == 1 and year["solved"]["medium"] == 1
    assert year["interviews"] == {
        "completed": 2,
        "strong": 1,
        "fair": 0,
        "weak": 1,
        "average": 62,
    }


async def test_a_new_account_has_an_empty_year(outreach_db: Factory) -> None:  # noqa: F811
    async with outreach_db() as session:
        user = await make_user(session)
        year = await year_activity(str(user.id), now=NOW, session=session)

    assert year is not None
    assert len(year["days"]) == 53 * 7
    assert year["totals"] == {"problems": 0, "applications": 0, "interviews": 0}
    assert year["streak"] == {"current": 0, "longest": 0}
    assert year["interviews"]["average"] is None


async def test_an_id_that_is_not_a_user_is_none() -> None:
    assert await year_activity("not-a-uuid", now=NOW) is None
