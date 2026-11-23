"""
tests/test_ats_live_roster.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Live search reads the roster (``ingestion/scrapers/ats_scraper.py``), and a role it finds is
the same row the sweep writes.

The database is an in-memory SQLite built from the real models, wired in where the roster read
and ``persist_live_jobs`` open their own sessions. Every request goes to a fake ATS host that
serves one India role per board and records what was asked. Each test names a rule:

* live search reads the 25 active Greenhouse and Ashby boards with the most India roles
* a Lever, Workable, Recruitee or Keka board is never read by live search, however large
* with no database, or an empty roster, live search reads the constants (minus the dead boards)
* a roster that cannot answer in time is not waited for: the constants are read instead
* the roster is cached for an hour under ``tr:boards:live-top``, as JSON of [ats, board] pairs
* a role found by live search and by the sweep is one row, keyed by the URL's MD5

Every company, board and role is fictional.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import storage.database as database
from domain.entities import Job
from domain.job_boards import BoardRef
from ingestion.boards import READERS
from ingestion.engine import job_dicts_to_entities, job_to_dict
from ingestion.scrapers import ats_scraper
from ingestion.scrapers.ats_scraper import (
    ASHBY_COMPANIES,
    GREENHOUSE_COMPANIES,
    ATSScraper,
)
from services import job_persistence
from services.cache_backend import CacheBackend
from services.job_boards.write import write_board_roles
from services.job_persistence import _stable_external_id, persist_live_jobs
from storage.database import Base
from storage.models import Job as JobRow
from storage.models import JobBoard

LIVE_CACHE_KEY = "tr:boards:live-top"
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


# -- the database and the cache --------------------------------------------------------------


@pytest_asyncio.fixture
async def factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory database, wired in where the roster and ``persist_live_jobs`` open sessions."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
    yield sessions
    await engine.dispose()


@pytest.fixture(autouse=True)
async def memory_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


async def seed_board(
    factory: async_sessionmaker[AsyncSession],
    ats: str,
    board: str,
    *,
    status: str = "active",
    india_jobs: int | None = 1,
) -> None:
    async with factory() as session:
        session.add(
            JobBoard(
                ats=ats,
                board=board,
                discovered_via="manual",
                status=status,
                india_jobs=india_jobs,
                first_seen_at=NOW,
            )
        )
        await session.commit()


# -- the ATS hosts -----------------------------------------------------------------------------


def greenhouse_payload(board: str, posting: int = 4242) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": posting,
                "title": f"Backend Engineer at {board}",
                "absolute_url": f"https://boards.greenhouse.io/{board}/jobs/{posting}",
                "location": {"name": "Bengaluru, India"},
                "content": "<p>Build the services.</p>",
                "first_published": "2026-11-01T10:00:00Z",
                "updated_at": "2026-11-02T10:00:00Z",
            }
        ]
    }


def ashby_payload(board: str) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": "a-1",
                "title": f"Backend Engineer at {board}",
                "jobUrl": f"https://jobs.ashbyhq.com/{board}/a-1",
                "location": "Bengaluru, India",
                "isRemote": False,
                "publishedAt": "2026-11-01T10:00:00Z",
            }
        ]
    }


class AtsHosts:
    """Serves one India role on any Greenhouse or Ashby board, and records every request."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(f"{request.url.host}{request.url.path}")
        parts = request.url.path.strip("/").split("/")
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json=greenhouse_payload(parts[2]))
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(200, json=ashby_payload(parts[-1]))
        return httpx.Response(404, text="")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def boards_read(self) -> set[tuple[str, str]]:
        """The (ats, board) pairs the fake hosts were asked about, one per request."""
        found: set[tuple[str, str]] = set()
        for asked in self.asked:
            host, _, path = asked.partition("/")
            parts = path.strip("/").split("/")
            if host == "boards-api.greenhouse.io":
                found.add(("greenhouse", parts[2]))
            elif host == "api.ashbyhq.com":
                found.add(("ashby", parts[-1]))
            else:
                found.add((host, "?"))
        return found


@pytest.fixture
def hosts(monkeypatch: pytest.MonkeyPatch) -> AtsHosts:
    """The fake ATS hosts, behind the client live search opens for its fan-out."""
    served = AtsHosts()
    monkeypatch.setattr(ats_scraper, "_board_client", served.client)
    return served


