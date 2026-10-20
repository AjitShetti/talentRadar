"""
tests/test_event_refresh.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Events refresh steps (``services/events/refresh.py``) end to end, with the fetchers
patched to recorded fixtures and the database an in-memory SQLite with
``PRAGMA foreign_keys=ON`` behind a patched session factory.

Nothing here touches the network: ``httpx.AsyncClient.send`` is replaced by a function
that raises, ``asyncio.sleep`` records what it was asked, the clock is fixed, and the cache
is the in-process fallback. Every person and organiser is fictional.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_settings
from domain.event_facts import SourceScope
from domain.ical import parse_ical
from services import cache_backend, job_retention
from services.cache_backend import CacheBackend
from services.events import eventbrite, gdg, luma, refresh, store
from services.events.sources import GdgChapter, LumaFeed, SourceList
from services.source_health import SourceHealthService
from storage.database import Base
from storage.models import Company, Event, EventCompany, EventContact, EventSave, User

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
TABLES = ("users", "companies", "events", "event_companies", "event_saves", "event_contacts")

NOW = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)  # Fri 16 Oct, 08:30 IST: the recordings' day

DISCOVER = LumaFeed("discover", "discplace-Disc0ver1", "Bengaluru", False, True)
SWIFT = LumaFeed("calendar", "cal-SwiftBlr01", "SwiftBengaluru", True, True)
LOSSFUNK = LumaFeed("calendar", "cal-Lossfunk01", "Lossfunk", True, True)
GDG_BLR = GdgChapter("gdg-bangalore", "GDG Bangalore")
GDG_CLOUD = GdgChapter("gdg-cloud-bengaluru", "GDG Cloud Bengaluru")
SOURCES = SourceList(
    luma=(DISCOVER, SWIFT, LOSSFUNK),
    gdg=(GDG_BLR, GDG_CLOUD),
    eventbrite_organizers=("5001",),
    eventbrite_venues=("7001",),
)
FEEDS: dict[str, bytes] = {
    DISCOVER.id: (FIXTURES / "luma_discover_bengaluru.ics").read_bytes(),
    SWIFT.id: (FIXTURES / "luma_calendar_swiftbengaluru.ics").read_bytes(),
    LOSSFUNK.id: (FIXTURES / "luma_calendar_lossfunk.ics").read_bytes(),
}

DEVFEST = "google-gdg-cloud-bengaluru-presents-road-to-devfest-opensource-edition"
EVENTBRITE_LIST = json.loads((FIXTURES / "eventbrite_organizer_events.json").read_text("utf-8"))
TOKEN = "tok_Fictional0Token9"


# ── the world every test runs in ─────────────────────────────────────────────


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    sleeps: list[float] = field(default_factory=list)

    @property
    def pauses(self) -> list[float]:
        """The waits the steps asked for (``sleep(0)`` yields are not waits)."""
        return [seconds for seconds in self.sleeps if seconds > 0]


@pytest_asyncio.fixture(autouse=True)
async def world(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
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
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    state = World(factory=factory)

    async def fake_sleep(delay: float, result: Any = None) -> Any:
        state.sleeps.append(delay)
        return result

    async def no_network(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(refresh, "AsyncSessionLocal", factory)
    monkeypatch.setattr(refresh, "_now", lambda: NOW)
    monkeypatch.setattr(refresh, "load_sources", lambda: SOURCES)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(get_settings(), "eventbrite_token", None)
    monkeypatch.setattr(CacheBackend, "_initialised", True)
    monkeypatch.setattr(CacheBackend, "_client", None)
    cache_backend._MEMORY.clear()
    yield state
    cache_backend._MEMORY.clear()
    await engine.dispose()


def use_sources(monkeypatch: pytest.MonkeyPatch, sources: SourceList) -> None:
    monkeypatch.setattr(refresh, "load_sources", lambda: sources)


async def events_in(world: World) -> list[Event]:
    async with world.factory() as db:
        return list((await db.execute(select(Event).order_by(Event.external_id))).scalars())


async def count(world: World, model: type) -> int:
    async with world.factory() as db:
        return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def get_event(world: World, event_id: uuid.UUID) -> Event:
    async with world.factory() as db:
        found = await db.get(Event, event_id)
        assert found is not None
        return found


# ── stubs for the three fetchers ─────────────────────────────────────────────


def stub_luma(
    monkeypatch: pytest.MonkeyPatch, bodies: dict[str, bytes | Exception] | None = None
) -> list[str]:
    """``luma.fetch_feed`` serving ``bodies`` by feed id; the addresses asked for, in order."""
    served = FEEDS if bodies is None else bodies
    calls: list[str] = []

    async def fetch(_client: httpx.AsyncClient, url: str) -> bytes:
        calls.append(url)
        body = served[parse_qs(urlsplit(url).query)["id"][0]]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(luma, "fetch_feed", fetch)
    return calls


def chapter_html(*events: tuple[str, str]) -> bytes:
    """A chapter page listing ``(slug, start_date)`` events, shaped like the recorded ones."""
    results = [
        {
            "title": "Road to Devfest",
            "start_date": start,
            "url": f"{gdg.BASE}/events/details/{slug}/",
            "event_type_title": "Meetup",
            "description_short": "A community day.",
        }
        for slug, start in events
    ]
    blob = {"props": {"pageProps": {"prerenderData": {"upcomingEvents": {"results": results}}}}}
    return f'<html><body><script type="application/json">{json.dumps(blob)}</script></body></html>'.encode()


def recorded(name: str) -> bytes:
    return gzip.decompress((FIXTURES / name).read_bytes())


def gdg_pages() -> dict[str, bytes | Exception]:
    return {
        gdg.chapter_url(GDG_BLR.slug): recorded("gdg_chapter_gdg-bangalore.html.gz"),
        gdg.chapter_url(GDG_CLOUD.slug): recorded("gdg_chapter_gdg-cloud-bengaluru.html.gz"),
        gdg.event_page_url(DEVFEST): recorded(f"gdg_event_{DEVFEST}.html.gz"),
    }


def stub_gdg(
    monkeypatch: pytest.MonkeyPatch, pages: dict[str, bytes | Exception] | None = None
) -> list[str]:
    """``gdg.fetch_page`` serving ``pages`` by address; the addresses asked for, in order."""
    served = gdg_pages() if pages is None else pages
    calls: list[str] = []

    async def fetch(_client: httpx.AsyncClient, url: str) -> bytes:
        calls.append(url)
        body = served[url]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(gdg, "fetch_page", fetch)
    return calls


@dataclass
class EventbriteCall:
    kind: str
    ident: str
    continuation: str | None
    ordered: bool
    token: str


Pages = Callable[[EventbriteCall], dict[str, Any]]


def stub_eventbrite(
    monkeypatch: pytest.MonkeyPatch,
    organizers: Pages | None = None,
    venues: Pages | None = None,
) -> list[EventbriteCall]:
    calls: list[EventbriteCall] = []

    def serve(kind: str, pages: Pages | None) -> Any:
        async def fetch(
            _client: httpx.AsyncClient,
            token: str,
            ident: str,
            continuation: str | None = None,
            *,
            ordered: bool = True,
        ) -> dict[str, Any]:
            call = EventbriteCall(kind, ident, continuation, ordered, token)
            calls.append(call)
            if pages is None:
                raise AssertionError(f"unexpected {kind} call")
            return pages(call)

        return fetch

    monkeypatch.setattr(eventbrite, "organizer_events", serve("organizers", organizers))
    monkeypatch.setattr(eventbrite, "venue_events", serve("venues", venues))
    return calls


def status_error(
    status: int, url: str = "https://www.eventbriteapi.com/v3/x/"
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=httpx.Response(status, request=request)
    )


def eventbrite_page(
    ids: tuple[str, ...], *, cursor: str | None = None, **changes: Any
) -> dict[str, Any]:
    """A list page of the recorded organiser events, re-numbered to ``ids``."""
    template = EVENTBRITE_LIST["events"]
    events = []
    for i, ident in enumerate(ids):
        event = json.loads(json.dumps(template[i % len(template)]))
        event.update({"id": ident, **changes})
        events.append(event)
    pagination = {"has_more_items": cursor is not None}
    if cursor is not None:
        pagination["continuation"] = cursor
    return {"events": events, "pagination": pagination}


# ── seeding rows ─────────────────────────────────────────────────────────────

TRUSTED = {"key": "Rust Quill", "india": True, "trusted_tech": True}
UNTRUSTED = {"key": "Bengaluru", "india": True, "trusted_tech": False}


def make_row(**fields: Any) -> Event:
    """A stored Luma event in Indiranagar on a trusted tech calendar, as the store would write it."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source": "luma",
        "external_id": f"evt-{uuid.uuid4().hex[:12]}",
        "url": "https://luma.com/quill",
        "title": "Open Evening",
        "starts_at": NOW + timedelta(days=3),
        "address": "Indiranagar, Bengaluru, Karnataka, India",
        "is_online": False,
        "listed": False,
        "first_seen_at": NOW - timedelta(days=2),
        "last_seen_at": NOW - timedelta(days=1),
        "updated_at": NOW - timedelta(days=1),
        "extra_metadata": {"scope": TRUSTED, "pin": "placed"},
    }
    values.update(fields)
    return Event(**values)


