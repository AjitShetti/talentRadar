"""
tests/test_event_user_api.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
What a signed-in user does with events: save and unsave, add by link, add by hand, and
note the people they met (``api/routers/events.py`` over ``services/events/user.py``).

Everything runs on in-memory SQLite with ``PRAGMA foreign_keys=ON`` and never touches the
shared Postgres test database. The clock is the router's ``_now()``, patched to a fixed
Thursday; each user is a seeded ``users`` row with a token minted for it. The import cap
counter, the source-health counters and the Eventbrite settings are replaced, so no test needs
Redis or a token. A link import is tested at the service level where a test must prove that
*no* request leaves the process (patching ``httpx.AsyncClient.send`` would also break the test
client). Every person, organiser and company is fictional.
"""

from __future__ import annotations

import copy
import gzip
import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.auth import create_access_token
from api.dependencies import get_session
from domain.event_time import ist_date
from services.cache_backend import CacheBackend
from services.events import eventbrite, gdg, user
from services.source_health import SourceHealthService
from storage.database import Base
from storage.models import Company, Event, EventContact, EventSave, User

TABLES = ("users", "companies", "events", "event_companies", "event_saves", "event_contacts")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # Thu 15 Oct, 11:30 IST
LIST = "/api/v1/events"
TOKEN = "TESTTOKEN0123456789ABCDEF"
BACKSLASH = chr(92)

EVENTBRITE_LINK = (
    "https://www.eventbrite.com/e/async-rust-in-production-bengaluru-meetup-tickets-712345678901"
)
GDG_SLUG = "google-gdg-cloud-bengaluru-presents-road-to-devfest-opensource-edition"
GDG_LINK = f"https://gdg.community.dev/events/details/{GDG_SLUG}/"

PERSON = {
    "name": "Asha Verma",
    "title": "Staff Engineer",
    "company_name": "Lumen Works",
    "linkedin_url": "https://www.linkedin.com/in/asha-verma-example",
    "note": "Talked about async Rust in production",
}

Factory = async_sessionmaker[AsyncSession]


def days(count: float, *, base: datetime = NOW) -> datetime:
    return base + timedelta(days=count)


def make_event(starts: datetime, **fields: Any) -> Event:
    """A listed, public, in-person event. A test overrides what it is about."""
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


def started_event(**fields: Any) -> Event:
    """An event that began an hour before NOW and is still running."""
    return make_event(NOW - timedelta(hours=1), title="Swift Bengaluru × Okta", **fields)


def titles(body: dict[str, Any]) -> list[str]:
    return [event["title"] for event in body["events"]]


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://www.eventbriteapi.com/v3/events/712345678901/")
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(status))


