"""
tests/test_event_store.py
~~~~~~~~~~~~~~~~~~~~~~~~~
The events store (``services/events/store.py``): what a sighting of an event
becomes in the database.

* :func:`refine_facts` is pure and is tested without a database.
* Everything else runs on in-memory SQLite with ``PRAGMA foreign_keys=ON``, so
  ``ON DELETE SET NULL`` and the cascades are the database's own. The session
  mirrors production (``expire_on_commit`` and ``autoflush`` both off), so a
  store that forgets to flush fails here too.

Every person, organiser and company in the data is fictional, except the public
company names (Okta, Giottus, BitGo) that event titles are built around.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy import event as sa_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.areas import in_bengaluru
from domain.event_facts import EventFacts, SourceScope
from domain.event_identity import START_WINDOW
from domain.event_time import as_utc
from services.events import store
from services.events.store import (
    DEDUPE_WINDOW,
    HORIZON_DAYS,
    UpsertResult,
    find_duplicate,
    link_companies,
    refine_facts,
    resolve_companies,
    upsert_event,
)
from storage.database import Base
from storage.models import Company, Event, EventCompany, EventSave, User

TABLES = ("users", "companies", "events", "event_companies", "event_saves", "event_contacts")

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # Thu 15 Oct, 11:30 IST
START = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
BELLANDUR = (12.9208, 77.6874)
# 0.0027 degrees of latitude is about 300 m.
BELLANDUR_300M_NORTH = (12.9235, 77.6874)
SAN_FRANCISCO = (37.7749, -122.4194)

LUMA = SourceScope(key="Luma Bengaluru", india=True, trusted_tech=False)
EVENTBRITE = SourceScope(key="eventbrite", india=False, trusted_tech=False)
GDG = SourceScope(key="gdg-bangalore", india=True, trusted_tech=True)


def make_facts(**changes: Any) -> EventFacts:
    """A Luma meetup at an Okta office in Bellandur, with every field a test may vary."""
    base = EventFacts(
        source="luma",
        external_id="evt-okta-1",
        url="https://luma.com/okta-swift",
        title="Swift Bengaluru × Okta",
        starts_at=START,
        ends_at=START + timedelta(hours=2),
        snippet="An evening of Swift talks.",
        venue_name="Okta Identity India",
        address="Okta Identity India, Bellandur, Bengaluru, Karnataka, India",
        lat=BELLANDUR[0],
        lng=BELLANDUR[1],
        organiser_name="Swift Bengaluru",
    )
    return replace(base, **changes)


def eventbrite_twin(**changes: Any) -> EventFacts:
    """The same meetup as :func:`make_facts`, as Eventbrite words it, 15 minutes later."""
    return make_facts(
        source="eventbrite",
        external_id="1001",
        url="https://www.eventbrite.com/e/1001",
        title="Swift Bengaluru Meetup at Okta",
        starts_at=START + timedelta(minutes=15),
        ends_at=None,
        lat=BELLANDUR_300M_NORTH[0],
        lng=BELLANDUR_300M_NORTH[1],
        organiser_name="Okta India",
        **changes,
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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
    async with factory() as db:
        yield db
    await engine.dispose()


async def add_company(db: AsyncSession, name: str, **fields: Any) -> Company:
    """A listed tech employer (``is_tech`` and a summary) unless the test says otherwise."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": name,
        "is_tech": True,
        "summary": f"What {name} makes, in one line.",
    }
    values.update(fields)
    company = Company(**values)
    db.add(company)
    await db.commit()
    return company


async def add_user(db: AsyncSession) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x")
    db.add(user)
    await db.commit()
    return user


async def count(db: AsyncSession, model: type) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def upsert(
    db: AsyncSession,
    facts: EventFacts,
    scope: SourceScope = LUMA,
    *,
    now: datetime = NOW,
) -> UpsertResult:
    result = await upsert_event(db, facts, scope, now=now)
    await db.commit()
    return result


def stored(result: UpsertResult) -> Event:
    assert result.event is not None, result.skipped
    return result.event


# ── refine_facts: pure ───────────────────────────────────────────────────────


def test_constants_match_the_brief() -> None:
    assert HORIZON_DAYS == 90
    assert timedelta(minutes=45) == DEDUPE_WINDOW == START_WINDOW


def test_column_widths_match_the_model() -> None:
    columns = Event.__table__.c
    widths = {
        "source": store._SOURCE_MAX,
        "external_id": store._EXTERNAL_ID_MAX,
        "url": store._URL_MAX,
        "title": store._TITLE_MAX,
        "snippet": store._SNIPPET_MAX,
        "venue_name": store._VENUE_MAX,
        "address": store._ADDRESS_MAX,
        "area": store._AREA_MAX,
        "organiser_name": store._ORGANISER_MAX,
        "organiser_url": store._ORGANISER_URL_MAX,
    }
    for name, width in widths.items():
        assert getattr(columns[name].type, "length", None) == width, name


@pytest.mark.parametrize(
    "title",
    [
        "Swift Developers Meetup - Online (Dec 2025)",
        "ONLINE hackathon",
        "Rust Workshop (online)",
        "Online: Intro to Kubernetes",
    ],
)
def test_online_in_the_title_without_a_place_is_online(title: str) -> None:
    facts = make_facts(title=title, lat=None, lng=None, address=None, venue_name=None)
    assert refine_facts(facts).is_online is True


@pytest.mark.parametrize(
    "title",
    [
        "Swift Developers Meetup - Online/Offline hybrid",
        "Rust Night: in-person, with an online stream",
        "Online round, offline finale",
        "Hybrid AI Summit (online + in person)",
        "Onlinemart Engineering Meetup",
        "Swift Meetup",
    ],
)
def test_online_flip_needs_the_word_and_no_in_person_wording(title: str) -> None:
    facts = make_facts(title=title, lat=None, lng=None)
    assert refine_facts(facts).is_online is False


def test_online_in_the_title_does_not_override_a_stated_position() -> None:
    facts = make_facts(title="Online Safety Workshop")  # has Bellandur coordinates
    assert refine_facts(facts).is_online is False


def test_a_source_that_says_online_stays_online() -> None:
    facts = make_facts(title="Swift Meetup", is_online=True, lat=None, lng=None)
    assert refine_facts(facts).is_online is True


