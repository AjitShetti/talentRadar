"""
tests/test_practice_daily.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The daily question: ``services/practice/daily.py`` and ``/api/v1/practice``.

* Everything runs on SQLite in a temporary file, with ``PRAGMA foreign_keys=ON`` on every
  connection and the module's ``AsyncSessionLocal`` pointed at it. A file, not
  ``:memory:``, because the double-click tests need two real connections. The shared
  Postgres test database is never touched.
* The catalogue is thirty built questions patched into the module; the checked-in file
  does not exist yet.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, get_args
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from domain.practice_pick import Feel, Question, pick_question
from domain.practice_schedule import CONTEST_URL, LEETCODE_PROBLEMSET_URL
from services.practice import daily
from services.practice.catalogue import load_catalogue
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from storage.database import Base
from storage.models import PracticeDay, Profile, User

# Thursday 11:30 IST. The coming Sunday's contest has not started and its card is not showing.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
TODAY = date(2026, 10, 15)

TOPICS = ("array", "hash-table", "string", "two-pointers", "tree", "graph", "dynamic-programming")
LEVELS = ("easy", "medium", "hard")

# The shapes the Practice page types in frontend/lib/api.ts (plan Task 15).
TODAY_KEYS = {
    "day",
    "catalogue",
    "catalogue_size",
    "track",
    "track_label",
    "question",
    "reason",
    "relaxed",
    "done_at",
    "feel",
    "swaps_left",
    "leetcode_daily_url",
    "contest",
}
QUESTION_KEYS = {"id", "slug", "title", "difficulty", "topics", "url"}
CONTEST_KEYS = {"starts_at", "ends_at", "show", "live", "url"}
HISTORY_KEYS = {"days", "counts", "topics"}
HISTORY_DAY_KEYS = {"day", "done", "difficulty", "topic"}
COUNT_KEYS = {"this_month", "last_30", "longest_run"}


def build_catalogue(size: int = 30) -> tuple[Question, ...]:
    return tuple(
        Question(
            id=number,
            slug=f"fixture-question-{number:02d}",
            title=f"Fixture question {number}",
            difficulty=LEVELS[number % 3],  # type: ignore[arg-type]
            topics=(TOPICS[number % len(TOPICS)], TOPICS[(number + 3) % len(TOPICS)]),
        )
        for number in range(1, size + 1)
    )


QUESTIONS = build_catalogue()


@pytest.fixture(autouse=True)
def catalogue(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Thirty questions in place of the file; the real loader's cache is cleared around it."""
    load_catalogue.cache_clear()
    monkeypatch.setattr(daily, "load_catalogue", lambda: QUESTIONS)
    yield
    load_catalogue.cache_clear()


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------


@dataclass
class Db:
    engine: Any
    factory: async_sessionmaker[AsyncSession]
    path: Path

    def insert_elsewhere(self, row: PracticeDay) -> None:
        """Commit ``row`` over a second, independent connection to the same file: another request."""
        other = create_engine(f"sqlite:///{self.path.as_posix()}")
        with Session(other) as session:
            session.add(row)
            session.commit()
        other.dispose()


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    path = tmp_path / "practice.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        Base.metadata.tables[name] for name in ("users", "resumes", "profiles", "practice_days")
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(daily, "AsyncSessionLocal", factory)
    yield Db(engine=engine, factory=factory, path=path)
    await engine.dispose()


async def add_user(db: Db, name: str = "asha") -> uuid.UUID:
    async with db.factory() as session:
        user = User(email=f"{name}@example.com", hashed_password="not-a-real-hash")
        session.add(user)
        await session.commit()
        return user.id


async def add_profile(
    db: Db, user_id: uuid.UUID, roles: list[str], years: float | None = None
) -> None:
    async with db.factory() as session:
        session.add(Profile(user_id=user_id, target_roles=roles, years_experience=years))
        await session.commit()


async def add_day(
    db: Db,
    user_id: uuid.UUID,
    day: date,
    *,
    slug: str = "fixture-question-01",
    topic: str = "array",
    difficulty: str = "easy",
    swaps: int = 0,
    done: bool = False,
    feel: str | None = None,
) -> None:
    async with db.factory() as session:
        session.add(
            PracticeDay(
                user_id=user_id,
                day=day,
                question_slug=slug,
                topic=topic,
                difficulty=difficulty,
                swaps=swaps,
                done_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=6)
                if done
                else None,
                feel=feel,
            )
        )
        await session.commit()


async def days_of(db: Db, user_id: uuid.UUID | None = None) -> list[PracticeDay]:
    async with db.factory() as session:
        query = select(PracticeDay).order_by(PracticeDay.day)
        if user_id is not None:
            query = query.where(PracticeDay.user_id == user_id)
        return list((await session.execute(query)).scalars().all())


def utc(moment: datetime | None) -> datetime | None:
    """SQLite hands timezone-aware columns back naive; every stored instant here is UTC."""
    return moment.replace(tzinfo=UTC) if moment is not None and moment.tzinfo is None else moment


@pytest_asyncio.fixture
async def asha(db: Db) -> str:
    return str(await add_user(db))


def test_the_ratings_are_the_domains() -> None:
    assert get_args(Feel) == daily.FEELS


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------


