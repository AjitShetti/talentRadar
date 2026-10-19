"""
tests/test_event_copilot_cards.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The three day-plan cards events add to the Career Copilot briefing
(``services/copilot.py::_event_cards``): ``event_today``, ``event_soon`` and
``event_met_prompt``, spec section 9.4.

Everything runs on in-memory SQLite with ``PRAGMA foreign_keys=ON`` and never touches the
shared Postgres test database. The copilot and the dismissal store both open their sessions
through ``AsyncSessionLocal``, which is pointed at that SQLite engine. The clock is passed in
(``now``) except for the one test that goes through ``build_briefing``, which pins
``copilot._now``. Every event, organiser and person is fictional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services import agent_memory, copilot
from services.agent_memory import dismiss_card
from services.copilot import _card, _event_cards, build_briefing
from storage.database import Base
from storage.models import Event, EventContact, EventSave, User

TABLES = (
    "users",
    "companies",
    "events",
    "event_companies",
    "event_saves",
    "event_contacts",
    "agent_memory",
)

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # Thu 15 Oct, 11:30 IST
TONIGHT = NOW + timedelta(hours=7)  # 13:00 UTC = 18:30 IST, the same IST day

Factory = async_sessionmaker[AsyncSession]


def hours(count: float, *, base: datetime = NOW) -> datetime:
    return base + timedelta(hours=count)


def days(count: float, *, base: datetime = NOW) -> datetime:
    return base + timedelta(days=count)


@pytest_asyncio.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Factory]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all, tables=[Base.metadata.tables[name] for name in TABLES]
        )
    made: Factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(copilot, "AsyncSessionLocal", made)
    monkeypatch.setattr(agent_memory, "AsyncSessionLocal", made)
    yield made
    await engine.dispose()


@pytest.fixture
def add(factory: Factory) -> Callable[..., Any]:
    async def _add(*rows: Any) -> None:
        async with factory() as session:
            session.add_all(rows)
            await session.commit()

    return _add


async def new_user(add: Callable[..., Any]) -> User:
    person = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x"
    )
    await add(person)
    return person


@pytest_asyncio.fixture
async def me(add: Callable[..., Any]) -> User:
    return await new_user(add)


def make_event(starts: datetime, ends: datetime | None = None, **fields: Any) -> Event:
    """A listed, public, in-person event that lasts two hours unless told otherwise."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source": "luma",
        "external_id": f"evt-{uuid.uuid4().hex[:12]}",
        "url": "https://luma.com/rust-night",
        "title": "Rust Night Bengaluru",
        "starts_at": starts,
        "ends_at": ends if ends is not None else starts + timedelta(hours=2),
        "venue_name": "Hall Four",
        "area": "Indiranagar",
        "geo_precision": "exact",
        "is_online": False,
        "organiser_name": "Rust Bengaluru",
        "format": "meetup",
        "topics": ["languages"],
        "listed": True,
        "reasons": ["Runs in Bengaluru"],
    }
    values.update(fields)
    return Event(**values)


def saved(person: User, event: Event, status: str = "going") -> EventSave:
    return EventSave(user_id=person.id, event_id=event.id, status=status)


async def cards_for(person: User, *, now: datetime = NOW) -> list[dict[str, Any]]:
    return await _event_cards(str(person.id), now=now)


def kinds(cards: list[dict[str, Any]]) -> list[str]:
    return [card["kind"] for card in cards]


# ═════════════════════════════════════════════════════════════════════════════
# event_today
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("status", ["going", "saved"])
async def test_event_today_card(add: Callable[..., Any], me: User, status: str) -> None:
    event = make_event(TONIGHT)
    await add(event, saved(me, event, status))

    cards = await cards_for(me)

    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "event_today"
    assert card["id"] == f"event_today:{event.id}"
    assert card["title"] == "Tonight: Rust Night Bengaluru"
    assert card["detail"] == (
        "18:30 at Indiranagar, hosted by Rust Bengaluru. Add the people you meet afterwards."
    )
    assert card["tone"] == "primary"
    assert card["dismissible"] is True
    assert card["actions"] == [
        {"label": "Open event", "href": f"/events?event={event.id}", "style": "primary"}
    ]
    assert card["meta"] == {
        "event_id": str(event.id),
        "starts_at": "2026-10-15T13:00:00+00:00",
        "status": status,
    }


