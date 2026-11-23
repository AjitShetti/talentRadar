"""
tests/test_job_boards_write.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Writing one board's India roles into ``jobs`` (``services/job_boards/write.py``).

The database is an in-memory SQLite with foreign keys on, built from the real models, so
the unique key a role is matched on (``uq_jobs_external_id_source``) and the company
foreign key are the real constraints. Each test names a rule the sweep depends on:

* the same posting found by live search and by the sweep is one row
* a role that stays on its board is never closed, wherever its location text moved to
* only a complete read closes anything, and never a row of another board
* a board is never attributed to a company by name
* nothing here commits, and nothing here makes a request

Every company, board and role is fictional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import storage.database as database
from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from domain.job_boards import AtsName, BoardRef
from domain.liveness import title_key
from ingestion.boards.base import BoardRead, build_job
from services import job_persistence
from services.cache_backend import CacheBackend
from services.job_boards import write as write_module
from services.job_boards.write import (
    MAX_ROLES_PER_BOARD,
    WriteSummary,
    board_company_id,
    write_board_roles,
)
from services.job_persistence import _seen_key, _stable_external_id, persist_live_jobs
from storage.database import Base
from storage.models import Company, JobBoard, RoleSighting
from storage.models import Job as JobRow

GREENHOUSE = BoardRef("greenhouse", "northwind")
INDIA = "Bengaluru, India"


@pytest_asyncio.fixture
async def factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory database, also wired in where ``persist_live_jobs`` opens its own session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Production's flags: nothing flushes on its own, and a commit expires nothing.
    sessions = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
    yield sessions
    await engine.dispose()


@pytest_asyncio.fixture
async def session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as db:
        yield db


@pytest.fixture(autouse=True)
async def memory_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


def role(
    number: int | str = 1,
    *,
    location: str | None = INDIA,
    title: str | None = None,
    ref: BoardRef = GREENHOUSE,
    url: str | None = None,
) -> Job:
    """A role as a reader returns it: the ATS's own id as ``external_id``, India or not."""
    return build_job(
        ref,
        external_id=f"ats-{number}",
        title=title or f"Backend Engineer {number}",
        url=url or f"https://boards.greenhouse.io/{ref.board}/jobs/{number}",
        location=location,
        description="Build and run the services behind the product.",
        company_name="Northwind Labs",
    )


def read_of(*roles: Job, complete: bool = True) -> BoardRead:
    return BoardRead(outcome="ok", roles=list(roles), total=len(roles), complete=complete)


async def add_board(
    session: AsyncSession,
    *,
    ats: AtsName = "greenhouse",
    board: str = "northwind",
    region: str | None = None,
    company_id: uuid.UUID | None = None,
    company_name: str | None = "Northwind Labs",
) -> JobBoard:
    row = JobBoard(
        ats=ats,
        board=board,
        region=region,
        company_id=company_id,
        company_name=company_name,
        discovered_via="manual",
    )
    session.add(row)
    await session.flush()
    return row


async def jobs_of(session: AsyncSession, source: str | None = None) -> list[JobRow]:
    stmt = select(JobRow).order_by(JobRow.title).execution_options(populate_existing=True)
    if source is not None:
        stmt = stmt.where(JobRow.source == source)
    return list((await session.execute(stmt)).scalars().all())


async def job_at(session: AsyncSession, url: str) -> JobRow:
    stmt = select(JobRow).where(JobRow.source_url == url).execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one()


async def write(
    session: AsyncSession, board: JobBoard, read: BoardRead, *, allow_new: bool = True
) -> tuple[WriteSummary, list[dict[str, Any]]]:
    return await write_board_roles(session, board, read, allow_new=allow_new)


# -- which roles are written ----------------------------------------------------------


async def test_new_india_roles_inserted_foreign_dropped(session: AsyncSession) -> None:
    board = await add_board(session)
    read = read_of(
        role(1, location="Bengaluru, India"),
        role(2, location="Pune, Maharashtra"),
        role(3, location="Skopje, North Macedonia"),
    )

    summary, items = await write(session, board, read)

    assert summary.inserted == 2
    assert summary.updated == 0 and summary.closed == 0
    assert summary.rejected_url == 0 and summary.skipped_budget == 0
    stored = await jobs_of(session)
    assert [row.title for row in stored] == ["Backend Engineer 1", "Backend Engineer 2"]
    assert len(items) == 2