async def seed(world: World, *rows: Any) -> None:
    async with world.factory() as db:
        db.add_all(rows)
        await db.commit()


async def add_company(world: World, name: str) -> Company:
    company = Company(id=uuid.uuid4(), name=name, is_tech=True, summary=f"What {name} makes.")
    await seed(world, company)
    return company


async def add_user(world: World) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x")
    await seed(world, user)
    return user


# ═════════════════════════════════════════════════════════════════════════════
# the contract
# ═════════════════════════════════════════════════════════════════════════════


def test_the_steps_and_ceilings_are_the_documented_ones() -> None:
    assert refresh.STEPS == ("luma", "gdg", "eventbrite", "link", "relevance", "prune", "purge")
    assert refresh.MAX_LIMIT == {
        "luma": 26,
        "gdg": 30,
        "eventbrite": 60,
        "link": 500,
        "relevance": 2000,
        "prune": 1,
        "purge": 1,
    }
    assert timedelta(hours=11) == refresh.LUMA_MIN_INTERVAL


async def test_unknown_step() -> None:
    result = await refresh.run_step("nope", 1)
    assert "error" in result and "nope" in str(result["error"])
    assert "step" not in result


async def test_run_step_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_luma(monkeypatch)
    monkeypatch.setattr(refresh, "upsert_event", AsyncMock(side_effect=RuntimeError("boom")))

    result = await refresh.run_step("luma", 26)

    assert result["step"] == "luma"
    assert "RuntimeError" in str(result["error"])


async def test_an_unexpected_failure_in_one_feed_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    stub_luma(monkeypatch)
    genuine = store.upsert_event

    async def flaky(session: AsyncSession, facts: Any, scope: SourceScope, **kwargs: Any) -> Any:
        if scope.key == DISCOVER.name:
            raise RuntimeError("a bug in one feed")
        return await genuine(session, facts, scope, **kwargs)

    monkeypatch.setattr(refresh, "upsert_event", flaky)
    result = await refresh.run_step("luma", 26)

    assert "RuntimeError" in str(result["error"])  # the bug is reported …
    assert int(result["created"]) > 0  # … and the calendars after it were stored
    assert {row.source for row in await events_in(world)} == {"luma"}


# ═════════════════════════════════════════════════════════════════════════════
# luma
# ═════════════════════════════════════════════════════════════════════════════