@pytest.mark.parametrize(
    "text",
    [
        "The exact location will be shared once the invite is accepted",
        "The exact location will be shared after the invite is accepted.",
        "Exact address will be sent to you once your registration is confirmed.",
        "the EXACT VENUE is revealed upon approval",
    ],
)
def test_exact_location_shared_later_is_address_withheld(text: str) -> None:
    facts = make_facts(snippet=f"Doors open at 6 PM. {text}", venue_name="Indiranagar")
    refined = refine_facts(facts)
    assert refined.address_withheld is True
    assert refined.venue_name == "Indiranagar"  # the area hint stays


def test_the_phrase_is_found_in_the_address_too() -> None:
    facts = make_facts(snippet=None, address="Indiranagar. Exact location shared after approval")
    assert refine_facts(facts).address_withheld is True


@pytest.mark.parametrize(
    "text",
    [
        "Exact location: 12th Main, Indiranagar. Slides will be shared after the talk.",
        "We will share our exact learnings once the invite goes out.",
        "Location will be shared with attendees.",
        "The exact location of the venue is on the map.",
    ],
)
def test_other_sentences_do_not_withhold_the_address(text: str) -> None:
    facts = make_facts(snippet=text)
    assert refine_facts(facts).address_withheld is False


def test_an_event_that_needs_no_refining_comes_back_unchanged() -> None:
    facts = make_facts()
    assert refine_facts(facts) is facts


def test_an_end_outside_2000_to_2100_is_dropped() -> None:
    far = datetime(9999, 12, 31, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert refine_facts(make_facts(ends_at=far)).ends_at is None
    assert refine_facts(make_facts(ends_at=datetime(1970, 1, 1, tzinfo=UTC))).ends_at is None
    assert refine_facts(make_facts()).ends_at == START + timedelta(hours=2)


def test_refine_never_raises_on_hostile_facts() -> None:
    hostile = make_facts(
        title=chr(0) * 10 + "ONLINE" + chr(0x202E) * 5,
        snippet="exact location " * 30_000,
        address="Online" * 10_000,
        venue_name="",
        lat=float("nan"),
        lng=float("inf"),
        hosts=("",) * 50,
        ends_at=datetime.max.replace(tzinfo=UTC),
    )
    started = time.perf_counter()
    refined = refine_facts(hostile)
    assert time.perf_counter() - started < 1.0
    assert isinstance(refined, EventFacts)


# ── upsert: identity, idempotence, nulls ─────────────────────────────────────


async def test_upsert_is_idempotent(session: AsyncSession) -> None:
    facts = make_facts()
    first = await upsert(session, facts)
    second = await upsert(session, facts, now=NOW + timedelta(hours=1))

    assert first.created is True and second.created is False
    assert stored(first).id == stored(second).id
    assert await count(session, Event) == 1
    row = stored(second)
    assert as_utc(row.first_seen_at) == NOW
    assert as_utc(row.last_seen_at) == NOW + timedelta(hours=1)
    assert first.skipped is None and second.skipped is None


async def test_a_new_sighting_updates_what_the_source_changed(session: AsyncSession) -> None:
    await upsert(session, make_facts())
    moved = START + timedelta(days=1)
    second = await upsert(
        session,
        make_facts(
            title="Swift Bengaluru × Okta (moved)",
            starts_at=moved,
            ends_at=moved + timedelta(hours=3),
            is_free=True,
        ),
    )
    row = stored(second)
    assert row.title == "Swift Bengaluru × Okta (moved)"
    assert as_utc(row.starts_at) == moved
    assert as_utc(row.ends_at) == moved + timedelta(hours=3)  # type: ignore[arg-type]
    assert row.is_free is True
    assert row.listed is True and row.format == "talk"  # the description says "talks"
    assert row.area == "Bellandur"
    assert row.reasons and "in Bellandur" in row.reasons


async def test_null_does_not_erase(session: AsyncSession) -> None:
    await upsert(session, make_facts(hosts=("Zeta Labs",), is_free=True))
    sparse = make_facts(
        venue_name=None,
        snippet=None,
        ends_at=None,
        organiser_name=None,
        organiser_url=None,
        hosts=(),
        is_free=None,
        title="Swift Bengaluru × Okta, second sighting",
    )
    row = stored(await upsert(session, sparse, now=NOW + timedelta(hours=2)))

    assert row.title == "Swift Bengaluru × Okta, second sighting"  # the update was applied
    assert row.venue_name == "Okta Identity India"
    assert row.snippet == "An evening of Swift talks."
    assert as_utc(row.ends_at) == START + timedelta(hours=2)  # type: ignore[arg-type]
    assert row.organiser_name == "Swift Bengaluru"
    assert list(row.hosts or []) == ["Zeta Labs"]
    assert row.is_free is True
    assert (row.lat, row.lng) == BELLANDUR


async def test_a_stated_value_replaces_the_stored_one(session: AsyncSession) -> None:
    await upsert(session, make_facts(venue_name="Okta Identity India", is_free=False))
    row = stored(await upsert(session, make_facts(venue_name="Okta Campus 31", is_free=True)))
    assert row.venue_name == "Okta Campus 31"
    assert row.is_free is True


# ── cancellation ─────────────────────────────────────────────────────────────


async def test_absent_from_feed_is_not_cancelled(session: AsyncSession) -> None:
    first = stored(await upsert(session, make_facts(external_id="evt-a")))
    await upsert(session, make_facts(external_id="evt-b", title="Rust Bengaluru Meetup"))

    await session.refresh(first)
    assert first.is_cancelled is False and first.listed is True

    cancelled = await upsert(session, make_facts(external_id="evt-a", is_cancelled=True))
    assert stored(cancelled).is_cancelled is True

    again = await upsert(session, make_facts(external_id="evt-a", is_cancelled=False))
    assert stored(again).is_cancelled is False  # the source lists it uncancelled again


async def test_a_cancelled_event_can_be_stored_cancelled(session: AsyncSession) -> None:
    result = await upsert(session, make_facts(is_cancelled=True))
    assert stored(result).is_cancelled is True


# ── when: bad time, horizon, past ────────────────────────────────────────────


@pytest.mark.parametrize("source", ["luma", "gdg", "eventbrite"])
@pytest.mark.parametrize(
    "starts_at",
    [
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1999, 12, 31, 23, 59, tzinfo=UTC),
        datetime(2101, 1, 1, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, tzinfo=timezone(timedelta(hours=-5))),
        datetime.max.replace(tzinfo=UTC),
    ],
)
async def test_a_start_outside_2000_to_2100_is_bad_time(
    session: AsyncSession, source: str, starts_at: datetime
) -> None:
    result = await upsert(session, make_facts(source=source, starts_at=starts_at, ends_at=None))
    assert result.skipped == "bad_time"
    assert result.event is None and result.created is False
    assert await count(session, Event) == 0