async def search() -> list[Job]:
    return await ATSScraper.search_all_ats("", None, None)


# -- what live search reads -------------------------------------------------------------------


async def test_live_reads_top_roster_boards(
    factory: async_sessionmaker[AsyncSession], hosts: AtsHosts
) -> None:
    for number in range(1, 31):
        await seed_board(factory, "greenhouse", f"gh-{number:02d}", india_jobs=number)
    await seed_board(factory, "ashby", "zeta", india_jobs=500)
    await seed_board(factory, "greenhouse", "idle", status="no_india", india_jobs=999)
    await seed_board(factory, "ashby", "candidate", status="candidate", india_jobs=999)

    await search()

    ranked = sorted(
        [("ashby", "zeta", 500)]
        + [("greenhouse", f"gh-{number:02d}", number) for number in range(1, 31)],
        key=lambda row: (-row[2], row[1]),
    )
    expected = {(ats, board) for ats, board, _ in ranked[:25]}
    assert len(expected) == 25
    assert hosts.boards_read() == expected


async def test_live_never_reads_slow_ats(
    factory: async_sessionmaker[AsyncSession], hosts: AtsHosts
) -> None:
    await seed_board(factory, "greenhouse", "gh-ok", india_jobs=1)
    await seed_board(factory, "lever", "lever-top", india_jobs=900)
    await seed_board(factory, "workable", "workable-top", india_jobs=800)
    await seed_board(factory, "recruitee", "recruitee-top", india_jobs=700)
    await seed_board(factory, "keka", "keka-top", india_jobs=600)

    await search()

    assert hosts.boards_read() == {("greenhouse", "gh-ok")}
    assert not any("lever" in asked or "workable" in asked for asked in hosts.asked)


async def test_live_falls_back_to_constants_without_db(
    monkeypatch: pytest.MonkeyPatch, hosts: AtsHosts
) -> None:
    def no_database() -> AsyncSession:
        raise RuntimeError("no database")

    monkeypatch.setattr(database, "AsyncSessionLocal", no_database)

    await search()

    assert hosts.boards_read() == {("greenhouse", s) for s in GREENHOUSE_COMPANIES} | {
        ("ashby", s) for s in ASHBY_COMPANIES
    }
    # A fallback is not cached: the roster is asked again on the next search.
    assert await CacheBackend.get(LIVE_CACHE_KEY) is None


async def test_live_falls_back_to_constants_when_the_roster_is_empty(
    factory: async_sessionmaker[AsyncSession], hosts: AtsHosts
) -> None:
    await seed_board(factory, "lever", "lever-only", india_jobs=50)

    await search()

    assert hosts.boards_read() == {("greenhouse", s) for s in GREENHOUSE_COMPANIES} | {
        ("ashby", s) for s in ASHBY_COMPANIES
    }


async def test_a_slow_roster_is_not_waited_for(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    hosts: AtsHosts,
) -> None:
    from services.job_boards import store

    async def never_answers(_session: object, _n: int = 25) -> list[BoardRef]:
        await asyncio.sleep(60)
        return []

    monkeypatch.setattr(store, "top_live_boards", never_answers)
    monkeypatch.setattr(ats_scraper, "_ROSTER_LOOKUP_SECONDS", 0.05)

    await search()

    assert hosts.boards_read() == {("greenhouse", s) for s in GREENHOUSE_COMPANIES} | {
        ("ashby", s) for s in ASHBY_COMPANIES
    }


