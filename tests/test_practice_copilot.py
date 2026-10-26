"""
tests/test_practice_copilot.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Practice cards on the Overview: ``_practice_deadline_cards`` and
``_practice_suggestion_card`` in ``services/copilot.py``, and where ``build_briefing`` puts them.

* The producers run for real on SQLite in a temporary file with ``PRAGMA foreign_keys=ON`` on
  every connection and ``AsyncSessionLocal`` of both the copilot and the daily-question
  service pointed at it. The shared Postgres test database is never touched. The catalogue is
  thirty fictional questions patched into the module.
* The briefing tests patch every producer, as ``tests/test_copilot.py`` does, so the order of the
  cards and the dismissal filter are tested without a database.
* A failing producer must never blank the briefing: it gives ``[]`` or ``None`` and is logged.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.practice_pick import Question
from domain.practice_schedule import CONTEST_URL, IST
from services import copilot
from services.copilot import (
    _practice_deadline_cards,
    _practice_suggestion_card,
    build_briefing,
)
from services.practice import daily
from services.practice.catalogue import load_catalogue
from storage.database import Base
from storage.models import (
    PracticeAttempt,
    PracticeChallenge,
    PracticeDay,
    PracticeSettings,
    User,
)

# --------------------------------------------------------------------------- the clock

#: Friday 18:40 IST: when the build in the examples is due.
DEADLINE = datetime(2026, 10, 17, 13, 10, tzinfo=UTC)
#: Saturday 21:00 IST: the weekly contest's card is showing (it appears at 20:00).
SATURDAY_EVENING = datetime(2026, 10, 17, 21, 0, tzinfo=IST)
#: Sunday 08:30 IST: the contest is running.
SUNDAY_RUNNING = datetime(2026, 10, 18, 8, 30, tzinfo=IST)
#: Sunday 10:00 IST: the contest ended at 09:30.
SUNDAY_LATE = datetime(2026, 10, 18, 10, 0, tzinfo=IST)
#: Thursday 11:30 IST: no contest card, an ordinary day.
THURSDAY = datetime(2026, 10, 15, 11, 30, tzinfo=IST)

TOPICS = ("array", "hash-table", "string", "two-pointers", "tree", "graph", "dynamic-programming")
LEVELS = ("easy", "medium", "hard")

CARD_KEYS = {"id", "kind", "title", "detail", "tone", "actions", "meta", "dismissible"}


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


# --------------------------------------------------------------------------- the database


@dataclass
class Db:
    engine: Any
    factory: async_sessionmaker[AsyncSession]
    path: Path


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    path = tmp_path / "practice_copilot.db"
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
            "practice_challenges",
            "practice_attempts",
            "practice_settings",
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(copilot, "AsyncSessionLocal", factory)
    monkeypatch.setattr(daily, "AsyncSessionLocal", factory)
    yield Db(engine=engine, factory=factory, path=path)
    await engine.dispose()


async def add_user(db: Db, name: str = "asha") -> str:
    async with db.factory() as session:
        user = User(email=f"{name}@example.com", hashed_password="not-a-real-hash")
        session.add(user)
        await session.commit()
        return str(user.id)


async def add_edition(
    db: Db,
    *,
    opens_at: datetime = DEADLINE - timedelta(days=3),
    title: str = "Idempotent webhook relay",
    hours: int = 24,
) -> PracticeChallenge:
    row = PracticeChallenge(
        slug=f"relay-sample-{opens_at.date().isoformat()}",
        brief_slug="relay-sample",
        title=title,
        track="backend",
        hours=hours,
        brief={"slug": "relay-sample"},
        opens_at=opens_at,
        closes_at=opens_at + timedelta(days=7),
    )
    async with db.factory() as session:
        session.add(row)
        await session.commit()
    return row


async def add_attempt(
    db: Db,
    user_id: str,
    edition: PracticeChallenge,
    *,
    deadline: datetime = DEADLINE,
    status: str = "started",
) -> uuid.UUID:
    row = PracticeAttempt(
        user_id=uuid.UUID(user_id),
        challenge_id=edition.id,
        status=status,
        started_at=deadline - timedelta(hours=edition.hours),
        deadline_at=deadline,
        proof_code=uuid.uuid4().hex[:10].upper(),
    )
    async with db.factory() as session:
        session.add(row)
        await session.commit()
        return row.id


async def add_day(db: Db, user_id: str, day: date, *, done: bool = True) -> None:
    async with db.factory() as session:
        session.add(
            PracticeDay(
                user_id=uuid.UUID(user_id),
                day=day,
                question_slug="fixture-question-01",
                topic="array",
                difficulty="easy",
                done_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=6)
                if done
                else None,
            )
        )
        await session.commit()


async def set_reminder(db: Db, user_id: str, *, on: bool) -> None:
    async with db.factory() as session:
        session.add(PracticeSettings(user_id=uuid.UUID(user_id), contest_reminder=on))
        await session.commit()


@pytest_asyncio.fixture
async def asha(db: Db) -> str:
    return await add_user(db, "asha")


@pytest_asyncio.fixture
async def edition(db: Db) -> PracticeChallenge:
    return await add_edition(db)


def ist_day(moment: datetime) -> date:
    return moment.astimezone(IST).date()


# =========================================================================== deadline cards


class TestDeadlineCards:
    async def test_a_started_build_due_in_five_hours_is_one_warning_card(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        attempt = await add_attempt(db, asha, edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=5))

        assert set(card) == CARD_KEYS
        assert card["id"] == f"practice_deadline:{attempt}"
        assert card["kind"] == "practice_deadline"
        assert card["tone"] == "warning"
        assert card["dismissible"] is True

    async def test_the_copy_names_the_build_the_time_and_the_hours(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=5))

        assert card["title"] == 'Submit "Idempotent webhook relay" by 18:40'
        assert card["detail"] == (
            "Your 24-hour build closes at 18:40 IST. Pushes after that are not read."
        )

    async def test_the_action_opens_the_submission_page_of_that_build(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=5))

        assert card["actions"] == [
            {
                "label": "Open submission",
                "href": f"/practice/challenges/{edition.slug}",
                "style": "primary",
            }
        ]
        assert card["meta"]["attempt_id"] == card["id"].split(":", 1)[1]

    async def test_a_forty_eight_hour_build_says_so(self, db: Db, asha: str) -> None:
        long_edition = await add_edition(db, hours=48)
        await add_attempt(db, asha, long_edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=2))

        assert "Your 48-hour build closes at 18:40 IST." in card["detail"]

    async def test_twelve_hours_out_is_inside_the_window(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        cards = await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=12))

        assert len(cards) == 1

    async def test_thirteen_hours_out_has_no_card(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        assert await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=13)) == []

    @pytest.mark.parametrize(
        "status", ["submitted", "queued", "judged", "rejected", "judge_failed", "missed"]
    )
    async def test_a_build_that_was_submitted_or_ended_has_no_card(
        self, db: Db, asha: str, edition: PracticeChallenge, status: str
    ) -> None:
        await add_attempt(db, asha, edition, status=status)

        assert await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=5)) == []

    async def test_the_write_up_hour_after_the_deadline_still_has_a_card(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        inside = await _practice_deadline_cards(asha, now=DEADLINE + timedelta(minutes=59))
        outside = await _practice_deadline_cards(asha, now=DEADLINE + timedelta(minutes=60))

        assert len(inside) == 1
        assert outside == []

    async def test_after_the_deadline_the_card_names_when_the_write_up_closes(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE + timedelta(minutes=10))

        assert card["title"] == 'Finish the write-up for "Idempotent webhook relay" by 19:40'
        assert card["detail"] == (
            "Your 24-hour build closed at 18:40 IST. Pushes after that are not read; "
            "the write-up closes at 19:40 IST."
        )
        assert "Submit" not in card["title"]
        assert card["tone"] == "warning"
        assert card["meta"]["deadline_at"] == DEADLINE.isoformat()

    async def test_the_last_minute_of_the_write_up_hour_still_says_the_write_up(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE + timedelta(minutes=59))

        assert card["title"].startswith("Finish the write-up for")

    async def test_at_the_deadline_itself_the_build_is_not_yet_over(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        (card,) = await _practice_deadline_cards(asha, now=DEADLINE)

        assert card["title"] == 'Submit "Idempotent webhook relay" by 18:40'

    async def test_another_persons_build_is_not_shown(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        bina = await add_user(db, "bina")
        await add_attempt(db, bina, edition)

        assert await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=5)) == []

    async def test_the_soonest_deadline_is_first(self, db: Db, asha: str) -> None:
        later = await add_edition(db, opens_at=DEADLINE - timedelta(days=2), title="Later build")
        sooner = await add_edition(db, opens_at=DEADLINE - timedelta(days=4), title="Sooner build")
        await add_attempt(db, asha, later, deadline=DEADLINE + timedelta(hours=3))
        await add_attempt(db, asha, sooner)

        cards = await _practice_deadline_cards(asha, now=DEADLINE - timedelta(hours=5))

        assert [card["title"].split('"')[1] for card in cards] == ["Sooner build", "Later build"]

    async def test_a_user_id_that_is_not_an_id_gives_no_cards(self, db: Db) -> None:
        assert await _practice_deadline_cards("nope", now=DEADLINE) == []

    async def test_a_failing_database_gives_no_cards_and_is_logged(
        self,
        asha: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def refuse() -> Any:
            raise RuntimeError("database is down")

        monkeypatch.setattr(copilot, "AsyncSessionLocal", refuse)

        with caplog.at_level("WARNING", logger=copilot.logger.name):
            cards = await _practice_deadline_cards(asha, now=DEADLINE)

        assert cards == []
        assert any("database is down" in record.getMessage() for record in caplog.records)


# =========================================================================== suggestion card


class TestSuggestionCardNeedsRecentPractice:
    async def test_someone_who_never_practised_gets_no_card_at_all(self, db: Db, asha: str) -> None:
        # Not the contest on Saturday evening, and not the daily question either.
        assert await _practice_suggestion_card(asha, now=SATURDAY_EVENING) is None
        assert await _practice_suggestion_card(asha, now=THURSDAY) is None

    async def test_someone_who_last_practised_fifteen_days_ago_gets_none(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=15))

        assert await _practice_suggestion_card(asha, now=SATURDAY_EVENING) is None

    async def test_fourteen_days_ago_still_counts(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=14))

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert card is not None

    async def test_a_day_that_was_not_marked_done_is_not_practice(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3), done=False)

        assert await _practice_suggestion_card(asha, now=SATURDAY_EVENING) is None


class TestContestCard:
    async def test_saturday_evening_after_recent_practice_is_the_contest(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3))

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert card is not None
        assert set(card) == CARD_KEYS
        assert card["kind"] == "practice_contest"
        assert card["id"] == "practice_contest:2026-10-18"
        assert card["tone"] == "action"
        assert card["dismissible"] is True
        assert card["title"] == "LeetCode weekly contest, Sunday 08:00 IST"
        assert card["actions"][0] == {
            "label": "Open contest",
            "href": CONTEST_URL,
            "style": "primary",
        }

    async def test_the_card_is_still_there_while_the_contest_runs(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(SUNDAY_RUNNING) - timedelta(days=3))

        card = await _practice_suggestion_card(asha, now=SUNDAY_RUNNING)

        assert card is not None
        assert card["kind"] == "practice_contest"
        assert card["id"] == "practice_contest:2026-10-18"

    async def test_it_is_gone_when_the_contest_ends_at_half_past_nine(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, date(2026, 10, 16))
        await add_day(db, asha, date(2026, 10, 18))  # the question of the day is done as well

        assert (
            await _practice_suggestion_card(asha, now=SUNDAY_RUNNING + timedelta(hours=1)) is None
        )

    async def test_it_does_not_show_before_eight_in_the_evening(self, db: Db, asha: str) -> None:
        before = datetime(2026, 10, 17, 19, 59, tzinfo=IST)
        await add_day(db, asha, ist_day(before) - timedelta(days=3))

        card = await _practice_suggestion_card(asha, now=before)

        assert card is not None
        assert card["kind"] == "practice_today"

    async def test_sunday_at_ten_has_no_contest_card(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(SUNDAY_LATE) - timedelta(days=3))

        card = await _practice_suggestion_card(asha, now=SUNDAY_LATE)

        assert card is not None
        assert card["kind"] == "practice_today"

    async def test_turning_the_reminder_off_falls_back_to_the_daily_question(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3))
        await set_reminder(db, asha, on=False)

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert card is not None
        assert card["kind"] == "practice_today"

    async def test_with_the_reminder_off_and_the_question_done_there_is_no_card(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING))
        await set_reminder(db, asha, on=False)

        assert await _practice_suggestion_card(asha, now=SATURDAY_EVENING) is None

    async def test_a_reminder_that_was_saved_on_still_shows_the_contest(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3))
        await set_reminder(db, asha, on=True)

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert card is not None
        assert card["kind"] == "practice_contest"

    async def test_one_persons_reminder_setting_is_not_anothers(self, db: Db, asha: str) -> None:
        bina = await add_user(db, "bina")
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3))
        await set_reminder(db, bina, on=False)

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert card is not None
        assert card["kind"] == "practice_contest"

    async def test_the_contest_wins_over_the_daily_question_so_there_is_only_one(
        self, db: Db, asha: str
    ) -> None:
        # Today's question is not done, so both cards qualify on Saturday evening: one comes back.
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3))

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert isinstance(card, dict)
        assert card["kind"] == "practice_contest"

    async def test_the_contest_card_does_not_depend_on_todays_question(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(SATURDAY_EVENING))  # done today

        card = await _practice_suggestion_card(asha, now=SATURDAY_EVENING)

        assert card is not None
        assert card["kind"] == "practice_contest"


class TestTodayCard:
    async def test_an_undone_question_is_suggested_with_its_reason(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(THURSDAY) - timedelta(days=2))
        shown = await daily.today(asha, now=THURSDAY)

        card = await _practice_suggestion_card(asha, now=THURSDAY)

        assert card is not None
        assert set(card) == CARD_KEYS
        assert card["kind"] == "practice_today"
        assert card["id"] == "practice_today:2026-10-15"
        assert card["tone"] == "action"
        assert card["dismissible"] is True
        assert card["title"] == f"Solve today's question: {shown['question']['title']}"
        assert card["actions"][0] == {
            "label": "Open on LeetCode",
            "href": shown["question"]["url"],
            "style": "primary",
        }

    async def test_the_detail_is_difficulty_then_topic_then_the_reason(
        self, db: Db, asha: str
    ) -> None:
        await add_day(db, asha, ist_day(THURSDAY) - timedelta(days=2))
        shown = await daily.today(asha, now=THURSDAY)
        label, _, why = shown["reason"].partition(" — ")

        card = await _practice_suggestion_card(asha, now=THURSDAY)

        assert card is not None
        difficulty = shown["question"]["difficulty"].title()
        assert card["detail"] == f"{difficulty} · {label} · {why}"

    async def test_the_card_can_be_reached_to_mark_it_done(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(THURSDAY) - timedelta(days=2))

        card = await _practice_suggestion_card(asha, now=THURSDAY)

        assert card is not None
        assert card["actions"][1] == {
            "label": "Open Practice",
            "href": "/practice",
            "style": "ghost",
        }

    async def test_a_question_marked_done_today_has_no_card(self, db: Db, asha: str) -> None:
        await add_day(db, asha, ist_day(THURSDAY))

        assert await _practice_suggestion_card(asha, now=THURSDAY) is None

    async def test_the_id_changes_with_the_ist_day(self, db: Db, asha: str) -> None:
        await add_day(db, asha, date(2026, 10, 13))
        before_midnight = datetime(2026, 10, 15, 23, 59, tzinfo=IST)
        after_midnight = datetime(2026, 10, 16, 0, 1, tzinfo=IST)

        first = await _practice_suggestion_card(asha, now=before_midnight)
        second = await _practice_suggestion_card(asha, now=after_midnight)

        assert first is not None and second is not None
        assert first["id"] == "practice_today:2026-10-15"
        assert second["id"] == "practice_today:2026-10-16"

    async def test_an_empty_catalogue_has_no_card(
        self, db: Db, asha: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await add_day(db, asha, ist_day(THURSDAY) - timedelta(days=2))
        monkeypatch.setattr(daily, "load_catalogue", lambda: ())

        assert await _practice_suggestion_card(asha, now=THURSDAY) is None

    async def test_a_user_id_that_is_not_an_id_gives_no_card(self, db: Db) -> None:
        assert await _practice_suggestion_card("nope", now=THURSDAY) is None

    async def test_a_failing_database_gives_no_card_and_is_logged(
        self,
        asha: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def refuse() -> Any:
            raise RuntimeError("database is down")

        monkeypatch.setattr(daily, "AsyncSessionLocal", refuse)
        monkeypatch.setattr(copilot, "AsyncSessionLocal", refuse)

        with caplog.at_level("WARNING", logger=copilot.logger.name):
            card = await _practice_suggestion_card(asha, now=THURSDAY)

        assert card is None
        assert any("database is down" in record.getMessage() for record in caplog.records)


# =========================================================================== the briefing


def _card(card_id: str, kind: str, tone: str = "action") -> dict[str, Any]:
    return copilot._card(card_id=card_id, kind=kind, title=kind, detail="d", tone=tone)


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Every producer the briefing pulls from, replaced with a controllable stub."""
    state: dict[str, Any] = {
        "next_action": {
            "recommendation": "Apply to your saved jobs",
            "detail": "You have 6 saved job(s).",
            "action": "apply_saved",
            "href": "/applications",
            "context": {"total_applications": 8, "saved": 6, "applied": 1, "interviews": 0},
        },
        "scheduled": [],
        "deadlines": [],
        "applications": [],
        "interview": None,
        "suggestion": None,
        "dismissed": set(),
    }

    async def fake_next_action(*, user_id: str) -> dict[str, Any]:
        return dict(state["next_action"])

    async def fake_scheduled(user_id: str) -> list[dict[str, Any]]:
        return list(state["scheduled"])

    async def fake_deadlines(user_id: str) -> list[dict[str, Any]]:
        return list(state["deadlines"])

    async def fake_applications(user_id: str) -> list[dict[str, Any]]:
        return list(state["applications"])

    async def fake_interview(user_id: str, applied: int) -> dict[str, Any] | None:
        card: dict[str, Any] | None = state["interview"]
        return card

    async def fake_suggestion(user_id: str) -> dict[str, Any] | None:
        card: dict[str, Any] | None = state["suggestion"]
        return card

    async def fake_dismissals(*, user_id: str) -> set[str]:
        return set(state["dismissed"])

    monkeypatch.setattr(copilot, "recommend_next_action", fake_next_action)
    monkeypatch.setattr(copilot, "_scheduled_interview_cards", fake_scheduled)
    monkeypatch.setattr(copilot, "_practice_deadline_cards", fake_deadlines)
    monkeypatch.setattr(copilot, "_application_cards", fake_applications)
    monkeypatch.setattr(copilot, "_interview_card", fake_interview)
    monkeypatch.setattr(copilot, "_practice_suggestion_card", fake_suggestion)
    monkeypatch.setattr(copilot, "active_dismissals", fake_dismissals)
    return state