async def test_an_end_that_would_overflow_is_dropped_not_raised(session: AsyncSession) -> None:
    far = datetime(9999, 12, 31, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
    row = stored(await upsert(session, make_facts(ends_at=far)))
    assert row.ends_at is None


async def test_horizon_and_past_skipped(session: AsyncSession) -> None:
    far = await upsert(session, make_facts(starts_at=NOW + timedelta(days=120), ends_at=None))
    assert far.skipped == "horizon" and far.event is None

    over = await upsert(
        session,
        make_facts(
            external_id="evt-over",
            starts_at=NOW - timedelta(hours=3),
            ends_at=NOW - timedelta(hours=1),
        ),
    )
    assert over.skipped == "ended" and over.event is None

    assert await count(session, Event) == 0


async def test_the_horizon_is_exactly_90_days(session: AsyncSession) -> None:
    edge = NOW + timedelta(days=HORIZON_DAYS)
    inside = await upsert(session, make_facts(external_id="evt-in", starts_at=edge, ends_at=None))
    outside = await upsert(
        session,
        make_facts(external_id="evt-out", starts_at=edge + timedelta(seconds=1), ends_at=None),
    )
    assert inside.skipped is None and inside.created is True
    assert outside.skipped == "horizon"


async def test_with_no_end_a_start_in_the_past_has_ended(session: AsyncSession) -> None:
    past = await upsert(
        session, make_facts(external_id="evt-p", starts_at=NOW - timedelta(minutes=1), ends_at=None)
    )
    soon = await upsert(
        session, make_facts(external_id="evt-s", starts_at=NOW + timedelta(minutes=1), ends_at=None)
    )
    assert past.skipped == "ended"
    assert soon.created is True


async def test_an_event_still_running_is_stored(session: AsyncSession) -> None:
    running = make_facts(starts_at=NOW - timedelta(hours=5), ends_at=NOW + timedelta(hours=3))
    assert (await upsert(session, running)).created is True


async def test_an_end_before_the_start_is_a_data_error_not_an_end(session: AsyncSession) -> None:
    backwards = make_facts(starts_at=NOW - timedelta(hours=5), ends_at=NOW - timedelta(hours=6))
    assert (await upsert(session, backwards)).skipped == "ended"  # judged by the start


async def test_a_stored_event_that_has_since_ended_is_left_alone(session: AsyncSession) -> None:
    first = stored(await upsert(session, make_facts()))
    late = await upsert(
        session, make_facts(title="Changed after the event"), now=START + timedelta(days=1)
    )
    assert late.skipped == "ended"
    await session.refresh(first)
    assert first.title == "Swift Bengaluru × Okta"


# ── where: location ──────────────────────────────────────────────────────────


async def test_outside_bengaluru_not_stored(session: AsyncSession) -> None:
    result = await upsert(
        session,
        make_facts(lat=SAN_FRANCISCO[0], lng=SAN_FRANCISCO[1], address="Mission St, San Francisco"),
    )
    assert result.skipped == "location" and result.event is None
    assert await count(session, Event) == 0


async def test_online_without_an_india_signal_is_not_stored(session: AsyncSession) -> None:
    abroad = make_facts(
        title="Swift Workshop",
        snippet="An evening of Swift.",
        is_online=True,
        lat=None,
        lng=None,
        address=None,
        venue_name=None,
    )
    result = await upsert(session, abroad, SourceScope("global", india=False, trusted_tech=False))
    assert result.skipped == "location"


async def test_a_moved_out_of_town_sighting_leaves_the_stored_row_alone(
    session: AsyncSession,
) -> None:
    first = stored(await upsert(session, make_facts()))
    result = await upsert(session, make_facts(lat=SAN_FRANCISCO[0], lng=SAN_FRANCISCO[1]))
    assert result.skipped == "location"
    await session.refresh(first)
    assert (first.lat, first.lng) == BELLANDUR


async def test_refine_runs_before_assess(session: AsyncSession) -> None:
    """A Luma "Online" hackathon with no GEO is online from India, not a location miss."""
    facts = make_facts(
        title="Agents Hackathon - Online",
        lat=None,
        lng=None,
        address=None,
        venue_name=None,
        is_online=False,
    )
    result = await upsert(session, facts)
    row = stored(result)
    assert row.is_online is True
    assert row.format == "hackathon" and row.listed is True
    assert row.geo_precision == "none"


async def test_withheld_address_is_kept_as_an_area_hint(session: AsyncSession) -> None:
    facts = make_facts(
        title="Agents Meetup",
        venue_name="Indiranagar",
        address="Indiranagar, Bengaluru, Karnataka, India",
        snippet="The exact location will be shared once the invite is accepted.",
    )
    row = stored(await upsert(session, facts))
    assert row.venue_name == "Indiranagar"
    assert row.area == "Indiranagar"
    assert (row.extra_metadata or {}).get("address_withheld") is True


# ── geo precision (spec 6.4) ─────────────────────────────────────────────────


async def test_eventbrite_venue_coordinates_are_exact(session: AsyncSession) -> None:
    row = stored(await upsert(session, eventbrite_twin(), EVENTBRITE))
    assert row.geo_precision == "exact"
    assert (row.lat, row.lng) == BELLANDUR_300M_NORTH


async def test_a_luma_pin_is_an_area_not_a_venue(session: AsyncSession) -> None:
    row = stored(await upsert(session, make_facts()))
    assert row.geo_precision == "area"
    assert (row.lat, row.lng) == BELLANDUR


async def test_a_postal_address_is_placed_at_its_area_with_jitter(session: AsyncSession) -> None:
    gdg = make_facts(
        source="gdg",
        external_id="build-with-gemini-bangalore",
        url="https://gdg.community.dev/events/details/build-with-gemini-bangalore/",
        title="Build with Gemini",
        lat=None,
        lng=None,
        venue_name="Koramangala Hub",
        address="Koramangala 4th Block, Bengaluru, Karnataka, India",
    )
    row = stored(await upsert(session, gdg, GDG))
    assert row.area == "Koramangala"
    assert row.geo_precision == "area"
    assert row.lat is not None and row.lng is not None and in_bengaluru(row.lat, row.lng)
    placed = (row.lat, row.lng)

    again = stored(await upsert(session, gdg, GDG, now=NOW + timedelta(hours=1)))
    assert (again.lat, again.lng) == placed  # a sighting does not move the pin

    other = stored(await upsert(session, replace(gdg, external_id="another-event"), GDG))
    assert (other.lat, other.lng) != placed  # the jitter is per event


async def test_a_city_only_address_cannot_be_placed(session: AsyncSession) -> None:
    gdg = make_facts(
        source="gdg",
        external_id="city-only",
        title="Cloud Study Jam",
        lat=None,
        lng=None,
        venue_name=None,
        address="Bengaluru, Karnataka, India",
    )
    row = stored(await upsert(session, gdg, GDG))
    assert row.geo_precision == "none"
    assert row.lat is None and row.lng is None


async def test_an_online_event_is_not_on_the_map(session: AsyncSession) -> None:
    online = make_facts(
        title="Agents Workshop", is_online=True, lat=None, lng=None, address=None, venue_name=None
    )
    assert stored(await upsert(session, online)).geo_precision == "none"


async def test_a_later_sighting_without_a_position_keeps_the_pin(session: AsyncSession) -> None:
    await upsert(session, eventbrite_twin(), EVENTBRITE)
    without = eventbrite_twin()
    without = replace(without, lat=None, lng=None, address="Bellandur, Bengaluru")
    row = stored(await upsert(session, without, EVENTBRITE))
    assert row.geo_precision == "exact"
    assert (row.lat, row.lng) == BELLANDUR_300M_NORTH


# ── metadata: what the relevance step needs to judge a row again ─────────────


async def test_the_inputs_of_the_verdict_are_kept_in_metadata(session: AsyncSession) -> None:
    facts = eventbrite_twin(
        taxonomy={"category": "102", "subcategory": "2004"},
        timezone="Asia/Kolkata",
        locality="Bengaluru",
    )
    row = stored(await upsert(session, facts, EVENTBRITE))
    meta = row.extra_metadata or {}
    assert meta["taxonomy"] == {"category": "102", "subcategory": "2004"}
    assert meta["timezone"] == "Asia/Kolkata"
    assert meta["locality"] == "Bengaluru"
    assert meta["scope"] == {"key": "eventbrite", "india": False, "trusted_tech": False}

    sparse = stored(await upsert(session, eventbrite_twin(), EVENTBRITE))
    kept = sparse.extra_metadata or {}
    assert kept["taxonomy"] == {"category": "102", "subcategory": "2004"}
    assert kept["timezone"] == "Asia/Kolkata"


# ── companies ────────────────────────────────────────────────────────────────


async def test_links_only_listed_tech_companies(session: AsyncSession) -> None:
    okta = await add_company(session, "Okta", domain="okta.com")
    await add_company(session, "Giottus", is_tech=None, domain="giottus.example")
    await add_company(session, "Zeta Labs", is_tech=False, domain="zeta.example")
    await add_company(session, "Quill Systems", summary=None, domain="quill.example")

    facts = make_facts(hosts=("Giottus", "Zeta Labs", "Quill Systems"))
    row = stored(await upsert(session, facts))

    links = list((await session.execute(select(EventCompany))).scalars())
    assert len(links) == 1
    assert (links[0].event_id, links[0].company_id) == (row.id, okta.id)
    assert (links[0].role, links[0].matched_on) == ("host", "title_pair")


async def test_a_tech_company_with_no_summary_or_description_is_not_listed(
    session: AsyncSession,
) -> None:
    await add_company(session, "Okta", domain="okta.com", summary=None, description=None)
    await upsert(session, make_facts())
    assert await count(session, EventCompany) == 0

    await add_company(session, "Okta Described", domain="okta.example", description="Identity.")
    # A curated description counts as text, exactly as on the map.
    await upsert(session, make_facts(title="Swift Bengaluru × Okta Described"))
    assert await count(session, EventCompany) == 1


async def test_the_organiser_is_linked_through_its_website(session: AsyncSession) -> None:
    okta = await add_company(session, "Okta", domain="okta.com")
    facts = make_facts(
        title="Identity Meetup",
        organiser_name="Okta India Events",  # the name alone would not match
        organiser_url="https://www.okta.com/events/in",
    )
    await upsert(session, facts)

    link = (await session.execute(select(EventCompany))).scalars().one()
    assert link.company_id == okta.id
    assert (link.role, link.matched_on) == ("organiser", "organiser_domain")


async def test_the_organiser_is_linked_by_name_when_its_website_says_nothing(
    session: AsyncSession,
) -> None:
    okta = await add_company(session, "Okta", domain="okta.com")
    facts = make_facts(title="Identity Meetup", organiser_name="Okta", organiser_url=None)
    await upsert(session, facts)

    link = (await session.execute(select(EventCompany))).scalars().one()
    assert link.company_id == okta.id
    assert (link.role, link.matched_on) == ("organiser", "organiser_name")


@pytest.mark.parametrize(
    "organiser_url",
    [
        "https://www.eventbrite.com/o/swift-bengaluru-123",
        "https://www.eventbrite.co.in/o/swift-bengaluru-123",
        "https://lu.ma/swift-bengaluru",
        "https://luma.com/swift-bengaluru",
        "https://gdg.community.dev/gdg-bangalore/",
    ],
)
async def test_an_event_platform_is_never_the_organisers_company(
    session: AsyncSession, organiser_url: str
) -> None:
    await add_company(session, "Eventbrite Inc", domain="eventbrite.com")
    await add_company(session, "Eventbrite India", domain="eventbrite.co.in")
    await add_company(session, "Luma Labs", domain="lu.ma")
    await add_company(session, "Luma Labs Site", domain="luma.com")
    await add_company(session, "Community Dev", domain="community.dev")

    await upsert(
        session,
        make_facts(
            title="Swift Meetup", organiser_name="Swift Bengaluru", organiser_url=organiser_url
        ),
    )
    assert await count(session, EventCompany) == 0


async def test_never_creates_company(session: AsyncSession) -> None:
    before = await count(session, Company)
    row = stored(await upsert(session, make_facts(title="BitGo x Giottus", hosts=("BitGo",))))
    assert await count(session, Company) == before == 0
    assert await count(session, EventCompany) == 0
    assert row.id is not None


async def test_an_ambiguous_name_links_nothing(session: AsyncSession) -> None:
    await add_company(session, "Okta", domain="okta.com")
    await add_company(session, "Okta", domain="okta.in")
    await upsert(session, make_facts())
    assert await count(session, EventCompany) == 0


async def test_one_company_named_twice_is_one_link(session: AsyncSession) -> None:
    """The organiser's website and a host's name can both lead to the same company."""
    okta = await add_company(session, "Okta", domain="okta.com")
    facts = make_facts(
        title="Identity Meetup",
        organiser_name="Okta India Events",
        organiser_url="https://www.okta.com/events",
        hosts=("Okta",),
    )
    await upsert(session, facts)
    link = (await session.execute(select(EventCompany))).scalars().one()
    assert link.company_id == okta.id
    assert (link.role, link.matched_on) == ("organiser", "organiser_domain")  # the strongest wins


async def test_links_are_replaced_not_piled_up(session: AsyncSession) -> None:
    await add_company(session, "Okta", domain="okta.com")
    await upsert(session, make_facts())
    await upsert(session, make_facts(), now=NOW + timedelta(hours=1))
    assert await count(session, EventCompany) == 1

    await upsert(
        session, make_facts(title="Rust Bengaluru Meetup", organiser_name="Rust Bengaluru")
    )
    assert await count(session, EventCompany) == 0  # the title no longer names Okta


@pytest.mark.parametrize(
    "stated",
    [
        {"hosts": ("Zeta Labs",)},
        {"organiser_name": "Zeta Labs"},
        {"organiser_name": "Zeta Events", "organiser_url": "https://www.zeta.example/events"},
    ],
    ids=["host", "organiser name", "organiser website"],
)
async def test_a_sparser_sighting_keeps_the_links_the_stored_row_earned(
    session: AsyncSession, stated: dict[str, Any]
) -> None:
    await add_company(session, "Zeta Labs", domain="zeta.example")
    await upsert(session, make_facts(title="Swift Meetup", **stated))
    assert await count(session, EventCompany) == 1

    sparse = make_facts(title="Swift Meetup", hosts=(), organiser_name=None, organiser_url=None)
    await upsert(session, sparse)
    assert await count(session, EventCompany) == 1


async def test_resolve_companies_returns_the_company_and_the_evidence(
    session: AsyncSession,
) -> None:
    okta = await add_company(session, "Okta", domain="okta.com")
    resolved = await resolve_companies(session, make_facts())
    assert [(company.id, cand.role, cand.matched_on) for company, cand in resolved] == [
        (okta.id, "host", "title_pair")
    ]
    assert await resolve_companies(session, make_facts(title="Rust Meetup")) == []


async def test_link_companies_relinks_from_the_stored_row(session: AsyncSession) -> None:
    row = stored(await upsert(session, make_facts(hosts=("Zeta Labs",))))
    assert await count(session, EventCompany) == 0

    await add_company(session, "Okta", domain="okta.com")  # the map grew
    await add_company(session, "Zeta Labs", domain="zeta.example")
    assert await link_companies(session, row) == 2
    await session.commit()
    assert await count(session, EventCompany) == 2

    assert await link_companies(session, row) == 2  # idempotent
    await session.commit()
    assert await count(session, EventCompany) == 2


async def test_link_companies_drops_a_company_that_is_no_longer_listed(
    session: AsyncSession,
) -> None:
    okta = await add_company(session, "Okta", domain="okta.com")
    row = stored(await upsert(session, make_facts()))
    assert await count(session, EventCompany) == 1

    okta.is_tech = False
    await session.commit()
    assert await link_companies(session, row) == 0
    await session.commit()
    assert await count(session, EventCompany) == 0


async def test_a_hiring_event_is_listed_only_for_a_listed_host(session: AsyncSession) -> None:
    drive = make_facts(
        title="Okta Walk-in Drive: backend engineers",
        organiser_name="Okta",
        external_id="evt-drive",
    )
    unlisted = stored(await upsert(session, drive))
    assert unlisted.format == "hiring" and unlisted.listed is False

    await add_company(session, "Okta", domain="okta.com")
    listed = stored(await upsert(session, drive))
    assert listed.listed is True
    assert any(reason.startswith("hosted by") for reason in listed.reasons or [])


# ── dedupe ───────────────────────────────────────────────────────────────────


async def test_cross_source_duplicate_is_hidden_and_linked(session: AsyncSession) -> None:
    await add_company(session, "Okta", domain="okta.com")
    first = await upsert(session, make_facts())
    second = await upsert(session, eventbrite_twin(), EVENTBRITE)

    assert first.duplicate_of is None
    assert second.created is True
    assert second.duplicate_of == stored(first).id
    assert stored(second).duplicate_of == stored(first).id
    assert stored(first).duplicate_of is None
    assert await count(session, Event) == 2
    assert await count(session, EventCompany) == 2  # both rows keep their company link


async def test_a_duplicate_is_not_re_matched_on_the_next_sighting(session: AsyncSession) -> None:
    first = stored(await upsert(session, make_facts()))
    second = stored(await upsert(session, eventbrite_twin(), EVENTBRITE))
    again = await upsert(session, eventbrite_twin(), EVENTBRITE, now=NOW + timedelta(hours=1))
    assert again.created is False and again.duplicate_of == first.id
    assert second.duplicate_of == first.id
    assert first.duplicate_of is None


@pytest.mark.parametrize(
    ("minutes_apart", "title"),
    [
        (60, "Rust Bengaluru Meetup"),  # the brief: an hour later, a different meetup
        (30, "Rust Bengaluru Meetup"),  # inside the window, but a different meetup
        (60, "Swift Bengaluru Meetup at Okta"),  # the same words, but outside the window
    ],
)
async def test_same_venue_different_event_stays_two(
    session: AsyncSession, minutes_apart: int, title: str
) -> None:
    first = await upsert(session, make_facts())
    second = await upsert(
        session,
        replace(
            eventbrite_twin(),
            title=title,
            starts_at=START + timedelta(minutes=minutes_apart),
            lat=BELLANDUR[0],
            lng=BELLANDUR[1],
        ),
        EVENTBRITE,
    )
    assert first.duplicate_of is None and second.duplicate_of is None
    assert stored(second).duplicate_of is None


async def test_the_window_includes_exactly_45_minutes(session: AsyncSession) -> None:
    await upsert(session, make_facts())
    at_edge = replace(eventbrite_twin(), starts_at=START + DEDUPE_WINDOW)
    past_edge = replace(
        at_edge, external_id="1002", starts_at=START + DEDUPE_WINDOW + timedelta(seconds=1)
    )
    assert (await upsert(session, at_edge, EVENTBRITE)).duplicate_of is not None
    assert (await upsert(session, past_edge, EVENTBRITE)).duplicate_of is None


async def test_online_and_in_person_listings_never_merge(session: AsyncSession) -> None:
    await upsert(session, make_facts())
    online = eventbrite_twin(is_online=True)
    online = replace(online, lat=None, lng=None, address=None, venue_name=None)
    result = await upsert(
        session, online, SourceScope("eventbrite", india=True, trusted_tech=False)
    )
    assert result.created is True and result.duplicate_of is None


async def test_the_same_source_never_merges_with_itself(session: AsyncSession) -> None:
    await upsert(session, make_facts())
    twin = await upsert(session, make_facts(external_id="evt-other", url="https://luma.com/other"))
    assert twin.created is True and twin.duplicate_of is None


async def test_an_unlisted_primary_never_absorbs_a_listed_event(session: AsyncSession) -> None:
    """Hiding the only listed copy behind an unlisted one would make the event vanish."""
    quiet = make_facts(title="Weekend Pottery Meetup", snippet="Clay and chai.")
    first = await upsert(session, quiet)
    assert stored(first).listed is False

    tech = replace(
        eventbrite_twin(taxonomy={"category": "102"}),
        title="Weekend Pottery Meetup",
        starts_at=START,
    )
    second = await upsert(session, tech, EVENTBRITE)
    assert stored(second).listed is True
    assert second.duplicate_of is None


async def test_a_private_manual_event_never_absorbs_a_public_one(session: AsyncSession) -> None:
    owner = await add_user(session)
    session.add(
        Event(
            id=uuid.uuid4(),
            source="user",
            external_id="manual-1",
            url=None,
            title="Swift Bengaluru × Okta",
            starts_at=START,
            lat=BELLANDUR[0],
            lng=BELLANDUR[1],
            is_online=False,
            listed=True,
            owner_user_id=owner.id,
        )
    )
    await session.commit()

    public = await upsert(session, make_facts())
    assert public.created is True and public.duplicate_of is None


async def test_find_duplicate_is_a_read_that_returns_the_primary(session: AsyncSession) -> None:
    first = stored(await upsert(session, make_facts()))
    probe = eventbrite_twin()
    found = await find_duplicate(session, probe, "Bellandur")
    assert found is not None and found.id == first.id
    assert await find_duplicate(session, replace(probe, title="Quantum Night"), "Bellandur") is None
    assert await find_duplicate(session, make_facts(), "Bellandur") is None  # same source


# ── dedupe: a primary that went away, or a verdict that changed ──────────────


async def _three_sources(db: AsyncSession) -> tuple[Event, Event, Event]:
    """One meetup on Luma (primary), Eventbrite and GDG (its duplicates), stored a minute apart."""
    luma = stored(await upsert(db, make_facts(), now=NOW))
    eventbrite = stored(
        await upsert(db, eventbrite_twin(), EVENTBRITE, now=NOW + timedelta(minutes=1))
    )
    gdg = stored(
        await upsert(
            db,
            make_facts(
                source="gdg",
                external_id="swift-okta-gdg",
                url="https://gdg.community.dev/events/details/swift-okta-gdg/",
                title="Swift Bengaluru Meetup at Okta",
                starts_at=START + timedelta(minutes=10),
                lat=None,
                lng=None,
                address="Bellandur, Bengaluru, Karnataka, India",
            ),
            GDG,
            now=NOW + timedelta(minutes=2),
        )
    )
    assert eventbrite.duplicate_of == luma.id and gdg.duplicate_of == luma.id
    return luma, eventbrite, gdg


async def test_a_row_whose_primary_was_deleted_is_matched_again(session: AsyncSession) -> None:
    luma, eventbrite, gdg = await _three_sources(session)
    await session.execute(delete(Event).where(Event.id == luma.id))
    await session.commit()
    await session.refresh(eventbrite)
    await session.refresh(gdg)
    assert eventbrite.duplicate_of is None and gdg.duplicate_of is None  # SET NULL: two strays

    # The earlier-stored stray stays the primary: nothing earlier matches it.
    first = await upsert(session, eventbrite_twin(), EVENTBRITE, now=NOW + timedelta(hours=1))
    assert first.duplicate_of is None

    # The later stray is re-assessed and joins it.
    later = await upsert(
        session,
        make_facts(
            source="gdg",
            external_id="swift-okta-gdg",
            title="Swift Bengaluru Meetup at Okta",
            starts_at=START + timedelta(minutes=10),
            lat=None,
            lng=None,
            address="Bellandur, Bengaluru, Karnataka, India",
        ),
        GDG,
        now=NOW + timedelta(hours=1),
    )
    assert later.created is False
    assert later.duplicate_of == eventbrite.id
    assert stored(later).duplicate_of == eventbrite.id


async def test_a_stray_with_a_save_stays_a_primary(session: AsyncSession) -> None:
    """Hiding a row somebody saved would make their saved event disappear."""
    luma, eventbrite, gdg = await _three_sources(session)
    user = await add_user(session)
    await session.execute(delete(Event).where(Event.id == luma.id))
    session.add(EventSave(id=uuid.uuid4(), user_id=user.id, event_id=gdg.id, status="going"))
    await session.commit()
    await session.refresh(eventbrite)
    await session.refresh(gdg)
    assert gdg.duplicate_of is None

    later = await upsert(
        session,
        make_facts(
            source="gdg",
            external_id="swift-okta-gdg",
            title="Swift Bengaluru Meetup at Okta",
            starts_at=START + timedelta(minutes=10),
            lat=None,
            lng=None,
            address="Bellandur, Bengaluru, Karnataka, India",
        ),
        GDG,
        now=NOW + timedelta(hours=1),
    )
    assert later.duplicate_of is None


async def test_a_primary_listed_later_takes_the_duplicates_of_its_challenger(
    session: AsyncSession,
) -> None:
    """The relevance step lists an earlier row: the later primary and its duplicates join it."""
    quiet = stored(
        await upsert(
            session,
            make_facts(title="Weekend Pottery Meetup", snippet="Clay and chai."),
            now=NOW,
        )
    )
    assert quiet.listed is False
    tech = replace(
        eventbrite_twin(taxonomy={"category": "102"}),
        title="Weekend Pottery Meetup",
        starts_at=START,
    )
    challenger = stored(await upsert(session, tech, EVENTBRITE, now=NOW + timedelta(minutes=1)))
    follower = stored(
        await upsert(
            session,
            make_facts(
                source="gdg",
                external_id="pottery-gdg",
                title="Weekend Pottery Meetup",
                starts_at=START,
                lat=None,
                lng=None,
                address="Bellandur, Bengaluru, Karnataka, India",
            ),
            GDG,
            now=NOW + timedelta(minutes=2),
        )
    )
    assert challenger.duplicate_of is None and follower.duplicate_of == challenger.id

    quiet.listed = True  # what the relevance step does once the rules list it
    await session.commit()

    again = await upsert(session, tech, EVENTBRITE, now=NOW + timedelta(hours=1))
    assert again.duplicate_of == quiet.id
    await session.refresh(follower)
    assert follower.duplicate_of == quiet.id  # no chain: the primary is always a primary


# ── validation, and what is not swallowed ────────────────────────────────────


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "   "},
        {"external_id": ""},
        {"external_id": "x" * 161},
        {"source": ""},
        {"source": "a-source-name-over-sixteen"},
        {"external_id": "evt-" + chr(0) + "1"},
    ],
)
async def test_unusable_identity_is_skipped_as_invalid(
    session: AsyncSession, changes: dict[str, Any]
) -> None:
    result = await upsert(session, make_facts(**changes))
    assert result.skipped == "invalid" and result.event is None
    assert await count(session, Event) == 0


