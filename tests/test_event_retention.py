"""
Events retention (``services/job_retention.py::prune_events``).

The rule is about what a person kept: an event somebody saved, or noted people at, is
their history and no age ever removes it. Everything else is removed once it is over (14
days; 2 days when cancelled; at once when it was never listed).

The statement runs against an in-memory SQLite with ``PRAGMA foreign_keys=ON``, so the
cascade and ``SET NULL`` behaviour the rule leans on are the real ones. Its dates are bound
values rather than ``NOW()`` and ``make_interval``, which SQLite does not have; that is what
lets these tests exercise the statement itself. How ``prune_stale_jobs`` wires the step in
is tested with a recording fake session (the job statements are Postgres-only). Every
person and organiser is fictional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import storage.database as database
from services import job_retention
from storage.database import Base
from storage.models import Company, Event, EventCompany, EventContact, EventSave, User

TABLES = ("users", "companies", "events", "event_companies", "event_saves", "event_contacts")


def ago(*, days: float = 0, hours: float = 0) -> datetime:
    return datetime.now(UTC) - timedelta(days=days, hours=hours)


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


def make_event(*, ends: datetime | None, starts: datetime | None = None, **fields: Any) -> Event:
    """A stored event. Listed unless a test says otherwise, so only the rule under test can take it."""
    when = starts if starts is not None else (ends - timedelta(hours=2) if ends else ago())
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source": "luma",
        "external_id": f"evt-{uuid.uuid4().hex[:12]}",
        "title": "Open Evening",
        "starts_at": when,
        "ends_at": ends,
        "listed": True,
    }
    values.update(fields)
    return Event(**values)


async def add(session: AsyncSession, *rows: Any) -> None:
    session.add_all(rows)
    await session.commit()


async def add_user(session: AsyncSession) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x")
    await add(session, user)
    return user


async def stored_ids(session: AsyncSession) -> set[uuid.UUID]:
    return set((await session.execute(select(Event.id))).scalars())


# ═════════════════════════════════════════════════════════════════════════════
# the age rule
# ═════════════════════════════════════════════════════════════════════════════


def test_the_windows_are_the_ones_the_spec_states() -> None:
    assert job_retention.EVENT_RETENTION_DAYS == 14
    assert job_retention.CANCELLED_EVENT_RETENTION_DAYS == 2


async def test_prunes_old_unsaved(session: AsyncSession) -> None:
    old = make_event(ends=ago(days=15))
    recent = make_event(ends=ago(days=13))
    await add(session, old, recent)

    deleted = await job_retention.prune_events(session)

    assert deleted == 1
    assert await stored_ids(session) == {recent.id}


@pytest.mark.parametrize(
    ("hours_past", "gone"),
    [(14 * 24 + 3, True), (14 * 24 + 1, False)],
    ids=["effective end is over the window", "effective end is inside it"],
)
async def test_an_event_without_an_end_is_over_two_hours_after_it_starts(
    session: AsyncSession, hours_past: int, gone: bool
) -> None:
    row = make_event(ends=None, starts=ago(hours=hours_past))
    await add(session, row)

    await job_retention.prune_events(session)

    assert (row.id not in await stored_ids(session)) is gone


async def test_prunes_unlisted_once_ended(session: AsyncSession) -> None:
    over = make_event(ends=ago(hours=1), listed=False)
    upcoming = make_event(ends=None, starts=ago(hours=-48), listed=False)
    under_way = make_event(ends=ago(hours=-1), starts=ago(hours=1), listed=False)
    open_ended_under_way = make_event(ends=None, starts=ago(hours=1), listed=False)
    open_ended_over = make_event(ends=None, starts=ago(hours=3), listed=False)
    listed_just_over = make_event(ends=ago(hours=1), listed=True)
    await add(
        session, over, upcoming, under_way, open_ended_under_way, open_ended_over, listed_just_over
    )

    deleted = await job_retention.prune_events(session)

    assert deleted == 2
    assert await stored_ids(session) == {
        upcoming.id,
        under_way.id,
        open_ended_under_way.id,
        listed_just_over.id,
    }


async def test_prunes_cancelled_after_two_days(session: AsyncSession) -> None:
    gone = make_event(ends=None, starts=ago(days=3), is_cancelled=True)
    yesterday = make_event(ends=None, starts=ago(days=1), is_cancelled=True)
    upcoming = make_event(ends=None, starts=ago(days=-2), is_cancelled=True)
    not_cancelled = make_event(ends=None, starts=ago(days=3), is_cancelled=False)
    await add(session, gone, yesterday, upcoming, not_cancelled)

    deleted = await job_retention.prune_events(session)

    assert deleted == 1
    assert await stored_ids(session) == {yesterday.id, upcoming.id, not_cancelled.id}


# ═════════════════════════════════════════════════════════════════════════════
# what a person kept
# ═════════════════════════════════════════════════════════════════════════════


async def test_keeps_saved_and_noted_forever(session: AsyncSession) -> None:
    user = await add_user(session)
    # Unlisted, cancelled and long over: every rule would take these but for the user.
    saved = make_event(ends=ago(days=400), listed=False, is_cancelled=True)
    noted = make_event(ends=ago(days=400), listed=False, is_cancelled=True)
    control = make_event(ends=ago(days=400), listed=False, is_cancelled=True)
    await add(session, saved, noted, control)
    await add(
        session,
        EventSave(user_id=user.id, event_id=saved.id, status="saved"),
        EventContact(user_id=user.id, event_id=noted.id, name="Asha Verma"),
    )

    deleted = await job_retention.prune_events(session)

    assert deleted == 1
    assert await stored_ids(session) == {saved.id, noted.id}
    # Nothing was cascaded away with them.
    assert (await session.execute(select(EventSave.event_id))).scalars().all() == [saved.id]
    assert (await session.execute(select(EventContact.event_id))).scalars().all() == [noted.id]


async def test_it_goes_once_the_last_save_and_contact_are_gone(session: AsyncSession) -> None:
    user = await add_user(session)
    row = make_event(ends=ago(days=30))
    await add(session, row)
    await add(session, EventSave(user_id=user.id, event_id=row.id, status="going"))
    assert await job_retention.prune_events(session) == 0

    await session.execute(text("DELETE FROM event_saves"))
    await session.commit()

    assert await job_retention.prune_events(session) == 1
    assert await stored_ids(session) == set()


async def test_a_pruned_event_takes_its_links_and_leaves_a_saved_duplicate(
    session: AsyncSession,
) -> None:
    user = await add_user(session)
    company = Company(id=uuid.uuid4(), name="Quill Labs", is_tech=True)
    primary = make_event(ends=ago(days=20))
    duplicate = make_event(ends=ago(days=20), listed=False, duplicate_of=primary.id, source="gdg")
    await add(session, company, primary)
    await add(session, duplicate)
    await add(
        session,
        EventCompany(event_id=primary.id, company_id=company.id, role="organiser", matched_on="x"),
        EventSave(user_id=user.id, event_id=duplicate.id, status="saved"),
    )

    deleted = await job_retention.prune_events(session)

    assert deleted == 1
    assert await stored_ids(session) == {duplicate.id}
    assert (await session.execute(select(EventCompany))).first() is None
    kept = (await session.execute(select(Event.duplicate_of))).scalars().all()
    assert kept == [None]


# ═════════════════════════════════════════════════════════════════════════════
# bounded, and contained
# ═════════════════════════════════════════════════════════════════════════════


async def test_the_oldest_go_first_when_the_cap_binds(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job_retention, "MAX_DELETIONS_PER_RUN", 2)
    rows = [make_event(ends=ago(days=20 + age)) for age in range(5)]
    await add(session, *rows)

    deleted = await job_retention.prune_events(session)

    assert deleted == 2
    assert await stored_ids(session) == {row.id for row in rows[:3]}


async def test_prune_failure_is_contained(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    rollback = AsyncMock()
    monkeypatch.setattr(session, "execute", AsyncMock(side_effect=RuntimeError("disk full")))
    monkeypatch.setattr(session, "rollback", rollback)

    assert await job_retention.prune_events(session) == 0

    rollback.assert_awaited_once()


async def test_a_database_without_the_events_tables_prunes_nothing(session: AsyncSession) -> None:
    survivor = make_event(ends=ago(hours=1), listed=False)
    await add(session, survivor)
    survivor_id = survivor.id  # the rollback below expires every loaded row
    await session.execute(text("DROP TABLE event_contacts"))
    await session.commit()

    assert await job_retention.prune_events(session) == 0

    # The session came out of the failure usable: it rolled back rather than wedging.
    assert await stored_ids(session) == {survivor_id}


# ═════════════════════════════════════════════════════════════════════════════
# how the job prune wires it in
# ═════════════════════════════════════════════════════════════════════════════

HIGH_WATER_PLUS = job_retention.STORAGE_HIGH_WATER_BYTES + 1


class _Result:
    def __init__(self, rowcount: int, scalar: int) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar(self) -> int:
        return self._scalar


class _JobPruneSession:
    """Records the statements ``prune_stale_jobs`` sends, and fails the ones it is told to."""

    def __init__(self, *, fail_on: str | None = None, size: int = 0) -> None:
        self.fail_on = fail_on
        self.size = size
        self.statements: list[str] = []
        self.params: dict[str, dict[str, Any]] = {}
        self.rollbacks = 0

    async def __aenter__(self) -> _JobPruneSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        if "pg_database_size" in sql:
            return _Result(0, self.size)
        self.statements.append(sql)
        self.params[sql] = dict(params or {})
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("relation does not exist")
        counts = {
            "DELETE FROM events": 4,
            "DELETE FROM outreach_threads": 2,
            "DELETE FROM company_news": 3,
            "DELETE FROM jobs": 5,
        }
        return _Result(next(n for key, n in counts.items() if key in sql), 0)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1

    def index_of(self, fragment: str) -> int:
        return next(i for i, sql in enumerate(self.statements) if fragment in sql)


async def test_the_job_prune_runs_the_event_prune_after_the_news_and_outreach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _JobPruneSession()
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: fake)

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_events"] == 4
    assert summary["deleted_by_age"] == 5 and summary["deleted_news"] == 3
    assert summary["deleted_outreach_threads"] == 2
    # News, then Outreach, then Events: the order the merged prune runs them in.
    assert fake.index_of("DELETE FROM company_news") < fake.index_of("DELETE FROM outreach_threads")
    assert fake.index_of("DELETE FROM outreach_threads") < fake.index_of("DELETE FROM events")
    events_sql = fake.statements[fake.index_of("DELETE FROM events")]
    assert fake.params[events_sql]["cap"] == job_retention.MAX_DELETIONS_PER_RUN


async def test_a_failed_event_prune_does_not_stop_the_job_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _JobPruneSession(fail_on="DELETE FROM events", size=HIGH_WATER_PLUS)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: fake)

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_events"] == 0
    assert fake.rollbacks == 1
    # The age rule before it counted, and the pressure rule after it still ran.
    assert summary["deleted_by_age"] == 5
    assert summary["deleted_news"] == 3
    assert summary["deleted_outreach_threads"] == 2
    assert summary["deleted_by_pressure"] == 5


async def test_a_failed_news_prune_does_not_stop_the_event_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _JobPruneSession(fail_on="DELETE FROM company_news")
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: fake)

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_news"] == 0
    assert summary["deleted_events"] == 4
    assert fake.rollbacks == 1


async def test_a_failed_outreach_prune_does_not_stop_the_event_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each step has its own rollback: a database without the Outreach tables still prunes events."""
    fake = _JobPruneSession(fail_on="DELETE FROM outreach_threads")
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: fake)

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_outreach_threads"] == 0
    assert summary["deleted_events"] == 4
    assert summary["deleted_news"] == 3
    assert fake.rollbacks == 1