@pytest.mark.parametrize(
    ("starts", "word"),
    [
        (hours(-1), "Today"),  # 10:30 IST, already running
        (hours(4.5), "Today"),  # 16:00 IST
        (hours(5) + timedelta(minutes=29), "Today"),  # 16:59 IST
        (hours(5.5), "Tonight"),  # 17:00 IST
        (TONIGHT, "Tonight"),  # 18:30 IST
    ],
    ids=["10:30", "16:00", "16:59", "17:00", "18:30"],
)
async def test_the_card_says_today_before_5pm_ist_and_tonight_from_then(
    add: Callable[..., Any], me: User, starts: datetime, word: str
) -> None:
    event = make_event(starts)
    await add(event, saved(me, event))

    (card,) = await cards_for(me)

    assert card["title"] == f"{word}: Rust Night Bengaluru"


async def test_the_briefing_words_a_morning_event_as_today(
    add: Callable[..., Any], me: User, other_sources_quiet: None
) -> None:
    """Through ``build_briefing``, with the clock pinned on ``copilot._now``."""
    morning = make_event(NOW + timedelta(hours=1), title="Breakfast Meetup")  # 12:30 IST
    evening = make_event(TONIGHT, title="Rust Night")
    await add(morning, evening, saved(me, morning), saved(me, evening))

    briefing = await build_briefing(user_id=str(me.id))

    assert [c["title"] for c in briefing["cards"]] == [
        "Today: Breakfast Meetup",
        "Tonight: Rust Night",
    ]


async def test_event_today_detail_names_only_what_the_event_states(
    add: Callable[..., Any], me: User
) -> None:
    online = make_event(
        TONIGHT, is_online=True, venue_name=None, area=None, organiser_name=None, title="Rust Talk"
    )
    unplaced = make_event(
        TONIGHT + timedelta(hours=1), area=None, venue_name="Hall Four", organiser_name=None
    )
    await add(online, unplaced, saved(me, online), saved(me, unplaced))

    details = {card["title"]: card["detail"] for card in await cards_for(me)}

    assert details["Tonight: Rust Talk"] == "18:30 online. Add the people you meet afterwards."
    assert details["Tonight: Rust Night Bengaluru"] == (
        "19:30 at Hall Four. Add the people you meet afterwards."
    )


async def test_ended_event_not_today(add: Callable[..., Any], me: User) -> None:
    over = make_event(hours(-3), hours(-1))  # 08:30-10:30 IST, over by 11:30
    mine = make_event(hours(-3), hours(-1), title="Saved and missed")
    await add(over, mine, saved(me, over, "going"), saved(me, mine, "saved"))

    cards = await cards_for(me)

    assert "event_today" not in kinds(cards)
    # A going event that has just ended asks who was met; a merely saved one asks nothing.
    assert [(c["kind"], c["meta"]["event_id"]) for c in cards] == [
        ("event_met_prompt", str(over.id))
    ]


async def test_an_event_that_is_running_is_still_today(add: Callable[..., Any], me: User) -> None:
    running = make_event(hours(-1), hours(1))
    await add(running, saved(me, running))

    assert kinds(await cards_for(me)) == ["event_today"]


async def test_an_event_that_began_on_an_earlier_day_and_is_still_running_is_today(
    add: Callable[..., Any], me: User
) -> None:
    """A conference or an overnight event began on another IST date and has not ended."""
    conference = make_event(days(-1), days(1), title="Rust Conf")  # Wed 11:30 IST to Fri 11:30
    overnight = make_event(
        hours(-13.5), hours(2.5), title="Hack Night"
    )  # Wed 22:00 IST to Thu 14:00 IST, the same Thursday it is now
    over = make_event(days(-2), hours(-1), title="Already over")  # began Tue, ended 10:30 today
    await add(
        conference,
        overnight,
        over,
        saved(me, conference),
        saved(me, overnight, "saved"),
        saved(me, over),
    )

    cards = await cards_for(me)

    today = {c["meta"]["event_id"]: c for c in cards if c["kind"] == "event_today"}
    assert set(today) == {str(conference.id), str(overnight.id)}
    # Neither starts today, and the second began in the evening: neither is "Tonight".
    assert today[str(conference.id)]["title"] == "Today: Rust Conf"
    assert today[str(overnight.id)]["title"] == "Today: Hack Night"
    assert today[str(overnight.id)]["id"] == f"event_today:{overnight.id}"