async def test_luma_step_reads_fixture_and_counts(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    calls = stub_luma(monkeypatch)

    result = await refresh.run_step("luma", 26)

    assert "error" not in result and "errors" not in result
    assert result["step"] == "luma"
    assert result["feeds"] == 3 == len(calls)
    assert int(result["created"]) >= 10
    assert int(result["listed"]) >= 1
    assert int(result["seen"]) == int(result["created"]) + int(result["updated"]) + int(
        result["skipped"]
    )
    assert int(result["created"]) == await count(world, Event)
    assert set(result) == {
        "step", "feeds", "seen", "created", "updated", "duplicates", "skipped", "listed",
    }  # fmt: skip


async def test_luma_asks_only_for_the_listed_feed_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = stub_luma(monkeypatch)
    await refresh.run_step("luma", 26)
    assert calls == [luma.feed_url(feed.kind, feed.id) for feed in SOURCES.luma]


async def test_luma_feed_skipped_within_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_luma(monkeypatch)
    first = await refresh.run_step("luma", 26)
    asked = len(calls)

    second = await refresh.run_step("luma", 26)

    assert first["feeds"] == 3
    assert second["feeds"] == 0
    assert len(calls) == asked  # no fetch call
    assert "error" not in second and "errors" not in second


async def test_a_read_is_remembered_for_eleven_hours_under_the_documented_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_luma(monkeypatch)
    await refresh.run_step("luma", 26)

    expires_at, _ = cache_backend._MEMORY[f"tr:events:luma:{SWIFT.id}:read"]
    assert expires_at - time.time() == pytest.approx(11 * 3600, abs=30)


async def test_a_feed_that_failed_is_not_remembered_and_the_others_are_kept(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    calls = stub_luma(monkeypatch, {**FEEDS, SWIFT.id: httpx.ConnectError("connection refused")})

    first = await refresh.run_step("luma", 26)

    assert first["feeds"] == 3
    assert "SwiftBengaluru" in str(first["errors"])
    assert "error" not in first
    assert int(first["created"]) > 0

    calls.clear()
    stub_luma(monkeypatch)  # the calendar is back
    second = await refresh.run_step("luma", 26)
    assert second["feeds"] == 1  # only the one that failed is read again
    assert await count(world, Event) >= int(first["created"])


async def test_luma_waits_a_second_between_feeds_not_after_the_last(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    stub_luma(monkeypatch)
    await refresh.run_step("luma", 26)
    assert world.pauses == [1.0, 1.0]


async def test_luma_reads_at_most_limit_feeds_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_luma(monkeypatch)
    one = await refresh.run_step("luma", 1)
    assert one["feeds"] == 1 == len(calls)

    two = await refresh.run_step("luma", 2)  # the first is remembered, so the cap moves on
    assert two["feeds"] == 2 == len(calls) - 1


async def test_luma_never_exceeds_its_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    many = tuple(
        LumaFeed("calendar", f"cal-Town{i:05d}", f"Town {i}", True, True) for i in range(40)
    )
    use_sources(monkeypatch, SourceList(many, (), (), ()))
    calls = stub_luma(
        monkeypatch, dict.fromkeys((f.id for f in many), b"BEGIN:VCALENDAR\r\nEND:VCALENDAR")
    )

    result = await refresh.run_step("luma", 1000)

    assert result["feeds"] == 26 == len(calls)


async def test_a_cache_failure_costs_at_most_one_fetch_per_feed_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = stub_luma(monkeypatch)
    genuine_get, genuine_set = CacheBackend.get, CacheBackend.set

    async def get(key: str) -> str | None:
        if key.startswith("tr:events:"):
            raise RuntimeError("the cache is down")
        return await genuine_get(key)

    async def put(key: str, value: str, ttl_seconds: int) -> None:
        if key.startswith("tr:events:"):
            raise RuntimeError("the cache is down")
        await genuine_set(key, value, ttl_seconds)

    monkeypatch.setattr(CacheBackend, "get", staticmethod(get))
    monkeypatch.setattr(CacheBackend, "set", staticmethod(put))

    result = await refresh.run_step("luma", 26)

    assert "error" not in result
    assert result["feeds"] == 3
    assert len(calls) == 3  # once per feed, never a loop
    assert int(result["created"]) > 0


async def test_luma_records_each_read_against_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_luma(monkeypatch)
    await refresh.run_step("luma", 26)

    health = await SourceHealthService.get("events:luma")
    parsed = sum(len(luma.parse_feed(body)) for body in FEEDS.values())
    assert health.attempts == 3
    assert health.successes == 3
    assert health.total_jobs == parsed  # job_count=len(events)


async def test_failed_reads_open_the_breaker_and_the_rest_of_the_source_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    many = tuple(
        LumaFeed("calendar", f"cal-Town{i:05d}", f"Town {i}", True, True) for i in range(6)
    )
    use_sources(monkeypatch, SourceList(many, (), (), ()))
    calls = stub_luma(monkeypatch, dict.fromkeys((f.id for f in many), httpx.ReadTimeout("slow")))

    result = await refresh.run_step("luma", 26)

    assert len(calls) == 3  # three failures open it
    assert "luma: circuit open" in str(result["errors"])
    assert result["feeds"] == 3


async def test_breaker_open_skips_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_luma(monkeypatch)
    monkeypatch.setattr(SourceHealthService, "is_available", AsyncMock(return_value=False))

    result = await refresh.run_step("luma", 26)

    assert "luma: circuit open" in str(result["errors"])
    assert result["feeds"] == 0
    assert calls == []


async def test_a_database_error_rolls_the_feed_back_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    calls = stub_luma(monkeypatch)
    genuine = store.upsert_event

    async def broken_for_one_feed(
        session: AsyncSession, facts: Any, scope: SourceScope, **kwargs: Any
    ) -> Any:
        if scope.key == DISCOVER.name:
            raise IntegrityError("INSERT INTO events", {}, Exception("duplicate key"))
        return await genuine(session, facts, scope, **kwargs)

    monkeypatch.setattr(refresh, "upsert_event", broken_for_one_feed)
    first = await refresh.run_step("luma", 26)

    assert "error" not in first  # a database error is counted, not a failed step
    assert "Bengaluru" in str(first["errors"]) and "IntegrityError" in str(first["errors"])
    assert int(first["created"]) > 0
    # Nothing of the failed feed was kept: it did not commit, and it is read again next run.
    rows = await events_in(world)
    assert rows and all(
        (row.extra_metadata or {}).get("scope", {}).get("key") != DISCOVER.name for row in rows
    )
    monkeypatch.setattr(refresh, "upsert_event", genuine)
    calls.clear()
    second = await refresh.run_step("luma", 26)
    assert second["feeds"] == 1
    assert calls == [luma.feed_url(DISCOVER.kind, DISCOVER.id)]


async def test_the_exact_location_events_are_stored_withheld(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    stub_luma(monkeypatch)
    # The four events that say so ran in Aug-Sep 2025: read the calendar as of 1 Aug 2025.
    monkeypatch.setattr(refresh, "_now", lambda: datetime(2025, 8, 1, tzinfo=UTC))
    await refresh.run_step("luma", 26)

    sentence = {
        e.uid.removesuffix("@events.lu.ma")
        for e in parse_ical(FEEDS[LOSSFUNK.id])
        if "exact location" in (e.description or "").lower()
    }
    rows = [row for row in await events_in(world) if row.external_id in sentence]
    assert len(sentence) == len(rows) == 4
    assert all((row.extra_metadata or {})["address_withheld"] is True for row in rows)


# ═════════════════════════════════════════════════════════════════════════════
# gdg
# ═════════════════════════════════════════════════════════════════════════════


async def test_gdg_step_waits_between_requests(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    fetched = stub_gdg(monkeypatch)

    result = await refresh.run_step("gdg", 30)

    assert fetched == [
        gdg.chapter_url(GDG_BLR.slug),
        gdg.chapter_url(GDG_CLOUD.slug),
        gdg.event_page_url(DEVFEST),
    ]
    assert world.pauses == [2.0, 2.0]  # between requests, not after the last
    assert not any("/api/" in url or "/gql/" in url for url in fetched)
    assert result["pages"] == 3
    assert result["created"] == 1


async def test_gdg_events_carry_the_gdg_source_and_are_listed(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    stub_gdg(monkeypatch)
    result = await refresh.run_step("gdg", 30)

    (row,) = await events_in(world)
    assert row.source == "gdg"
    assert row.external_id == DEVFEST
    assert row.organiser_name == GDG_CLOUD.name
    assert row.listed is True
    assert (row.reasons or [])[0] == "GDG chapter"
    assert row.detail_fetched_at is not None
    assert result["listed"] == 1


async def test_an_event_page_is_read_once_in_three_days(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    fetched = stub_gdg(monkeypatch)
    await refresh.run_step("gdg", 30)
    fetched.clear()

    again = await refresh.run_step("gdg", 30)

    assert fetched == [gdg.chapter_url(GDG_BLR.slug), gdg.chapter_url(GDG_CLOUD.slug)]
    assert again["pages"] == 2
    assert again["created"] == 0
    assert await count(world, Event) == 1


async def test_an_event_page_is_read_again_when_its_start_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = gdg_pages()
    pages[gdg.chapter_url(GDG_CLOUD.slug)] = chapter_html((DEVFEST, "2026-10-24T04:00:00Z"))
    fetched = stub_gdg(monkeypatch, pages)
    await refresh.run_step("gdg", 30)

    pages[gdg.chapter_url(GDG_CLOUD.slug)] = chapter_html((DEVFEST, "2026-10-24T05:00:00Z"))
    fetched.clear()
    moved = await refresh.run_step("gdg", 30)

    assert gdg.event_page_url(DEVFEST) in fetched
    assert moved["pages"] == 3


async def test_an_event_page_is_read_again_after_three_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = stub_gdg(monkeypatch)
    await refresh.run_step("gdg", 30)
    fetched.clear()

    monkeypatch.setattr(refresh, "_now", lambda: NOW + timedelta(days=3, minutes=1))
    cache_backend._MEMORY.clear()  # the cache is disposable; the stored row remembers too
    await refresh.run_step("gdg", 30)

    assert gdg.event_page_url(DEVFEST) in fetched


async def test_a_stored_row_remembers_the_read_without_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = stub_gdg(monkeypatch)
    await refresh.run_step("gdg", 30)
    fetched.clear()
    cache_backend._MEMORY.clear()  # Render slept; the in-process cache is gone

    await refresh.run_step("gdg", 30)

    assert gdg.event_page_url(DEVFEST) not in fetched


async def test_gdg_makes_at_most_thirty_requests_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    towns = tuple(GdgChapter(f"gdg-town-{i}", f"GDG Town {i}") for i in range(40))
    use_sources(monkeypatch, SourceList((), towns, (), ()))
    fetched = stub_gdg(monkeypatch, {gdg.chapter_url(t.slug): chapter_html() for t in towns})

    result = await refresh.run_step("gdg", 1000)

    assert result["pages"] == 30 == len(fetched)


async def test_every_chapter_page_is_read_before_any_event_page_when_the_budget_is_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = stub_gdg(monkeypatch)

    result = await refresh.run_step("gdg", 2)

    assert fetched == [gdg.chapter_url(GDG_BLR.slug), gdg.chapter_url(GDG_CLOUD.slug)]
    assert result["pages"] == 2


async def test_a_chapter_that_fails_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    pages = gdg_pages()
    pages[gdg.chapter_url(GDG_BLR.slug)] = httpx.ConnectError("refused")
    stub_gdg(monkeypatch, pages)

    result = await refresh.run_step("gdg", 30)

    assert "/gdg-bangalore/: ConnectError" in str(result["errors"])
    assert "error" not in result
    assert result["created"] == 1


async def test_gdg_breaker_open_skips_source(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = stub_gdg(monkeypatch)
    monkeypatch.setattr(SourceHealthService, "is_available", AsyncMock(return_value=False))

    result = await refresh.run_step("gdg", 30)

    assert str(result["errors"]).count("gdg: circuit open") == 1
    assert fetched == []
    assert result["pages"] == 0


async def test_gdg_page_that_will_not_parse_is_counted_and_not_read_again(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    pages = gdg_pages()
    pages[gdg.event_page_url(DEVFEST)] = b"<html><body>no event here</body></html>"
    fetched = stub_gdg(monkeypatch, pages)

    first = await refresh.run_step("gdg", 30)
    fetched.clear()
    await refresh.run_step("gdg", 30)

    assert first["created"] == 0 and first["skipped"] == 1
    assert gdg.event_page_url(DEVFEST) not in fetched
    assert await count(world, Event) == 0


# ═════════════════════════════════════════════════════════════════════════════
# eventbrite
# ═════════════════════════════════════════════════════════════════════════════


async def test_eventbrite_without_token_skips(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", None)
    calls = stub_eventbrite(monkeypatch)

    result = await refresh.run_step("eventbrite", 60)

    assert result == {"step": "eventbrite", "skipped": "no token"}
    assert calls == []  # no request
    assert (await SourceHealthService.get("events:eventbrite")).attempts == 0
    assert await count(world, Event) == 0


async def test_eventbrite_reads_organisers_then_venues_and_pages_through(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)

    def organizers(call: EventbriteCall) -> dict[str, Any]:
        if call.continuation is None:
            return eventbrite_page(("1001", "1002", "1003"), cursor="cursorTwo")
        return eventbrite_page(("1004",))

    def venues(call: EventbriteCall) -> dict[str, Any]:
        return eventbrite_page(("2001",))

    calls = stub_eventbrite(monkeypatch, organizers, venues)

    result = await refresh.run_step("eventbrite", 60)

    assert [(c.kind, c.ident, c.continuation) for c in calls] == [
        ("organizers", "5001", None),
        ("organizers", "5001", "cursorTwo"),
        ("venues", "7001", None),
    ]
    assert all(c.token == TOKEN for c in calls)
    assert result["calls"] == 3
    assert result["seen"] == 5
    assert int(result["created"]) >= 4
    assert int(result["created"]) == await count(world, Event)
    assert {row.source for row in await events_in(world)} == {"eventbrite"}
    assert set(result) == {
        "step", "calls", "seen", "created", "updated", "duplicates", "skipped", "listed",
    }  # fmt: skip
    assert world.pauses == [1.0, 1.0]  # 1 req/s, none after the last


async def test_eventbrite_events_that_are_not_listed_are_skipped(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    page = eventbrite_page(("1001", "1002", "1003"))
    page["events"][2]["listed"] = False
    stub_eventbrite(monkeypatch, lambda call: page, lambda call: {"events": []})

    result = await refresh.run_step("eventbrite", 60)

    assert [row.external_id for row in await events_in(world)] == ["1001", "1002"]
    assert result["skipped"] == 1
    assert result["seen"] == 3


async def test_eventbrite_calls_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    counter = iter(range(10_000))

    def endless(call: EventbriteCall) -> dict[str, Any]:  # always another page, always new
        return eventbrite_page((), cursor=f"cursor{next(counter)}")

    calls = stub_eventbrite(monkeypatch, endless, endless)

    small = await refresh.run_step("eventbrite", 5)
    assert small["calls"] == 5 == len(calls)

    calls.clear()
    big = await refresh.run_step("eventbrite", 10_000)
    assert big["calls"] == 60 == len(calls)  # the hard cap


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(lambda call: eventbrite_page(("1001",), cursor="sameCursor"), id="repeated"),
        pytest.param(lambda call: eventbrite_page(("1001",), cursor="not a cursor!"), id="invalid"),
    ],
)
async def test_eventbrite_stops_on_a_repeated_or_invalid_continuation(
    monkeypatch: pytest.MonkeyPatch, answer: Pages
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    use_sources(monkeypatch, replace(SOURCES, eventbrite_venues=()))
    calls = stub_eventbrite(monkeypatch, answer, None)

    await refresh.run_step("eventbrite", 60)

    assert len(calls) <= 2
    assert [c.continuation for c in calls][:1] == [None]
    if len(calls) == 2:  # the repeat is followed once and then recognised
        assert calls[1].continuation == "sameCursor"


async def test_a_venue_that_refuses_order_by_is_asked_again_without_it(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    use_sources(
        monkeypatch, replace(SOURCES, eventbrite_organizers=(), eventbrite_venues=("7001", "7002"))
    )

    def venues(call: EventbriteCall) -> dict[str, Any]:
        if call.ordered:
            raise status_error(400)
        return eventbrite_page((f"{call.ident}1",))

    calls = stub_eventbrite(monkeypatch, None, venues)

    result = await refresh.run_step("eventbrite", 60)

    assert [(c.ident, c.ordered) for c in calls] == [
        ("7001", True),
        ("7001", False),  # retried once without order_by
        ("7002", False),  # and the next venue does not waste a call finding out again
    ]
    assert result["calls"] == 3
    assert result["created"] == 2
    assert "errors" not in result
    assert (await SourceHealthService.get("events:eventbrite")).consecutive_failures == 0


async def test_a_venue_that_still_fails_counts_an_error_and_the_run_moves_on(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    use_sources(
        monkeypatch, replace(SOURCES, eventbrite_organizers=(), eventbrite_venues=("7001", "7002"))
    )

    def venues(call: EventbriteCall) -> dict[str, Any]:
        if call.ident == "7001":
            raise status_error(400)
        return eventbrite_page(("2002",))

    calls = stub_eventbrite(monkeypatch, None, venues)

    result = await refresh.run_step("eventbrite", 60)

    assert [(c.ident, c.ordered) for c in calls] == [
        ("7001", True),
        ("7001", False),
        ("7002", False),
    ]
    assert "7001" in str(result["errors"]) and "400" in str(result["errors"])
    assert "error" not in result
    assert result["created"] == 1
    # A venue this token cannot read is not a sick source: nothing was held against it.
    health = await SourceHealthService.get("events:eventbrite")
    assert health.last_error is None
    assert health.attempts == 1  # only the venue that answered was recorded


async def test_an_organiser_the_token_cannot_read_does_not_stop_the_venues(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)

    def organizers(call: EventbriteCall) -> dict[str, Any]:
        raise status_error(403)

    calls = stub_eventbrite(monkeypatch, organizers, lambda call: eventbrite_page(("3001",)))

    result = await refresh.run_step("eventbrite", 60)

    assert [c.kind for c in calls] == ["organizers", "venues"]
    assert result["created"] == 1
    assert "403" in str(result["errors"])
    assert (await SourceHealthService.get("events:eventbrite")).last_error is None


@pytest.mark.parametrize("status", [400, 403, 404])
async def test_refusals_alone_never_open_the_breaker(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    ids = tuple(str(7000 + i) for i in range(5))
    use_sources(monkeypatch, replace(SOURCES, eventbrite_organizers=ids, eventbrite_venues=()))

    def refuse(call: EventbriteCall) -> dict[str, Any]:
        raise status_error(status)

    calls = stub_eventbrite(monkeypatch, refuse, None)

    result = await refresh.run_step("eventbrite", 60)

    assert [c.ident for c in calls] == list(ids)  # every organiser was still asked
    assert "circuit open" not in str(result["errors"])
    health = await SourceHealthService.get("events:eventbrite")
    assert (health.attempts, health.consecutive_failures) == (0, 0)


@pytest.mark.parametrize("status", [401, 429])
async def test_a_refused_token_or_rate_limit_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)

    def refuse(call: EventbriteCall) -> dict[str, Any]:
        raise status_error(status)

    calls = stub_eventbrite(monkeypatch, refuse, refuse)

    result = await refresh.run_step("eventbrite", 60)

    assert len(calls) == 1
    assert str(status) in str(result["errors"])
    assert (await SourceHealthService.get("events:eventbrite")).consecutive_failures == 1


async def test_eventbrite_breaker_open_skips_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    calls = stub_eventbrite(monkeypatch)
    monkeypatch.setattr(SourceHealthService, "is_available", AsyncMock(return_value=False))

    result = await refresh.run_step("eventbrite", 60)

    assert "eventbrite: circuit open" in str(result["errors"])
    assert calls == []


async def test_eventbrite_records_job_count_against_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    stub_eventbrite(
        monkeypatch,
        lambda call: eventbrite_page(("1001", "1002")),
        lambda call: eventbrite_page(("2001",)),
    )

    await refresh.run_step("eventbrite", 60)

    health = await SourceHealthService.get("events:eventbrite")
    assert (health.attempts, health.total_jobs) == (2, 3)


async def test_the_token_never_appears_in_a_result_or_a_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)

    def refuse(call: EventbriteCall) -> dict[str, Any]:
        # An error that happened to quote everything it was handed.
        raise status_error(
            500, f"https://www.eventbriteapi.com/v3/organizers/5001/?token={call.token}"
        )

    stub_eventbrite(monkeypatch, refuse, refuse)
    with caplog.at_level(logging.DEBUG):
        failed = await refresh.run_step("eventbrite", 60)
        monkeypatch.setattr(get_settings(), "eventbrite_token", "bad token with spaces")
        invalid = await refresh.run_step("eventbrite", 60)

    for text in (json.dumps(failed), json.dumps(invalid), caplog.text):
        assert TOKEN not in text
        assert "bad token with spaces" not in text
    assert "errors" in failed


# ═════════════════════════════════════════════════════════════════════════════
# relevance
# ═════════════════════════════════════════════════════════════════════════════


async def test_relevance_step_rewrites_verdicts(world: World) -> None:
    row = make_row(listed=False, reasons=None)
    await seed(world, row)

    result = await refresh.run_step("relevance", 100)

    assert result["step"] == "relevance"
    assert result["seen"] == 1 and result["updated"] == 1
    after = await get_event(world, row.id)
    assert after.listed is True
    assert (after.reasons or [])[0] == "Rust Quill calendar"
    assert after.area == "Indiranagar"
    assert after.format == "other"


async def test_relevance_unlists_what_the_rules_no_longer_list(world: World) -> None:
    row = make_row(
        title="Pottery Evening",
        listed=True,
        reasons=["stale"],
        extra_metadata={"scope": UNTRUSTED, "pin": "placed"},
    )
    await seed(world, row)

    result = await refresh.run_step("relevance", 100)

    after = await get_event(world, row.id)
    assert after.listed is False
    assert result["unlisted"] == 1


async def test_relevance_judges_from_the_stored_metadata(world: World) -> None:
    # An Eventbrite event with a generic title: only its technology category (kept in the
    # metadata) lists it, and only its timezone makes the online one Indian.
    in_person = make_row(
        source="eventbrite",
        external_id="9001",
        title="Evening Meetup",
        listed=False,
        extra_metadata={
            "scope": {"key": "eventbrite", "india": False, "trusted_tech": False},
            "taxonomy": {"category": "102", "subcategory": "2004"},
            "pin": "placed",
        },
    )
    online = make_row(
        source="eventbrite",
        external_id="9002",
        title="Evening Workshop online",
        address=None,
        is_online=True,
        listed=False,
        extra_metadata={
            "scope": {"key": "eventbrite", "india": False, "trusted_tech": False},
            "taxonomy": {"category": "102"},
            "timezone": "Asia/Kolkata",
        },
    )
    abroad = make_row(
        source="eventbrite",
        external_id="9003",
        title="Evening Workshop online",
        address=None,
        is_online=True,
        listed=True,
        extra_metadata={
            "scope": {"key": "eventbrite", "india": False, "trusted_tech": False},
            "taxonomy": {"category": "102"},
            "timezone": "America/New_York",
        },
    )
    await seed(world, in_person, online, abroad)

    await refresh.run_step("relevance", 100)

    assert (await get_event(world, in_person.id)).listed is True
    assert (await get_event(world, in_person.id)).reasons[0] == "Eventbrite: Science & Technology"
    assert (await get_event(world, online.id)).listed is True
    assert (await get_event(world, abroad.id)).listed is False


def test_a_stated_pin_is_handed_back_and_a_placed_one_is_not() -> None:
    # The store puts a pin at the centre of the address's area when the source stated none,
    # and the verdict was made from the address: judged again, it must be again.
    pinned = {"lat": 12.9208, "lng": 77.6874}
    stated = refresh._facts_from_row(
        make_row(**pinned, extra_metadata={"scope": TRUSTED, "pin": "stated"})
    )
    placed = refresh._facts_from_row(
        make_row(**pinned, extra_metadata={"scope": TRUSTED, "pin": "placed"})
    )
    unrecorded = refresh._facts_from_row(make_row(**pinned, extra_metadata={"scope": TRUSTED}))
    assert (stated.lat, stated.lng) == (12.9208, 77.6874)
    assert (placed.lat, placed.lng) == (None, None)
    assert (unrecorded.lat, unrecorded.lng) == (None, None)  # of unrecorded origin: placed


def test_the_facts_are_rebuilt_from_the_row_and_what_the_store_kept() -> None:
    row = make_row(
        source="eventbrite",
        external_id="1001",
        url="https://www.eventbrite.com/e/1001",
        title="Evening Meetup",
        snippet="A short description.",
        ends_at=NOW + timedelta(days=3, hours=3),
        venue_name="Quill Hall",
        is_online=True,
        is_free=False,
        organiser_name="Quill Collective",
        organiser_url="https://quill.example/",
        hosts=["Ada Quill", "Ben Quill"],
        is_cancelled=True,
        extra_metadata={
            "scope": {"key": "eventbrite", "india": False, "trusted_tech": False},
            "taxonomy": {"category": "102", "subcategory": "2004"},
            "timezone": "Asia/Kolkata",
            "locality": "Bengaluru",
            "address_withheld": True,
        },
    )

    facts = refresh._facts_from_row(row)

    assert (facts.source, facts.external_id, facts.url, facts.title) == (
        "eventbrite", "1001", "https://www.eventbrite.com/e/1001", "Evening Meetup",
    )  # fmt: skip
    assert (facts.starts_at, facts.ends_at) == (row.starts_at, row.ends_at)
    assert (facts.snippet, facts.venue_name, facts.address) == (
        "A short description.", "Quill Hall", row.address,
    )  # fmt: skip
    assert (facts.is_online, facts.is_free, facts.is_cancelled) == (True, False, True)
    assert (facts.organiser_name, facts.organiser_url) == (
        "Quill Collective",
        "https://quill.example/",
    )
    assert facts.hosts == ("Ada Quill", "Ben Quill")
    assert facts.taxonomy == {"category": "102", "subcategory": "2004"}
    assert (facts.timezone, facts.locality, facts.address_withheld) == (
        "Asia/Kolkata",
        "Bengaluru",
        True,
    )
    assert refresh._scope_from_row(row) == SourceScope(
        "eventbrite", india=False, trusted_tech=False
    )


def test_a_row_with_no_metadata_is_judged_on_what_its_source_vouches_for() -> None:
    bare = make_row(source="gdg", extra_metadata=None)
    facts = refresh._facts_from_row(bare)
    assert facts.taxonomy == {} and facts.timezone is None and facts.address_withheld is False
    assert refresh._scope_from_row(bare) == SourceScope("gdg", india=True, trusted_tech=True)
    unknown = make_row(source="luma", extra_metadata=None)
    assert refresh._scope_from_row(unknown) == SourceScope("luma", india=False, trusted_tech=False)


async def test_relevance_lists_a_hiring_event_once_its_host_is_a_listed_company(
    world: World,
) -> None:
    await add_company(world, "Quillsoft")
    drive = make_row(
        title="Campus Hiring Drive",
        organiser_name="Quillsoft",
        listed=False,
        extra_metadata={"scope": TRUSTED, "pin": "placed"},
    )
    await seed(world, drive)

    await refresh.run_step("relevance", 100)

    after = await get_event(world, drive.id)
    assert after.listed is True
    assert "hosted by Quillsoft" in (after.reasons or [])


async def test_relevance_is_bounded_and_takes_the_oldest_assessed_first(world: World) -> None:
    rows = [
        make_row(external_id=f"evt-{i}", updated_at=NOW - timedelta(days=10 - i)) for i in range(5)
    ]
    await seed(world, *rows)

    result = await refresh.run_step("relevance", 2)

    assert result["seen"] == 2
    by_id = {row.external_id: row for row in await events_in(world)}
    assert by_id["evt-0"].listed is True and by_id["evt-1"].listed is True
    assert by_id["evt-2"].listed is False

    await refresh.run_step("relevance", 2)  # the next run moves on to the ones not yet looked at
    by_id = {row.external_id: row for row in await events_in(world)}
    assert by_id["evt-2"].listed is True and by_id["evt-3"].listed is True
    assert by_id["evt-4"].listed is False


async def test_looking_at_a_row_that_needs_no_change_still_moves_it_to_the_back(
    world: World,
) -> None:
    rows = [
        make_row(external_id=f"evt-{i}", updated_at=NOW - timedelta(days=10 - i)) for i in range(4)
    ]
    await seed(world, *rows)
    await refresh.run_step("relevance", 100)  # settles every verdict

    async with world.factory() as db:  # back to the old stamps; the verdicts now need no change
        for i, row in enumerate(rows):
            stored = await db.get(Event, row.id)
            assert stored is not None
            stored.updated_at = NOW - timedelta(days=10 - i)
        await db.commit()

    first = await refresh.run_step("relevance", 2)
    stamps = {row.external_id: as_utc_stamp(row.updated_at) for row in await events_in(world)}
    assert first["updated"] == 0  # nothing changed …
    assert stamps["evt-0"] == stamps["evt-1"] == NOW  # … yet the two looked at went to the back
    assert stamps["evt-2"] < NOW and stamps["evt-3"] < NOW

    await refresh.run_step("relevance", 2)
    stamps = {row.external_id: as_utc_stamp(row.updated_at) for row in await events_in(world)}
    assert all(stamp == NOW for stamp in stamps.values())


def as_utc_stamp(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def test_a_row_taken_off_the_list_is_not_matched_against_by_the_rows_after_it(
    world: World,
) -> None:
    # `earlier` no longer qualifies; `later` is a second listing of its event. The two are
    # judged in one batch, `earlier` first, and `later` must see that it has gone.
    earlier = make_row(
        **same_meetup(
            id=uuid.UUID("a0000000-0000-4000-8000-000000000001"), title="Pottery Evening", first_seen_at=NOW - timedelta(days=2),
            extra_metadata={"scope": UNTRUSTED, "pin": "stated"},
        )
    )  # fmt: skip
    later = make_row(
        **same_meetup(
            id=uuid.UUID("b0000000-0000-4000-8000-000000000002"), source="eventbrite", external_id="1001",
            title="Pottery Evening", first_seen_at=NOW - timedelta(days=1),
            extra_metadata={"scope": TRUSTED, "pin": "stated"},
        )
    )  # fmt: skip
    await seed(world, earlier, later)

    result = await refresh.run_step("relevance", 100)

    assert (await get_event(world, earlier.id)).listed is False
    assert (await get_event(world, later.id)).duplicate_of is None
    assert result["merged"] == 0 and result["promoted"] == 0


async def test_relevance_leaves_past_events_alone(world: World) -> None:
    past = make_row(starts_at=NOW - timedelta(days=5), listed=False)
    await seed(world, past)

    result = await refresh.run_step("relevance", 100)

    assert result["seen"] == 0
    assert (await get_event(world, past.id)).listed is False


async def test_relevance_leaves_private_events_alone(world: World) -> None:
    user = await add_user(world)
    mine = make_row(source="user", owner_user_id=user.id, title="Pottery Evening", listed=True)
    await seed(world, mine)

    result = await refresh.run_step("relevance", 100)

    assert result["seen"] == 0
    assert (await get_event(world, mine.id)).listed is True


def same_meetup(**changes: Any) -> dict[str, Any]:
    """Fields of the Swift × Okta meetup the store tests use as one event on two platforms."""
    base: dict[str, Any] = {
        "lat": 12.9208,
        "lng": 77.6874,
        "area": "Bellandur",
        "address": "Okta Identity India, Bellandur, Bengaluru, Karnataka, India",
        "starts_at": NOW + timedelta(days=4),
        "listed": True,
        "extra_metadata": {"scope": TRUSTED, "pin": "stated"},
    }
    base.update(changes)
    return base


async def test_an_unlisted_primary_hands_its_event_to_the_earliest_listed_duplicate(
    world: World,
) -> None:
    primary = make_row(
        **same_meetup(
            title="Pottery Evening",
            listed=True,
            extra_metadata={"scope": UNTRUSTED, "pin": "stated"},
        )
    )
    first = make_row(
        **same_meetup(
            source="eventbrite", external_id="1001", title="Swift Bengaluru Meetup at Okta",
            duplicate_of=primary.id, first_seen_at=NOW - timedelta(days=1),
        )
    )  # fmt: skip
    second = make_row(
        **same_meetup(
            source="gdg", external_id="swift-okta", title="Swift Bengaluru x Okta",
            duplicate_of=primary.id, first_seen_at=NOW - timedelta(hours=5),
        )
    )  # fmt: skip
    await seed(world, primary, first, second)

    result = await refresh.run_step("relevance", 100)

    assert (await get_event(world, primary.id)).listed is False
    assert (await get_event(world, first.id)).duplicate_of is None  # the new primary
    assert (await get_event(world, second.id)).duplicate_of == first.id
    assert result["promoted"] == 1


async def test_promotion_keeps_a_same_source_event_apart_and_leaves_private_rows(
    world: World,
) -> None:
    user = await add_user(world)
    primary = make_row(
        **same_meetup(title="Pottery Evening", extra_metadata={"scope": UNTRUSTED, "pin": "stated"})
    )
    new = make_row(
        **same_meetup(source="eventbrite", external_id="1001", duplicate_of=primary.id,
                      first_seen_at=NOW - timedelta(days=1))
    )  # fmt: skip
    sibling = make_row(
        **same_meetup(source="eventbrite", external_id="1002", duplicate_of=primary.id,
                      first_seen_at=NOW - timedelta(hours=3))
    )  # fmt: skip
    private = make_row(
        **same_meetup(source="user", owner_user_id=user.id, duplicate_of=primary.id,
                      first_seen_at=NOW - timedelta(days=3))
    )  # fmt: skip
    await seed(world, primary, new, sibling, private)

    await refresh.run_step("relevance", 100)

    assert (await get_event(world, new.id)).duplicate_of is None
    # Two Eventbrite events are two events however alike they read: never one behind the other.
    assert (await get_event(world, sibling.id)).duplicate_of is None
    # A private event is never a primary, and the step does not touch it.
    assert (await get_event(world, private.id)).duplicate_of == primary.id


async def test_a_primary_nobody_else_could_stand_in_for_changes_nothing(world: World) -> None:
    primary = make_row(
        **same_meetup(title="Pottery Evening", extra_metadata={"scope": UNTRUSTED, "pin": "stated"})
    )
    hidden = make_row(
        **same_meetup(source="eventbrite", external_id="1001", title="Pottery Evening",
                      listed=False, duplicate_of=primary.id,
                      extra_metadata={"scope": UNTRUSTED, "pin": "stated"})
    )  # fmt: skip
    await seed(world, primary, hidden)

    result = await refresh.run_step("relevance", 100)

    assert (await get_event(world, hidden.id)).duplicate_of == primary.id
    assert result["promoted"] == 0


async def test_a_duplicate_whose_primary_was_deleted_joins_the_earlier_event_again(
    world: World,
) -> None:
    # SET NULL leaves the later listing a stray primary of its own; the relevance step
    # looks at it again and puts it behind the event stored before it.
    earlier = make_row(
        **same_meetup(title="Swift Bengaluru × Okta", first_seen_at=NOW - timedelta(days=2))
    )
    stray = make_row(
        **same_meetup(
            source="eventbrite", external_id="1001", title="Swift Bengaluru Meetup at Okta",
            starts_at=NOW + timedelta(days=4, minutes=15), first_seen_at=NOW - timedelta(days=1),
        )
    )  # fmt: skip
    await seed(world, earlier, stray)

    result = await refresh.run_step("relevance", 100)

    assert (await get_event(world, stray.id)).duplicate_of == earlier.id
    assert (await get_event(world, earlier.id)).duplicate_of is None
    assert result["merged"] == 1


async def test_a_private_event_is_never_the_primary_of_a_stray(world: World) -> None:
    user = await add_user(world)
    private = make_row(
        **same_meetup(source="user", owner_user_id=user.id, title="Swift Bengaluru × Okta",
                      first_seen_at=NOW - timedelta(days=2))
    )  # fmt: skip
    public = make_row(
        **same_meetup(source="eventbrite", external_id="1001", title="Swift Bengaluru Meetup at Okta",
                      first_seen_at=NOW - timedelta(days=1))
    )  # fmt: skip
    await seed(world, private, public)

    await refresh.run_step("relevance", 100)

    assert (await get_event(world, public.id)).duplicate_of is None


async def test_relevance_never_hides_an_event_somebody_saved(world: World) -> None:
    user = await add_user(world)
    earlier = make_row(
        **same_meetup(title="Swift Bengaluru × Okta", first_seen_at=NOW - timedelta(days=2))
    )
    saved = make_row(
        **same_meetup(source="eventbrite", external_id="1001", title="Swift Bengaluru Meetup at Okta",
                      first_seen_at=NOW - timedelta(days=1))
    )  # fmt: skip
    await seed(world, earlier, saved)
    await seed(world, EventSave(user_id=user.id, event_id=saved.id, status="saved"))

    await refresh.run_step("relevance", 100)

    assert (await get_event(world, saved.id)).duplicate_of is None


async def test_relevance_commits_each_batch_on_its_own(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(refresh, "BATCH", 2)
    rows = [
        make_row(external_id=f"evt-{i}", updated_at=NOW - timedelta(days=9 - i)) for i in range(4)
    ]
    await seed(world, *rows)
    real = refresh.assess
    seen: list[str] = []

    def failing_third(facts: Any, scope: SourceScope, **kwargs: Any) -> Any:
        seen.append(facts.external_id)
        if facts.external_id == "evt-2":
            raise RuntimeError("one row is poison")
        return real(facts, scope, **kwargs)

    monkeypatch.setattr(refresh, "assess", failing_third)

    result = await refresh.run_step("relevance", 100)

    by_id = {row.external_id: row for row in await events_in(world)}
    assert by_id["evt-0"].listed is True and by_id["evt-1"].listed is True  # the first batch stood
    assert (
        by_id["evt-2"].listed is False and by_id["evt-3"].listed is False
    )  # the second rolled back
    assert "RuntimeError" in str(result["error"])


# ═════════════════════════════════════════════════════════════════════════════
# link
# ═════════════════════════════════════════════════════════════════════════════


async def test_link_step_links_upcoming_events_to_listed_companies(world: World) -> None:
    company = await add_company(world, "Quillsoft")
    event = make_row(organiser_name="Quillsoft", listed=True)
    past = make_row(starts_at=NOW - timedelta(days=5), organiser_name="Quillsoft")
    await seed(world, event, past)

    result = await refresh.run_step("link", 100)

    assert result["step"] == "link"
    assert result["seen"] == 1 and result["linked"] == 1
    async with world.factory() as db:
        links = list((await db.execute(select(EventCompany))).scalars())
    assert [(link.event_id, link.company_id) for link in links] == [(event.id, company.id)]


async def test_link_step_follows_the_map_as_it_grows(world: World) -> None:
    event = make_row(organiser_name="Quillsoft", listed=True)
    await seed(world, event)
    first = await refresh.run_step("link", 100)
    assert first["linked"] == 0  # no such company yet, and none is created
    assert await count(world, Company) == 0

    await add_company(world, "Quillsoft")
    second = await refresh.run_step("link", 100)
    assert second["linked"] == 1


async def test_link_step_is_bounded_and_skips_private_events(world: World) -> None:
    user = await add_user(world)
    await add_company(world, "Quillsoft")
    mine = make_row(source="user", owner_user_id=user.id, organiser_name="Quillsoft")
    public = [make_row(external_id=f"evt-{i}", organiser_name="Quillsoft") for i in range(5)]
    await seed(world, mine, *public)

    result = await refresh.run_step("link", 3)

    assert result["seen"] == 3
    assert result["linked"] == 3
    async with world.factory() as db:
        linked = {link.event_id for link in (await db.execute(select(EventCompany))).scalars()}
    assert mine.id not in linked


# ═════════════════════════════════════════════════════════════════════════════
# prune and purge
# ═════════════════════════════════════════════════════════════════════════════


async def test_prune_step_runs_the_retention_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    prune = AsyncMock(return_value=7)
    monkeypatch.setattr(job_retention, "prune_events", prune)

    result = await refresh.run_step("prune", 1)

    assert result == {"step": "prune", "deleted": 7}
    assert prune.await_count == 1


async def test_prune_step_deletes_what_the_rule_deletes_and_keeps_what_a_user_kept(
    world: World,
) -> None:
    user = await add_user(world)
    when = datetime.now(UTC) - timedelta(days=30)
    old = make_row(starts_at=when, ends_at=when + timedelta(hours=2), listed=True)
    saved = make_row(starts_at=when, ends_at=when + timedelta(hours=2), listed=True)
    await seed(world, old, saved)
    await seed(world, EventSave(user_id=user.id, event_id=saved.id, status="saved"))

    result = await refresh.run_step("prune", 1)

    assert result == {"step": "prune", "deleted": 1}
    async with world.factory() as db:
        assert set((await db.execute(select(Event.id))).scalars()) == {saved.id}


async def test_a_failing_prune_is_an_error_not_a_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_retention, "prune_events", AsyncMock(side_effect=RuntimeError("disk")))
    result = await refresh.run_step("prune", 1)
    assert result["step"] == "prune" and "RuntimeError" in str(result["error"])


@pytest.mark.parametrize("source", [None, "", "luma", "Eventbrite", "gdg"])
async def test_purge_needs_source_eventbrite(world: World, source: str | None) -> None:
    await seed(world, make_row(source="eventbrite", external_id="1001"))

    result = await refresh.run_step("purge", 1, source=source)

    assert result["step"] == "purge" and "eventbrite" in str(result["error"])
    assert await count(world, Event) == 1


async def test_purge_deletes_only_unwanted_eventbrite_rows(world: World) -> None:
    user = await add_user(world)
    plain = make_row(source="eventbrite", external_id="1001")
    saved = make_row(source="eventbrite", external_id="1002")
    noted = make_row(source="eventbrite", external_id="1003")
    luma_row = make_row(source="luma", external_id="evt-keep")
    stray = make_row(source="luma", external_id="evt-behind", duplicate_of=plain.id)
    await seed(world, plain, saved, noted, luma_row)
    await seed(world, stray)
    await seed(
        world,
        EventSave(user_id=user.id, event_id=saved.id, status="going"),
        EventContact(user_id=user.id, event_id=noted.id, name="Ada Quill"),
    )

    result = await refresh.run_step("purge", 1, source="eventbrite")

    assert result == {"step": "purge", "deleted": 1}
    assert {row.external_id for row in await events_in(world)} == {
        "1002", "1003", "evt-keep", "evt-behind",
    }  # fmt: skip
    # What hid behind a purged row is no longer hidden.
    assert (await get_event(world, stray.id)).duplicate_of is None


async def test_purge_is_bounded(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    monkeypatch.setattr(refresh, "PURGE_CAP", 2)
    await seed(world, *(make_row(source="eventbrite", external_id=str(1000 + i)) for i in range(5)))

    first = await refresh.run_step("purge", 1, source="eventbrite")
    assert first["deleted"] == 2
    assert await count(world, Event) == 3

    while (await refresh.run_step("purge", 1, source="eventbrite"))["deleted"]:
        pass
    assert await count(world, Event) == 0


async def test_purge_never_deletes_a_private_event(world: World) -> None:
    # A pasted Eventbrite link is stored as an eventbrite row that belongs to its user.
    user = await add_user(world)
    mine = make_row(source="eventbrite", external_id="9001", owner_user_id=user.id)
    public = make_row(source="eventbrite", external_id="9002")
    await seed(world, mine, public)

    result = await refresh.run_step("purge", 1, source="eventbrite")

    assert result == {"step": "purge", "deleted": 1}
    assert [row.external_id for row in await events_in(world)] == ["9001"]


# ═════════════════════════════════════════════════════════════════════════════
# the wall-clock budget
# ═════════════════════════════════════════════════════════════════════════════


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def tick_on(
    monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, clock: Clock, seconds: float
) -> None:
    """Make every call of ``owner.name`` take ``seconds`` of ``clock`` (wrap it after stubbing)."""
    genuine = getattr(owner, name)

    async def slow(*args: Any, **kwargs: Any) -> Any:
        try:
            return await genuine(*args, **kwargs)
        finally:
            clock.t += seconds

    monkeypatch.setattr(owner, name, slow)


def tick_per_batch(
    monkeypatch: pytest.MonkeyPatch, name: str, clock: Clock, seconds: float
) -> list[int]:
    """Make each unit built by ``refresh.<name>`` take ``seconds``; the sizes of the batches run."""
    genuine = getattr(refresh, name)
    ran: list[int] = []

    def build(ids: list[uuid.UUID], *rest: Any) -> Any:
        work = genuine(ids, *rest)

        async def timed(session: AsyncSession) -> dict[str, int]:
            ran.append(len(ids))
            clock.t += seconds
            counts: dict[str, int] = await work(session)
            return counts

        return timed

    monkeypatch.setattr(refresh, name, build)
    return ran


def test_the_step_budget_is_eight_minutes_and_sits_under_the_workflows_timeout() -> None:
    workflow = (Path(__file__).resolve().parent.parent / ".github/workflows/events.yml").read_text(
        "utf-8"
    )
    assert refresh.STEP_BUDGET_SECONDS == 480
    assert "--max-time 600" in workflow


async def test_luma_stops_starting_feeds_once_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    clock = Clock()
    calls = stub_luma(monkeypatch)
    tick_on(monkeypatch, luma, "fetch_feed", clock, 100)
    monkeypatch.setattr(refresh, "STEP_BUDGET_SECONDS", 150)

    result = await refresh.run_step("luma", 26, clock=clock)

    assert calls == [luma.feed_url(feed.kind, feed.id) for feed in SOURCES.luma[:2]]
    assert result["feeds"] == 2
    assert result["stopped"] == "budget"
    assert "error" not in result
    # What was committed before the stop stays, and is not read again.
    assert 0 < int(result["created"]) == await count(world, Event)
    second = await refresh.run_step("luma", 26, clock=Clock())
    assert second["feeds"] == 1
    assert "stopped" not in second


@pytest.mark.parametrize(("budget", "pages"), [(150, 2), (50, 1)])
async def test_gdg_stops_starting_requests_once_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, budget: float, pages: int
) -> None:
    clock = Clock()
    fetched = stub_gdg(monkeypatch)
    tick_on(monkeypatch, gdg, "fetch_page", clock, 100)
    monkeypatch.setattr(refresh, "STEP_BUDGET_SECONDS", budget)

    result = await refresh.run_step("gdg", 30, clock=clock)

    # Chapter pages first; the event page (and a second chapter) wait for the next run.
    assert fetched == [gdg.chapter_url(c.slug) for c in SOURCES.gdg][:pages]
    assert result["pages"] == pages
    assert result["stopped"] == "budget"


async def test_eventbrite_stops_starting_calls_once_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    follows = {None: "c1", "c1": "c2", "c2": None}
    ids = {None: ("1001",), "c1": ("1002",), "c2": ("1003",)}

    def organizers(call: EventbriteCall) -> dict[str, Any]:
        return eventbrite_page(ids[call.continuation], cursor=follows[call.continuation])

    clock = Clock()
    calls = stub_eventbrite(monkeypatch, organizers, lambda call: {"events": []})
    tick_on(monkeypatch, eventbrite, "organizer_events", clock, 100)
    monkeypatch.setattr(refresh, "STEP_BUDGET_SECONDS", 150)

    result = await refresh.run_step("eventbrite", 60, clock=clock)

    assert [(c.kind, c.continuation) for c in calls] == [("organizers", None), ("organizers", "c1")]
    assert result["calls"] == 2
    assert result["stopped"] == "budget"
    assert 0 < int(result["created"]) == await count(world, Event)


@pytest.mark.parametrize(
    ("step", "builder"), [("link", "_link_batch"), ("relevance", "_reassess_batch")]
)
async def test_link_and_relevance_stop_between_batches_once_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, world: World, step: str, builder: str
) -> None:
    monkeypatch.setattr(refresh, "BATCH", 1)
    await seed(world, *(make_row(starts_at=NOW + timedelta(days=3 + i)) for i in range(4)))
    clock = Clock()
    ran = tick_per_batch(monkeypatch, builder, clock, 100)
    monkeypatch.setattr(refresh, "STEP_BUDGET_SECONDS", 150)

    result = await refresh.run_step(step, 100, clock=clock)

    assert ran == [1, 1]
    assert result["seen"] == 2
    assert result["stopped"] == "budget"


async def test_relevance_stops_promoting_once_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    promoted: list[uuid.UUID] = []

    def promote(primary_id: uuid.UUID) -> Any:
        async def work(_session: AsyncSession) -> dict[str, int]:
            promoted.append(primary_id)
            clock.t += 100
            return {"promoted": 0}

        return work

    strays = [uuid.uuid4() for _ in range(3)]
    monkeypatch.setattr(refresh, "_stranded_primaries", AsyncMock(return_value=strays))
    monkeypatch.setattr(refresh, "_promote", promote)
    monkeypatch.setattr(refresh, "STEP_BUDGET_SECONDS", 150)

    result = await refresh.run_step("relevance", 50, clock=clock)

    assert promoted == strays[:2]
    assert result["stopped"] == "budget"


@pytest.mark.parametrize("step", ["luma", "gdg", "eventbrite", "link", "relevance"])
async def test_a_spent_budget_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, world: World, step: str
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    monkeypatch.setattr(refresh, "STEP_BUDGET_SECONDS", 0)
    asked = [stub_luma(monkeypatch), stub_gdg(monkeypatch), stub_eventbrite(monkeypatch)]
    await seed(world, make_row())

    result = await refresh.run_step(step, 100)  # the real clock: a budget of 0 is over at once

    assert result["stopped"] == "budget"
    assert asked == [[], [], []]
    assert all(value == 0 for value in result.values() if isinstance(value, int))


# ═════════════════════════════════════════════════════════════════════════════
# one bad item does not cost the rest
# ═════════════════════════════════════════════════════════════════════════════


async def test_a_luma_feed_the_parser_chokes_on_costs_that_feed_only(
    monkeypatch: pytest.MonkeyPatch, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    use_sources(monkeypatch, SourceList((DISCOVER, SWIFT), (), (), ()))
    stub_luma(monkeypatch)
    genuine = luma.parse_feed

    def parse(body: bytes) -> Any:
        if body == FEEDS[DISCOVER.id]:
            raise ValueError("odd input SECRET-DETAIL")
        return genuine(body)

    monkeypatch.setattr(luma, "parse_feed", parse)
    with caplog.at_level(logging.WARNING):
        result = await refresh.run_step("luma", 26)

    assert result["feeds"] == 2
    assert "error" not in result  # a known kind of trouble, not a crash of the step
    assert f"luma {DISCOVER.name}: " in str(result["errors"])
    assert "ValueError" in str(result["errors"])
    assert "SECRET-DETAIL" not in json.dumps(result)
    assert "ValueError" in caplog.text
    # The other feed was stored whole.
    wanted = {fact.external_id for fact in genuine(FEEDS[SWIFT.id])}
    stored = {row.external_id for row in await events_in(world)}
    assert stored and stored <= wanted
    assert int(result["created"]) == len(stored)
    # It counts against the source's health like any other failed read.
    health = await SourceHealthService.get("events:luma")
    assert (health.attempts, health.successes) == (2, 1)
    assert health.last_error == "error: ValueError"


async def test_a_gdg_chapter_the_parser_chokes_on_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    pages = gdg_pages()
    stub_gdg(monkeypatch, pages)
    genuine = gdg.parse_chapter

    def parse(body: bytes) -> Any:
        if body == pages[gdg.chapter_url(GDG_BLR.slug)]:
            raise KeyError("odd input")
        return genuine(body)

    monkeypatch.setattr(gdg, "parse_chapter", parse)

    result = await refresh.run_step("gdg", 30)

    assert "KeyError" in str(result["errors"])
    assert "error" not in result
    assert result["created"] == 1  # the other chapter's event
    health = await SourceHealthService.get("events:gdg")
    assert (health.attempts, health.successes) == (3, 2)  # the chapter that failed, then two reads
    assert health.last_error == "error: KeyError"


async def test_a_gdg_event_page_the_parser_chokes_on_is_counted_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    fetched = stub_gdg(monkeypatch)

    def parse(body: bytes, **kwargs: Any) -> Any:
        raise TypeError("odd input")

    monkeypatch.setattr(gdg, "parse_event_page", parse)

    first = await refresh.run_step("gdg", 30)
    fetched.clear()
    await refresh.run_step("gdg", 30)

    assert "TypeError" in str(first["errors"]) and "error" not in first
    assert first["created"] == 0 and first["skipped"] == 1
    # Our parser's failure is not a page that states no event: it is read again once fixed.
    assert gdg.event_page_url(DEVFEST) in fetched


async def test_an_eventbrite_page_the_parser_chokes_on_does_not_stop_the_venues(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    monkeypatch.setattr(get_settings(), "eventbrite_token", TOKEN)
    calls = stub_eventbrite(
        monkeypatch,
        lambda call: eventbrite_page(("1001", "1002"), cursor="more"),
        lambda call: eventbrite_page(("2001",)),
    )
    genuine = eventbrite.parse_event_list

    def parse(payload: dict[str, Any]) -> Any:
        ids = [str(e["id"]) for e in payload["events"]]
        if "1001" in ids:
            raise ValueError("odd input")
        return genuine(payload)

    monkeypatch.setattr(eventbrite, "parse_event_list", parse)

    result = await refresh.run_step("eventbrite", 60)

    assert [(c.kind, c.continuation) for c in calls] == [("organizers", None), ("venues", None)]
    assert "ValueError" in str(result["errors"]) and "error" not in result
    assert [row.external_id for row in await events_in(world)] == ["2001"]
    health = await SourceHealthService.get("events:eventbrite")
    assert (health.attempts, health.successes) == (2, 1)


# ═════════════════════════════════════════════════════════════════════════════
# an error names its class and nothing else
# ═════════════════════════════════════════════════════════════════════════════

FAKE_SQL = "SELECT users.hashed_password FROM users WHERE users.email = 'ada@example.test'"
LEAKS = ("SELECT", "hashed_password", "ada@example.test", "connection string")


def test_a_fault_carries_the_exception_class_and_not_its_text() -> None:
    report = refresh._Report.of("link")
    report.broke(SQLAlchemyError(FAKE_SQL))
    report.broke(OperationalError(FAKE_SQL, {"email": "ada@example.test"}, Exception("conn")))

    result = report.as_dict()

    assert result["error"] == "SQLAlchemyError"
    assert result["errors"] == "OperationalError"


async def test_a_database_error_outside_a_unit_reports_its_class_and_logs_the_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    boom = OperationalError(FAKE_SQL, {"email": "ada@example.test"}, Exception("connection string"))
    monkeypatch.setattr(refresh, "_upcoming_ids", AsyncMock(side_effect=boom))

    with caplog.at_level(logging.WARNING):
        result = await refresh.run_step("link", 10)

    assert result == {"step": "link", "seen": 0, "linked": 0, "error": "OperationalError"}
    for leak in LEAKS:
        assert leak not in json.dumps(result)
    assert "hashed_password" in caplog.text  # the detail is for the log


async def test_a_database_error_in_a_unit_reports_its_class_and_logs_the_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stub_luma(monkeypatch)
    monkeypatch.setattr(refresh, "upsert_event", AsyncMock(side_effect=SQLAlchemyError(FAKE_SQL)))

    with caplog.at_level(logging.WARNING):
        result = await refresh.run_step("luma", 26)

    assert "SQLAlchemyError" in str(result["errors"])
    for leak in LEAKS:
        assert leak not in json.dumps(result)
    assert "hashed_password" in caplog.text
