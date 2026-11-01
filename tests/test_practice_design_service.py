"""
tests/test_practice_design_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Opening, saving and reading a system design answer: ``services/practice/design.py`` and its
routes. SQLite in a temporary file, three problems in place of the list.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.practice_design import DesignProblem
from domain.practice_pick import Question
from services.cache_backend import CacheBackend
from services.practice import daily, design
from services.practice.errors import (
    PracticeConflict,
    PracticeInvalid,
    PracticeNotFound,
    PracticeRateLimited,
)
from storage.database import Base
from storage.models import PracticeDay, PracticeDesignAnswer
from tests.test_practice_daily import Db, _token, add_day, add_user

TUESDAY = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
TUESDAY_DAY = date(2026, 11, 3)
#: Wednesday 05:00 and 06:30 IST: yesterday's answer open, then closed.
BEFORE_SIX = datetime(2026, 11, 3, 23, 30, tzinfo=UTC)
AFTER_SIX = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
#: Wednesday 00:05 IST.
JUST_AFTER_MIDNIGHT = datetime(2026, 11, 3, 18, 35, tzinfo=UTC)

DRAWING: list[dict[str, Any]] = [{"id": "a", "type": "text", "text": "API"}]
QUESTION = Question(id=1, slug="a-question", title="Q", difficulty="easy", topics=("array",))


def problem(number: int) -> DesignProblem:
    return DesignProblem(
        slug=f"design-problem-{number:02d}",
        title=f"Design problem {number}",
        tracks=("general",),
        minutes=30,
        topics=("caching",),
        summary="A summary.",
        context=("Some context.",),
        must_handle=("One thing.",),
        show_in_answer=("A box.",),
    )


PROBLEMS = (problem(1), problem(2), problem(3))


@pytest.fixture(autouse=True)
def lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily, "load_catalogue", lambda: (QUESTION,))
    monkeypatch.setattr(daily, "load_problems", lambda: PROBLEMS)


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    await CacheBackend.reset()
    path = tmp_path / "design.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        Base.metadata.tables[name]
        for name in (
            "users",
            "resumes",
            "profiles",
            "practice_days",
            "practice_settings",
            "practice_design_answers",
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(daily, "AsyncSessionLocal", factory)
    monkeypatch.setattr(design, "AsyncSessionLocal", factory)
    yield Db(engine=engine, factory=factory, path=path)
    await engine.dispose()


@pytest_asyncio.fixture
async def asha(db: Db) -> str:
    return str(await add_user(db))


@pytest_asyncio.fixture
async def ben(db: Db) -> str:
    return str(await add_user(db, "ben"))


@pytest_asyncio.fixture
async def client(asha: str) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {_token(asha)}"},
    ) as signed_in:
        yield signed_in


async def todays_slug(user: str, now: datetime = TUESDAY) -> str:
    payload = await daily.today(user, now=now)
    return str(payload["design"]["slug"])


def another_slug(slug: str) -> str:
    return next(p.slug for p in PROBLEMS if p.slug != slug)


async def add_answer(
    db: Db, user: str, day: date, slug: str, *, scene: list[Any] | None = DRAWING
) -> None:
    async with db.factory() as session:
        session.add(
            PracticeDesignAnswer(
                user_id=uuid.UUID(user), day=day, question_slug=slug, scene=scene, reasoning="Why."
            )
        )
        await session.commit()


async def answers_of(db: Db) -> list[PracticeDesignAnswer]:
    async with db.factory() as session:
        query = select(PracticeDesignAnswer).order_by(PracticeDesignAnswer.day)
        return list((await session.execute(query)).scalars().all())


async def days_of(db: Db) -> list[PracticeDay]:
    async with db.factory() as session:
        return list((await session.execute(select(PracticeDay))).scalars().all())


async def test_todays_problem_opens_with_no_answer_and_writes_nothing(db: Db, asha: str) -> None:
    slug = await todays_slug(asha)
    page = await design.read_page(asha, slug, now=TUESDAY)
    assert page["problem"]["slug"] == slug
    assert page["problem"]["context"] == ["Some context."]
    assert page["answer"] is None
    assert page["editable"] is True
    assert page["done"] is False
    assert page["day"] == TUESDAY_DAY.isoformat()
    assert page["last_answer_day"] is None
    assert page["feedback"] is None
    assert page["feedback_problem"] is None
    assert await days_of(db) == []
    assert await answers_of(db) == []


async def test_a_problem_that_is_not_today_and_never_answered_is_a_404(asha: str) -> None:
    slug = another_slug(await todays_slug(asha))
    with pytest.raises(PracticeNotFound):
        await design.read_page(asha, slug, now=TUESDAY)


async def test_another_persons_answer_is_a_404(db: Db, asha: str, ben: str) -> None:
    slug = another_slug(await todays_slug(ben))
    await add_answer(db, asha, date(2026, 10, 1), slug)
    with pytest.raises(PracticeNotFound):
        await design.read_page(ben, slug, date(2026, 10, 1), now=TUESDAY)
    with pytest.raises(PracticeNotFound):
        await design.read_page(ben, slug, now=TUESDAY)


async def test_first_save_pins_the_problem_and_the_day(db: Db, asha: str) -> None:
    slug = await todays_slug(asha)
    saved = await design.save_answer(asha, slug, DRAWING, "  Why.\r\nBecause. ", now=TUESDAY)
    assert saved["day"] == TUESDAY_DAY.isoformat()
    assert saved["saved_at"]
    (row,) = await days_of(db)
    assert (row.kind, row.question_slug, row.difficulty, row.done_at) == (
        "design",
        slug,
        "medium",
        None,
    )
    (answer,) = await answers_of(db)
    assert answer.scene == DRAWING
    assert answer.reasoning == "Why.\nBecause."


async def test_saving_twice_keeps_one_answer(db: Db, asha: str) -> None:
    slug = await todays_slug(asha)
    await design.save_answer(asha, slug, DRAWING, "First.", now=TUESDAY)
    await design.save_answer(asha, slug, [], "Second.", now=TUESDAY)
    (answer,) = await answers_of(db)
    assert answer.reasoning == "Second."
    assert answer.scene == []


async def test_two_first_saves_at_once_end_in_one_row(
    db: Db, asha: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await todays_slug(asha)
    real = design._find_day
    raced: list[bool] = []

    async def lose_the_race(session: AsyncSession, user: uuid.UUID, day: date) -> Any:
        found = await real(session, user, day)
        if not raced:
            raced.append(True)
            await add_day(db, uuid.UUID(asha), day, slug=slug, topic="caching", kind="design")
        return found

    monkeypatch.setattr(design, "_find_day", lose_the_race)
    await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)
    assert len(await days_of(db)) == 1
    assert len(await answers_of(db)) == 1


async def test_saving_for_an_account_that_has_gone_is_a_404(db: Db) -> None:
    gone = str(uuid.uuid4())
    slug = await todays_slug(gone)
    with pytest.raises(PracticeNotFound):
        await design.save_answer(gone, slug, DRAWING, "Why.", now=TUESDAY)
    assert await days_of(db) == []


@pytest.mark.parametrize("bad", ["x", 5, None, [{"type": "image"}]])
async def test_a_bad_drawing_is_refused_in_words(db: Db, asha: str, bad: Any) -> None:
    slug = await todays_slug(asha)
    with pytest.raises(PracticeInvalid):
        await design.save_answer(asha, slug, bad, "Why.", now=TUESDAY)
    assert await answers_of(db) == []


async def test_reasoning_over_the_limit_is_refused(asha: str) -> None:
    slug = await todays_slug(asha)
    with pytest.raises(PracticeInvalid):
        await design.save_answer(asha, slug, DRAWING, "x" * 4_001, now=TUESDAY)
    await design.save_answer(asha, slug, DRAWING, "x" * 4_000, now=TUESDAY)


async def test_the_eleventh_save_in_a_minute_is_limited(asha: str) -> None:
    slug = await todays_slug(asha)
    for _ in range(10):
        await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)
    with pytest.raises(PracticeRateLimited):
        await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)


async def test_yesterdays_problem_stays_open_until_six(db: Db, asha: str) -> None:
    slug = await todays_slug(asha)
    await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)
    page = await design.read_page(asha, slug, now=BEFORE_SIX)
    assert page["editable"] is True
    assert page["day"] == TUESDAY_DAY.isoformat()
    saved = await design.save_answer(asha, slug, DRAWING, "Later.", now=BEFORE_SIX)
    assert saved["day"] == TUESDAY_DAY.isoformat()
    (answer,) = await answers_of(db)
    assert answer.reasoning == "Later."


async def test_after_six_it_is_read_only_and_a_save_is_refused(asha: str) -> None:
    slug = await todays_slug(asha)
    await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)
    page = await design.read_page(asha, slug, now=AFTER_SIX)
    assert page["editable"] is False
    assert page["answer"]["reasoning"] == "Why."
    with pytest.raises(PracticeConflict):
        await design.save_answer(asha, slug, DRAWING, "Later.", now=AFTER_SIX)


async def test_a_swapped_away_answer_is_read_only_and_uncredited(asha: str) -> None:
    slug = await todays_slug(asha)
    await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)
    await daily.swap(asha, now=TUESDAY)
    page = await design.read_page(asha, slug, now=TUESDAY)
    assert page["editable"] is False
    assert page["done"] is False
    assert page["answer"]["reasoning"] == "Why."
    with pytest.raises(PracticeConflict):
        await design.save_answer(asha, slug, DRAWING, "Again.", now=TUESDAY)


async def test_a_repeat_starts_blank_and_points_at_the_last_answer(
    db: Db, asha: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    only = problem(1)
    monkeypatch.setattr(daily, "load_problems", lambda: (only,))
    await add_day(
        db, uuid.UUID(asha), date(2026, 8, 4), slug=only.slug, topic="caching", kind="design"
    )
    await add_answer(db, asha, date(2026, 8, 4), only.slug)
    page = await design.read_page(asha, only.slug, now=TUESDAY)
    assert page["answer"] is None and page["last_answer_day"] == "2026-08-04"
    assert page["editable"] is True


async def test_a_past_answer_opens_read_only_by_day(db: Db, asha: str) -> None:
    slug = another_slug(await todays_slug(asha))
    await add_answer(db, asha, date(2026, 10, 1), slug)
    page = await design.read_page(asha, slug, date(2026, 10, 1), now=TUESDAY)
    assert page["editable"] is False
    assert page["day"] == "2026-10-01"
    assert page["answer"]["elements"] == DRAWING
    with pytest.raises(PracticeNotFound):
        await design.read_page(asha, slug, date(2026, 10, 2), now=TUESDAY)


async def test_a_day_that_is_not_a_date_is_a_422(client: AsyncClient, asha: str) -> None:
    slug = await todays_slug(asha)
    response = await client.get(f"/api/v1/practice/design/{slug}?day=soon")
    assert response.status_code == 422


async def test_a_past_answer_whose_problem_was_removed_is_a_404(db: Db, asha: str) -> None:
    await add_answer(db, asha, date(2026, 10, 1), "no-longer-listed")
    with pytest.raises(PracticeNotFound):
        await design.read_page(asha, "no-longer-listed", now=TUESDAY)


async def test_prune_clears_old_drawings_and_deletes_older_rows(db: Db, asha: str) -> None:
    today = TUESDAY_DAY
    await add_answer(db, asha, today - timedelta(days=10), "recent")
    await add_answer(db, asha, today - timedelta(days=200), "cleared")
    await add_answer(db, asha, today - timedelta(days=500), "deleted")

    assert await design.prune_answers(TUESDAY) == 1

    cleared, recent = await answers_of(db)
    assert (cleared.question_slug, cleared.scene, cleared.reasoning) == ("cleared", None, None)
    assert recent.scene == DRAWING
    async with db.factory() as session:
        count = await session.execute(
            select(PracticeDesignAnswer).where(PracticeDesignAnswer.scene.is_(None))
        )
        assert [row.question_slug for row in count.scalars()] == ["cleared"]


async def test_the_routes_need_a_token(asha: str) -> None:
    from api.main import app

    slug = await todays_slug(asha)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        read = await anonymous.get(f"/api/v1/practice/design/{slug}")
        save = await anonymous.put(
            f"/api/v1/practice/design/{slug}/answer", json={"elements": [], "reasoning": ""}
        )
    assert read.status_code in (401, 403)
    assert save.status_code in (401, 403)


async def test_a_nul_in_a_drawing_is_taken_out_before_it_is_stored(db: Db, asha: str) -> None:
    # PostgreSQL's JSONB cannot hold one: stored as it came, this save and every later one is a 500.
    slug = await todays_slug(asha)
    drawing = [{"id": "a", "type": "text", "text": "A\x00PI", "customData": {"n\x00te": ["x\x00"]}}]
    await design.save_answer(asha, slug, drawing, "Why.", now=TUESDAY)
    (answer,) = await answers_of(db)
    assert answer.scene == [
        {"id": "a", "type": "text", "text": "API", "customData": {"nte": ["x"]}}
    ]


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
async def test_a_number_json_cannot_hold_is_refused(db: Db, asha: str, number: float) -> None:
    slug = await todays_slug(asha)
    with pytest.raises(PracticeInvalid):
        await design.save_answer(asha, slug, [{"id": "a", "x": number}], "Why.", now=TUESDAY)
    assert await answers_of(db) == []


async def test_the_first_save_after_midnight_goes_to_yesterday(db: Db, asha: str) -> None:
    # Opened at 23:50 and drawn past midnight: nothing was saved on the day it was opened.
    slug = await todays_slug(asha)
    page = await design.read_page(asha, slug, now=JUST_AFTER_MIDNIGHT)
    assert page["editable"] is True
    assert page["day"] == TUESDAY_DAY.isoformat()
    saved = await design.save_answer(asha, slug, DRAWING, "Why.", now=JUST_AFTER_MIDNIGHT)
    assert saved["day"] == TUESDAY_DAY.isoformat()
    async with db.factory() as session:
        rows = (await session.execute(select(PracticeDay))).scalars().all()
    assert [(row.day, row.kind, row.question_slug) for row in rows] == [
        (TUESDAY_DAY, "design", slug)
    ]


async def test_after_midnight_only_yesterdays_problem_is_open(asha: str) -> None:
    slug = await todays_slug(asha)
    other = another_slug(slug)
    with pytest.raises(PracticeNotFound):
        await design.read_page(asha, other, now=JUST_AFTER_MIDNIGHT)
    with pytest.raises(PracticeConflict):
        await design.save_answer(asha, other, DRAWING, "Why.", now=JUST_AFTER_MIDNIGHT)


async def test_a_save_that_keeps_losing_the_race_can_be_tried_again(
    asha: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not a plain 409: that is what "this answer has closed" answers, and the page locks on it.
    slug = await todays_slug(asha)
    await design.save_answer(asha, slug, DRAWING, "Why.", now=TUESDAY)

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        raise IntegrityError("first save", {}, Exception("taken"))

    monkeypatch.setattr(design, "_write", lost)
    with pytest.raises(PracticeRateLimited):
        await design.save_answer(asha, slug, DRAWING, "Again.", now=TUESDAY)
