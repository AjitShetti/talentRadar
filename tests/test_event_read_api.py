"""
tests/test_event_read_api.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The events read API: ``GET /api/v1/events``, ``/events/{id}`` and ``/events/{id}/ics``
(``api/routers/events.py`` over ``services/events/read.py``).

Everything runs on in-memory SQLite with ``PRAGMA foreign_keys=ON`` and never touches the
shared Postgres test database. The session dependency is overridden with one from that
engine; the clock is the router's ``_now()``, patched to a fixed Thursday so no test depends
on the day it runs. Each user is a seeded ``users`` row with a token minted for it, because
the saves and contacts the tests read have a foreign key to ``users``. Every person,
organiser and company is fictional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.auth import create_access_token
from api.dependencies import get_session
from services.events import read
from services.source_health import SourceHealth, SourceHealthService
from storage.database import Base
from storage.models import Company, Event, EventCompany, EventContact, EventSave, User

TABLES = ("users", "companies", "events", "event_companies", "event_saves", "event_contacts")

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # Thu 15 Oct, 11:30 IST
LIST = "/api/v1/events"

Factory = async_sessionmaker[AsyncSession]


def days(count: float, *, base: datetime = NOW) -> datetime:
    return base + timedelta(days=count)


def make_event(starts: datetime, **fields: Any) -> Event:
    """A listed, public, in-person event that is not over. A test overrides what it is about."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source": "luma",
        "external_id": f"evt-{uuid.uuid4().hex[:12]}",
        "url": "https://luma.com/rust-night",
        "title": "Rust Night Bengaluru",
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=2),
        "venue_name": "Hall Four",
        "area": "Indiranagar",
        "geo_precision": "exact",
        "lat": 12.9784,
        "lng": 77.6408,
        "is_online": False,
        "organiser_name": "Rust Bengaluru",
        "format": "meetup",
        "topics": ["languages"],
        "listed": True,
        "reasons": ["Runs in Bengaluru", "Programming language"],
    }
    values.update(fields)
    return Event(**values)


def titles(body: dict[str, Any]) -> list[str]:
    return [event["title"] for event in body["events"]]


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[Factory]:
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
    yield async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    await engine.dispose()


@pytest.fixture
def add(factory: Factory) -> Callable[..., Any]:
    async def _add(*rows: Any) -> None:
        async with factory() as session:
            session.add_all(rows)
            await session.commit()

    return _add


async def new_user(add: Callable[..., Any]) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x")
    await add(user)
    return user