async def test_overlong_text_is_clamped_and_overlong_links_are_dropped(
    session: AsyncSession,
) -> None:
    facts = make_facts(
        title="T" * 400,
        snippet="S" * 700,
        venue_name="V" * 300,
        address="Bellandur, Bengaluru " + "A" * 400,
        organiser_name="O" * 300,
        organiser_url="https://example.test/" + "u" * 600,
        url="https://luma.com/" + "p" * 1100,
    )
    row = stored(await upsert(session, facts))
    assert len(row.title) == 300
    assert row.snippet is not None and len(row.snippet) == 600
    assert row.venue_name is not None and len(row.venue_name) == 200
    assert row.address is not None and len(row.address) == 300
    assert row.organiser_name is not None and len(row.organiser_name) == 200
    assert row.organiser_url is None  # a cut link is a wrong link
    assert row.url is None


async def test_nul_never_reaches_the_database(session: AsyncSession) -> None:
    """PostgreSQL refuses NUL in text and JSON; one bad field must not fail a whole batch."""
    nul = chr(0)
    facts = make_facts(
        title=f"Swift{nul} Meetup",
        snippet=f"Talks{nul}",
        venue_name=f"Okta{nul}",
        hosts=(f"Zeta{nul} Labs",),
        locality=f"Bengaluru{nul}",
        timezone=f"Asia/Kolkata{nul}",
        taxonomy={f"category{nul}": f"102{nul}"},
    )
    row = stored(await upsert(session, facts))
    assert (row.title, row.snippet, row.venue_name) == ("Swift Meetup", "Talks", "Okta")
    assert list(row.hosts or []) == ["Zeta Labs"]
    meta = row.extra_metadata or {}
    assert meta["locality"] == "Bengaluru" and meta["timezone"] == "Asia/Kolkata"
    assert meta["taxonomy"] == {"category": "102"}


