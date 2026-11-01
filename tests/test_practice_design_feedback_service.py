"""
tests/test_practice_design_feedback_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Get feedback: ``services/practice/design_feedback.py``. The model is patched; SQLite in a temporary
file, the cache is the in-process fallback.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.practice_design_feedback import CRITERIA
from services import cache_backend
from services.cache_backend import CacheBackend
from services.practice import daily, design, design_feedback
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from services.practice.judge import JUDGING_KEY
from storage.database import Base
from storage.models import PracticeDay, PracticeDesignAnswer
from tests.test_practice_daily import Db, add_user
from tests.test_practice_design_service import (
    AFTER_SIX,
    BEFORE_SIX,
    PROBLEMS,
    QUESTION,
    TUESDAY,
    TUESDAY_DAY,
    another_slug,
    todays_slug,
)

REASONING = "The queue absorbs bursts, so the writers can stay small and cheap."


def labelled(number: int) -> list[dict[str, Any]]:
    return [
        {"id": f"s{number}", "type": "rectangle"},
        {
            "id": f"s{number}-t",
            "type": "text",
            "text": f"Box {number}",
            "containerId": f"s{number}",
        },
    ]


DRAWING = labelled(1) + labelled(2) + labelled(3)
REPLY = json.dumps(
    {
        "scores": dict.fromkeys(CRITERIA, 7),
        "notes": dict.fromkeys(CRITERIA, "Fine."),
        "work_on_next": "Say what fails first.",
    }
)


@pytest.fixture(autouse=True)
def lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily, "load_catalogue", lambda: (QUESTION,))
    monkeypatch.setattr(daily, "load_problems", lambda: PROBLEMS)


@pytest.fixture(autouse=True)
def memory_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(CacheBackend, "_client", None)
    monkeypatch.setattr(CacheBackend, "_initialised", True)
    cache_backend._MEMORY.clear()
    yield
    cache_backend._MEMORY.clear()


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    stub = SimpleNamespace(groq_model="test-groq-model", design_feedback_per_minute=1)
    monkeypatch.setattr(design_feedback, "get_settings", lambda: stub)
    return stub


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=REPLY)
    monkeypatch.setattr(design_feedback, "_chat", mock)
    return mock


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    path = tmp_path / "feedback.db"
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
    monkeypatch.setattr(design_feedback, "AsyncSessionLocal", factory)
    yield Db(engine=engine, factory=factory, path=path)
    await engine.dispose()


@pytest_asyncio.fixture
async def asha(db: Db) -> str:
    return str(await add_user(db))


@pytest_asyncio.fixture
async def ben(db: Db) -> str:
    return str(await add_user(db, "ben"))


async def drawn(user: str, reasoning: str = REASONING, now: datetime = TUESDAY) -> str:
    slug = await todays_slug(user, now)
    await design.save_answer(user, slug, DRAWING, reasoning, now=now)
    return slug


async def the_day(db: Db, day: date = TUESDAY_DAY) -> PracticeDay:
    async with db.factory() as session:
        return (
            await session.execute(select(PracticeDay).where(PracticeDay.day == day))
        ).scalar_one()


async def the_answer(db: Db) -> PracticeDesignAnswer:
    async with db.factory() as session:
        return (await session.execute(select(PracticeDesignAnswer))).scalar_one()


async def test_a_good_reply_is_stored_and_returned(db: Db, asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert page["feedback_problem"] is None
    assert page["done"] is True
    assert page["feedback"]["total"] == 70
    assert page["feedback"]["work_on_next"] == "Say what fails first."
    assert set(page["feedback"]["notes"]) == set(CRITERIA)
    answer = await the_answer(db)
    assert answer.total == 70
    assert answer.model == "test-groq-model"
    assert answer.prompt_version == design_feedback.DESIGN_PROMPT_VERSION
    assert answer.submitted_at is not None
    assert answer.feedback_at is not None
    assert answer.input_hash is not None and len(answer.input_hash) == 64


async def test_the_day_is_done_before_the_model_is_called(
    db: Db, asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    seen: list[Any] = []

    async def look(*_args: Any, **_kwargs: Any) -> str:
        seen.append((await the_day(db)).done_at)
        return REPLY

    chat.side_effect = look
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert seen and seen[0] is not None


async def test_a_model_failure_stores_no_score_and_keeps_the_day(
    db: Db, asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    chat.side_effect = RuntimeError("the model is down")
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert page["feedback"] is None and page["feedback_problem"] == "busy" and page["done"] is True
    assert (await the_answer(db)).scores is None


async def test_an_unreadable_reply_stores_no_score(db: Db, asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    chat.return_value = "not json at all"
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert page["feedback"] is None and page["feedback_problem"] == "busy"
    assert (await the_answer(db)).scores is None


async def test_the_same_input_makes_no_second_call_and_spends_no_budget(
    asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY + timedelta(minutes=5))
    assert chat.await_count == 1
    assert page["feedback"]["total"] == 70
    assert page["feedback_problem"] is None
    user_key = f"tr:budget:design-feedback:user:{asha}:2026-11-03"
    assert await CacheBackend.get(user_key) == "1"


async def test_a_changed_prompt_version_calls_again(
    asha: str, chat: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await drawn(asha)
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    monkeypatch.setattr(design_feedback, "DESIGN_PROMPT_VERSION", "2099-01-01.1")
    await design_feedback.request_feedback(asha, slug, now=TUESDAY + timedelta(minutes=5))
    assert chat.await_count == 2


async def test_too_little_drawn_is_refused_and_not_marked_done(
    db: Db, asha: str, chat: AsyncMock
) -> None:
    slug = await todays_slug(asha)
    await design.save_answer(asha, slug, labelled(1), REASONING, now=TUESDAY)
    with pytest.raises(PracticeInvalid, match="shapes"):
        await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert (await the_day(db)).done_at is None
    chat.assert_not_awaited()


async def test_too_little_reasoning_is_refused_and_not_marked_done(
    db: Db, asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha, reasoning="Too short.")
    with pytest.raises(PracticeInvalid, match="reasoning"):
        await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert (await the_day(db)).done_at is None
    chat.assert_not_awaited()


async def test_nothing_saved_yet_is_refused_and_writes_nothing(
    db: Db, asha: str, chat: AsyncMock
) -> None:
    slug = await todays_slug(asha)
    with pytest.raises(PracticeInvalid):
        await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    async with db.factory() as session:
        assert (await session.execute(select(PracticeDay))).first() is None


async def test_the_fourth_request_of_a_day_is_over_the_limit(asha: str, chat: AsyncMock) -> None:
    slug = await todays_slug(asha)
    for number in range(4):
        now = TUESDAY + timedelta(minutes=number)
        await design.save_answer(asha, slug, DRAWING, f"{REASONING} Take {number}.", now=now)
        page = await design_feedback.request_feedback(asha, slug, now=now)
    assert chat.await_count == 3
    assert page["feedback_problem"] == "limit"
    assert page["feedback"]["total"] == 70  # the last good reading stays


async def test_the_shared_budget_stops_everyone(asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    await CacheBackend.set("tr:budget:design-feedback:2026-11-03", "150", 3600)
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert page["feedback_problem"] == "limit"
    chat.assert_not_awaited()


async def test_busy_over_the_minute_costs_no_daily_unit(
    asha: str, ben: str, chat: AsyncMock
) -> None:
    first = await drawn(asha)
    second = await drawn(ben)
    await design_feedback.request_feedback(asha, first, now=TUESDAY)
    page = await design_feedback.request_feedback(ben, second, now=TUESDAY)
    assert page["feedback_problem"] == "busy"
    assert chat.await_count == 1
    assert await CacheBackend.get(f"tr:budget:design-feedback:user:{ben}:2026-11-03") is None
    assert await CacheBackend.get("tr:budget:design-feedback:2026-11-03") == "1"


async def test_during_a_judge_run_feedback_is_busy_and_makes_no_call(
    asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    await CacheBackend.set(JUDGING_KEY, "1", 600)
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert page["feedback_problem"] == "busy" and page["done"] is True
    chat.assert_not_awaited()


async def test_two_presses_at_once_make_one_call(asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)

    async def slow(*_args: Any, **_kwargs: Any) -> str:
        await asyncio.sleep(0.2)
        return REPLY

    chat.side_effect = slow

    async def press() -> dict[str, Any]:
        return await design_feedback.request_feedback(asha, slug, now=TUESDAY)

    results = await asyncio.gather(press(), press(), return_exceptions=True)
    assert chat.await_count == 1
    assert sum(isinstance(result, PracticeConflict) for result in results) == 1


async def test_the_lock_is_released_after_a_failure(asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    chat.side_effect = RuntimeError("the model is down")
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    chat.side_effect = None
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY + timedelta(minutes=2))
    assert page["feedback"] is not None


async def test_a_swapped_away_answer_gets_no_feedback(asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    await daily.swap(asha, now=TUESDAY)
    with pytest.raises(PracticeConflict):
        await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    chat.assert_not_awaited()


async def test_after_six_gets_no_feedback(db: Db, asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    with pytest.raises(PracticeConflict):
        await design_feedback.request_feedback(asha, slug, now=AFTER_SIX)
    assert (await the_day(db)).done_at is None
    chat.assert_not_awaited()


async def test_yesterdays_answer_before_six_marks_yesterday(
    db: Db, asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    page = await design_feedback.request_feedback(asha, slug, now=BEFORE_SIX)
    assert page["day"] == TUESDAY_DAY.isoformat()
    assert page["feedback"] is not None
    assert (await the_day(db)).done_at is not None


async def test_another_persons_answer_is_a_404(asha: str, ben: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    other = slug if slug != await todays_slug(ben) else another_slug(slug)
    with pytest.raises(PracticeNotFound):
        await design_feedback.request_feedback(ben, other, now=TUESDAY)
    chat.assert_not_awaited()


async def test_the_model_id_comes_from_settings(asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    kwargs = chat.await_args.kwargs
    assert kwargs["model"] == "test-groq-model"
    assert kwargs["temperature"] == 0
    assert kwargs["json_mode"] is True
    assert kwargs["reasoning_effort"] == "medium"
    assert kwargs["max_tokens"] == design_feedback.MAX_FEEDBACK_TOKENS


async def test_a_newer_reading_replaces_the_older(db: Db, asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    first = (await the_answer(db)).submitted_at
    later = TUESDAY + timedelta(minutes=5)
    await design.save_answer(asha, slug, DRAWING, f"{REASONING} And more.", now=later)
    chat.return_value = REPLY.replace(": 7", ": 9")
    page = await design_feedback.request_feedback(asha, slug, now=later)
    assert page["feedback"]["total"] == 90
    assert (await the_answer(db)).submitted_at == first


def test_the_budgets_are_the_spec_s() -> None:
    assert design_feedback.PER_USER_DAILY_DESIGN_FEEDBACK == 3
    assert design_feedback.DAILY_DESIGN_FEEDBACK_BUDGET == 150


async def test_a_failed_call_costs_the_person_none_of_their_three(
    asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    chat.side_effect = RuntimeError("the model is down")
    for number in range(4):
        page = await design_feedback.request_feedback(
            asha, slug, now=TUESDAY + timedelta(minutes=number)
        )
        assert page["feedback_problem"] == "busy"
    assert await CacheBackend.get(f"tr:budget:design-feedback:user:{asha}:2026-11-03") is None
    # What it spent still counts against everyone's budget.
    assert await CacheBackend.get("tr:budget:design-feedback:2026-11-03") == "4"

    chat.side_effect = None
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY + timedelta(minutes=9))
    assert page["feedback_problem"] is None
    assert page["feedback"]["total"] == 70
    assert await CacheBackend.get(f"tr:budget:design-feedback:user:{asha}:2026-11-03") == "1"


async def test_an_unreadable_reply_costs_the_person_nothing(asha: str, chat: AsyncMock) -> None:
    slug = await drawn(asha)
    chat.return_value = "not json"
    await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert await CacheBackend.get(f"tr:budget:design-feedback:user:{asha}:2026-11-03") is None


async def test_someone_over_their_limit_does_not_take_the_minute(
    asha: str, chat: AsyncMock
) -> None:
    slug = await drawn(asha)
    await CacheBackend.set(f"tr:budget:design-feedback:user:{asha}:2026-11-03", "3", 3600)
    page = await design_feedback.request_feedback(asha, slug, now=TUESDAY)
    assert page["feedback_problem"] == "limit"
    chat.assert_not_awaited()
    minute = int(TUESDAY.timestamp() // 60)
    assert await CacheBackend.get(f"tr:budget:design-feedback-minute:{minute}") is None