async def test_the_roster_is_cached_for_an_hour_as_pairs(
    factory: async_sessionmaker[AsyncSession],
    hosts: AtsHosts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_board(factory, "greenhouse", "acme", india_jobs=3)
    await seed_board(factory, "ashby", "zeta", india_jobs=9)
    calls: list[tuple[str, str, int]] = []
    original_set = CacheBackend.set

    async def recording_set(key: str, value: str, ttl_seconds: int) -> None:
        calls.append((key, value, ttl_seconds))
        await original_set(key, value, ttl_seconds)

    monkeypatch.setattr(CacheBackend, "set", recording_set)

    await search()

    (key, value, ttl) = calls[0]
    assert key == LIVE_CACHE_KEY and ttl == 3600
    assert json.loads(value) == [["ashby", "zeta"], ["greenhouse", "acme"]]

    # The next search reads the cache, not the table: no database is needed for it.
    def no_database() -> AsyncSession:
        raise RuntimeError("the cache should have answered")

    monkeypatch.setattr(database, "AsyncSessionLocal", no_database)
    hosts.asked.clear()
    await search()
    assert hosts.boards_read() == {("ashby", "zeta"), ("greenhouse", "acme")}


async def test_a_corrupt_cached_roster_is_ignored(
    factory: async_sessionmaker[AsyncSession], hosts: AtsHosts
) -> None:
    await seed_board(factory, "greenhouse", "acme", india_jobs=3)
    await CacheBackend.set(LIVE_CACHE_KEY, '{"not": "a list of pairs"}', 3600)

    await search()

    assert hosts.boards_read() == {("greenhouse", "acme")}


def test_constants_drop_dead_boards() -> None:
    assert "postman" not in GREENHOUSE_COMPANIES
    assert "vercel" not in ASHBY_COMPANIES
    assert "stripe" in GREENHOUSE_COMPANIES
    assert "openai" in ASHBY_COMPANIES


# -- a role found by live search and by the sweep is one row -----------------------------------


def _key_of(row: JobRow) -> tuple[str | None, str]:
    return row.external_id, row.source


async def _stored(session: AsyncSession) -> list[JobRow]:
    return list((await session.execute(select(JobRow))).scalars().all())


async def test_persisting_a_board_role_keys_it_by_its_url(
    factory: async_sessionmaker[AsyncSession],
    hosts: AtsHosts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader's own id (4242) is the ATS's, not the row's: the row key is the URL's MD5."""

    async def no_embedding(_items: list[dict[str, Any]]) -> None:
        return None

    monkeypatch.setattr(job_persistence, "_embed_quietly", no_embedding)
    live = await ATSScraper.fetch_greenhouse_company(
        "Stripe", "", None, None, client=hosts.client()
    )
    (role,) = live
    assert role.external_id == "4242"

    summary = await persist_live_jobs(live)

    assert summary.inserted == 1
    async with factory() as session:
        (row,) = await _stored(session)
    assert _key_of(row) == (_stable_external_id(role), "greenhouse:stripe")


async def test_live_and_sweep_agree_on_row_key(
    factory: async_sessionmaker[AsyncSession],
    hosts: AtsHosts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A role from the live ATS path, converted the way the search response and its background
    write convert it (``job_to_dict`` then ``job_dicts_to_entities``), is stored under the same
    (external_id, source) the sweep stores for the same posting, and the two make one row.
    """

    async def no_embedding(_items: list[dict[str, Any]]) -> None:
        return None

    monkeypatch.setattr(job_persistence, "_embed_quietly", no_embedding)
    async with factory() as session:
        session.add(
            JobBoard(
                ats="greenhouse",
                board="stripe",
                discovered_via="manual",
                status="active",
                first_seen_at=NOW,
            )
        )
        await session.commit()

    # Mixed case in, the board's canonical id out: the source must match what the sweep stores.
    live = await ATSScraper.fetch_greenhouse_company(
        "Stripe", "", None, None, client=hosts.client()
    )
    assert [role.source for role in live] == ["greenhouse:stripe"]
    entities = job_dicts_to_entities([job_to_dict(role) for role in live])
    summary = await persist_live_jobs(entities)
    assert summary.inserted == 1

    async with factory() as session:
        board = (
            await session.execute(select(JobBoard).where(JobBoard.board == "stripe"))
        ).scalar_one()
        read = await READERS["greenhouse"](hosts.client(), BoardRef("greenhouse", "stripe"))
        swept_summary, _items = await write_board_roles(session, board, read, allow_new=True)
        await session.commit()
        stored = await _stored(session)

    assert swept_summary.inserted == 0 and swept_summary.updated == 1
    (row,) = stored
    (swept,) = read.roles
    assert _key_of(row) == (_stable_external_id(swept), "greenhouse:stripe")
    assert _key_of(row) == (_stable_external_id(live[0]), "greenhouse:stripe")
