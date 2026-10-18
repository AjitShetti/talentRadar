"""
tests/test_event_met_contract.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Outreach contract (spec section 8): ``services/events/met.py`` and
``GET /api/v1/events/people-met``.

Outreach is built on its own branch and its tables are unknown here, so Events publishes a
read-only contract and these tests pin it: the exact fields, the idempotency key, the IST date
of the meeting, the ``since`` filter, and that every row is the caller's own. Everything runs
on in-memory SQLite with ``PRAGMA foreign_keys=ON`` and never touches the shared Postgres test
database. Every person, organiser and company is fictional.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.auth import create_access_token
from api.dependencies import get_session
from services.events.met import MetAtEvent, context_line, people_met
from storage.database import Base
from storage.models import Company, Event, EventContact, User

TABLES = ("users", "companies", "events", "event_companies", "event_saves", "event_contacts")

NOW = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
# 19:00Z on the 15th is 00:30 IST on the 16th: the UTC date and the IST date differ.
SWIFT_STARTS = datetime(2026, 10, 15, 19, 0, tzinfo=UTC)
URL = "/api/v1/events/people-met"

Factory = async_sessionmaker[AsyncSession]

SPEC_FIELDS = [
    "source_ref",
    "name",
    "title",
    "company_name",
    "company_id",
    "linkedin_url",
    "x_url",
    "github_url",
    "email",
    "note",
    "event_id",
    "event_title",
    "event_url",
    "met_on",
    "created_at",
]


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


def swift_okta(**fields: Any) -> Event:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source": "luma",
        "external_id": f"evt-{uuid.uuid4().hex[:12]}",
        "url": "https://luma.com/swift-okta",
        "title": "Swift Bengaluru × Okta",
        "starts_at": SWIFT_STARTS,
        "ends_at": SWIFT_STARTS + timedelta(hours=2),
        "is_online": False,
        "listed": True,
    }
    values.update(fields)
    return Event(**values)


def contact(
    person: User, event: Event, name: str, created: datetime, **fields: Any
) -> EventContact:
    return EventContact(
        id=uuid.uuid4(),
        user_id=person.id,
        event_id=event.id,
        name=name,
        created_at=created,
        updated_at=created,
        **fields,
    )


@pytest_asyncio.fixture
async def me(add: Callable[..., Any]) -> User:
    return await new_user(add)


@pytest_asyncio.fixture
async def client(factory: Factory, me: User) -> AsyncIterator[AsyncClient]:
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


async def met_by(factory: Factory, person: User, **kwargs: Any) -> list[MetAtEvent]:
    async with factory() as session:
        return await people_met(session, str(person.id), **kwargs)


# ═════════════════════════════════════════════════════════════════════════════
# the contract
# ═════════════════════════════════════════════════════════════════════════════


async def test_people_met_contract(factory: Factory, add: Callable[..., Any], me: User) -> None:
    lumen = Company(id=uuid.uuid4(), name="Lumen Works", is_tech=True)
    event = swift_okta()
    created = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)
    person = contact(
        me,
        event,
        "Asha Verma",
        created,
        title="Staff Engineer",
        company_name="Lumen Works",
        company_id=lumen.id,
        linkedin_url="https://www.linkedin.com/in/asha-verma-example",
        x_url="https://x.com/asha_example",
        github_url="https://github.com/asha-example",
        email="asha@example.test",
        note="Async Rust in production",
    )
    await add(lumen, event)
    await add(person)

    [found] = await met_by(factory, me)

    assert found == MetAtEvent(
        source_ref=f"event_contact:{person.id}",
        name="Asha Verma",
        title="Staff Engineer",
        company_name="Lumen Works",
        company_id=str(lumen.id),
        linkedin_url="https://www.linkedin.com/in/asha-verma-example",
        x_url="https://x.com/asha_example",
        github_url="https://github.com/asha-example",
        email="asha@example.test",
        note="Async Rust in production",
        event_id=str(event.id),
        event_title="Swift Bengaluru × Okta",
        event_url="https://luma.com/swift-okta",
        met_on=date(2026, 10, 16),
        created_at=created,
    )
    assert found.source_ref == f"event_contact:{person.id}"
    assert found.met_on == date(2026, 10, 16) != SWIFT_STARTS.date()  # the IST date, not UTC's
    assert found.created_at.tzinfo is not None
    assert context_line(found) == "Met at Swift Bengaluru × Okta on 16 Oct"


def test_the_fields_are_exactly_the_spec_s_and_in_its_order() -> None:
    assert [field.name for field in dataclasses.fields(MetAtEvent)] == SPEC_FIELDS


def test_a_met_person_cannot_be_changed_after_it_is_read() -> None:
    sample = MetAtEvent(**dict.fromkeys(SPEC_FIELDS))  # type: ignore[arg-type]

    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.name = "someone else"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 16), "Met at Rust Night on 16 Oct"),
        (date(2026, 11, 5), "Met at Rust Night on 5 Nov"),
        (date(2027, 1, 1), "Met at Rust Night on 1 Jan"),
        (date(2026, 12, 31), "Met at Rust Night on 31 Dec"),
    ],
)
def test_context_line_has_no_zero_padded_day(day: date, expected: str) -> None:
    sample = MetAtEvent(
        source_ref="event_contact:x",
        name="Asha Verma",
        title=None,
        company_name=None,
        company_id=None,
        linkedin_url=None,
        x_url=None,
        github_url=None,
        email=None,
        note=None,
        event_id="e",
        event_title="Rust Night",
        event_url="",
        met_on=day,
        created_at=NOW,
    )

    assert context_line(sample) == expected


async def test_the_since_filter_excludes_older_rows(
    factory: Factory, add: Callable[..., Any], me: User
) -> None:
    event = swift_okta()
    await add(event)
    await add(
        contact(me, event, "Older", datetime(2026, 9, 1, 8, 0, tzinfo=UTC)),
        contact(me, event, "Boundary", datetime(2026, 10, 1, 0, 0, tzinfo=UTC)),
        contact(me, event, "Newer", datetime(2026, 10, 16, 9, 30, tzinfo=UTC)),
    )
    since = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

    everything = await met_by(factory, me)
    recent = await met_by(factory, me, since=since)
    after = await met_by(factory, me, since=since + timedelta(seconds=1))
    naive = await met_by(factory, me, since=datetime(2026, 10, 1, 0, 0))
    ahead = await met_by(factory, me, since=NOW)

    assert [m.name for m in everything] == ["Older", "Boundary", "Newer"]
    assert [m.name for m in recent] == ["Boundary", "Newer"]  # inclusive: a repeat is safe
    assert [m.name for m in after] == ["Newer"]
    assert [m.name for m in naive] == ["Boundary", "Newer"]  # a bare time is read as UTC
    assert ahead == []


async def test_people_come_oldest_first_and_limit_is_held(
    factory: Factory, add: Callable[..., Any], me: User
) -> None:
    event = swift_okta()
    await add(event)
    base = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    await add(
        *[contact(me, event, f"Person {n}", base + timedelta(minutes=n)) for n in (3, 1, 2, 0)]
    )

    assert [m.name for m in await met_by(factory, me)] == [f"Person {n}" for n in range(4)]
    assert [m.name for m in await met_by(factory, me, limit=2)] == ["Person 0", "Person 1"]
    assert len(await met_by(factory, me, limit=0)) == 1  # held between 1 and the maximum
    assert len(await met_by(factory, me, limit=10**9)) == 4


async def test_people_met_is_the_callers_only(
    factory: Factory, add: Callable[..., Any], me: User
) -> None:
    other = await new_user(add)
    event = swift_okta()
    await add(event)
    await add(
        contact(me, event, "Mine", datetime(2026, 10, 16, 9, 0, tzinfo=UTC)),
        contact(other, event, "Theirs", datetime(2026, 10, 16, 9, 0, tzinfo=UTC)),
    )

    assert [m.name for m in await met_by(factory, me)] == ["Mine"]
    assert [m.name for m in await met_by(factory, other)] == ["Theirs"]
    assert await met_by(factory, await new_user(add)) == []


async def test_a_manual_event_with_no_link_has_an_empty_event_url(
    factory: Factory, add: Callable[..., Any], me: User
) -> None:
    dinner = swift_okta(
        source="user", title="Founders dinner", url=None, owner_user_id=me.id, listed=True
    )
    await add(dinner)
    await add(contact(me, dinner, "Ravi Menon", datetime(2026, 10, 16, 9, 0, tzinfo=UTC)))

    [found] = await met_by(factory, me)

    assert found.event_url == ""
    assert context_line(found) == "Met at Founders dinner on 16 Oct"


async def test_a_typed_company_that_matched_nothing_reads_with_no_company_id(
    factory: Factory, add: Callable[..., Any], me: User
) -> None:
    event = swift_okta()
    await add(event)
    await add(
        contact(
            me,
            event,
            "Asha Verma",
            datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
            company_name="Gone Inc",
        )
    )

    [found] = await met_by(factory, me)

    assert (found.company_name, found.company_id) == ("Gone Inc", None)


# ═════════════════════════════════════════════════════════════════════════════
# the JSON
# ═════════════════════════════════════════════════════════════════════════════


async def test_the_json_has_the_same_keys_as_the_dataclass(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    event = swift_okta()
    await add(event)
    person = contact(
        me,
        event,
        "Asha Verma",
        datetime(2026, 10, 16, 9, 30, tzinfo=UTC),
        email="asha@example.test",
    )
    await add(person)

    response = await client.get(URL)

    assert response.status_code == 200, response.text
    [row] = response.json()
    assert set(row) == {field.name for field in dataclasses.fields(MetAtEvent)}
    assert row["source_ref"] == f"event_contact:{person.id}"
    assert row["met_on"] == "2026-10-16"
    assert row["event_url"] == "https://luma.com/swift-okta"
    assert row["email"] == "asha@example.test"
    assert row["title"] is None
    assert row["created_at"].startswith("2026-10-16T09:30:00")


async def test_the_endpoint_filters_and_pages(
    client: AsyncClient, add: Callable[..., Any], me: User
) -> None:
    event = swift_okta()
    await add(event)
    base = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    await add(*[contact(me, event, f"Person {n}", base + timedelta(hours=n)) for n in range(3)])

    since = (base + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    recent = await client.get(URL, params={"since": since})
    paged = await client.get(URL, params={"limit": 1})

    assert [row["name"] for row in recent.json()] == ["Person 1", "Person 2"]
    assert [row["name"] for row in paged.json()] == ["Person 0"]


@pytest.mark.parametrize(
    "params",
    [
        {"since": "yesterday"},
        {"since": "0001-01-01T00:00:00+14:00"},
        {"since": "9999-12-31T23:59:59-12:00"},
        {"limit": 0},
        {"limit": 501},
    ],
    ids=lambda params: "-".join(f"{k}={v}" for k, v in params.items()),
)
async def test_bad_people_met_query_is_422(client: AsyncClient, params: dict[str, Any]) -> None:
    assert (await client.get(URL, params=params)).status_code == 422


async def test_people_met_needs_a_signed_in_user(client: AsyncClient) -> None:
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get(URL)).status_code == 401


async def test_people_met_makes_no_outside_request(
    client: AsyncClient, add: Callable[..., Any], me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = swift_okta()
    await add(event)
    await add(
        contact(
            me,
            event,
            "Asha Verma",
            datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
            linkedin_url="https://www.linkedin.com/in/asha-verma-example",
        )
    )

    async def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("reading the contract tried to reach the network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)

    assert (await client.get(URL)).status_code == 200