async def test_a_cancelled_event_that_is_running_across_days_is_still_the_warning(
    add: Callable[..., Any], me: User
) -> None:
    conference = make_event(days(-1), days(1), is_cancelled=True)
    await add(conference, saved(me, conference))

    (card,) = await cards_for(me)

    assert card["kind"] == "event_today"
    assert card["tone"] == "warning"


async def test_today_is_the_ist_day_not_the_utc_day(add: Callable[..., Any], me: User) -> None:
    """00:30 IST on Friday is the same UTC date as Thursday 23:50 IST, and still tomorrow."""
    friday_early = datetime(2026, 10, 15, 19, 0, tzinfo=UTC)  # Fri 16 Oct 00:30 IST
    event = make_event(friday_early)
    await add(event, saved(me, event))

    thursday_late = datetime(2026, 10, 15, 18, 20, tzinfo=UTC)  # Thu 23:50 IST
    friday_just_after_midnight = datetime(2026, 10, 15, 18, 40, tzinfo=UTC)  # Fri 00:10 IST

    late = await cards_for(me, now=thursday_late)
    after = await cards_for(me, now=friday_just_after_midnight)

    assert kinds(late) == ["event_soon"]
    assert late[0]["title"].startswith("Friday: ")
    assert kinds(after) == ["event_today"]


async def test_cancelled_saved_event_warns(add: Callable[..., Any], me: User) -> None:
    event = make_event(TONIGHT, is_cancelled=True)
    await add(event, saved(me, event, "saved"))

    cards = await cards_for(me)

    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "event_today"
    assert card["id"] == f"event_today:{event.id}"
    assert card["tone"] == "warning"
    assert card["title"] == "Rust Night Bengaluru was cancelled by the organiser"
    assert card["title"].endswith("was cancelled by the organiser")
    assert card["actions"][0]["href"] == f"/events?event={event.id}"
    assert card["meta"]["status"] == "saved"


async def test_a_cancelled_event_is_never_soon_or_a_met_prompt(
    add: Callable[..., Any], me: User
) -> None:
    ahead = make_event(days(3), is_cancelled=True)
    behind = make_event(days(-1), is_cancelled=True)
    await add(ahead, behind, saved(me, ahead), saved(me, behind))

    assert await cards_for(me) == []


# ═════════════════════════════════════════════════════════════════════════════
# event_soon
# ═════════════════════════════════════════════════════════════════════════════


async def test_only_soonest_event_soon(add: Callable[..., Any], me: User) -> None:
    later = make_event(days(4), title="Later meetup")
    sooner = make_event(days(2), title="Sooner meetup")  # Saturday 17 Oct
    await add(later, sooner, saved(me, later, "going"), saved(me, sooner, "saved"))

    cards = await cards_for(me)

    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "event_soon"
    assert card["id"] == f"event_soon:{sooner.id}"
    assert card["title"] == "Saturday: Sooner meetup"
    assert card["detail"] == "You saved it — decide whether you're going."
    assert card["tone"] == "action"
    assert card["actions"] == [
        {"label": "Open event", "href": f"/events?event={sooner.id}", "style": "primary"}
    ]
    assert card["meta"] == {
        "event_id": str(sooner.id),
        "starts_at": "2026-10-17T06:00:00+00:00",
        "status": "saved",
    }


async def test_a_going_event_soon_says_so(add: Callable[..., Any], me: User) -> None:
    event = make_event(days(2))
    await add(event, saved(me, event, "going"))

    (card,) = await cards_for(me)

    assert card["detail"] == "You're going. Add it to your calendar."
    assert card["meta"]["status"] == "going"