async def test_a_role_with_no_location_is_not_written(session: AsyncSession) -> None:
    board = await add_board(session)

    summary, _ = await write(session, board, read_of(role(1, location=None)))

    assert summary.inserted == 0
    assert await jobs_of(session) == []


async def test_stored_row_carries_the_roles_fields_and_the_boards_source(
    session: AsyncSession,
) -> None:
    board = await add_board(session)
    posted = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    listed = role(7).model_copy(update={"posted_at": posted, "source_posted_at": posted})

    await write(session, board, read_of(listed))

    (row,) = await jobs_of(session)
    assert row.source == "greenhouse:northwind"
    assert row.title == "Backend Engineer 7"
    assert row.location_raw == INDIA
    assert row.enrichment_status == "raw"
    assert row.closed_at is None
    assert row.last_seen_at is not None
    assert row.posted_at is not None and row.posted_at.replace(tzinfo=UTC) == posted
    assert row.description_clean == "Build and run the services behind the product."


async def test_external_id_is_the_md5_of_the_url_not_the_ats_id(session: AsyncSession) -> None:
    board = await add_board(session)
    listed = role(11)
    assert listed.external_id == "ats-11"

    await write(session, board, read_of(listed))

    (row,) = await jobs_of(session)
    assert row.external_id == _stable_external_id(listed)
    assert row.external_id != "ats-11"
    assert len(row.external_id or "") == 32


async def test_a_url_that_fails_validation_is_rejected_not_written(session: AsyncSession) -> None:
    board = await add_board(session)
    read = read_of(
        role(1), role(2, url="https://example.com/"), role(3, url="https://boards.greenhouse.io/")
    )

    summary, _ = await write(session, board, read)

    assert summary.inserted == 1
    assert summary.rejected_url == 2
    assert [row.title for row in await jobs_of(session)] == ["Backend Engineer 1"]


async def test_at_most_200_roles_per_board(session: AsyncSession) -> None:
    assert MAX_ROLES_PER_BOARD == 200
    board = await add_board(session)

    summary, items = await write(session, board, read_of(*(role(n) for n in range(250))))

    assert summary.inserted == 200
    assert len(items) == 200
    count = (await session.execute(select(func.count()).select_from(JobRow))).scalar_one()
    assert count == 200


async def test_a_posting_listed_twice_in_one_read_is_one_row(session: AsyncSession) -> None:
    board = await add_board(session)

    summary, _ = await write(session, board, read_of(role(1), role(1)))

    assert summary.inserted == 1
    assert len(await jobs_of(session)) == 1


# -- the same posting from two paths -----------------------------------------------------