async def test_a_database_error_is_not_swallowed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(session, "flush", boom)
    with pytest.raises(RuntimeError, match="database is gone"):
        await upsert_event(session, make_facts(), LUMA, now=NOW)


# ── fix round 1: races, reasons, hosts, event links, sticky flags, retained pins ─


async def test_a_lost_insert_race_takes_the_update_path(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another run stored the event between our lookup and our insert."""
    first = stored(await upsert(session, make_facts()))

    real = store._find_stored
    lookups = 0

    async def blind_once(db: AsyncSession, source: str, external_id: str) -> Event | None:
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else await real(db, source, external_id)

    monkeypatch.setattr(store, "_find_stored", blind_once)
    renamed = make_facts(title="Swift Bengaluru × Okta, renamed")
    result = await upsert(session, renamed, now=NOW + timedelta(hours=1))

    assert result.skipped is None and result.created is False
    assert stored(result).id == first.id
    assert stored(result).title == "Swift Bengaluru × Okta, renamed"
    assert as_utc(stored(result).last_seen_at) == NOW + timedelta(hours=1)
    assert await count(session, Event) == 1
    assert lookups == 2  # the miss, then the re-select after the unique violation


async def test_an_integrity_error_that_is_not_a_race_propagates(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ghost(*args: Any, **kwargs: Any) -> Event:
        return Event(id=uuid.uuid4())  # a primary that is not in the database

    monkeypatch.setattr(store, "find_duplicate", ghost)
    with pytest.raises(IntegrityError):
        await upsert_event(session, make_facts(), LUMA, now=NOW)

    await session.rollback()
    assert await count(session, Event) == 0  # the session is still usable


async def test_reasons_are_cleaned_and_clamped(session: AsyncSession) -> None:
    """``assess`` writes "hosted by <organiser>" from the facts, so the facts are cleaned first."""
    await add_company(session, "Okta", domain="okta.com")
    nul = chr(0)
    facts = make_facts(
        title="Okta Walk-in Drive: backend engineers",
        external_id="evt-drive",
        hosts=("Okta",),
        organiser_name="Org" + nul + "A" * 5000,
    )
    row = stored(await upsert(session, facts))

    assert row.listed is True  # a hiring event whose host is a listed company
    reasons = list(row.reasons or [])
    assert reasons and all(len(reason) <= 200 and nul not in reason for reason in reasons)
    hosted = [reason for reason in reasons if reason.startswith("hosted by")]
    assert len(hosted) == 1
    assert hosted[0].startswith("hosted by OrgAAA") and len(hosted[0]) == 200
    assert row.organiser_name is not None
    assert nul not in row.organiser_name and len(row.organiser_name) == 200


def _count_lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    names: list[str] = []
    real = store.find_existing

    async def counting(db: AsyncSession, **kwargs: Any) -> Company | None:
        names.append(kwargs["name"])
        return await real(db, **kwargs)

    monkeypatch.setattr(store, "find_existing", counting)
    return names


async def test_hosts_are_capped_deduplicated_and_cleaned(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _count_lookups(monkeypatch)
    nul = chr(0)
    hosts = (
        "Host Co 0",
        "host co 0",  # the same name in another case
        " ",
        "",
        f"Host{nul} Co 1",
        *(f"Host Co {number}" for number in range(50)),
    )
    facts = make_facts(title="Swift Meetup", organiser_name=None, hosts=hosts)
    row = stored(await upsert(session, facts))

    assert list(row.hosts or []) == [f"Host Co {number}" for number in range(10)]
    assert len(lookups) <= 10


async def test_resolving_looks_up_at_most_ten_hosts(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _count_lookups(monkeypatch)
    many = make_facts(
        title="Swift Meetup",
        organiser_name=None,
        hosts=tuple(f"Host Co {number}" for number in range(50)),
    )
    await resolve_companies(session, many)
    assert len(lookups) == 10


@pytest.mark.parametrize(
    "url",
    [
        "http://luma.com/okta-swift",
        "javascript:alert(1)",
        "ftp://luma.com/x",
        "//luma.com/x",
        "luma.com/x",
        "https://",
        "   ",
    ],
)
async def test_an_event_link_that_is_not_https_is_dropped_not_the_event(
    session: AsyncSession, url: str
) -> None:
    result = await upsert(session, make_facts(url=url))
    row = stored(result)
    assert result.created is True and row.url is None
    assert row.listed is True


async def test_https_links_are_kept_and_a_later_bad_one_does_not_erase_them(
    session: AsyncSession,
) -> None:
    first = stored(await upsert(session, make_facts(url="HTTPS://luma.com/okta-swift")))
    assert first.url == "HTTPS://luma.com/okta-swift"

    later = stored(await upsert(session, make_facts(url="javascript:alert(1)")))
    assert later.url == "HTTPS://luma.com/okta-swift"


async def test_address_withheld_only_moves_to_true(session: AsyncSession) -> None:
    phrase = "The exact location will be shared once the invite is accepted."

    never = stored(await upsert(session, make_facts(external_id="evt-never")))
    assert (never.extra_metadata or {})["address_withheld"] is False

    first = stored(await upsert(session, make_facts(snippet=f"Doors at 6. {phrase}")))
    assert (first.extra_metadata or {})["address_withheld"] is True

    again = stored(await upsert(session, make_facts(snippet="Doors at 6.")))
    assert (again.extra_metadata or {})["address_withheld"] is True  # the phrase is gone

    flagged = stored(
        await upsert(session, make_facts(external_id="evt-never", address_withheld=True))
    )
    assert (flagged.extra_metadata or {})["address_withheld"] is True
    unflagged = stored(await upsert(session, make_facts(external_id="evt-never")))
    assert (unflagged.extra_metadata or {})["address_withheld"] is True


def _online(facts: EventFacts) -> EventFacts:
    return replace(facts, is_online=True, lat=None, lng=None, address=None, venue_name=None)


def _no_position(facts: EventFacts) -> EventFacts:
    return replace(facts, lat=None, lng=None, address="Bellandur, Bengaluru")


async def test_precision_follows_the_retained_pin(session: AsyncSession) -> None:
    twin = eventbrite_twin()
    row = stored(await upsert(session, twin, EVENTBRITE))
    pin = (row.lat, row.lng)
    assert row.geo_precision == "exact"

    online = stored(await upsert(session, _online(twin), EVENTBRITE, now=NOW + timedelta(hours=1)))
    assert online.geo_precision == "none" and (online.lat, online.lng) == pin

    # Back in person, no position stated: the pin it kept is Eventbrite's own again.
    back = stored(
        await upsert(session, _no_position(twin), EVENTBRITE, now=NOW + timedelta(hours=2))
    )
    assert (back.lat, back.lng) == pin and back.geo_precision == "exact"

    # The listing now says the exact location comes later: the pin is only near.
    phrase = "The exact location will be shared once the invite is accepted."
    withheld = replace(_no_position(twin), snippet=phrase)
    near = stored(await upsert(session, withheld, EVENTBRITE, now=NOW + timedelta(hours=3)))
    assert (near.lat, near.lng) == pin and near.geo_precision == "area"

    # ... and stays so once the sentence is gone.
    later = stored(
        await upsert(session, _no_position(twin), EVENTBRITE, now=NOW + timedelta(hours=4))
    )
    assert later.geo_precision == "area"


async def test_a_placed_pin_never_becomes_exact_by_coming_back(session: AsyncSession) -> None:
    """An Eventbrite event with only an address was placed by us: that pin is never "exact"."""
    twin = replace(_no_position(eventbrite_twin()), external_id="1002")
    row = stored(await upsert(session, twin, EVENTBRITE))
    pin = (row.lat, row.lng)
    assert pin != (None, None) and row.geo_precision == "area"

    stored(await upsert(session, _online(twin), EVENTBRITE, now=NOW + timedelta(hours=1)))
    back = stored(await upsert(session, twin, EVENTBRITE, now=NOW + timedelta(hours=2)))
    assert (back.lat, back.lng) == pin and back.geo_precision == "area"