class TestToday:
    async def test_writes_nothing_and_gives_the_same_question_twice(
        self, db: Db, asha: str
    ) -> None:
        statements: list[str] = []

        @event.listens_for(db.engine.sync_engine, "before_cursor_execute")
        def _record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement.lstrip().split(None, 1)[0].upper())

        first = await daily.today(asha, now=NOW)
        second = await daily.today(asha, now=NOW)

        assert first["question"] is not None
        assert first["question"]["slug"] == second["question"]["slug"]
        assert await days_of(db) == []
        assert statements
        assert set(statements) == {"SELECT"}

    async def test_payload_matches_the_page_types(self, asha: str) -> None:
        payload = await daily.today(asha, now=NOW)

        assert set(payload) == TODAY_KEYS
        assert set(payload["question"]) == QUESTION_KEYS
        assert set(payload["contest"]) == CONTEST_KEYS
        assert payload["day"] == "2026-10-15"
        assert payload["catalogue"] == "ok"
        assert payload["catalogue_size"] == 30
        assert payload["done_at"] is None
        assert payload["feel"] is None
        assert payload["swaps_left"] == 1
        assert payload["leetcode_daily_url"] == LEETCODE_PROBLEMSET_URL
        assert isinstance(payload["relaxed"], list)
        assert isinstance(payload["reason"], str) and payload["reason"]
        assert "streak" not in json.dumps(payload).lower()

    async def test_question_url_is_built_from_the_catalogue_slug(self, asha: str) -> None:
        question = (await daily.today(asha, now=NOW))["question"]

        assert question["url"] == f"https://leetcode.com/problems/{question['slug']}/"
        assert question["slug"] in {q.slug for q in QUESTIONS}

    async def test_no_profile_is_the_general_track(self, asha: str) -> None:
        payload = await daily.today(asha, now=NOW)

        assert payload["track"] == "general"
        assert payload["track_label"] == "General"

    async def test_target_role_sets_the_track(self, db: Db) -> None:
        user = await add_user(db, "dev")
        await add_profile(db, user, ["Backend Engineer"], years=2)

        payload = await daily.today(str(user), now=NOW)

        assert payload["track"] == "backend"
        assert payload["track_label"] == "Backend"

    async def test_empty_catalogue_says_so_and_still_gives_the_links(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        monkeypatch.setattr(daily, "load_catalogue", lambda: ())

        payload = await daily.today(asha, now=NOW)

        assert set(payload) == TODAY_KEYS
        assert payload["catalogue"] == "empty"
        assert payload["catalogue_size"] == 0
        assert payload["question"] is None
        assert payload["reason"] == ""
        assert payload["relaxed"] == []
        assert payload["leetcode_daily_url"] == LEETCODE_PROBLEMSET_URL
        assert payload["contest"]["url"] == CONTEST_URL
        assert await days_of(db) == []

    @pytest.mark.parametrize("content", [None, '{"questions": []}', "not json at all"])
    async def test_a_missing_or_empty_catalogue_file_is_the_empty_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        asha: str,
        content: str | None,
    ) -> None:
        # The real loader, pointed at a file that is absent, empty or unreadable: nothing raises.
        path = tmp_path / "leetcode_free.json"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(daily, "load_catalogue", lambda: load_catalogue.__wrapped__(path))

        payload = await daily.today(asha, now=NOW)

        assert payload["catalogue"] == "empty"
        assert payload["question"] is None
        assert payload["leetcode_daily_url"] == LEETCODE_PROBLEMSET_URL
        assert payload["contest"]["url"] == CONTEST_URL

    async def test_contest_block_follows_the_schedule(self, asha: str) -> None:
        before = (await daily.today(asha, now=NOW))["contest"]
        during = (await daily.today(asha, now=datetime(2026, 10, 18, 3, 0, tzinfo=UTC)))["contest"]

        assert before["starts_at"] == "2026-10-18T02:30:00+00:00"
        assert before["ends_at"] == "2026-10-18T04:00:00+00:00"
        assert (before["show"], before["live"]) == (False, False)
        assert before["url"] == CONTEST_URL
        assert (during["show"], during["live"]) == (True, True)

    async def test_ist_day_rolls_at_ist_midnight_not_utc(self, asha: str) -> None:
        # 2026-10-14 23:59Z is 05:29 IST and 2026-10-15 00:01Z is 05:31 IST: one IST day,
        # either side of UTC midnight, so the question must not change.
        before_utc_midnight = await daily.today(
            asha, now=datetime(2026, 10, 14, 23, 59, tzinfo=UTC)
        )
        after_utc_midnight = await daily.today(asha, now=datetime(2026, 10, 15, 0, 1, tzinfo=UTC))
        last_minute = await daily.today(asha, now=datetime(2026, 10, 15, 18, 29, tzinfo=UTC))
        ist_midnight = await daily.today(asha, now=datetime(2026, 10, 15, 18, 30, tzinfo=UTC))

        assert before_utc_midnight["day"] == after_utc_midnight["day"] == "2026-10-15"
        assert before_utc_midnight["question"] == after_utc_midnight["question"]
        assert last_minute["day"] == "2026-10-15"
        assert last_minute["question"] == before_utc_midnight["question"]
        assert ist_midnight["day"] == "2026-10-16"

    async def test_a_naive_now_is_refused(self, asha: str) -> None:
        with pytest.raises(ValueError, match="naive"):
            await daily.today(asha, now=datetime(2026, 10, 15, 6, 0))

    async def test_a_stored_swap_count_above_the_maximum_is_capped(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        # pick_question replays swaps + 1 draws, so an unbounded stored value is CPU on a read.
        await add_day(db, uuid.UUID(asha), TODAY, swaps=500)
        seen: list[int] = []
        real = daily.pick_question

        def spy(**kwargs: Any) -> Any:
            seen.append(kwargs["swaps"])
            return real(**kwargs)

        monkeypatch.setattr(daily, "pick_question", spy)

        payload = await daily.today(asha, now=NOW)

        assert seen and max(seen) <= daily.MAX_SWAPS
        assert payload["swaps_left"] == 0

    async def test_the_pick_sees_only_this_users_last_180_days_before_today(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        mine = uuid.UUID(asha)
        other = await add_user(db, "other")
        for offset, slug in (
            (181, "too-old"),
            (180, "oldest-kept"),
            (1, "yesterday"),
            (0, "today"),
        ):
            await add_day(db, mine, TODAY - timedelta(days=offset), slug=slug, done=True)
        await add_day(db, other, TODAY - timedelta(days=1), slug="not-mine", done=True)
        histories: list[Any] = []
        real = daily.pick_question

        def spy(**kwargs: Any) -> Any:
            histories.append(kwargs["history"])
            return real(**kwargs)

        monkeypatch.setattr(daily, "pick_question", spy)

        await daily.today(asha, now=NOW)

        assert {item.slug for item in histories[0]} == {"oldest-kept", "yesterday"}

    async def test_another_users_done_is_never_visible(self, db: Db, asha: str) -> None:
        other = str(await add_user(db, "other"))
        await daily.mark_done(other, "hard", now=NOW)

        payload = await daily.today(asha, now=NOW)

        assert payload["done_at"] is None
        assert payload["feel"] is None

    async def test_an_unreadable_user_id_is_not_found(self) -> None:
        with pytest.raises(PracticeNotFound):
            await daily.today("not-a-uuid", now=NOW)


# ---------------------------------------------------------------------------
# done, feel, undo
# ---------------------------------------------------------------------------


class TestDone:
    async def test_stores_one_row_with_the_picked_slug(self, db: Db, asha: str) -> None:
        shown = (await daily.today(asha, now=NOW))["question"]

        payload = await daily.mark_done(asha, None, now=NOW)

        rows = await days_of(db)
        assert len(rows) == 1
        assert rows[0].question_slug == shown["slug"]
        assert rows[0].day == TODAY
        assert rows[0].swaps == 0
        assert utc(rows[0].done_at) == NOW
        assert payload["done_at"] == NOW.isoformat()
        assert payload["question"]["slug"] == shown["slug"]
        assert payload["swaps_left"] == 0

    async def test_stored_topic_and_difficulty_are_the_picks(self, db: Db, asha: str) -> None:
        shown = (await daily.today(asha, now=NOW))["question"]

        await daily.mark_done(asha, None, now=NOW)

        row = (await days_of(db))[0]
        assert row.difficulty == shown["difficulty"]
        assert row.topic in shown["topics"]

    async def test_a_second_call_is_idempotent(self, db: Db, asha: str) -> None:
        first = await daily.mark_done(asha, None, now=NOW)
        second = await daily.mark_done(asha, None, now=NOW + timedelta(hours=2))

        rows = await days_of(db)
        assert len(rows) == 1
        assert utc(rows[0].done_at) == NOW
        assert first["done_at"] == second["done_at"] == NOW.isoformat()

    async def test_feel_can_come_with_done(self, asha: str) -> None:
        payload = await daily.mark_done(asha, "hard", now=NOW)

        assert payload["feel"] == "hard"

    async def test_a_repeat_with_a_feel_rates_it_and_one_without_keeps_the_rating(
        self, db: Db, asha: str
    ) -> None:
        await daily.mark_done(asha, None, now=NOW)
        rated = await daily.mark_done(asha, "easy", now=NOW + timedelta(minutes=1))
        kept = await daily.mark_done(asha, None, now=NOW + timedelta(minutes=2))

        assert rated["feel"] == kept["feel"] == "easy"
        assert utc((await days_of(db))[0].done_at) == NOW

    async def test_done_after_ist_midnight_is_new_day(self, db: Db, asha: str) -> None:
        late = datetime(2026, 10, 15, 18, 40, tzinfo=UTC)  # 00:10 IST on the 16th

        payload = await daily.mark_done(asha, None, now=late)

        assert payload["day"] == "2026-10-16"
        assert [row.day for row in await days_of(db)] == [date(2026, 10, 16)]

    async def test_done_without_a_catalogue_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        monkeypatch.setattr(daily, "load_catalogue", lambda: ())

        with pytest.raises(PracticeNotFound):
            await daily.mark_done(asha, None, now=NOW)
        assert await days_of(db) == []

    async def test_an_unknown_feel_is_invalid(self, db: Db, asha: str) -> None:
        with pytest.raises(PracticeInvalid):
            await daily.mark_done(asha, "meh", now=NOW)  # type: ignore[arg-type]
        assert await days_of(db) == []

    async def test_the_done_question_survives_a_catalogue_refresh(
        self, monkeypatch: pytest.MonkeyPatch, asha: str
    ) -> None:
        done = (await daily.mark_done(asha, None, now=NOW))["question"]
        # The monthly rebuild renumbers the questions. Find a renumbering that would make a
        # fresh pick for this user and day a different question, so the test can tell.
        for shift in range(1, len(QUESTIONS)):
            renumbered = tuple(
                Question(
                    id=(q.id + shift) % len(QUESTIONS) + 1,
                    slug=q.slug,
                    title=q.title,
                    difficulty=q.difficulty,
                    topics=q.topics,
                )
                for q in QUESTIONS
            )
            fresh = pick_question(
                user_id=asha,
                day=TODAY,
                swaps=0,
                track="general",
                years=None,
                history=[],
                catalogue=renumbered,
            )
            if fresh is not None and fresh.question.slug != done["slug"]:
                break
        else:
            pytest.fail("no renumbering changes the pick")
        monkeypatch.setattr(daily, "load_catalogue", lambda: renumbered)

        again = await daily.today(asha, now=NOW)

        assert again["question"]["slug"] == done["slug"]
        assert again["done_at"] is not None


class TestFeel:
    async def test_rates_the_done_question(self, db: Db, asha: str) -> None:
        await daily.mark_done(asha, None, now=NOW)

        payload = await daily.set_feel(asha, "right", now=NOW)

        assert payload["feel"] == "right"
        assert (await days_of(db))[0].feel == "right"

    async def test_can_be_changed(self, asha: str) -> None:
        await daily.mark_done(asha, "easy", now=NOW)

        assert (await daily.set_feel(asha, "hard", now=NOW))["feel"] == "hard"

    async def test_needs_a_done_question(self, db: Db, asha: str) -> None:
        with pytest.raises(PracticeConflict):
            await daily.set_feel(asha, "right", now=NOW)
        assert await days_of(db) == []

    async def test_a_swapped_but_undone_day_cannot_be_rated(self, asha: str) -> None:
        await daily.swap(asha, now=NOW)

        with pytest.raises(PracticeConflict):
            await daily.set_feel(asha, "right", now=NOW)

    async def test_an_unknown_feel_is_invalid(self, asha: str) -> None:
        await daily.mark_done(asha, None, now=NOW)

        with pytest.raises(PracticeInvalid):
            await daily.set_feel(asha, "meh", now=NOW)  # type: ignore[arg-type]

    async def test_yesterdays_done_cannot_be_rated_today(self, db: Db, asha: str) -> None:
        await add_day(db, uuid.UUID(asha), TODAY - timedelta(days=1), done=True)

        with pytest.raises(PracticeConflict):
            await daily.set_feel(asha, "hard", now=NOW)
        assert (await days_of(db))[0].feel is None


class TestUndo:
    async def test_clears_done_the_same_day(self, db: Db, asha: str) -> None:
        await daily.mark_done(asha, "hard", now=NOW)

        payload = await daily.undo_done(asha, now=NOW + timedelta(hours=1))

        assert payload["done_at"] is None
        assert payload["feel"] is None
        rows = await days_of(db)
        assert len(rows) == 1
        assert rows[0].done_at is None
        assert rows[0].feel is None

    async def test_can_be_done_again_afterwards(self, asha: str) -> None:
        await daily.mark_done(asha, None, now=NOW)
        await daily.undo_done(asha, now=NOW)

        again = await daily.mark_done(asha, None, now=NOW + timedelta(hours=3))

        assert again["done_at"] == (NOW + timedelta(hours=3)).isoformat()

    async def test_for_yesterdays_row_is_a_noop(self, db: Db, asha: str) -> None:
        yesterday = TODAY - timedelta(days=1)
        await add_day(db, uuid.UUID(asha), yesterday, done=True, feel="easy")

        payload = await daily.undo_done(asha, now=NOW)

        row = (await days_of(db))[0]
        assert row.day == yesterday
        assert row.done_at is not None
        assert row.feel == "easy"
        assert payload["done_at"] is None

    async def test_with_nothing_done_is_a_noop(self, db: Db, asha: str) -> None:
        payload = await daily.undo_done(asha, now=NOW)

        assert payload["done_at"] is None
        assert await days_of(db) == []

    async def test_leaves_another_users_row_alone(self, db: Db, asha: str) -> None:
        other = str(await add_user(db, "other"))
        await daily.mark_done(other, None, now=NOW)

        await daily.undo_done(asha, now=NOW)

        assert (await days_of(db, uuid.UUID(other)))[0].done_at is not None


# ---------------------------------------------------------------------------
# swap
# ---------------------------------------------------------------------------


class TestSwap:
    async def test_changes_the_question_and_persists_one_swap(self, db: Db, asha: str) -> None:
        before = (await daily.today(asha, now=NOW))["question"]

        payload = await daily.swap(asha, now=NOW)

        assert payload["question"]["slug"] != before["slug"]
        assert payload["swaps_left"] == 0
        rows = await days_of(db)
        assert len(rows) == 1
        assert rows[0].swaps == 1
        assert rows[0].question_slug == payload["question"]["slug"]
        assert rows[0].done_at is None

    async def test_today_afterwards_returns_the_swapped_question(self, asha: str) -> None:
        swapped = (await daily.swap(asha, now=NOW))["question"]

        again = await daily.today(asha, now=NOW + timedelta(hours=3))

        assert again["question"] == swapped
        assert again["swaps_left"] == 0

    async def test_a_second_swap_is_a_conflict_and_changes_nothing(self, db: Db, asha: str) -> None:
        swapped = (await daily.swap(asha, now=NOW))["question"]

        with pytest.raises(PracticeConflict):
            await daily.swap(asha, now=NOW)

        rows = await days_of(db)
        assert [row.swaps for row in rows] == [1]
        assert rows[0].question_slug == swapped["slug"]

    async def test_the_next_ist_day_has_a_swap_again(self, asha: str) -> None:
        await daily.swap(asha, now=NOW)

        tomorrow = await daily.today(asha, now=NOW + timedelta(days=1))

        assert tomorrow["swaps_left"] == 1

    async def test_after_done_is_a_conflict(self, db: Db, asha: str) -> None:
        # The "done" belongs to the question shown; a swap would hand it to another one.
        done = (await daily.mark_done(asha, None, now=NOW))["question"]

        with pytest.raises(PracticeConflict):
            await daily.swap(asha, now=NOW)

        row = (await days_of(db))[0]
        assert (row.swaps, row.question_slug) == (0, done["slug"])

    async def test_after_an_undo_is_allowed(self, asha: str) -> None:
        await daily.mark_done(asha, None, now=NOW)
        await daily.undo_done(asha, now=NOW)

        payload = await daily.swap(asha, now=NOW)

        assert payload["swaps_left"] == 0

    async def test_a_done_after_a_swap_keeps_the_swapped_question(self, db: Db, asha: str) -> None:
        swapped = (await daily.swap(asha, now=NOW))["question"]

        done = await daily.mark_done(asha, None, now=NOW + timedelta(hours=1))

        assert done["question"] == swapped
        rows = await days_of(db)
        assert len(rows) == 1
        assert (rows[0].swaps, rows[0].question_slug) == (1, swapped["slug"])

    async def test_without_a_catalogue_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        monkeypatch.setattr(daily, "load_catalogue", lambda: ())

        with pytest.raises(PracticeNotFound):
            await daily.swap(asha, now=NOW)
        assert await days_of(db) == []


# ---------------------------------------------------------------------------
# a double click
# ---------------------------------------------------------------------------


class TestDoubleClick:
    async def test_two_swaps_at_once_give_one_swap_and_one_conflict(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        _both_read_before_either_writes(monkeypatch)

        results = await asyncio.gather(
            daily.swap(asha, now=NOW), daily.swap(asha, now=NOW), return_exceptions=True
        )

        failures = [r for r in results if isinstance(r, BaseException)]
        winners = [r for r in results if not isinstance(r, BaseException)]
        assert len(winners) == 1
        assert len(failures) == 1 and isinstance(failures[0], PracticeConflict)
        rows = await days_of(db)
        assert len(rows) == 1 and rows[0].swaps == 1
        assert rows[0].question_slug == winners[0]["question"]["slug"]

    async def test_two_dones_at_once_make_one_row_and_both_succeed(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        _both_read_before_either_writes(monkeypatch)

        results = await asyncio.gather(
            daily.mark_done(asha, None, now=NOW), daily.mark_done(asha, None, now=NOW)
        )

        assert results[0]["done_at"] == results[1]["done_at"] == NOW.isoformat()
        assert len(await days_of(db)) == 1

    async def test_done_loses_the_insert_race_and_still_succeeds(self, db: Db, asha: str) -> None:
        raced = _race(
            db,
            lambda: PracticeDay(
                user_id=uuid.UUID(asha),
                day=TODAY,
                question_slug="fixture-question-05",
                topic="array",
                difficulty="easy",
                swaps=0,
                done_at=NOW - timedelta(minutes=5),
            ),
        )

        payload = await daily.mark_done(asha, "right", now=NOW)

        assert raced.fired
        rows = await days_of(db)
        assert len(rows) == 1
        assert utc(rows[0].done_at) == NOW - timedelta(minutes=5)  # the first click's moment
        assert payload["feel"] == "right"

    async def test_swap_loses_the_insert_race_to_a_swap(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        await add_day(db, uuid.UUID(asha), TODAY, slug="fixture-question-05", swaps=1)
        blind = _stale_view(monkeypatch)

        with pytest.raises(PracticeConflict):
            await daily.swap(asha, now=NOW)

        assert blind.insert_attempted
        rows = await days_of(db)
        assert [(r.swaps, r.question_slug) for r in rows] == [(1, "fixture-question-05")]

    async def test_swap_loses_the_insert_race_to_a_done(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        await add_day(db, uuid.UUID(asha), TODAY, slug="fixture-question-05", done=True)
        blind = _stale_view(monkeypatch)

        with pytest.raises(PracticeConflict):
            await daily.swap(asha, now=NOW)

        assert blind.insert_attempted
        rows = await days_of(db)
        assert [(r.swaps, r.done_at is not None) for r in rows] == [(0, True)]

    async def test_swap_that_misses_a_fresh_row_still_swaps_it_once(
        self, monkeypatch: pytest.MonkeyPatch, db: Db, asha: str
    ) -> None:
        # The row is there (nothing swapped, nothing done) but this request did not see it:
        # the conditional update finds it all the same, and swaps it exactly once.
        await add_day(db, uuid.UUID(asha), TODAY, slug="fixture-question-05")
        _stale_view(monkeypatch)

        payload = await daily.swap(asha, now=NOW)

        assert payload["swaps_left"] == 0
        rows = await days_of(db)
        assert len(rows) == 1 and rows[0].swaps == 1
        assert rows[0].question_slug == payload["question"]["slug"]

    async def test_a_swap_for_a_missing_account_is_not_found(self, db: Db) -> None:
        with pytest.raises(PracticeNotFound):
            await daily.swap(str(uuid.uuid4()), now=NOW)
        assert await days_of(db) == []

    async def test_a_done_for_a_missing_account_is_not_found(self, db: Db) -> None:
        with pytest.raises(PracticeNotFound):
            await daily.mark_done(str(uuid.uuid4()), None, now=NOW)
        assert await days_of(db) == []


@dataclass
class Race:
    fired: bool = False


def _both_read_before_either_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold each of two concurrent requests after its first read until the other has read too.

    Both then believe there is no row for today, which is exactly a double click.
    """
    barrier = asyncio.Barrier(2)
    real_load = daily._load
    started = {"loads": 0}

    async def load(*args: Any, **kwargs: Any) -> Any:
        state = await real_load(*args, **kwargs)
        ordinal = started["loads"]
        started["loads"] += 1
        if ordinal < 2:  # the two requests' first reads; later reads build the response
            await barrier.wait()
        return state

    monkeypatch.setattr(daily, "_load", load)


@dataclass
class Blind:
    insert_attempted: bool = False


def _stale_view(monkeypatch: pytest.MonkeyPatch) -> Blind:
    """Make one request read "no row for today" although another request has just written it.

    SQLite takes a database-wide write lock at the first UPDATE, so a second connection cannot
    slip a row in mid-request the way Postgres lets it. This reproduces the same result: the
    first read and the first re-read both miss the row (the later ones, which build the
    response, see it), so the request goes on to INSERT and meets the unique (user, day)
    constraint.
    """
    blind = Blind()
    real_load = daily._load
    real_find = daily._find
    misses = {"load": 1, "find": 1}

    async def stale_load(*args: Any, **kwargs: Any) -> Any:
        state = await real_load(*args, **kwargs)
        if misses["load"]:
            misses["load"] -= 1
            return replace(state, row=None)
        return state

    async def stale_find(*args: Any, **kwargs: Any) -> Any:
        if misses["find"]:
            misses["find"] -= 1
            blind.insert_attempted = True
            return None
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(daily, "_load", stale_load)
    monkeypatch.setattr(daily, "_find", stale_find)
    return blind


def _race(db: Db, competitor: Callable[[], PracticeDay]) -> Race:
    """Commit ``competitor()`` from another connection just before this one's first INSERT.

    That is the interleaving a double click produces: both requests read "no row for today",
    one inserts first, and the other's INSERT meets the unique (user, day) constraint.
    """
    race = Race()

    @event.listens_for(db.engine.sync_engine, "before_cursor_execute")
    def _before(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if race.fired or not statement.lstrip().upper().startswith("INSERT INTO PRACTICE_DAYS"):
            return
        race.fired = True
        db.insert_elsewhere(competitor())

    return race


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


class TestHistory:
    async def test_counts_three_done_days_with_a_gap(self, db: Db, asha: str) -> None:
        user = uuid.UUID(asha)
        today = date(2026, 10, 16)
        for day in (date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 13)):
            await add_day(db, user, day, done=True)
        # A swap that was never done is not practice.
        await add_day(db, user, date(2026, 10, 12), swaps=1)

        payload = await daily.history(asha, now=datetime(2026, 10, 16, 6, 0, tzinfo=UTC))

        assert payload["counts"] == {"this_month": 3, "last_30": 3, "longest_run": 2}
        assert set(payload) == HISTORY_KEYS
        assert today.isoformat() == payload["days"][-1]["day"]

    async def test_has_no_streak_anywhere(self, db: Db, asha: str) -> None:
        await add_day(db, uuid.UUID(asha), TODAY, done=True)

        payload = await daily.history(asha, now=NOW)

        text = json.dumps(payload).lower()
        assert "streak" not in text
        assert set(payload["counts"]) == COUNT_KEYS

    async def test_this_month_last_30_and_longest_run_differ(self, db: Db, asha: str) -> None:
        user = uuid.UUID(asha)
        now = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
        await add_day(db, user, date(2026, 10, 14), done=True)
        await add_day(db, user, date(2026, 10, 15), done=True)
        await add_day(
            db, user, date(2026, 9, 20), done=True
        )  # 26 days back: last_30, not this month
        await add_day(db, user, date(2026, 9, 16), done=True)  # 30 days back: outside last_30
        # A run of five long before the calendar window still counts as the longest.
        for offset in range(5):
            await add_day(db, user, date(2026, 6, 1) + timedelta(days=offset), done=True)

        counts = (await daily.history(asha, now=now))["counts"]

        assert counts == {"this_month": 2, "last_30": 3, "longest_run": 5}

    async def test_days_are_one_entry_per_day_oldest_first(self, db: Db, asha: str) -> None:
        user = uuid.UUID(asha)
        await add_day(db, user, TODAY, topic="graph", difficulty="hard", done=True)
        await add_day(db, user, TODAY - timedelta(days=2), swaps=1, topic="tree")

        payload = await daily.history(asha, weeks=2, now=NOW)

        days = payload["days"]
        assert len(days) == 14
        assert days[0]["day"] == (TODAY - timedelta(days=13)).isoformat()
        assert days[-1] == {
            "day": "2026-10-15",
            "done": True,
            "difficulty": "hard",
            "topic": "graph",
        }
        assert all(set(entry) == HISTORY_DAY_KEYS for entry in days)
        swapped_only = next(e for e in days if e["day"] == (TODAY - timedelta(days=2)).isoformat())
        assert swapped_only == {
            "day": swapped_only["day"],
            "done": False,
            "difficulty": None,
            "topic": None,
        }
        assert [e["day"] for e in days] == sorted(e["day"] for e in days)

    async def test_topics_count_done_days_in_the_window(self, db: Db, asha: str) -> None:
        user = uuid.UUID(asha)
        for offset, topic in ((0, "graph"), (1, "graph"), (2, "tree"), (3, "array"), (4, "array")):
            await add_day(db, user, TODAY - timedelta(days=offset), topic=topic, done=True)
        await add_day(db, user, TODAY - timedelta(days=200), topic="stack", done=True)

        payload = await daily.history(asha, weeks=4, now=NOW)

        assert payload["topics"] == [
            {"topic": "array", "count": 2},
            {"topic": "graph", "count": 2},
            {"topic": "tree", "count": 1},
        ]

    async def test_weeks_are_bounded(self, asha: str) -> None:
        assert len((await daily.history(asha, weeks=0, now=NOW))["days"]) == 7
        assert len((await daily.history(asha, weeks=99, now=NOW))["days"]) == 26 * 7
        assert len((await daily.history(asha, now=NOW))["days"]) == 12 * 7

    async def test_empty_history(self, asha: str) -> None:
        payload = await daily.history(asha, now=NOW)

        assert payload["counts"] == {"this_month": 0, "last_30": 0, "longest_run": 0}
        assert payload["topics"] == []
        assert not any(entry["done"] for entry in payload["days"])

    async def test_another_users_days_are_not_counted(self, db: Db, asha: str) -> None:
        other = await add_user(db, "other")
        await add_day(db, other, TODAY, done=True)

        counts = (await daily.history(asha, now=NOW))["counts"]

        assert counts == {"this_month": 0, "last_30": 0, "longest_run": 0}


# ---------------------------------------------------------------------------
# practised_recently
# ---------------------------------------------------------------------------


class TestPractisedRecently:
    async def test_true_within_14_days_of_a_done_row(self, db: Db, asha: str) -> None:
        await add_day(db, uuid.UUID(asha), TODAY - timedelta(days=14), done=True)

        assert await daily.practised_recently(asha, now=NOW) is True

    async def test_false_after_14_days(self, db: Db, asha: str) -> None:
        await add_day(db, uuid.UUID(asha), TODAY - timedelta(days=15), done=True)

        assert await daily.practised_recently(asha, now=NOW) is False

    async def test_false_with_no_rows(self, asha: str) -> None:
        assert await daily.practised_recently(asha, now=NOW) is False

    async def test_false_for_an_unreadable_user_id(self) -> None:
        assert await daily.practised_recently("not-a-uuid", now=NOW) is False

    async def test_a_swap_that_was_never_done_is_not_practice(self, db: Db, asha: str) -> None:
        await add_day(db, uuid.UUID(asha), TODAY, swaps=1)

        assert await daily.practised_recently(asha, now=NOW) is False

    async def test_the_window_is_a_parameter(self, db: Db, asha: str) -> None:
        await add_day(db, uuid.UUID(asha), TODAY - timedelta(days=5), done=True)

        assert await daily.practised_recently(asha, days=5, now=NOW) is True
        assert await daily.practised_recently(asha, days=4, now=NOW) is False

    async def test_another_users_practice_does_not_count(self, db: Db, asha: str) -> None:
        other = await add_user(db, "other")
        await add_day(db, other, TODAY, done=True)

        assert await daily.practised_recently(asha, now=NOW) is False


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------


def _token(user_id: uuid.UUID | str) -> str:
    from api.auth import create_access_token

    return create_access_token(data={"sub": str(user_id), "email": "x@example.com", "role": "user"})


@pytest_asyncio.fixture
async def anonymous() -> AsyncIterator[AsyncClient]:
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def client(db: Db, asha: str) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {_token(asha)}"},
    ) as signed_in:
        yield signed_in


API = "/api/v1/practice"


class TestRoutes:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/today"),
            ("POST", "/today/done"),
            ("PATCH", "/today/feel"),
            ("DELETE", "/today/done"),
            ("POST", "/today/swap"),
            ("GET", "/history"),
        ],
    )
    async def test_every_route_needs_a_signed_in_user(
        self, anonymous: AsyncClient, method: str, path: str
    ) -> None:
        response = await anonymous.request(method, API + path)

        assert response.status_code in (401, 403)

    async def test_today_without_a_token_is_401(self, anonymous: AsyncClient) -> None:
        response = await anonymous.get(f"{API}/today")

        assert response.status_code == 401

    async def test_today_with_a_token_has_the_page_shape(self, client: AsyncClient) -> None:
        response = await client.get(f"{API}/today")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == TODAY_KEYS
        assert set(body["question"]) == QUESTION_KEYS
        assert set(body["contest"]) == CONTEST_KEYS
        assert body["catalogue"] == "ok"
        assert body["swaps_left"] == 1
        assert "streak" not in response.text.lower()

    async def test_today_with_no_catalogue_still_answers(
        self, monkeypatch: pytest.MonkeyPatch, client: AsyncClient
    ) -> None:
        monkeypatch.setattr(daily, "load_catalogue", lambda: ())

        response = await client.get(f"{API}/today")

        assert response.status_code == 200
        body = response.json()
        assert body["catalogue"] == "empty"
        assert body["question"] is None
        assert body["leetcode_daily_url"] == LEETCODE_PROBLEMSET_URL

    async def test_swap_twice_is_409(self, client: AsyncClient) -> None:
        first = await client.post(f"{API}/today/swap")
        second = await client.post(f"{API}/today/swap")

        assert first.status_code == 200
        assert first.json()["swaps_left"] == 0
        assert second.status_code == 409
        assert second.json()["detail"]

    async def test_done_feel_and_undo_round_trip(self, client: AsyncClient) -> None:
        done = await client.post(f"{API}/today/done", json={"feel": "hard"})
        rated = await client.patch(f"{API}/today/feel", json={"feel": "easy"})
        undone = await client.delete(f"{API}/today/done")

        assert done.status_code == 200 and done.json()["done_at"] is not None
        assert done.json()["feel"] == "hard"
        assert rated.status_code == 200 and rated.json()["feel"] == "easy"
        assert undone.status_code == 200
        assert undone.json()["done_at"] is None and undone.json()["feel"] is None

    async def test_done_needs_no_body(self, client: AsyncClient) -> None:
        response = await client.post(f"{API}/today/done")

        assert response.status_code == 200
        assert response.json()["done_at"] is not None

    async def test_done_twice_is_still_200(self, client: AsyncClient) -> None:
        first = await client.post(f"{API}/today/done")
        second = await client.post(f"{API}/today/done")

        assert second.status_code == 200
        assert second.json()["done_at"] == first.json()["done_at"]

    async def test_rating_before_done_is_409(self, client: AsyncClient) -> None:
        response = await client.patch(f"{API}/today/feel", json={"feel": "easy"})

        assert response.status_code == 409

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("POST", "/today/done", {"feel": "meh"}),
            ("PATCH", "/today/feel", {"feel": "meh"}),
            ("PATCH", "/today/feel", {}),
            ("PATCH", "/today/feel", {"feel": None}),
        ],
    )
    async def test_a_bad_body_is_422(
        self, client: AsyncClient, method: str, path: str, body: dict[str, Any]
    ) -> None:
        response = await client.request(method, API + path, json=body)

        assert response.status_code == 422

    async def test_history_has_the_page_shape(self, client: AsyncClient) -> None:
        await client.post(f"{API}/today/done")

        response = await client.get(f"{API}/history?weeks=4")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == HISTORY_KEYS
        assert len(body["days"]) == 28
        assert set(body["counts"]) == COUNT_KEYS
        assert body["counts"]["last_30"] == 1
        assert "streak" not in response.text.lower()

    @pytest.mark.parametrize("weeks", ["0", "27", "-1", "x"])
    async def test_history_weeks_outside_1_to_26_is_422(
        self, client: AsyncClient, weeks: str
    ) -> None:
        response = await client.get(f"{API}/history?weeks={weeks}")

        assert response.status_code == 422

    @pytest.mark.parametrize("weeks", ["1", "26"])
    async def test_history_weeks_at_the_bounds_is_200(
        self, client: AsyncClient, weeks: str
    ) -> None:
        assert (await client.get(f"{API}/history?weeks={weeks}")).status_code == 200

    @pytest.mark.parametrize(
        ("error", "status"),
        [(PracticeConflict, 409), (PracticeInvalid, 422), (PracticeNotFound, 404)],
    )
    async def test_service_errors_map_to_their_status(
        self, client: AsyncClient, error: type[Exception], status: int
    ) -> None:
        boom = AsyncMock(side_effect=error("Said for the person."))
        with (
            patch.object(daily, "today", boom),
            patch.object(daily, "swap", boom),
            patch.object(daily, "history", boom),
        ):
            responses = [
                await client.get(f"{API}/today"),
                await client.post(f"{API}/today/swap"),
                await client.get(f"{API}/history"),
            ]

        assert [r.status_code for r in responses] == [status] * 3
        assert all(r.json()["detail"] == "Said for the person." for r in responses)

    async def test_each_user_sees_only_their_own_day(self, db: Db, asha: str) -> None:
        from api.main import app

        other = await add_user(db, "other")
        async with (
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {_token(asha)}"},
            ) as mine,
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {_token(other)}"},
            ) as theirs,
        ):
            await theirs.post(f"{API}/today/done", json={"feel": "hard"})
            my_day = (await mine.get(f"{API}/today")).json()
            their_day = (await theirs.get(f"{API}/today")).json()

        assert my_day["done_at"] is None and my_day["feel"] is None
        assert their_day["done_at"] is not None and their_day["feel"] == "hard"

    async def test_a_token_for_a_deleted_account_cannot_write(self, db: Db) -> None:
        from api.main import app

        ghost = _token(uuid.uuid4())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {ghost}"},
        ) as signed_in:
            read = await signed_in.get(f"{API}/today")
            write = await signed_in.post(f"{API}/today/swap")

        assert read.status_code == 200
        assert write.status_code == 404
        async with db.factory() as session:
            assert (
                await session.execute(select(func.count()).select_from(PracticeDay))
            ).scalar() == 0