async def test_same_posting_from_live_search_is_one_row(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_embedding(_items: list[dict[str, Any]]) -> None:
        return None

    monkeypatch.setattr(job_persistence, "_embed_quietly", no_embedding)
    board = await add_board(session)
    listed = role(5)
    # What live search hands ``persist_live_jobs``: no id of its own, so the URL's MD5.
    live = Job(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        external_id=None,
        source="greenhouse:northwind",
        source_url=listed.source_url,
        title=listed.title,
        status=JobStatus.ACTIVE,
        employment_type=EmploymentType.FULL_TIME,
        seniority=SeniorityLevel.MID,
        location_raw=INDIA,
        country="IN",
        city="Bengaluru",
        is_remote=False,
        extra_metadata={"company_name": "Northwind Labs"},
    )
    stored = await persist_live_jobs([live])
    assert stored.inserted == 1

    summary, items = await write(session, board, read_of(listed))

    assert summary.inserted == 0 and summary.updated == 1
    assert items == []
    assert len(await jobs_of(session)) == 1


async def test_reread_refreshes_last_seen_without_seen_cache(session: AsyncSession) -> None:
    board = await add_board(session)
    listed = role(4)
    await write(session, board, read_of(listed))
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    await session.execute(update(JobRow).values(last_seen_at=an_hour_ago))
    # Live persistence skips a URL it saw this week; the board writer must not.
    await CacheBackend.set(_seen_key(listed.source_url or ""), "1", 3600)

    summary, items = await write(session, board, read_of(listed))

    assert summary.updated == 1 and summary.inserted == 0
    assert items == []
    (row,) = await jobs_of(session)
    assert row.last_seen_at is not None
    assert row.last_seen_at.replace(tzinfo=UTC) > an_hour_ago + timedelta(minutes=30)


async def test_a_role_seen_again_is_reopened(session: AsyncSession) -> None:
    board = await add_board(session)
    listed = role(4)
    await write(session, board, read_of(listed))
    await session.execute(update(JobRow).values(closed_at=datetime.now(UTC) - timedelta(days=1)))

    summary, _ = await write(session, board, read_of(listed))

    assert summary.updated == 1
    (row,) = await jobs_of(session)
    assert row.closed_at is None


async def test_a_sighting_is_recorded_for_a_new_role(session: AsyncSession) -> None:
    board = await add_board(session)

    await write(session, board, read_of(role(9, title="Staff Data Engineer")))

    (row,) = await jobs_of(session)
    sighting = await session.get(RoleSighting, (row.company_id, title_key("Staff Data Engineer")))
    assert sighting is not None
    assert sighting.times_seen == 1
    assert sighting.last_external_id == row.external_id


async def test_a_sighting_is_recorded_for_an_existing_role_too(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SQLite hands datetimes back naive, which ``record_sighting``'s repost arithmetic cannot
    # compare with an aware one, so a second real sighting cannot be exercised here: the call is
    # recorded instead.
    calls: list[dict[str, Any]] = []

    async def recorder(_session: AsyncSession, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(write_module, "record_sighting", recorder)
    board = await add_board(session)
    listed = role(9, title="Staff Data Engineer")
    await write(session, board, read_of(listed))
    (row,) = await jobs_of(session)

    await write(session, board, read_of(listed))

    assert len(calls) == 2
    again = calls[1]
    assert again["company_id"] == row.company_id
    assert again["title"] == "Staff Data Engineer"
    assert again["external_id"] == row.external_id
    assert isinstance(again["seen_at"], datetime)


# -- a role that moves is not a role that left ------------------------------------------


async def test_role_moved_to_remote_kept_not_closed(session: AsyncSession) -> None:
    board = await add_board(session)
    listed = role(3, location="Bengaluru, India")
    await write(session, board, read_of(listed))

    moved = role(3, location="Remote (India)")
    summary, _ = await write(session, board, read_of(moved))

    assert summary.closed == 0
    assert summary.updated == 1 and summary.inserted == 0
    (row,) = await jobs_of(session)
    assert row.closed_at is None
    assert row.location_raw == "Remote (India)"


async def test_role_moved_to_a_place_with_no_country_is_still_not_closed(
    session: AsyncSession,
) -> None:
    board = await add_board(session)
    await write(session, board, read_of(role(3, location="Bengaluru, India")))

    # "Remote" alone says nothing of India: not rewritten, but still on the board.
    summary, _ = await write(session, board, read_of(role(3, location="Remote")))

    assert summary.closed == 0 and summary.inserted == 0
    (row,) = await jobs_of(session)
    assert row.closed_at is None


async def test_a_role_beyond_the_cap_is_still_on_the_board_so_not_closed(
    session: AsyncSession,
) -> None:
    board = await add_board(session)
    late = role(230)
    await write(session, board, read_of(late))

    summary, _ = await write(session, board, read_of(*(role(n) for n in range(250))))

    assert summary.inserted == 200
    assert summary.closed == 0
    assert (await job_at(session, late.source_url or "")).closed_at is None


# -- closing ---------------------------------------------------------------------------


async def test_complete_read_closes_absent_role(session: AsyncSession) -> None:
    board = await add_board(session)
    stays, goes = role(1), role(2)
    await write(session, board, read_of(stays, goes))

    summary, _ = await write(session, board, read_of(stays))

    assert summary.closed == 1
    assert (await job_at(session, goes.source_url or "")).closed_at is not None
    assert (await job_at(session, stays.source_url or "")).closed_at is None


async def test_a_role_already_closed_keeps_the_date_it_was_first_found_gone(
    session: AsyncSession,
) -> None:
    board = await add_board(session)
    stays, goes = role(1), role(2)
    await write(session, board, read_of(stays, goes))
    first = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    await session.execute(
        update(JobRow).where(JobRow.source_url == goes.source_url).values(closed_at=first)
    )

    summary, _ = await write(session, board, read_of(stays))

    assert summary.closed == 0
    closed_at = (await job_at(session, goes.source_url or "")).closed_at
    assert closed_at is not None and closed_at.replace(tzinfo=UTC) == first


async def test_incomplete_read_closes_nothing(session: AsyncSession) -> None:
    board = await add_board(session)
    stays, goes = role(1), role(2)
    await write(session, board, read_of(stays, goes))

    summary, _ = await write(session, board, read_of(stays, complete=False))

    assert summary.closed == 0
    assert (await job_at(session, goes.source_url or "")).closed_at is None


async def test_a_read_that_lists_no_india_role_still_closes_what_is_gone(
    session: AsyncSession,
) -> None:
    board = await add_board(session)
    goes = role(2)
    await write(session, board, read_of(goes))

    summary, _ = await write(session, board, read_of(role(8, location="Skopje, North Macedonia")))

    assert summary.inserted == 0 and summary.closed == 1
    assert (await job_at(session, goes.source_url or "")).closed_at is not None


async def test_closing_only_touches_this_boards_rows(session: AsyncSession) -> None:
    board = await add_board(session)
    other = BoardRef("greenhouse", "contoso")
    other_board = await add_board(session, board="contoso", company_name="Contoso")
    theirs = role(1, ref=other)
    await write(session, other_board, read_of(theirs))
    await write(session, board, read_of(role(2)))

    summary, _ = await write(session, board, read_of(role(2)))

    assert summary.closed == 0
    assert (await job_at(session, theirs.source_url or "")).closed_at is None


async def test_a_de_read_never_closes_a_com_boards_roles(session: AsyncSession) -> None:
    de = BoardRef("personio", "northwind", "de")
    com = BoardRef("personio", "northwind", "com")
    assert de.source == com.source
    board_de = await add_board(session, ats="personio", board="northwind", region="de")
    board_com = await add_board(session, ats="personio", board="northwind", region="com")
    on_de = role(1, ref=de, url="https://northwind.jobs.personio.de/job/1")
    on_com = role(2, ref=com, url="https://northwind.jobs.personio.com/job/2")
    gone_de = role(3, ref=de, url="https://northwind.jobs.personio.de/job/3")
    await write(session, board_com, read_of(on_com))
    await write(session, board_de, read_of(on_de, gone_de))

    summary, _ = await write(session, board_de, read_of(on_de))

    assert summary.closed == 1
    assert (await job_at(session, on_com.source_url or "")).closed_at is None
    assert (await job_at(session, gone_de.source_url or "")).closed_at is not None


# -- which company ---------------------------------------------------------------------


async def test_unlinked_board_never_uses_name_matched_company(session: AsyncSession) -> None:
    # A listed employer called "Slice"; the open-list board ``greenhouse:slice`` is another company.
    listed = Company(name="Slice", domain="slice.in")
    session.add(listed)
    board = await add_board(session, board="slice", company_name="Slice")
    skopje = BoardRef("greenhouse", "slice")
    await session.flush()

    summary, _ = await write(session, board, read_of(role(1, ref=skopje)))

    assert summary.inserted == 1
    (row,) = await jobs_of(session)
    assert row.company_id != listed.id
    placeholder = await session.get(Company, row.company_id)
    assert placeholder is not None
    assert placeholder.domain == "greenhouse-slice.board.talentradar.internal"
    assert placeholder.name == "Slice"


async def test_the_placeholder_company_is_reused_by_the_next_write(session: AsyncSession) -> None:
    board = await add_board(session, company_name=None)

    first = await board_company_id(session, board)
    second = await board_company_id(session, board)
    await write(session, board, read_of(role(1), role(2)))

    assert first == second
    count = (
        await session.execute(
            select(func.count())
            .select_from(Company)
            .where(Company.domain == "greenhouse-northwind.board.talentradar.internal")
        )
    ).scalar_one()
    assert count == 1
    placeholder = await session.get(Company, first)
    assert placeholder is not None and placeholder.name == "northwind"
    assert {row.company_id for row in await jobs_of(session)} == {first}


async def test_a_linked_board_writes_to_its_company(session: AsyncSession) -> None:
    company = Company(name="Northwind Labs", domain="northwind.example")
    session.add(company)
    await session.flush()
    board = await add_board(session, company_id=company.id)

    assert await board_company_id(session, board) == company.id
    await write(session, board, read_of(role(1)))

    assert {row.company_id for row in await jobs_of(session)} == {company.id}
    placeholders = (
        await session.execute(
            select(func.count())
            .select_from(Company)
            .where(Company.domain.like("%.board.talentradar.internal"))
        )
    ).scalar_one()
    assert placeholders == 0


async def test_a_linked_board_moves_a_live_search_row_to_its_company(
    session: AsyncSession,
) -> None:
    company = Company(name="Northwind Labs", domain="northwind.example")
    session.add(company)
    await session.flush()
    board = await add_board(session)
    listed = role(1)
    await write(session, board, read_of(listed))
    board.company_id = company.id  # a later discovery step linked the board to a company we hold
    await session.flush()

    summary, _ = await write(session, board, read_of(listed))

    assert summary.updated == 1
    assert (await job_at(session, listed.source_url or "")).company_id == company.id


# -- budget, embedding, transaction -------------------------------------------------------


async def test_allow_new_false_only_updates(session: AsyncSession) -> None:
    board = await add_board(session)
    known = role(1)
    await write(session, board, read_of(known))

    summary, items = await write(session, board, read_of(known, role(2), role(3)), allow_new=False)

    assert summary.updated == 1
    assert summary.inserted == 0
    assert summary.skipped_budget == 2
    assert items == []
    assert [row.title for row in await jobs_of(session)] == ["Backend Engineer 1"]


async def test_a_board_with_nothing_to_write_creates_no_placeholder_company(
    session: AsyncSession,
) -> None:
    board = await add_board(session)

    await write(session, board, read_of(role(1)), allow_new=False)

    count = (await session.execute(select(func.count()).select_from(Company))).scalar_one()
    assert count == 0


async def test_embedding_items_are_for_new_rows_only(session: AsyncSession) -> None:
    board = await add_board(session)
    first, second = role(1), role(2)

    _, items = await write(session, board, read_of(first))
    assert len(items) == 1
    stored = (await jobs_of(session))[0]
    item = items[0]
    assert item["job_id"] == stored.external_id
    assert item["internal_job_id"] == stored.id
    assert "Backend Engineer 1" in item["text"] and "Northwind Labs" in item["text"]
    metadata = item["metadata"]
    assert metadata["title"] == "Backend Engineer 1"
    assert metadata["company"] == "Northwind Labs"
    assert metadata["location"] == INDIA
    assert metadata["source"] == "greenhouse:northwind"
    assert metadata["source_url"] == first.source_url

    _, items = await write(session, board, read_of(first, second))
    assert [entry["metadata"]["title"] for entry in items] == ["Backend Engineer 2"]


async def test_nothing_is_committed(session: AsyncSession) -> None:
    board = await add_board(session)
    goes = role(2)
    await write(session, board, read_of(role(1), goes))
    await session.commit()
    await write(session, board, read_of(role(1), role(3)))

    await session.rollback()

    assert [row.title for row in await jobs_of(session)] == [
        "Backend Engineer 1",
        "Backend Engineer 2",
    ]
    assert (await job_at(session, goes.source_url or "")).closed_at is None


async def test_the_summary_starts_at_zero() -> None:
    assert WriteSummary() == WriteSummary(
        inserted=0, updated=0, closed=0, rejected_url=0, skipped_budget=0
    )