def auth(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id), "email": user.email, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def now_is(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    def _set(moment: datetime) -> None:
        monkeypatch.setattr("api.routers.events._now", lambda: moment)

    _set(NOW)
    return _set


@pytest.fixture(autouse=True)
def quiet_outside(monkeypatch: pytest.MonkeyPatch) -> None:
    """No cache and no Eventbrite token: a read must not need either."""
    monkeypatch.setattr(
        SourceHealthService, "get", AsyncMock(side_effect=lambda name: SourceHealth(name=name))
    )
    monkeypatch.setattr(read, "get_settings", lambda: SimpleNamespace(eventbrite_token=None))


@pytest_asyncio.fixture
async def me(add: Callable[..., Any]) -> User:
    return await new_user(add)


@pytest_asyncio.fixture
async def client(
    factory: Factory, me: User, now_is: Callable[[datetime], None]
) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=auth(me)
        ) as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def anonymous(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def listing(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(LIST, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ═════════════════════════════════════════════════════════════════════════════
# what the list holds
# ═════════════════════════════════════════════════════════════════════════════


async def test_list_defaults_to_in_person_next_30_days(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(
        make_event(days(2), title="Soon, in person"),
        make_event(days(2), title="Soon, online", is_online=True, venue_name=None),
        make_event(days(40), title="Too far"),
        make_event(days(-1), title="Yesterday"),
    )

    body = await listing(client)

    assert titles(body) == ["Soon, in person"]
    assert body["total"] == 1


async def test_default_window_is_thirty_days_counting_today(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    # Thu 15 Oct is day one, so Fri 13 Nov is day thirty and Sat 14 Nov is outside.
    await add(
        make_event(days(29), title="Day thirty"),
        make_event(days(30), title="Day thirty-one"),
    )

    assert titles(await listing(client)) == ["Day thirty"]


async def test_a_lone_from_opens_a_thirty_day_window_too(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(
        make_event(datetime(2026, 11, 30, 6, 30, tzinfo=UTC), title="Last day, 12:00 IST"),
        make_event(datetime(2026, 11, 30, 19, 0, tzinfo=UTC), title="Next day, 00:30 IST"),
        make_event(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), title="First day"),
    )

    body = await listing(client, **{"from": "2026-11-01"})

    assert titles(body) == ["First day", "Last day, 12:00 IST"]


async def test_a_running_event_is_listed_and_an_unstated_end_means_two_hours(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(
        make_event(NOW - timedelta(hours=1), title="Running", ends_at=NOW + timedelta(hours=1)),
        make_event(NOW - timedelta(hours=1), title="No end, still inside two hours", ends_at=None),
        make_event(NOW - timedelta(hours=3), title="No end, over", ends_at=None),
        make_event(NOW - timedelta(hours=5), title="Ended", ends_at=NOW - timedelta(hours=3)),
        make_event(
            NOW - timedelta(hours=1),
            title="End before start, so no end stated",
            ends_at=NOW - timedelta(hours=2),
        ),
        make_event(
            NOW - timedelta(hours=3),
            title="End before start, over",
            ends_at=NOW - timedelta(hours=4),
        ),
    )

    body = await listing(client)

    assert sorted(titles(body)) == [
        "End before start, so no end stated",
        "No end, still inside two hours",
        "Running",
    ]


async def test_a_multi_day_event_stays_listed_until_it_ends(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(
        make_event(days(-1), title="Hackathon", ends_at=days(1), format="hackathon"),
        make_event(days(-3), title="Hackathon, over", ends_at=days(-1), format="hackathon"),
    )

    assert titles(await listing(client)) == ["Hackathon"]
    assert titles(await listing(client, when="today")) == ["Hackathon"]
    assert titles(await listing(client, **{"from": "2026-10-22"})) == []


async def test_list_is_ordered_by_start(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(
        make_event(days(5), title="Third"),
        make_event(days(1), title="First"),
        make_event(days(3), title="Second"),
    )

    assert titles(await listing(client)) == ["First", "Second", "Third"]


async def test_mode_any_includes_online(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(
        make_event(days(1), title="In person"),
        make_event(days(2), title="Online", is_online=True, venue_name=None),
    )

    assert titles(await listing(client, mode="any")) == ["In person", "Online"]
    assert titles(await listing(client, mode="online")) == ["Online"]
    assert titles(await listing(client, mode="in_person")) == ["In person"]


async def test_when_today_uses_ist(
    client: AsyncClient, add: Callable[..., Any], now_is: Callable[[datetime], None]
) -> None:
    # 00:30 IST on Friday 16 Oct. The event at 04:00Z is 09:30 IST the same day; the one at
    # 13:00Z on the 15th is Thursday evening and long over.
    now_is(datetime(2026, 10, 15, 19, 0, tzinfo=UTC))
    await add(
        make_event(datetime(2026, 10, 16, 4, 0, tzinfo=UTC), title="Friday morning"),
        make_event(datetime(2026, 10, 15, 13, 0, tzinfo=UTC), title="Thursday evening"),
        make_event(datetime(2026, 10, 16, 19, 0, tzinfo=UTC), title="Saturday 00:30 IST"),
    )

    assert titles(await listing(client, when="today")) == ["Friday morning"]


async def test_when_overrides_from_and_to(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(make_event(days(2), title="This week"), make_event(days(20), title="Later"))

    body = await listing(client, when="week", **{"from": "2026-11-01", "to": "2026-11-30"})

    assert titles(body) == ["This week"]


async def test_from_and_to_are_ist_dates_and_to_is_inclusive(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(
        make_event(
            datetime(2026, 10, 19, 18, 0, tzinfo=UTC),
            ends_at=datetime(2026, 10, 19, 18, 20, tzinfo=UTC),
            title="Mon 23:30 IST",
        ),
        make_event(datetime(2026, 10, 19, 19, 0, tzinfo=UTC), title="Tue 00:30 IST"),
        make_event(datetime(2026, 10, 20, 17, 0, tzinfo=UTC), title="Tue 22:30 IST"),
        make_event(datetime(2026, 10, 20, 19, 0, tzinfo=UTC), title="Wed 00:30 IST"),
    )

    body = await listing(client, **{"from": "2026-10-20", "to": "2026-10-20"})

    assert titles(body) == ["Tue 00:30 IST", "Tue 22:30 IST"]


async def test_week_and_month_windows(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(
        make_event(days(6), title="Day 6"),
        make_event(days(8), title="Day 8"),
        make_event(days(29), title="Day 29"),
        make_event(days(31), title="Day 31"),
    )

    assert titles(await listing(client, when="week")) == ["Day 6"]
    assert titles(await listing(client, when="month")) == ["Day 6", "Day 8", "Day 29"]


async def test_weekend_window(
    client: AsyncClient, add: Callable[..., Any], now_is: Callable[[datetime], None]
) -> None:
    # Saturday is 17 Oct: 00:00 IST is the 16th 18:30Z, and Monday 00:00 IST the 18th 18:30Z.
    await add(
        make_event(
            datetime(2026, 10, 16, 17, 0, tzinfo=UTC),
            ends_at=datetime(2026, 10, 16, 18, 0, tzinfo=UTC),
            title="Friday late",
        ),
        make_event(datetime(2026, 10, 17, 4, 0, tzinfo=UTC), title="Saturday"),
        make_event(datetime(2026, 10, 18, 10, 0, tzinfo=UTC), title="Sunday"),
        make_event(datetime(2026, 10, 19, 4, 0, tzinfo=UTC), title="Monday"),
    )

    assert titles(await listing(client, when="weekend")) == ["Saturday", "Sunday"]


async def test_duplicates_not_listed(client: AsyncClient, add: Callable[..., Any]) -> None:
    primary = make_event(days(2), title="Swift Bengaluru x Lumen")
    duplicate = make_event(
        days(2),
        title="Swift Bengaluru Meetup at Lumen",
        source="eventbrite",
        external_id="123456789",
        url="https://www.eventbrite.com/e/swift-meetup-123456789",
        duplicate_of=primary.id,
    )
    await add(primary)
    await add(duplicate)

    body = await listing(client)
    detail = (await client.get(f"{LIST}/{primary.id}")).json()

    assert titles(body) == ["Swift Bengaluru x Lumen"]
    assert detail["also_on"] == [
        {"source": "eventbrite", "url": "https://www.eventbrite.com/e/swift-meetup-123456789"}
    ]


async def test_unlisted_not_listed(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(
        make_event(days(1), title="Listed"),
        make_event(days(1), title="Wellness retreat", listed=False),
    )

    assert titles(await listing(client)) == ["Listed"]
    assert (await listing(client))["total"] == 1


async def test_company_filter(client: AsyncClient, add: Callable[..., Any]) -> None:
    lumen = Company(id=uuid.uuid4(), name="Lumen Works", is_tech=True)
    quill = Company(id=uuid.uuid4(), name="Quillpad", is_tech=True)
    await add(lumen, quill)
    linked = make_event(days(2), title="At Lumen")
    other = make_event(days(2), title="At Quillpad")
    unlinked = make_event(days(2), title="Nobody")
    await add(linked, other, unlinked)
    await add(
        EventCompany(
            event_id=linked.id, company_id=lumen.id, role="organiser", matched_on="organiser_name"
        ),
        EventCompany(event_id=other.id, company_id=quill.id, role="host", matched_on="host_name"),
    )

    body = await listing(client, company_id=str(lumen.id), limit=3)

    assert titles(body) == ["At Lumen"]
    assert body["events"][0]["companies"] == [{"id": str(lumen.id), "name": "Lumen Works"}]


async def test_q_matches_title_or_organiser_case_insensitively(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(
        make_event(days(1), title="Kubernetes Night", organiser_name="Cloud Native Bengaluru"),
        make_event(days(2), title="Demo evening", organiser_name="Team KUBERNETES Guild"),
        make_event(days(3), title="Rust Night", organiser_name="Rust Bengaluru"),
    )

    assert titles(await listing(client, q="kubernetes")) == ["Kubernetes Night", "Demo evening"]
    assert titles(await listing(client, q="NIGHT")) == ["Kubernetes Night", "Rust Night"]


async def test_q_wildcards_are_literal(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(make_event(days(1), title="100% Rust"), make_event(days(2), title="Rust Night"))

    assert titles(await listing(client, q="%")) == ["100% Rust"]
    assert titles(await listing(client, q="_")) == []


async def test_topic_format_and_hide_paid(client: AsyncClient, add: Callable[..., Any]) -> None:
    await add(
        make_event(days(1), title="AI talk", topics=["ai", "data"], format="talk", is_free=True),
        make_event(days(2), title="Web workshop", topics=["web"], format="workshop", is_free=False),
        make_event(days(3), title="Web3 mixer", topics=["web3"], format="mixer", is_free=None),
    )

    assert titles(await listing(client, topic="data")) == ["AI talk"]
    assert titles(await listing(client, topic="web")) == ["Web workshop"]
    assert titles(await listing(client, format="mixer")) == ["Web3 mixer"]
    assert titles(await listing(client, hide_paid="true")) == ["AI talk", "Web3 mixer"]


@pytest.mark.parametrize(
    "params",
    [
        {"topic": "astrology"},
        {"format": "seance"},
        {"limit": 101},
        {"limit": 0},
        {"offset": -1},
        {"offset": 10_001},
        {"when": "fortnight"},
        {"mode": "everywhere"},
        {"saved": "maybe"},
        {"company_id": "not-a-uuid"},
        {"from": "2026-13-01"},
        {"from": "1850-01-01"},
        {"to": "9999-12-31"},
        {"from": "2026-10-20", "to": "2026-10-19"},
        {"q": "x" * 101},
    ],
    ids=lambda params: "-".join(f"{k}={str(v)[:12]}" for k, v in params.items()),
)
async def test_bad_query_is_422(client: AsyncClient, params: dict[str, Any]) -> None:
    assert (await client.get(LIST, params=params)).status_code == 422


async def test_pagination_total_counts_every_match(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(*[make_event(days(1 + n / 10), title=f"Night {n}") for n in range(5)])

    first = await listing(client, limit=2)
    second = await listing(client, limit=2, offset=2)
    last = await listing(client, limit=2, offset=4)

    assert first["total"] == second["total"] == last["total"] == 5
    assert titles(first) == ["Night 0", "Night 1"]
    assert titles(second) == ["Night 2", "Night 3"]
    assert titles(last) == ["Night 4"]


async def test_the_largest_allowed_offset_is_an_empty_page_not_an_error(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(make_event(days(1)))

    body = await listing(client, offset=10_000)

    assert body["events"] == []
    assert body["total"] == 1


async def test_the_service_bounds_an_offset_the_router_would_have_refused(
    factory: Factory, add: Callable[..., Any], me: User
) -> None:
    await add(make_event(days(1)))

    async with factory() as session:
        body = await read.list_events(session, str(me.id), read.EventQuery(offset=2**70), now=NOW)

    assert body["events"] == []
    assert body["total"] == 1


async def test_cancelled_event_is_listed_with_the_flag(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(make_event(days(1), title="Called off", is_cancelled=True))

    body = await listing(client)

    assert [event["is_cancelled"] for event in body["events"]] == [True]


async def test_card_has_exactly_the_documented_keys(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(make_event(days(1)))

    card = (await listing(client))["events"][0]

    assert set(card) == {
        "id", "title", "starts_at", "ends_at", "is_online", "venue_name", "area", "lat", "lng",
        "geo_precision", "is_free", "organiser_name", "format", "topics", "source", "url",
        "companies", "my_status", "is_cancelled",
    }  # fmt: skip
    assert card["starts_at"].endswith("Z") or card["starts_at"].endswith("+00:00")
    assert card["my_status"] is None
    assert card["companies"] == []


# ═════════════════════════════════════════════════════════════════════════════
# the user's side
# ═════════════════════════════════════════════════════════════════════════════


async def test_saved_and_going_filters_and_my_status(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    plain = make_event(days(1), title="Plain")
    saved = make_event(days(2), title="Saved")
    going = make_event(days(3), title="Going")
    await add(plain, saved, going)
    other = await new_user(add)
    await add(
        EventSave(user_id=me.id, event_id=saved.id, status="saved"),
        EventSave(user_id=me.id, event_id=going.id, status="going"),
        EventSave(user_id=other.id, event_id=plain.id, status="going"),
    )

    everything = await listing(client)
    assert {e["title"]: e["my_status"] for e in everything["events"]} == {
        "Plain": None,
        "Saved": "saved",
        "Going": "going",
    }
    assert titles(await listing(client, saved="saved")) == ["Saved", "Going"]
    assert titles(await listing(client, saved="going")) == ["Going"]


async def test_saved_and_going_views_list_everything_the_caller_saved(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    stranger = await new_user(add)
    primary = make_event(days(1), title="Primary")
    unlisted = make_event(days(2), title="Saved, unlisted", listed=False)
    duplicate = make_event(days(3), title="Going, a duplicate", duplicate_of=primary.id)
    theirs = make_event(
        days(4), title="Someone else's dinner", source="user", owner_user_id=stranger.id
    )
    mine = make_event(days(5), title="My dinner", source="user", owner_user_id=me.id)
    unsaved = make_event(days(6), title="Unlisted, not saved", listed=False)
    await add(primary, unlisted, theirs, mine, unsaved)
    await add(duplicate)
    await add(
        EventSave(user_id=me.id, event_id=unlisted.id, status="saved"),
        EventSave(user_id=me.id, event_id=duplicate.id, status="going"),
        EventSave(user_id=me.id, event_id=theirs.id, status="going"),
        EventSave(user_id=me.id, event_id=mine.id, status="going"),
    )

    assert titles(await listing(client)) == ["Primary", "My dinner"]
    assert titles(await listing(client, saved="saved")) == [
        "Saved, unlisted",
        "Going, a duplicate",
        "My dinner",
    ]
    assert titles(await listing(client, saved="going")) == ["Going, a duplicate", "My dinner"]
    assert (await listing(client))["counts"]["going"] == 2


async def test_counts(client: AsyncClient, add: Callable[..., Any], me: User) -> None:
    other = await new_user(add)
    going = make_event(days(2), title="Going, this week")
    later = make_event(days(10), title="Later, saved")
    online = make_event(days(3), title="Online, this week", is_online=True, venue_name=None)
    unlisted = make_event(days(2), title="Unlisted", listed=False)
    duplicate = make_event(days(2), title="Duplicate", duplicate_of=going.id)
    cancelled = make_event(days(3), title="Cancelled", is_cancelled=True)
    over = make_event(days(-1), title="Over, but I went")
    await add(going, later, online, unlisted, over)
    await add(duplicate, cancelled)
    await add(
        EventSave(user_id=me.id, event_id=going.id, status="going"),
        EventSave(user_id=me.id, event_id=later.id, status="saved"),
        EventSave(user_id=me.id, event_id=over.id, status="going"),
        EventSave(user_id=other.id, event_id=later.id, status="going"),
    )

    counts = (await listing(client))["counts"]

    assert counts == {"upcoming": 3, "this_week": 2, "going": 1}


async def test_counts_do_not_follow_the_filters(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    await add(make_event(days(2), title="One"), make_event(days(3), title="Two"))

    narrow = await listing(client, q="One", when="today")

    assert narrow["total"] == 0
    assert narrow["counts"]["upcoming"] == 2


async def test_a_private_event_is_visible_only_to_its_owner(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    stranger = await new_user(add)
    mine = make_event(
        days(1),
        title="My private dinner",
        source="user",
        listed=False,
        owner_user_id=me.id,
        url=None,
    )
    theirs = make_event(
        days(1),
        title="Their private dinner",
        source="user",
        listed=False,
        owner_user_id=stranger.id,
    )
    await add(mine, theirs)

    assert titles(await listing(client)) == ["My private dinner"]
    assert (await client.get(f"{LIST}/{mine.id}")).status_code == 200
    assert (await client.get(f"{LIST}/{theirs.id}")).status_code == 404
    assert (await client.get(f"{LIST}/{theirs.id}/ics")).status_code == 404
    seen_by_stranger = await client.get(LIST, headers=auth(stranger))
    assert titles(seen_by_stranger.json()) == ["Their private dinner"]


# ═════════════════════════════════════════════════════════════════════════════
# the sources strip
# ═════════════════════════════════════════════════════════════════════════════


async def test_sources_are_luma_gdg_eventbrite_and_eventbrite_is_off_without_a_token(
    client: AsyncClient,
) -> None:
    sources = (await listing(client))["sources"]

    assert sources == [
        {"source": "luma", "last_read_at": None, "status": "unknown"},
        {"source": "gdg", "last_read_at": None, "status": "unknown"},
        {"source": "eventbrite", "last_read_at": None, "status": "off"},
    ]


async def test_last_read_is_the_last_success_as_an_iso_utc_string(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    luma_read = datetime(2026, 10, 15, 0, 1, 2, tzinfo=UTC)
    asked: list[str] = []

    async def _health(name: str) -> SourceHealth:
        asked.append(name)
        if name == "events:luma":
            return SourceHealth(
                name=name, attempts=2, successes=2, last_success_at=luma_read.timestamp()
            )
        if name == "events:gdg":
            return SourceHealth(name=name, attempts=3, consecutive_failures=3, last_success_at=None)
        return SourceHealth(name=name, attempts=1, successes=1, last_success_at=5e20)

    monkeypatch.setattr(SourceHealthService, "get", AsyncMock(side_effect=_health))
    monkeypatch.setattr(read, "get_settings", lambda: SimpleNamespace(eventbrite_token="t"))

    sources = (await listing(client))["sources"]

    assert asked == ["events:luma", "events:gdg", "events:eventbrite"]
    assert sources[0] == {
        "source": "luma",
        "last_read_at": "2026-10-15T00:01:02Z",
        "status": "healthy",
    }
    assert sources[1] == {"source": "gdg", "last_read_at": None, "status": "failing"}
    # An impossible epoch is no read time, not an error.
    assert sources[2] == {"source": "eventbrite", "last_read_at": None, "status": "healthy"}


async def test_a_dead_cache_does_not_break_the_list(
    client: AsyncClient, add: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await add(make_event(days(1), title="Still here"))
    monkeypatch.setattr(
        SourceHealthService, "get", AsyncMock(side_effect=RuntimeError("cache is down"))
    )

    body = await listing(client)

    assert titles(body) == ["Still here"]
    assert [s["status"] for s in body["sources"]] == ["unknown", "unknown", "off"]


async def test_the_sources_are_read_before_the_session_runs_its_first_query(
    factory: Factory, add: Callable[..., Any], me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add(make_event(days(1)))
    seen: list[str] = []

    async def _sources() -> list[dict[str, Any]]:
        seen.append("cache")
        return []

    monkeypatch.setattr(read, "_sources", _sources)
    engine = factory.kw["bind"]

    @sa_event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _queried(*_args: Any) -> None:
        seen.append("query")

    try:
        async with factory() as session:
            await read.list_events(session, str(me.id), read.EventQuery(), now=NOW)
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", _queried)

    assert seen[0] == "cache"
    assert seen.count("cache") == 1
    assert "query" in seen


# ═════════════════════════════════════════════════════════════════════════════
# detail
# ═════════════════════════════════════════════════════════════════════════════


async def test_detail_unknown_404(client: AsyncClient) -> None:
    assert (await client.get(f"{LIST}/{uuid.uuid4()}")).status_code == 404
    assert (await client.get(f"{LIST}/{uuid.uuid4()}/ics")).status_code == 404


async def test_detail_with_a_malformed_id_is_422(client: AsyncClient) -> None:
    assert (await client.get(f"{LIST}/not-a-uuid")).status_code == 422


@pytest.mark.parametrize("suffix", ["", "/ics"])
async def test_another_users_private_event_is_indistinguishable_from_an_unknown_id(
    client: AsyncClient, add: Callable[..., Any], suffix: str
) -> None:
    stranger = await new_user(add)
    theirs = make_event(
        days(1), title="Their private dinner", source="user", owner_user_id=stranger.id
    )
    await add(theirs)

    private = await client.get(f"{LIST}/{theirs.id}{suffix}")
    unknown = await client.get(f"{LIST}/{uuid.uuid4()}{suffix}")

    assert private.status_code == unknown.status_code == 404
    assert private.json() == unknown.json()
    assert private.headers["content-type"] == unknown.headers["content-type"]
    assert "Their private dinner" not in private.text


async def test_detail_carries_the_card_and_the_rest(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    lumen = Company(id=uuid.uuid4(), name="Lumen Works", is_tech=True)
    quill = Company(id=uuid.uuid4(), name="Quillpad", is_tech=True)
    await add(lumen, quill)
    event = make_event(
        days(2),
        title="Rust Night at Lumen",
        snippet="x" * 500,
        hosts=["Asha Verma"],
        reasons=["Runs in Bengaluru", "Programming language"],
    )
    await add(event)
    await add(
        EventCompany(event_id=event.id, company_id=quill.id, role="host", matched_on="host_name"),
        EventCompany(
            event_id=event.id, company_id=lumen.id, role="organiser", matched_on="organiser_name"
        ),
        EventSave(user_id=me.id, event_id=event.id, status="going", note="Bring slides"),
    )

    detail = (await client.get(f"{LIST}/{event.id}")).json()

    assert detail["id"] == str(event.id)
    assert detail["title"] == "Rust Night at Lumen"
    assert detail["my_status"] == "going"
    assert detail["my"] == {"status": "going", "note": "Bring slides"}
    assert detail["snippet"] == "x" * 200
    assert detail["reasons"] == ["Runs in Bengaluru", "Programming language"]
    assert detail["hosts"] == ["Asha Verma"]
    assert detail["also_on"] == []
    assert detail["people"] == []
    assert detail["companies"] == [
        {"id": str(lumen.id), "name": "Lumen Works", "role": "organiser"},
        {"id": str(quill.id), "name": "Quillpad", "role": "host"},
    ]


async def test_detail_of_an_event_nobody_saved_has_no_my(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = make_event(days(2), snippet=None, reasons=None, topics=None, hosts=None)
    await add(event)

    detail = (await client.get(f"{LIST}/{event.id}")).json()

    assert detail["my"] is None
    assert detail["my_status"] is None
    assert detail["snippet"] is None
    assert detail["reasons"] == []
    assert detail["topics"] == []
    assert detail["hosts"] == []


async def test_detail_also_on_leaves_out_private_and_linkless_duplicates(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    stranger = await new_user(add)
    primary = make_event(days(2))
    await add(primary)
    await add(
        make_event(
            days(2),
            source="eventbrite",
            external_id="1",
            url="https://www.eventbrite.com/e/a-1",
            duplicate_of=primary.id,
        ),
        make_event(days(2), source="gdg", external_id="no-link", url=None, duplicate_of=primary.id),
        make_event(
            days(2),
            source="user",
            external_id="private",
            url="https://example.test/private",
            duplicate_of=primary.id,
            owner_user_id=stranger.id,
        ),
    )

    detail = (await client.get(f"{LIST}/{primary.id}")).json()

    assert detail["also_on"] == [
        {"source": "eventbrite", "url": "https://www.eventbrite.com/e/a-1"}
    ]


async def test_detail_people_are_the_callers_and_get_a_search_link_only_without_a_profile(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    stranger = await new_user(add)
    event = make_event(days(2))
    await add(event)
    await add(
        EventContact(
            user_id=me.id,
            event_id=event.id,
            name="Asha Verma",
            title="Engineer",
            company_name="Lumen Works",
            note="Likes Rust",
            created_at=NOW,
        ),
        EventContact(
            user_id=me.id,
            event_id=event.id,
            name="Dev Rao",
            linkedin_url="https://www.linkedin.com/in/dev-rao-example",
            created_at=NOW + timedelta(minutes=1),
        ),
        EventContact(user_id=stranger.id, event_id=event.id, name="Someone Else"),
    )

    people = (await client.get(f"{LIST}/{event.id}")).json()["people"]

    assert [p["name"] for p in people] == ["Asha Verma", "Dev Rao"]
    assert people[0]["linkedin_search_url"].startswith(
        "https://www.linkedin.com/search/results/people/"
    )
    assert "Asha%20Verma" in people[0]["linkedin_search_url"]
    assert "Lumen%20Works" in people[0]["linkedin_search_url"]
    assert people[0]["note"] == "Likes Rust"
    assert people[1]["linkedin_url"] == "https://www.linkedin.com/in/dev-rao-example"
    assert people[1]["linkedin_search_url"] is None
    assert set(people[0]) == {
        "id", "event_id", "name", "title", "company_name", "company_id", "linkedin_url", "x_url",
        "github_url", "email", "note", "created_at", "linkedin_search_url",
    }  # fmt: skip


async def test_detail_of_an_unlisted_public_event_is_still_readable(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = make_event(days(2), listed=False)
    await add(event)

    assert (await client.get(f"{LIST}/{event.id}")).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# .ics
# ═════════════════════════════════════════════════════════════════════════════


async def test_ics_download(client: AsyncClient, add: Callable[..., Any]) -> None:
    starts = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
    event = make_event(
        starts, title="Rust Night, Bengaluru: Edition 5", snippet="Talks, then pizza."
    )
    await add(event)

    response = await client.get(f"{LIST}/{event.id}/ics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["content-disposition"] == (
        'attachment; filename="rust-night-bengaluru-edition-5.ics"'
    )
    body = response.text
    assert "BEGIN:VEVENT" in body
    assert "DTSTART:20261017T130000Z" in body
    assert "DTEND:20261017T150000Z" in body
    assert "DTSTAMP:20261015T060000Z" in body
    assert "SUMMARY:Rust Night\\, Bengaluru: Edition 5" in body
    assert "LOCATION:Hall Four" in body
    assert "URL:https://luma.com/rust-night" in body
    assert "STATUS:CANCELLED" not in body
    assert body.count("BEGIN:VEVENT") == 1


async def test_ics_title_cannot_close_the_event_and_open_another(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    hostile = "Rust Night\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:Planted"
    event = make_event(days(1), title=hostile, snippet=hostile, venue_name=hostile)
    await add(event)

    response = await client.get(f"{LIST}/{event.id}/ics")

    assert response.status_code == 200
    lines = response.text.split("\r\n")
    assert lines.count("BEGIN:VEVENT") == 1
    assert lines.count("END:VEVENT") == 1
    assert not any(line.startswith("SUMMARY:Planted") for line in lines)


async def test_ics_end_and_location_when_the_source_stated_neither(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    online = make_event(
        days(2), is_online=True, venue_name=None, address=None, ends_at=None, url=None
    )
    placed = make_event(days(3), venue_name="Hall Four", address="12 Example Road, Indiranagar")
    await add(online, placed)

    online_body = (await client.get(f"{LIST}/{online.id}/ics")).text
    placed_body = (await client.get(f"{LIST}/{placed.id}/ics")).text

    ends = (days(2) + timedelta(hours=2)).strftime("DTEND:%Y%m%dT%H%M%SZ")
    assert ends in online_body
    assert "LOCATION:Online" in online_body
    assert "URL:" not in online_body
    assert "LOCATION:Hall Four\\, 12 Example Road\\, Indiranagar" in placed_body


async def test_ics_marks_a_cancelled_event(client: AsyncClient, add: Callable[..., Any]) -> None:
    event = make_event(days(2), is_cancelled=True)
    await add(event)

    assert "STATUS:CANCELLED" in (await client.get(f"{LIST}/{event.id}/ics")).text


async def test_ics_filename_is_ascii_even_for_a_title_with_no_ascii(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = make_event(days(2), title="ಬೆಂಗಳೂರು ರಸ್ಟ್")
    await add(event)

    disposition = (await client.get(f"{LIST}/{event.id}/ics")).headers["content-disposition"]

    assert disposition == 'attachment; filename="event.ics"'


# ═════════════════════════════════════════════════════════════════════════════
# access, ordering, and the read path's promises
# ═════════════════════════════════════════════════════════════════════════════


async def test_unauthenticated_401(anonymous: AsyncClient, add: Callable[..., Any]) -> None:
    event = make_event(days(1))
    await add(event)

    assert (await anonymous.get(LIST)).status_code == 401
    assert (await anonymous.get(f"{LIST}/{event.id}")).status_code == 401
    assert (await anonymous.get(f"{LIST}/{event.id}/ics")).status_code == 401


async def test_a_token_whose_subject_is_not_a_user_id_is_401(client: AsyncClient) -> None:
    token = create_access_token(data={"sub": "not-a-uuid", "role": "user"})

    response = await client.get(LIST, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_reads_make_no_outside_request(
    client: AsyncClient, add: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = make_event(days(1), url="https://luma.com/rust-night")
    await add(event)

    async def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a read tried to reach the network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)

    assert (await client.get(LIST)).status_code == 200
    assert (await client.get(f"{LIST}/{event.id}")).status_code == 200
    assert (await client.get(f"{LIST}/{event.id}/ics")).status_code == 200


def test_the_router_is_authenticated_and_mounted_under_v1() -> None:
    from api.main import app
    from api.routers import events

    paths = app.openapi()["paths"]
    assert {
        "/api/v1/events",
        "/api/v1/events/{event_id}",
        "/api/v1/events/{event_id}/ics",
    } <= set(paths)
    assert events.router.prefix == "/events"
    assert [d.dependency.__name__ for d in events.router.dependencies] == ["get_current_user"]


async def test_get_session_commits_on_success_and_rolls_back_on_error() -> None:
    from unittest.mock import MagicMock, patch

    from api import dependencies

    async def run(*, fail: bool) -> MagicMock:
        session = MagicMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        opened = MagicMock()
        opened.return_value.__aenter__ = AsyncMock(return_value=session)
        opened.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch.object(dependencies, "AsyncSessionLocal", opened):
            generator = dependencies.get_session()
            assert await anext(generator) is session
            if fail:
                with pytest.raises(RuntimeError):
                    await generator.athrow(RuntimeError("boom"))
            else:
                with pytest.raises(StopAsyncIteration):
                    await anext(generator)
        return session

    ok = await run(fail=False)
    ok.commit.assert_awaited_once()
    ok.rollback.assert_not_awaited()

    failed = await run(fail=True)
    failed.commit.assert_not_awaited()
    failed.rollback.assert_awaited_once()


def test_topic_filter_compiles_to_an_array_containment_on_postgres() -> None:
    from sqlalchemy import select

    statement = select(Event.id).where(read.topic_clause("postgresql", "ai"))

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "events.topics @>" in compiled


async def test_topic_filter_runs_on_a_text_array_column_in_postgres() -> None:
    """The migration makes ``events.topics`` ``text[]``; ``text[] @> varchar[]`` is a 500.

    SQLite cannot show this, so it runs on a temporary table of the same shape in the shared
    Postgres test database: nothing is left behind, and the real tables are not touched.
    """
    from sqlalchemy import select, text

    from config.settings import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("CREATE TEMP TABLE events (id uuid, topics text[])"))
            await connection.execute(
                text(
                    "INSERT INTO events VALUES"
                    " (CAST(:one AS uuid), ARRAY['ai','web']),"
                    " (CAST(:two AS uuid), ARRAY['web']),"
                    " (CAST(:three AS uuid), NULL)"
                ),
                {"one": str(uuid.UUID(int=1)), "two": str(uuid.UUID(int=2)),
                 "three": str(uuid.UUID(int=3))},
            )

            found = await connection.execute(
                select(Event.id).where(read.topic_clause("postgresql", "ai"))
            )

            assert [row[0] for row in found] == [uuid.UUID(int=1)]
    except OSError:
        pytest.skip("no Postgres to run against")
    finally:
        await engine.dispose()