async def test_event_soon_looks_seven_days_ahead_and_not_past(
    add: Callable[..., Any], me: User
) -> None:
    far = make_event(days(7) + timedelta(hours=1))
    await add(far, saved(me, far))

    assert await cards_for(me) == []

    edge = make_event(days(7) - timedelta(hours=1))
    await add(edge, saved(me, edge))

    assert [c["meta"]["event_id"] for c in await cards_for(me)] == [str(edge.id)]


async def test_an_event_later_today_is_today_not_soon(add: Callable[..., Any], me: User) -> None:
    tonight = make_event(TONIGHT)
    friday = make_event(days(1))
    await add(tonight, friday, saved(me, tonight), saved(me, friday))

    assert [(c["kind"], c["meta"]["event_id"]) for c in await cards_for(me)] == [
        ("event_today", str(tonight.id)),
        ("event_soon", str(friday.id)),
    ]


# ═════════════════════════════════════════════════════════════════════════════
# event_met_prompt
# ═════════════════════════════════════════════════════════════════════════════


async def test_met_prompt_after_going(add: Callable[..., Any], factory: Factory, me: User) -> None:
    event = make_event(days(-1) - timedelta(hours=2), days(-1))  # ended 24 h ago
    await add(event, saved(me, event, "going"))

    (card,) = await cards_for(me)

    assert card["kind"] == "event_met_prompt"
    assert card["id"] == f"event_met:{event.id}"
    assert card["title"] == "Who did you meet at Rust Night Bengaluru?"
    assert card["detail"] == "Note them now; each one can become an Outreach thread."
    assert card["tone"] == "action"
    assert card["actions"] == [
        {
            "label": "Add who you met",
            "href": f"/events?event={event.id}#people",
            "style": "primary",
        }
    ]
    assert card["meta"]["event_id"] == str(event.id)

    # Once a person is noted there is nothing left to ask.
    await add(
        EventContact(
            id=uuid.uuid4(),
            user_id=me.id,
            event_id=event.id,
            name="Asha Verma",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    assert await cards_for(me) == []


async def test_someone_elses_contact_does_not_answer_the_prompt(
    add: Callable[..., Any], me: User
) -> None:
    event = make_event(days(-1) - timedelta(hours=2), days(-1))
    stranger = await new_user(add)
    await add(event, saved(me, event, "going"))
    await add(
        EventContact(
            id=uuid.uuid4(),
            user_id=stranger.id,
            event_id=event.id,
            name="Ravi Menon",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    assert kinds(await cards_for(me)) == ["event_met_prompt"]


async def test_met_prompt_is_only_for_going_and_only_for_two_days(
    add: Callable[..., Any], me: User
) -> None:
    saved_only = make_event(days(-1) - timedelta(hours=2), days(-1), title="Only saved")
    too_old = make_event(days(-3) - timedelta(hours=2), days(-3), title="Too old")
    another_days_ago = make_event(days(-2) + timedelta(hours=2), days(-2) + timedelta(hours=4))
    await add(
        saved_only,
        too_old,
        another_days_ago,
        saved(me, saved_only, "saved"),
        saved(me, too_old, "going"),
        saved(me, another_days_ago, "going"),
    )

    assert [c["meta"]["event_id"] for c in await cards_for(me)] == [str(another_days_ago.id)]


async def test_a_long_event_asks_when_it_ends_not_when_it_began(
    add: Callable[..., Any], me: User
) -> None:
    conference = make_event(days(-4), days(-1))  # began four days ago, over yesterday
    await add(conference, saved(me, conference, "going"))

    assert kinds(await cards_for(me)) == ["event_met_prompt"]


# ═════════════════════════════════════════════════════════════════════════════
# privacy and failure
# ═════════════════════════════════════════════════════════════════════════════


async def test_cards_are_the_callers_own(add: Callable[..., Any], me: User) -> None:
    stranger = await new_user(add)
    event = make_event(TONIGHT)
    private = make_event(
        TONIGHT, source="user", owner_user_id=stranger.id, title="Their private dinner"
    )
    await add(event, private, saved(stranger, event), saved(stranger, private))

    assert await cards_for(me) == []
    assert kinds(await cards_for(stranger)) == ["event_today", "event_today"]


async def test_a_private_event_of_mine_gets_a_card(add: Callable[..., Any], me: User) -> None:
    private = make_event(
        TONIGHT, source="user", owner_user_id=me.id, title="Founders dinner", organiser_name=None
    )
    await add(private, saved(me, private))

    (card,) = await cards_for(me)

    assert card["title"] == "Tonight: Founders dinner"


async def test_a_user_id_that_is_not_a_user_gets_no_cards() -> None:
    assert await _event_cards("not-a-user-id", now=NOW) == []


async def test_a_failing_query_yields_no_cards_and_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, me: User
) -> None:
    class _Broken:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OperationalError("select", {}, Exception("no such table"))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(copilot, "AsyncSessionLocal", lambda: _Broken())

    with caplog.at_level("WARNING", logger="services.copilot"):
        cards = await cards_for(me)

    assert cards == []
    assert "Event cards unavailable" in caplog.text


# ═════════════════════════════════════════════════════════════════════════════
# the briefing
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def other_sources_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every producer except the events one, and the clock, replaced."""

    async def next_action(*, user_id: str) -> dict[str, Any]:
        return {"recommendation": "", "context": {}}

    async def none_yet(user_id: str) -> list[Any]:
        return []

    async def no_interview(user_id: str, applied: int) -> None:
        return None

    monkeypatch.setattr(copilot, "recommend_next_action", next_action)
    monkeypatch.setattr(copilot, "_scheduled_interview_cards", none_yet)
    monkeypatch.setattr(copilot, "_application_cards", none_yet)
    monkeypatch.setattr(copilot, "_interview_card", no_interview)
    monkeypatch.setattr(copilot, "_now", lambda: NOW)


async def test_event_cards_dismissible(
    add: Callable[..., Any], me: User, other_sources_quiet: None
) -> None:
    event = make_event(TONIGHT)
    await add(event, saved(me, event))

    before = await build_briefing(user_id=str(me.id))
    assert [c["id"] for c in before["cards"]] == [f"event_today:{event.id}"]
    assert before["hidden_count"] == 0

    await dismiss_card(user_id=str(me.id), card_id=f"event_today:{event.id}")

    after = await build_briefing(user_id=str(me.id))
    assert after["cards"] == []
    assert after["hidden_count"] == 1


async def test_a_broken_event_producer_cannot_break_the_briefing(
    me: User, other_sources_quiet: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_session() -> Any:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(copilot, "AsyncSessionLocal", _no_session)

    briefing = await build_briefing(user_id=str(me.id))

    assert briefing["cards"] == []
    assert briefing["headline"]


async def test_event_cards_sit_after_interviews_and_before_the_priority_card(
    monkeypatch: pytest.MonkeyPatch, other_sources_quiet: None
) -> None:
    async def interviews(user_id: str) -> list[dict[str, Any]]:
        return [
            _card(
                card_id="scheduled_interview:1", kind="scheduled_interview", title="i", detail="d"
            )
        ]

    async def events(user_id: str, *, now: datetime) -> list[dict[str, Any]]:
        return [_card(card_id="event_today:1", kind="event_today", title="e", detail="d")]

    async def next_action(*, user_id: str) -> dict[str, Any]:
        return {"recommendation": "Apply", "action": "apply", "context": {}}

    monkeypatch.setattr(copilot, "_scheduled_interview_cards", interviews)
    monkeypatch.setattr(copilot, "_event_cards", events)
    monkeypatch.setattr(copilot, "recommend_next_action", next_action)

    async def nobody_dismissed(*, user_id: str) -> set[str]:
        return set()

    monkeypatch.setattr(copilot, "active_dismissals", nobody_dismissed)

    briefing = await build_briefing(user_id="u1")

    assert [c["kind"] for c in briefing["cards"]] == [
        "scheduled_interview",
        "event_today",
        "priority",
    ]