def eventbrite_payload() -> dict[str, Any]:
    data = json.loads((FIXTURES / "eventbrite_event.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def gdg_page() -> bytes:
    path = FIXTURES / f"gdg_event_{GDG_SLUG}.html.gz"
    return gzip.decompress(path.read_bytes())


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
    person = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x"
    )
    await add(person)
    return person


def auth(person: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(person.id), "email": person.email, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


async def rows_of(factory: Factory, model: Any, *where: Any) -> list[Any]:
    async with factory() as session:
        return list((await session.execute(select(model).where(*where))).scalars())


def manual_row(owner: User, number: int) -> Event:
    """A private event the user added by hand (what ``POST /manual`` stores), for seeding."""
    event_id = uuid.uuid4()
    return make_event(
        days(1 + number / 1000),
        id=event_id,
        source="user",
        external_id=str(event_id),
        url=None,
        title=f"Mine {number}",
        owner_user_id=owner.id,
        geo_precision="none",
    )


def met_row(owner: User, event_id: uuid.UUID, number: int) -> EventContact:
    """A person the user met, for seeding."""
    return EventContact(
        id=uuid.uuid4(),
        user_id=owner.id,
        event_id=event_id,
        name=f"Person {number}",
        created_at=NOW,
        updated_at=NOW,
    )


def an_online_gdg_page(description: str) -> bytes:
    """A GDG event page for an online event; ``description`` is the only place a signal can hide."""
    block = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": "Cloud Study Jam",
        "startDate": "2026-10-22T13:30:00+00:00",
        "endDate": "2026-10-22T15:30:00+00:00",
        "description": description,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "organizer": {"@type": "Organization", "name": "Google Developer Groups"},
        "location": {"@type": "VirtualLocation", "url": "https://meet.example.test/jam"},
    }
    page = f'<html><head><script type="application/ld+json">{json.dumps(block)}</script></head>'
    return (page + "<body></body></html>").encode()


@pytest.fixture(autouse=True)
def quiet_outside(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """No cache, no health counters, an Eventbrite token, and a counting import cap."""
    seen: dict[str, Any] = {"counts": {}, "ttls": {}}

    async def _incr(key: str, ttl_seconds: int) -> int:
        seen["counts"][key] = seen["counts"].get(key, 0) + 1
        seen["ttls"][key] = ttl_seconds
        return int(seen["counts"][key])

    seen["record"] = AsyncMock()
    monkeypatch.setattr(CacheBackend, "incr", _incr)
    monkeypatch.setattr(SourceHealthService, "is_available", AsyncMock(return_value=True))
    monkeypatch.setattr(SourceHealthService, "record_attempt", seen["record"])
    monkeypatch.setattr(user, "get_settings", lambda: SimpleNamespace(eventbrite_token=TOKEN))
    return seen


@pytest.fixture
def now_is(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    def _set(moment: datetime) -> None:
        monkeypatch.setattr("api.routers.events._now", lambda: moment)

    _set(NOW)
    return _set


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
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

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


async def run_import(
    factory: Factory, who: User, link: str, *, now: datetime = NOW
) -> dict[str, Any]:
    async with factory() as session:
        result = await user.import_link(session, str(who.id), link, now=now)
        await session.commit()
    return result


@pytest.fixture
def no_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any request from this process fails the test: a service-level proof of "never fetched"."""

    async def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an import tried to reach the network")

    monkeypatch.setattr(httpx.AsyncClient, "send", _refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)


# ═════════════════════════════════════════════════════════════════════════════
# save, going, unsave
# ═════════════════════════════════════════════════════════════════════════════


async def test_save_then_going_then_unsave(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    event = make_event(days(2))
    await add(event)

    saved = await client.put(f"{LIST}/{event.id}/save", json={"status": "saved", "note": "go?"})
    assert saved.status_code == 200
    assert saved.json() == {"status": "saved", "note": "go?"}
    assert (await client.get(f"{LIST}/{event.id}")).json()["my"] == {
        "status": "saved",
        "note": "go?",
    }

    # Going, with no note sent: the note stays.
    going = await client.put(f"{LIST}/{event.id}/save", json={"status": "going"})
    assert going.json() == {"status": "going", "note": "go?"}
    assert titles(await listing(client, saved="going")) == ["Rust Night Bengaluru"]
    assert (await listing(client))["events"][0]["my_status"] == "going"
    assert len((await listing(client, saved="saved"))["events"]) == 1

    # An empty note clears it.
    cleared = await client.put(f"{LIST}/{event.id}/save", json={"status": "going", "note": ""})
    assert cleared.json() == {"status": "going", "note": None}

    assert (await client.delete(f"{LIST}/{event.id}/save")).status_code == 204
    assert (await client.get(f"{LIST}/{event.id}")).json()["my"] is None
    assert titles(await listing(client, saved="saved")) == []
    assert (await client.delete(f"{LIST}/{event.id}/save")).status_code == 404


async def test_a_save_is_private_to_its_user(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    other = await new_user(add)
    event = make_event(days(2))
    await add(event)

    await client.put(f"{LIST}/{event.id}/save", json={"status": "going"})

    seen_by_other = await client.get(f"{LIST}/{event.id}", headers=auth(other))
    assert seen_by_other.json()["my"] is None
    assert (await client.delete(f"{LIST}/{event.id}/save", headers=auth(other))).status_code == 404
    assert (await client.get(f"{LIST}/{event.id}")).json()["my"]["status"] == "going"


async def test_saving_an_unknown_or_foreign_private_event_is_the_same_404(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    stranger = await new_user(add)
    theirs = make_event(
        days(1), title="Their dinner", source="user", owner_user_id=stranger.id, listed=True
    )
    await add(theirs)

    private = await client.put(f"{LIST}/{theirs.id}/save", json={"status": "going"})
    unknown = await client.put(f"{LIST}/{uuid.uuid4()}/save", json={"status": "going"})

    assert private.status_code == unknown.status_code == 404
    assert private.json() == unknown.json()


@pytest.mark.parametrize(
    "body",
    [{"status": "maybe"}, {}, {"status": "going", "note": "x" * 2001}],
    ids=["bad-status", "no-status", "long-note"],
)
async def test_save_body_is_validated(
    client: AsyncClient, add: Callable[..., Any], body: dict[str, Any]
) -> None:
    event = make_event(days(1))
    await add(event)

    assert (await client.put(f"{LIST}/{event.id}/save", json=body)).status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
# add by link
# ═════════════════════════════════════════════════════════════════════════════


async def test_import_eventbrite_link_uses_api(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_event = AsyncMock(return_value=eventbrite_payload())
    monkeypatch.setattr(eventbrite, "get_event", get_event)

    result = await run_import(factory, me, f"{EVENTBRITE_LINK}?aff=newsletter#top")

    assert result["event"]["title"] == "Async Rust in Production: Bengaluru Meetup"
    assert result["event"]["source"] == "eventbrite"
    assert result["event"]["my_status"] == "saved"
    get_event.assert_awaited_once()
    assert get_event.await_args is not None
    assert get_event.await_args.args[1:] == (TOKEN, "712345678901")
    stored = await rows_of(factory, Event, Event.source == "eventbrite")
    assert [row.external_id for row in stored] == ["712345678901"]
    assert stored[0].detail_fetched_at is not None
    saves = await rows_of(factory, EventSave, EventSave.user_id == me.id)
    assert [(save.event_id, save.status) for save in saves] == [(stored[0].id, "saved")]

    # Index first: the same link again is answered from what is stored.
    again = await run_import(factory, me, EVENTBRITE_LINK)
    assert again["event"]["id"] == result["event"]["id"]
    get_event.assert_awaited_once()


async def test_import_leaves_an_existing_save_as_it_was(
    factory: Factory, add: Callable[..., Any], me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_event = AsyncMock(return_value=eventbrite_payload())
    monkeypatch.setattr(eventbrite, "get_event", get_event)
    first = await run_import(factory, me, EVENTBRITE_LINK)
    async with factory() as session:
        await user.save_event(session, str(me.id), uuid.UUID(first["event"]["id"]), "going", "yes")
        await session.commit()

    again = await run_import(factory, me, EVENTBRITE_LINK)

    assert again["event"]["my_status"] == "going"


async def test_import_eventbrite_is_off_without_a_token(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch, no_requests: None
) -> None:
    get_event = AsyncMock()
    monkeypatch.setattr(eventbrite, "get_event", get_event)
    monkeypatch.setattr(user, "get_settings", lambda: SimpleNamespace(eventbrite_token=None))

    result = await run_import(factory, me, EVENTBRITE_LINK)

    assert result == {"needs_details": True, "reason": "eventbrite_off"}
    get_event.assert_not_awaited()
    assert await rows_of(factory, Event) == []


@pytest.mark.parametrize(
    ("failure", "recorded"),
    [
        (http_error(404), None),
        (http_error(403), None),
        (http_error(503), "error: HTTP 503"),
        (http_error(401), "error: HTTP 401"),
        (httpx.ConnectError("no route"), "error: ConnectError"),
        (ValueError("body too large"), "error: ValueError"),
    ],
    ids=["404", "403", "503", "401", "transport", "too-large"],
)
async def test_import_eventbrite_failure_is_not_readable(
    factory: Factory,
    me: User,
    monkeypatch: pytest.MonkeyPatch,
    quiet_outside: dict[str, Any],
    failure: Exception,
    recorded: str | None,
) -> None:
    monkeypatch.setattr(eventbrite, "get_event", AsyncMock(side_effect=failure))

    result = await run_import(factory, me, EVENTBRITE_LINK)

    assert result == {"needs_details": True, "reason": "not_readable"}
    assert await rows_of(factory, Event) == []
    record = quiet_outside["record"]
    if recorded is None:  # the link was refused: that is not the source failing
        record.assert_not_awaited()
    else:
        record.assert_awaited_once()
        assert record.await_args.args == ("events:eventbrite",)
        assert record.await_args.kwargs["status"] == recorded


async def test_import_eventbrite_success_is_recorded_against_the_source(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch, quiet_outside: dict[str, Any]
) -> None:
    monkeypatch.setattr(eventbrite, "get_event", AsyncMock(return_value=eventbrite_payload()))

    await run_import(factory, me, EVENTBRITE_LINK)

    record = quiet_outside["record"]
    record.assert_awaited_once()
    assert record.await_args.args == ("events:eventbrite",)
    assert record.await_args.kwargs["status"] == "success"
    assert record.await_args.kwargs["job_count"] == 1


async def test_import_eventbrite_breaker_open_makes_no_request(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_event = AsyncMock()
    monkeypatch.setattr(eventbrite, "get_event", get_event)
    monkeypatch.setattr(SourceHealthService, "is_available", AsyncMock(return_value=False))

    result = await run_import(factory, me, EVENTBRITE_LINK)

    assert result == {"needs_details": True, "reason": "not_readable"}
    get_event.assert_not_awaited()


async def test_import_does_not_store_an_event_that_is_not_publicly_listed(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = eventbrite_payload()
    payload["listed"] = False
    monkeypatch.setattr(eventbrite, "get_event", AsyncMock(return_value=payload))

    result = await run_import(factory, me, EVENTBRITE_LINK)

    assert result == {"needs_details": True, "reason": "not_readable"}
    assert await rows_of(factory, Event) == []


async def test_import_ignores_a_response_for_a_different_event(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = eventbrite_payload()
    payload["id"] = "999999999999"
    monkeypatch.setattr(eventbrite, "get_event", AsyncMock(return_value=payload))

    result = await run_import(factory, me, EVENTBRITE_LINK)

    assert result == {"needs_details": True, "reason": "not_readable"}
    assert await rows_of(factory, Event) == []


async def test_import_event_outside_bengaluru(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = copy.deepcopy(eventbrite_payload())
    payload["venue"]["address"] = {
        "city": "Mumbai",
        "region": "MH",
        "latitude": "19.0760",
        "longitude": "72.8777",
        "localized_address_display": "Linking Road, Bandra, Mumbai, MH 400050",
    }
    payload["venue"].pop("latitude", None)
    payload["venue"].pop("longitude", None)
    monkeypatch.setattr(eventbrite, "get_event", AsyncMock(return_value=payload))

    result = await run_import(factory, me, EVENTBRITE_LINK)

    assert result == {"needs_details": True, "reason": "not_in_bengaluru"}
    assert await rows_of(factory, Event) == []
    assert await rows_of(factory, EventSave) == []


HOSTILE_LINKS = [
    "https://example.com/e/1",
    "https://eventbrite.com.evil.io/e/x-123",
    "https://www.eventbrite.com.evil.io/e/x-123",
    "https://user@gdg.community.dev/events/details/x/",
    "https://gdg.community.dev/api/event/1",
    "https://www.eventbrite.com/e/" + "a" * 5000,
    "http://www.eventbrite.com/e/x-123",
    "https://luma.com@evil.io/slug",
    "javascript:alert(1)",
    "ftp://luma.com/slug",
    "luma.com/slug",
    "",
]


@pytest.mark.parametrize(
    "link", HOSTILE_LINKS, ids=[f"hostile-{n}" for n in range(len(HOSTILE_LINKS))]
)
async def test_import_never_requests_unmatched_url(
    factory: Factory,
    me: User,
    monkeypatch: pytest.MonkeyPatch,
    no_requests: None,
    link: str,
) -> None:
    get_event = AsyncMock()
    fetch_page = AsyncMock()
    monkeypatch.setattr(eventbrite, "get_event", get_event)
    monkeypatch.setattr(gdg, "fetch_page", fetch_page)

    result = await run_import(factory, me, link)

    assert result == {"needs_details": True, "reason": "unrecognised"}
    get_event.assert_not_awaited()
    fetch_page.assert_not_awaited()
    assert await rows_of(factory, Event) == []


async def test_import_builds_its_request_from_the_slug_alone(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch_page = AsyncMock(return_value=b"<html></html>")
    monkeypatch.setattr(gdg, "fetch_page", fetch_page)

    await run_import(
        factory, me, "https://gdg.community.dev/events/details/some-event/?next=https://evil.io#x"
    )

    fetch_page.assert_awaited_once()
    assert fetch_page.await_args is not None
    assert fetch_page.await_args.args[1] == "https://gdg.community.dev/events/details/some-event/"


async def test_a_five_thousand_character_link_is_needs_details_not_a_422(
    client: AsyncClient,
) -> None:
    response = await client.post(f"{LIST}/import", json={"url": "https://luma.com/" + "a" * 5000})

    assert response.status_code == 200
    assert response.json() == {"needs_details": True, "reason": "unrecognised"}


async def test_import_luma_not_indexed(
    factory: Factory, add: Callable[..., Any], me: User, no_requests: None
) -> None:
    unknown = await run_import(factory, me, "https://luma.com/unknownslug")
    assert unknown == {"needs_details": True, "reason": "luma_not_indexed"}
    assert await rows_of(factory, Event) == []

    indexed = make_event(days(3), url="https://luma.com/rust-night", external_id="evt-rust0001")
    tracked = make_event(
        days(4), url="https://luma.com/with-tracking?tk=abc", external_id="evt-track001"
    )
    await add(indexed, tracked)

    for link in (
        "https://luma.com/rust-night",
        "https://lu.ma/rust-night",
        "https://luma.com/rust-night/",
        "https://luma.com/event/evt-rust0001",
    ):
        found = await run_import(factory, me, link)
        assert found["event"]["id"] == str(indexed.id), link
        assert found["event"]["my_status"] == "saved"
    assert (await run_import(factory, me, "https://luma.com/with-tracking"))["event"]["id"] == str(
        tracked.id
    )
    # A prefix of an indexed slug is a different page.
    prefix = await run_import(factory, me, "https://luma.com/rust-nigh")
    assert prefix["reason"] == "luma_not_indexed"


async def test_import_resolves_a_cross_source_duplicate_to_its_primary(
    factory: Factory, add: Callable[..., Any], me: User, no_requests: None
) -> None:
    primary = make_event(days(3), title="Swift Bengaluru × Okta", url="https://luma.com/swift-okta")
    duplicate = make_event(
        days(3),
        external_id="evt-dup00001",
        url="https://luma.com/swift-okta-copy",
        duplicate_of=primary.id,
        listed=False,
    )
    await add(primary)
    await add(duplicate)

    found = await run_import(factory, me, "https://luma.com/swift-okta-copy")

    assert found["event"]["id"] == str(primary.id)


async def test_import_does_not_return_another_users_private_event(
    factory: Factory, add: Callable[..., Any], me: User, no_requests: None
) -> None:
    stranger = await new_user(add)
    private = make_event(
        days(3), source="luma", url="https://luma.com/private-dinner", owner_user_id=stranger.id
    )
    await add(private)

    result = await run_import(factory, me, "https://luma.com/private-dinner")

    assert result["reason"] == "luma_not_indexed"


async def test_import_gdg_link_reads_the_event_page_once(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch_page = AsyncMock(return_value=gdg_page())
    monkeypatch.setattr(gdg, "fetch_page", fetch_page)

    result = await run_import(factory, me, GDG_LINK)

    assert result["event"]["title"] == "Road to Devfest: Opensource Edition"
    assert result["event"]["source"] == "gdg"
    fetch_page.assert_awaited_once()
    assert fetch_page.await_args is not None
    assert fetch_page.await_args.args[1] == GDG_LINK
    stored = await rows_of(factory, Event, Event.source == "gdg")
    assert [row.external_id for row in stored] == [GDG_SLUG]
    assert stored[0].detail_fetched_at is not None

    again = await run_import(factory, me, GDG_LINK.rstrip("/"))
    assert again["event"]["id"] == result["event"]["id"]
    fetch_page.assert_awaited_once()


@pytest.mark.parametrize(
    ("page", "failure"),
    [
        (b"<html><body>no event here</body></html>", None),
        (None, httpx.ConnectError("no route")),
        (None, http_error(404)),
        (None, ValueError("page too large")),
    ],
    ids=["no-event", "transport", "404", "too-large"],
)
async def test_import_gdg_page_that_cannot_be_read(
    factory: Factory,
    me: User,
    monkeypatch: pytest.MonkeyPatch,
    quiet_outside: dict[str, Any],
    page: bytes | None,
    failure: Exception | None,
) -> None:
    fetch_page = AsyncMock(return_value=page, side_effect=failure)
    monkeypatch.setattr(gdg, "fetch_page", fetch_page)

    result = await run_import(factory, me, GDG_LINK)

    assert result == {"needs_details": True, "reason": "not_readable"}
    assert await rows_of(factory, Event) == []
    if isinstance(failure, httpx.HTTPStatusError):  # a 404 is the link's fault, not GDG's
        quiet_outside["record"].assert_not_awaited()


ONLINE_GDG_SLUG = "gdg-cloud-study-jam-online"
ONLINE_GDG_LINK = f"https://gdg.community.dev/events/details/{ONLINE_GDG_SLUG}/"


async def test_pasted_online_gdg_page_without_an_india_signal_is_not_stored(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pasted page names no chapter and GDG is worldwide, so "online" alone vouches for nothing."""
    page = an_online_gdg_page("A hands-on study jam, online. Bring a laptop and a question.")
    monkeypatch.setattr(gdg, "fetch_page", AsyncMock(return_value=page))

    result = await run_import(factory, me, ONLINE_GDG_LINK)

    assert result == {"needs_details": True, "reason": "not_in_bengaluru"}
    assert await rows_of(factory, Event) == []
    assert await rows_of(factory, EventSave) == []


async def test_pasted_online_gdg_page_with_an_india_signal_is_stored(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = an_online_gdg_page("A hands-on study jam, online, at 7 pm IST. Bring a laptop.")
    monkeypatch.setattr(gdg, "fetch_page", AsyncMock(return_value=page))

    result = await run_import(factory, me, ONLINE_GDG_LINK)

    assert result["event"]["source"] == "gdg"
    assert result["event"]["is_online"] is True
    stored = await rows_of(factory, Event)
    assert [(row.source, row.external_id) for row in stored] == [("gdg", ONLINE_GDG_SLUG)]
    saves = await rows_of(factory, EventSave)
    assert [(save.user_id, save.status) for save in saves] == [(me.id, "saved")]


async def test_import_daily_cap(
    factory: Factory, add: Callable[..., Any], me: User, quiet_outside: dict[str, Any]
) -> None:
    for number in range(10):
        found = await run_import(factory, me, f"https://luma.com/not-indexed-{number}")
        assert found["reason"] == "luma_not_indexed"

    eleventh = await run_import(factory, me, "https://luma.com/not-indexed-10")
    assert eleventh == {"needs_details": True, "reason": "daily_cap"}
    # Every attempt counts, whatever it was.
    assert (await run_import(factory, me, "https://example.com/e/1"))["reason"] == "daily_cap"

    key = f"tr:events:import:{me.id}:{ist_date(NOW)}"
    assert quiet_outside["counts"][key] == 12
    assert quiet_outside["ttls"][key] == 48 * 3600
    assert user.IMPORT_DAILY_CAP == 10

    other = await new_user(add)
    assert (await run_import(factory, other, "https://luma.com/not-indexed-0"))["reason"] == (
        "luma_not_indexed"
    )
    tomorrow = await run_import(factory, me, "https://luma.com/not-indexed-0", now=days(1))
    assert tomorrow["reason"] == "luma_not_indexed"


async def test_import_day_is_the_ist_day(
    factory: Factory, me: User, quiet_outside: dict[str, Any]
) -> None:
    # 18:45Z on the 15th is 00:15 IST on the 16th.
    await run_import(
        factory, me, "https://luma.com/a-slug", now=datetime(2026, 10, 15, 18, 45, tzinfo=UTC)
    )

    assert list(quiet_outside["counts"]) == [f"tr:events:import:{me.id}:2026-10-16"]


async def test_import_cap_fails_open(
    factory: Factory, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down(_key: str, _ttl: int) -> int:
        raise RuntimeError("cache is down")

    monkeypatch.setattr(CacheBackend, "incr", _down)

    result = await run_import(factory, me, "https://luma.com/not-indexed")

    assert result["reason"] == "luma_not_indexed"


async def test_import_over_the_api(
    client: AsyncClient, add: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    indexed = make_event(days(3), url="https://luma.com/rust-night")
    await add(indexed)

    found = await client.post(f"{LIST}/import", json={"url": "https://luma.com/rust-night"})
    assert found.status_code == 200
    assert set(found.json()) == {"event"}
    assert found.json()["event"]["id"] == str(indexed.id)

    missing = await client.post(f"{LIST}/import", json={"url": "https://luma.com/nothing-here"})
    assert missing.json() == {"needs_details": True, "reason": "luma_not_indexed"}

    assert (await client.post(f"{LIST}/import", json={"url": ""})).status_code == 422
    assert (await client.post(f"{LIST}/import", json={})).status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
# add by hand
# ═════════════════════════════════════════════════════════════════════════════

MANUAL = {
    "title": "Founders dinner",
    "starts_at": "2026-10-20T18:30:00+05:30",
    "ends_at": "2026-10-20T21:00:00+05:30",
    "venue_name": "Hall Four",
    "is_online": False,
    "url": "https://example.test/dinner",
}


async def test_manual_event_invisible_to_others(
    client: AsyncClient, factory: Factory, add: Callable[..., Any], me: User
) -> None:
    created = await client.post(f"{LIST}/manual", json=MANUAL)

    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["source"] == "user"
    assert detail["title"] == "Founders dinner"
    assert detail["starts_at"] == "2026-10-20T13:00:00Z"
    assert detail["reasons"] == ["added by you"]
    assert detail["my"] == {"status": "going", "note": None}
    assert detail["url"] == "https://example.test/dinner"

    row = (await rows_of(factory, Event, Event.source == "user"))[0]
    assert str(row.id) == detail["id"]
    assert row.external_id == detail["id"]
    assert row.owner_user_id == me.id
    assert row.listed is True

    assert titles(await listing(client)) == ["Founders dinner"]
    assert titles(await listing(client, saved="going")) == ["Founders dinner"]

    stranger = await new_user(add)
    theirs = auth(stranger)
    assert titles((await client.get(LIST, headers=theirs)).json()) == []
    assert (await client.get(f"{LIST}/{detail['id']}", headers=theirs)).status_code == 404
    assert (await client.get(f"{LIST}/{detail['id']}/ics", headers=theirs)).status_code == 404
    assert (
        await client.put(f"{LIST}/{detail['id']}/save", json={"status": "going"}, headers=theirs)
    ).status_code == 404
    assert (
        await client.post(f"{LIST}/{detail['id']}/people", json=PERSON, headers=theirs)
    ).status_code == 404
    assert (await client.get(f"{LIST}/{detail['id']}/people", headers=theirs)).status_code == 404


async def test_manual_event_needs_only_a_title_and_a_time(client: AsyncClient) -> None:
    created = await client.post(
        f"{LIST}/manual", json={"title": "Coffee", "starts_at": "2026-10-20T10:00:00Z"}
    )

    assert created.status_code == 201, created.text
    assert created.json()["ends_at"] is None
    assert created.json()["url"] is None
    assert created.json()["is_online"] is False


async def test_manual_event_link_accepts_a_hyphenated_numbered_host(client: AsyncClient) -> None:
    """The host check is a positive pattern, so ordinary hosts (digits, hyphens, case) pass it."""
    link = "https://Rust-Night-2.example.test/dinner"
    created = await client.post(f"{LIST}/manual", json={**MANUAL, "url": link})

    assert created.status_code == 201, created.text
    assert created.json()["url"] == link


async def test_manual_event_text_is_made_safe(client: AsyncClient) -> None:
    nul, bell = chr(0), chr(7)
    created = await client.post(
        f"{LIST}/manual",
        json={
            "title": f"  Rust{nul}  Night{bell}\n\n tonight ",
            "starts_at": "2026-10-20T10:00:00Z",
            "venue_name": "   ",
            "url": "   ",
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["title"] == "Rust Night tonight"
    assert created.json()["venue_name"] is None
    assert created.json()["url"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 301},
        {"starts_at": "2026-10-20T18:30:00"},
        {"starts_at": "1999-12-31T18:30:00+05:30"},
        {"starts_at": "2101-01-01T00:00:00Z"},
        {"starts_at": "not a time"},
        {"ends_at": "2026-10-20T18:30:00+05:30"},
        {"ends_at": "2026-10-20T10:00:00+05:30"},
        {"ends_at": "2026-10-20T21:00:00"},
        {"url": "http://example.test/dinner"},
        {"url": "javascript:alert(1)"},
        {"url": "https://user:pw@example.test/dinner"},
        {"url": "https://exa mple.test/dinner"},
        {"url": f"https://evil.io{BACKSLASH}.example.test/dinner"},
        {"url": "https://evil.io!.example.test/dinner"},
        {"url": "https://exa_mple.test/dinner"},
        {"url": "https://example..test/dinner"},
        {"url": "https://.example.test/dinner"},
        {"url": "https://example.test./dinner"},
        {"venue_name": "v" * 201},
    ],
    ids=lambda changes: "-".join(f"{k}={str(v)[:18]}" for k, v in changes.items()),
)
async def test_manual_event_is_validated(client: AsyncClient, changes: dict[str, Any]) -> None:
    response = await client.post(f"{LIST}/manual", json={**MANUAL, **changes})

    assert response.status_code == 422, response.text


async def test_manual_events_are_capped_per_user(
    client: AsyncClient, factory: Factory, add: Callable[..., Any], me: User
) -> None:
    assert user.MAX_MANUAL_EVENTS == 200
    await add(*[manual_row(me, number) for number in range(200)])

    over = await client.post(f"{LIST}/manual", json=MANUAL)

    assert over.status_code == 422
    assert over.json() == {"detail": "You've added 200 events. Remove one to add another."}
    assert len(await rows_of(factory, Event)) == 200

    stranger = await new_user(add)
    elsewhere = await client.post(f"{LIST}/manual", json=MANUAL, headers=auth(stranger))
    assert elsewhere.status_code == 201, elsewhere.text

    first = (await rows_of(factory, Event, Event.owner_user_id == me.id))[0]
    assert (await client.delete(f"{LIST}/{first.id}")).status_code == 204
    again = await client.post(f"{LIST}/manual", json=MANUAL)
    assert again.status_code == 201, again.text


EARLIER = {"title": "Founders dinner", "starts_at": "2026-10-15T04:30:00Z"}  # before NOW


async def test_delete_own_manual_event_takes_its_save_and_people_with_it(
    client: AsyncClient, factory: Factory
) -> None:
    created = (await client.post(f"{LIST}/manual", json=EARLIER)).json()
    event_id = created["id"]
    assert (await client.post(f"{LIST}/{event_id}/people", json=PERSON)).status_code == 201
    assert len(await rows_of(factory, EventSave)) == 1
    assert len(await rows_of(factory, EventContact)) == 1

    removed = await client.delete(f"{LIST}/{event_id}")

    assert removed.status_code == 204
    assert removed.content == b""
    assert await rows_of(factory, Event) == []
    assert await rows_of(factory, EventSave) == []
    assert await rows_of(factory, EventContact) == []
    assert (await client.get(f"{LIST}/{event_id}")).status_code == 404
    assert (await client.delete(f"{LIST}/{event_id}")).status_code == 404


async def test_delete_is_for_your_own_manual_event_only(
    client: AsyncClient, factory: Factory, add: Callable[..., Any]
) -> None:
    stranger = await new_user(add)
    theirs = (await client.post(f"{LIST}/manual", json=MANUAL, headers=auth(stranger))).json()
    public = make_event(days(2))
    await add(public)
    assert (
        await client.put(f"{LIST}/{public.id}/save", json={"status": "going"})
    ).status_code == 200

    unknown = await client.delete(f"{LIST}/{uuid.uuid4()}")
    foreign = await client.delete(f"{LIST}/{theirs['id']}")
    listed = await client.delete(f"{LIST}/{public.id}")

    assert unknown.status_code == foreign.status_code == listed.status_code == 404
    assert unknown.json() == foreign.json() == listed.json() == {"detail": "Event not found"}
    assert {str(row.id) for row in await rows_of(factory, Event)} == {theirs["id"], str(public.id)}
    assert len(await rows_of(factory, EventSave)) == 2


# ═════════════════════════════════════════════════════════════════════════════
# people met
# ═════════════════════════════════════════════════════════════════════════════


async def test_people_are_private_to_their_user(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    event = started_event()
    await add(event)
    stranger = await new_user(add)
    theirs = auth(stranger)

    created = await client.post(f"{LIST}/{event.id}/people", json=PERSON)
    assert created.status_code == 201, created.text
    person_id = created.json()["id"]

    assert (await client.get(f"{LIST}/{event.id}/people")).json()[0]["id"] == person_id
    assert (await client.get(f"{LIST}/{event.id}")).json()["people"][0]["id"] == person_id
    assert (await client.get(f"{LIST}/people-met")).json()[0]["name"] == "Asha Verma"

    # Another user sees the public event, and none of the first user's people.
    assert (await client.get(f"{LIST}/{event.id}/people", headers=theirs)).json() == []
    assert (await client.get(f"{LIST}/{event.id}", headers=theirs)).json()["people"] == []
    assert (await client.get(f"{LIST}/people-met", headers=theirs)).json() == []
    patched = await client.patch(
        f"{LIST}/people/{person_id}", json={"note": "mine now"}, headers=theirs
    )
    removed = await client.delete(f"{LIST}/people/{person_id}", headers=theirs)
    unknown = await client.delete(f"{LIST}/people/{uuid.uuid4()}", headers=theirs)
    assert patched.status_code == removed.status_code == unknown.status_code == 404
    assert patched.json() == removed.json() == unknown.json()

    untouched = (await client.get(f"{LIST}/{event.id}/people")).json()
    assert untouched[0]["note"] == "Talked about async Rust in production"


async def test_cannot_add_people_before_start(
    client: AsyncClient, factory: Factory, add: Callable[..., Any]
) -> None:
    future = make_event(days(1))
    await add(future)

    response = await client.post(f"{LIST}/{future.id}/people", json=PERSON)

    assert response.status_code == 409
    assert await rows_of(factory, EventContact) == []
    assert await rows_of(factory, EventSave) == []


async def test_people_can_be_added_the_moment_an_event_starts(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    starting = make_event(NOW)
    await add(starting)

    assert (await client.post(f"{LIST}/{starting.id}/people", json=PERSON)).status_code == 201


async def test_adding_a_person_marks_the_event_going_unless_already_saved(
    client: AsyncClient, factory: Factory, add: Callable[..., Any], me: User
) -> None:
    fresh = started_event()
    kept = started_event(external_id="evt-kept")
    await add(fresh, kept)
    await add(EventSave(user_id=me.id, event_id=kept.id, status="saved", note="maybe"))

    await client.post(f"{LIST}/{fresh.id}/people", json=PERSON)
    await client.post(f"{LIST}/{kept.id}/people", json=PERSON)

    saves = {save.event_id: save for save in await rows_of(factory, EventSave)}
    assert saves[fresh.id].status == "going"
    assert (saves[kept.id].status, saves[kept.id].note) == ("saved", "maybe")


async def test_person_payload_reuses_the_detail_shape(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = started_event()
    await add(event)

    with_profile = (await client.post(f"{LIST}/{event.id}/people", json=PERSON)).json()
    without = (
        await client.post(
            f"{LIST}/{event.id}/people", json={"name": "Ravi Menon", "company_name": "Zinc Labs"}
        )
    ).json()

    assert set(with_profile) == {
        "id", "event_id", "name", "title", "company_name", "company_id", "linkedin_url",
        "x_url", "github_url", "email", "note", "created_at", "linkedin_search_url",
    }  # fmt: skip
    assert with_profile["event_id"] == str(event.id)
    assert with_profile["linkedin_search_url"] is None
    # No profile typed: a search link built here, never fetched, and no guessed address.
    assert without["linkedin_url"] is None
    assert without["linkedin_search_url"].startswith(
        "https://www.linkedin.com/search/results/people/"
    )
    assert without["email"] is None


async def test_a_person_is_linked_only_to_a_tech_company_on_the_map(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = started_event()
    lumen = Company(id=uuid.uuid4(), name="Lumen Works", is_tech=True)
    plain = Company(id=uuid.uuid4(), name="Plain Foods", is_tech=False)
    await add(event, lumen, plain)

    async def post(company_name: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": "Asha Verma", "company_name": company_name}
        reply = await client.post(f"{LIST}/{event.id}/people", json=body)
        assert reply.status_code == 201, reply.text
        person: dict[str, Any] = reply.json()
        return person

    assert (await post("Lumen Works"))["company_id"] == str(lumen.id)
    assert (await post("  lumen   WORKS "))["company_id"] == str(lumen.id)
    typed = await post("Plain Foods")
    assert typed["company_id"] is None
    assert typed["company_name"] == "Plain Foods"
    assert (await post("Nowhere Inc"))["company_id"] is None
    assert (await post(None))["company_id"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"linkedin_url": "https://evil.io/in/x"},
        {"linkedin_url": "http://www.linkedin.com/in/x"},
        {"linkedin_url": "https://linkedin.com.evil.io/in/x"},
        {"linkedin_url": "https://evil.io/linkedin.com/in/x"},
        {"linkedin_url": "https://notlinkedin.com/in/x"},
        {"linkedin_url": "https://user@www.linkedin.com/in/x"},
        {"linkedin_url": "https://www.linkedin.com:8443/in/x"},
        {"linkedin_url": "https://www.linkedin.com/in/x y"},
        {"linkedin_url": f"https://evil.io{BACKSLASH}.linkedin.com/in/x"},
        {"linkedin_url": "https://evil.io%2e.linkedin.com/in/x"},
        {"linkedin_url": "https://evil.io;.linkedin.com/in/x"},
        {"linkedin_url": "https://evil.io!.linkedin.com/in/x"},
        {"linkedin_url": "https://evil.io,.linkedin.com/in/x"},
        {"linkedin_url": "https://evil.io_.linkedin.com/in/x"},
        {"linkedin_url": "https://evil.io..linkedin.com/in/x"},
        {"linkedin_url": "https://.linkedin.com/in/x"},
        {"linkedin_url": "https://www.linkedin.com./in/x"},
        {"x_url": "https://a..x.com/asha"},
        {"github_url": "https://.github.com/asha"},
        {"x_url": "https://evil.io'.x.com/asha"},
        {"github_url": "https://evil.io$.github.com/asha"},
        {"x_url": f"https://evil.io{BACKSLASH}.x.com/asha"},
        {"github_url": "https://evil.io%2e.github.com/asha"},
        {"linkedin_url": "javascript:alert(1)"},
        {"linkedin_url": "https://x.com/asha"},
        {"x_url": "https://www.linkedin.com/in/x"},
        {"x_url": "https://x.com.evil.io/asha"},
        {"github_url": "https://gitlab.com/asha"},
        {"github_url": "https://github.com.evil.io/asha"},
        {"email": "not an address"},
        {"email": "a@b"},
        {"email": "a@b.co\nBcc: x@y.co"},
        {"name": ""},
        {"name": "   "},
        {"name": "n" * 201},
        {"title": "t" * 201},
        {"company_name": "c" * 201},
        {"note": "n" * 2001},
        {"linkedin_url": "https://www.linkedin.com/in/" + "a" * 600},
    ],
    ids=lambda changes: "-".join(f"{k}={str(v)[:24]}" for k, v in changes.items()),
)
async def test_contact_fields_are_checked(
    client: AsyncClient, add: Callable[..., Any], changes: dict[str, Any]
) -> None:
    event = started_event()
    await add(event)

    response = await client.post(f"{LIST}/{event.id}/people", json={**PERSON, **changes})

    assert response.status_code == 422, response.text


async def test_contact_url_host_checked(client: AsyncClient, add: Callable[..., Any]) -> None:
    event = started_event()
    await add(event)

    rejected = await client.post(
        f"{LIST}/{event.id}/people", json={**PERSON, "linkedin_url": "https://evil.io/in/x"}
    )
    assert rejected.status_code == 422

    accepted = await client.post(
        f"{LIST}/{event.id}/people",
        json={
            "name": "Ravi Menon",
            "linkedin_url": "https://in.linkedin.com/in/ravi-menon-example",
            "x_url": "https://twitter.com/ravi_example",
            "github_url": "https://github.com/ravi-example",
            "email": " ravi@example.test ",
        },
    )
    assert accepted.status_code == 201, accepted.text
    person = accepted.json()
    assert person["linkedin_url"] == "https://in.linkedin.com/in/ravi-menon-example"
    assert person["x_url"] == "https://twitter.com/ravi_example"
    assert person["github_url"] == "https://github.com/ravi-example"
    assert person["email"] == "ravi@example.test"
    assert person["linkedin_search_url"] is None


async def test_blank_optional_contact_fields_are_stored_as_nothing(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = started_event()
    await add(event)
    blanks = dict.fromkeys(
        ("title", "company_name", "linkedin_url", "x_url", "github_url", "email", "note"), "  "
    )

    response = await client.post(
        f"{LIST}/{event.id}/people", json={"name": "  Asha\tVerma ", **blanks}
    )

    assert response.status_code == 201, response.text
    person = response.json()
    assert person["name"] == "Asha Verma"
    assert all(person[field] is None for field in blanks)


async def test_patch_changes_only_what_is_sent(
    client: AsyncClient, add: Callable[..., Any]
) -> None:
    event = started_event()
    lumen = Company(id=uuid.uuid4(), name="Lumen Works", is_tech=True)
    zinc = Company(id=uuid.uuid4(), name="Zinc Labs", is_tech=True)
    await add(event, lumen, zinc)
    created = (await client.post(f"{LIST}/{event.id}/people", json=PERSON)).json()
    assert created["company_id"] == str(lumen.id)

    note_only = await client.patch(
        f"{LIST}/people/{created['id']}", json={"note": "Follow up Friday"}
    )
    assert note_only.status_code == 200, note_only.text
    assert note_only.json()["note"] == "Follow up Friday"
    assert note_only.json()["name"] == "Asha Verma"
    assert note_only.json()["company_id"] == str(lumen.id)
    assert note_only.json()["linkedin_url"] == PERSON["linkedin_url"]

    moved = await client.patch(
        f"{LIST}/people/{created['id']}", json={"company_name": "Zinc Labs", "linkedin_url": None}
    )
    assert moved.json()["company_id"] == str(zinc.id)
    assert moved.json()["linkedin_url"] is None
    assert moved.json()["linkedin_search_url"] is not None

    gone = await client.patch(f"{LIST}/people/{created['id']}", json={"company_name": None})
    assert gone.json()["company_id"] is None
    assert gone.json()["company_name"] is None

    assert (
        await client.patch(f"{LIST}/people/{created['id']}", json={"name": None})
    ).status_code == 422
    assert (
        await client.patch(f"{LIST}/people/{created['id']}", json={"x_url": "https://evil.io/a"})
    ).status_code == 422
    assert (
        await client.patch(f"{LIST}/people/{uuid.uuid4()}", json={"note": "x"})
    ).status_code == 404


async def test_delete_person(client: AsyncClient, add: Callable[..., Any]) -> None:
    event = started_event()
    await add(event)
    created = (await client.post(f"{LIST}/{event.id}/people", json=PERSON)).json()

    assert (await client.delete(f"{LIST}/people/{created['id']}")).status_code == 204

    assert (await client.get(f"{LIST}/{event.id}/people")).json() == []
    assert (await client.delete(f"{LIST}/people/{created['id']}")).status_code == 404


async def test_people_are_capped_per_event(
    client: AsyncClient, factory: Factory, add: Callable[..., Any], me: User
) -> None:
    assert user.MAX_PEOPLE_PER_EVENT == 100
    event, other = started_event(), started_event()
    await add(event, other)
    await add(*[met_row(me, event.id, number) for number in range(100)])

    over = await client.post(f"{LIST}/{event.id}/people", json=PERSON)

    assert over.status_code == 422
    assert over.json() == {
        "detail": "You've added 100 people to this event. Remove one to add another."
    }
    assert len(await rows_of(factory, EventContact, EventContact.event_id == event.id)) == 100
    assert await rows_of(factory, EventSave) == []  # refused before anything was written

    # Another event of the caller's, and another user at the full event, are unaffected.
    assert (await client.post(f"{LIST}/{other.id}/people", json=PERSON)).status_code == 201
    stranger = await new_user(add)
    theirs = auth(stranger)
    assert (
        await client.post(f"{LIST}/{event.id}/people", json=PERSON, headers=theirs)
    ).status_code == 201

    mine = await rows_of(
        factory, EventContact, EventContact.event_id == event.id, EventContact.user_id == me.id
    )
    assert (await client.delete(f"{LIST}/people/{mine[0].id}")).status_code == 204
    assert (await client.post(f"{LIST}/{event.id}/people", json=PERSON)).status_code == 201


async def test_people_met_are_capped_per_user(
    client: AsyncClient, factory: Factory, add: Callable[..., Any], me: User
) -> None:
    assert user.MAX_PEOPLE_TOTAL == 2000
    events = [started_event() for _ in range(20)]
    fresh = started_event()
    await add(*events, fresh)
    await add(*[met_row(me, event.id, number) for event in events for number in range(100)])

    over = await client.post(f"{LIST}/{fresh.id}/people", json=PERSON)

    assert over.status_code == 422
    assert over.json() == {
        "detail": "You've added 2,000 people in total. Remove one to add another."
    }
    assert await rows_of(factory, EventContact, EventContact.event_id == fresh.id) == []

    stranger = await new_user(add)
    elsewhere = await client.post(f"{LIST}/{fresh.id}/people", json=PERSON, headers=auth(stranger))
    assert elsewhere.status_code == 201, elsewhere.text


# ═════════════════════════════════════════════════════════════════════════════
# the router itself
# ═════════════════════════════════════════════════════════════════════════════


async def test_people_met_is_not_taken_for_an_event_id(client: AsyncClient) -> None:
    response = await client.get(f"{LIST}/people-met")

    assert response.status_code == 200
    assert response.json() == []


def test_fixed_paths_are_registered_above_the_event_id_routes() -> None:
    from api.routers import events

    paths = [getattr(route, "path", "") for route in events.router.routes]
    first_dynamic = paths.index("/events/{event_id}")
    for fixed in (
        "/events/people-met",
        "/events/import",
        "/events/manual",
        "/events/people/{person_id}",
    ):
        assert paths.index(fixed) < first_dynamic, fixed


def test_the_new_routes_are_mounted_and_authenticated() -> None:
    from api.main import app

    documented = app.openapi()["paths"]

    assert {
        "/api/v1/events/{event_id}/save",
        "/api/v1/events/import",
        "/api/v1/events/manual",
        "/api/v1/events/{event_id}/people",
        "/api/v1/events/people/{person_id}",
        "/api/v1/events/people-met",
    } <= set(documented)
    assert set(documented["/api/v1/events/{event_id}"]) == {"get", "delete"}
    assert set(documented["/api/v1/events/{event_id}/save"]) == {"put", "delete"}
    assert set(documented["/api/v1/events/{event_id}/people"]) == {"get", "post"}
    assert set(documented["/api/v1/events/people/{person_id}"]) == {"patch", "delete"}


async def test_every_new_route_needs_a_signed_in_user(
    anonymous: AsyncClient, add: Callable[..., Any]
) -> None:
    event = started_event()
    await add(event)
    someone = uuid.uuid4()
    calls = [
        anonymous.delete(f"{LIST}/{event.id}"),
        anonymous.put(f"{LIST}/{event.id}/save", json={"status": "going"}),
        anonymous.delete(f"{LIST}/{event.id}/save"),
        anonymous.post(f"{LIST}/import", json={"url": "https://luma.com/x-y-z"}),
        anonymous.post(f"{LIST}/manual", json=MANUAL),
        anonymous.get(f"{LIST}/{event.id}/people"),
        anonymous.post(f"{LIST}/{event.id}/people", json=PERSON),
        anonymous.patch(f"{LIST}/people/{someone}", json={"note": "x"}),
        anonymous.delete(f"{LIST}/people/{someone}"),
        anonymous.get(f"{LIST}/people-met"),
    ]

    assert [(await call).status_code for call in calls] == [401] * len(calls)


async def test_writes_make_no_outside_request(
    client: AsyncClient, add: Callable[..., Any], no_requests_through_transport: None
) -> None:
    event = started_event()
    await add(event)

    assert (
        await client.put(f"{LIST}/{event.id}/save", json={"status": "saved"})
    ).status_code == 200
    assert (await client.post(f"{LIST}/manual", json=MANUAL)).status_code == 201
    assert (await client.post(f"{LIST}/{event.id}/people", json=PERSON)).status_code == 201
    assert (await client.get(f"{LIST}/people-met")).status_code == 200
    assert (await client.post(f"{LIST}/import", json={"url": "https://example.com/e/1"})).json()[
        "reason"
    ] == "unrecognised"
    assert (await client.delete(f"{LIST}/{event.id}/save")).status_code == 204


@pytest.fixture
def no_requests_through_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a user action tried to reach the network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