def kinds(briefing: dict[str, Any]) -> list[str]:
    return [card["kind"] for card in briefing["cards"]]


class TestBriefing:
    async def test_a_deadline_comes_right_after_the_calendar_and_before_the_priority(
        self, sources: dict[str, Any]
    ) -> None:
        sources["scheduled"] = [_card("scheduled_interview:1", "scheduled_interview", "warning")]
        sources["deadlines"] = [_card("practice_deadline:1", "practice_deadline", "warning")]
        sources["applications"] = [_card("stale_application:1", "stale_application", "warning")]

        briefing = await build_briefing(user_id="u1")

        assert kinds(briefing) == [
            "scheduled_interview",
            "practice_deadline",
            "priority",
            "stale_application",
        ]

    async def test_a_deadline_leads_when_nothing_else_is_dated(
        self, sources: dict[str, Any]
    ) -> None:
        sources["deadlines"] = [_card("practice_deadline:1", "practice_deadline", "warning")]

        briefing = await build_briefing(user_id="u1")

        assert briefing["cards"][0]["kind"] == "practice_deadline"
        assert briefing["cards"][1]["kind"] == "priority"

    async def test_the_suggestion_is_the_last_card(self, sources: dict[str, Any]) -> None:
        sources["applications"] = [_card("saved_backlog", "saved_backlog")]
        sources["interview"] = _card("interview_cold_start", "interview_momentum")
        sources["suggestion"] = _card("practice_today:2026-10-15", "practice_today")

        briefing = await build_briefing(user_id="u1")

        assert kinds(briefing) == [
            "priority",
            "saved_backlog",
            "interview_momentum",
            "practice_today",
        ]

    async def test_with_nothing_to_suggest_no_practice_card_appears(
        self, sources: dict[str, Any]
    ) -> None:
        briefing = await build_briefing(user_id="u1")

        assert kinds(briefing) == ["priority"]

    @pytest.mark.parametrize(
        ("kind", "card_id"),
        [
            ("practice_today", "practice_today:2026-10-15"),
            ("practice_contest", "practice_contest:2026-10-18"),
        ],
    )
    async def test_a_dismissed_suggestion_is_filtered_and_counted(
        self, sources: dict[str, Any], kind: str, card_id: str
    ) -> None:
        sources["suggestion"] = _card(card_id, kind)
        sources["dismissed"] = {card_id}

        briefing = await build_briefing(user_id="u1")

        assert kind not in kinds(briefing)
        assert briefing["hidden_count"] == 1

    async def test_a_dismissed_deadline_is_filtered_too(self, sources: dict[str, Any]) -> None:
        sources["deadlines"] = [_card("practice_deadline:1", "practice_deadline", "warning")]
        sources["dismissed"] = {"practice_deadline:1"}

        briefing = await build_briefing(user_id="u1")

        assert "practice_deadline" not in kinds(briefing)
        assert briefing["hidden_count"] == 1

    async def test_a_deadline_counts_as_something_to_chase(self, sources: dict[str, Any]) -> None:
        sources["deadlines"] = [_card("practice_deadline:1", "practice_deadline", "warning")]

        briefing = await build_briefing(user_id="u1")

        assert "chasing today" in briefing["headline"]

    async def test_at_most_one_of_the_two_suggestions_is_ever_in_the_briefing(
        self, db: Db, asha: str, sources: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The real producer, on Saturday evening, when the contest and the daily question both
        # qualify: whatever it returns is a single card, never both.
        await add_day(db, asha, ist_day(SATURDAY_EVENING) - timedelta(days=3))
        real = _practice_suggestion_card

        async def at_saturday_evening(user_id: str) -> dict[str, Any] | None:
            return await real(user_id, now=SATURDAY_EVENING)

        monkeypatch.setattr(copilot, "_practice_suggestion_card", at_saturday_evening)

        briefing = await build_briefing(user_id=asha)

        practice = [k for k in kinds(briefing) if k in ("practice_contest", "practice_today")]
        assert practice == ["practice_contest"]

    async def test_a_broken_practice_producer_does_not_blank_the_briefing(
        self, sources: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse() -> Any:
            raise RuntimeError("database is down")

        # The real producers, with a database that refuses them.
        monkeypatch.setattr(copilot, "_practice_deadline_cards", _practice_deadline_cards)
        monkeypatch.setattr(copilot, "_practice_suggestion_card", _practice_suggestion_card)
        monkeypatch.setattr(copilot, "AsyncSessionLocal", refuse)
        monkeypatch.setattr(daily, "AsyncSessionLocal", refuse)

        briefing = await build_briefing(user_id=str(uuid.uuid4()))

        assert kinds(briefing) == ["priority"]
