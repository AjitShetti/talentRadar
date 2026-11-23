"""
tests/test_job_retention_boards.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Rule 1 of ``prune_stale_jobs`` for job-board rows.

A row whose ``source`` is one of the nine ATS prefixes (``"lever:meesho"``) is aged by
``coalesce(last_seen_at, created_at)``: a role the sweep still sees on its board is kept,
however old the row is, and a role gone from its board for longer than the window goes.
Every other row is still aged by ``created_at``, and a row somebody applied to is never
deleted.

The prune opens its session through ``storage.database.AsyncSessionLocal``, which the
fixture replaces with an in-memory SQLite database, so the statement that runs here is the
one that runs in production. Every company and person is fictional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import storage.database as database
from domain.job_boards import ATS
from services import job_retention
from storage.database import Base
from storage.models import (
    ApplicationStatus,
    Company,
    Job,
    JobApplication,
    JobStatus,
    User,
)

TABLES = (
    "users",
    "companies",
    "ingestion_runs",
    "resumes",
    "cover_letters",
    "jobs",
    "job_applications",
)

#: One day past the retention window: clearly over it, never on the boundary.
PAST_WINDOW = job_retention.RAW_RETENTION_DAYS + 1


def ago(*, days: float = 0, hours: float = 0) -> datetime:
    return datetime.now(UTC) - timedelta(days=days, hours=hours)


@dataclass
class Store:
    factory: async_sessionmaker[AsyncSession]
    company_id: uuid.UUID
    user_id: uuid.UUID

    async def add_job(
        self,
        *,
        source: str,
        created: datetime,
        seen: datetime | None,
        applied: bool = False,
    ) -> uuid.UUID:
        job = Job(
            id=uuid.uuid4(),
            company_id=self.company_id,
            external_id=uuid.uuid4().hex,
            source=source,
            title="Backend Engineer",
            status=JobStatus.ACTIVE,
            enrichment_status="raw",
            created_at=created,
            updated_at=created,
            last_seen_at=seen,
        )
        async with self.factory() as session:
            session.add(job)
            if applied:
                session.add(
                    JobApplication(
                        user_id=self.user_id,
                        job_id=job.id,
                        status=ApplicationStatus.APPLIED,
                    )
                )
            await session.commit()
        return job.id

    async def remaining(self) -> set[uuid.UUID]:
        async with self.factory() as session:
            return set((await session.execute(select(Job.id))).scalars())


@pytest_asyncio.fixture
async def store(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Store]:
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
    company = Company(id=uuid.uuid4(), domain="acme.talentradar.internal", name="Acme")
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", hashed_password="x"
    )
    async with factory() as session:
        session.add_all([company, user])
        await session.commit()

    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    yield Store(factory=factory, company_id=company.id, user_id=user.id)
    await engine.dispose()


async def test_board_row_seen_recently_is_kept(store: Store) -> None:
    kept = await store.add_job(source="lever:meesho", created=ago(days=60), seen=ago(days=2))

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_by_age"] == 0
    assert kept in await store.remaining()


async def test_board_row_unseen_45_days_is_pruned(store: Store) -> None:
    gone = await store.add_job(
        source="lever:meesho", created=ago(days=60), seen=ago(days=PAST_WINDOW)
    )

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_by_age"] == 1
    assert gone not in await store.remaining()


async def test_live_row_still_aged_by_created_at(store: Store) -> None:
    live = await store.add_job(source="linkedin", created=ago(days=60), seen=ago(days=1))

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_by_age"] == 1
    assert live not in await store.remaining()


@pytest.mark.parametrize("ats", ATS)
async def test_every_ats_prefix_is_aged_by_last_sighting(store: Store, ats: str) -> None:
    kept = await store.add_job(source=f"{ats}:acme", created=ago(days=60), seen=ago(days=2))

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert kept in await store.remaining()


@pytest.mark.parametrize("source", ["lever-mirror:meesho", "my-lever:meesho", "lever"])
async def test_a_source_that_only_contains_an_ats_name_is_not_a_board(
    store: Store, source: str
) -> None:
    pruned = await store.add_job(source=source, created=ago(days=60), seen=ago(days=2))

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert pruned not in await store.remaining()


async def test_an_applied_board_row_is_never_pruned(store: Store) -> None:
    applied = await store.add_job(
        source="lever:meesho",
        created=ago(days=60),
        seen=ago(days=PAST_WINDOW),
        applied=True,
    )

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_by_age"] == 0
    assert applied in await store.remaining()
