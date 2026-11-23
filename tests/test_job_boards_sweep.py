"""
tests/test_job_boards_sweep.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The roster's store (``services/job_boards/store.py``) and the paced sweep
(``services/job_boards/sweep.py``).

The database is an in-memory SQLite built from the real models, wired in where the sweep
opens its own short sessions. The readers are fakes put in ``READERS``, and time is a fake
clock and a fake sleep, so a day's pacing runs in milliseconds. Each sweep test names a rule:

* one queue per ATS, each ``PACE_SECONDS`` apart, and no board started after 480 s
* a 429 stops only that ATS's queue, records its failure, and leaves the boards behind it
  untouched
* an open breaker skips the ATS; ``gone`` and ``blocked`` boards are never read
* no database session is open while a request is in flight
* new rows stop at the size guard and at the per-run and total caps, while rows already
  stored keep being refreshed

Every company, board and role is fictional.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import storage.database as database
from domain.job_boards import ATS, PACE_SECONDS, AtsName, BoardRef, Schedule
from ingestion.boards import READERS
from ingestion.boards.base import BoardRead, build_job, failed
from services.cache_backend import CacheBackend
from services.job_boards import store, sweep
from services.job_boards.store import apply_schedule, due_boards, top_live_boards, upsert_board
from services.job_boards.sweep import sweep as run_sweep
from services.job_boards.write import write_board_roles
from services.job_persistence import build_job_kwargs
from services.source_health import FAILURE_THRESHOLD, SourceHealthService
from storage.database import Base
from storage.models import Company, JobBoard
from storage.models import Job as JobRow

INDIA = "Bengaluru, India"
ABROAD = "Skopje, North Macedonia"
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)

Reader = Callable[[httpx.AsyncClient, BoardRef], Awaitable[BoardRead]]


# -- the database, the clock and the cache --------------------------------------------------


class OpenSessions:
    """Wraps the session factory and counts the sessions that are open right now."""

    def __init__(self, inner: async_sessionmaker[AsyncSession]) -> None:
        self.inner = inner
        self.open = 0

    def __call__(self) -> Any:
        counter = self

        class Context:
            async def __aenter__(self) -> AsyncSession:
                counter.open += 1
                self.session = counter.inner()
                return await self.session.__aenter__()

            async def __aexit__(self, *exc: Any) -> Any:
                try:
                    return await self.session.__aexit__(*exc)
                finally:
                    counter.open -= 1

        return Context()


@pytest_asyncio.fixture
async def factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[OpenSessions]:
    """An in-memory database, wired in where the sweep (and ``_embed_quietly``) open sessions."""
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
    counting = OpenSessions(sessions)
    monkeypatch.setattr(sweep, "AsyncSessionLocal", counting)
    monkeypatch.setattr(database, "AsyncSessionLocal", counting)
    yield counting
    await engine.dispose()


@pytest.fixture(autouse=True)
async def memory_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


class FakeTime:
    """A clock that only moves when a test moves it; ``sleep`` records what it was asked for."""

    def __init__(self, start: float = 0.0, *, sleeping_takes_time: bool = False) -> None:
        self.now = start
        self.sleeping_takes_time = sleeping_takes_time
        self.sleeps: list[float] = []
        self.events: list[str] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.events.append(f"sleep:{seconds:g}")
        if self.sleeping_takes_time:
            self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def embedded(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    """Replaces the embedding step: records the batches it is handed."""
    batches: list[list[dict[str, Any]]] = []

    async def record(items: list[dict[str, Any]]) -> None:
        batches.append(items)

    monkeypatch.setattr(sweep, "_embed_quietly", record)
    return batches


# Boards added one after another were first seen one second apart, so that boards with no date
# of their own are due in the order they were added (SQLite's own default ties them).
_first_seen = itertools.count()


# -- seeding and reading back ---------------------------------------------------------------


async def add_board(
    factory: OpenSessions,
    ats: AtsName = "greenhouse",
    board: str = "northwind",
    *,
    status: str = "candidate",
    next_read_at: datetime | None = None,
    india_jobs: int | None = None,
    company_name: str | None = None,
    **extra: Any,
) -> uuid.UUID:
    extra.setdefault(
        "first_seen_at", datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=next(_first_seen))
    )
    async with factory() as session:
        row = JobBoard(
            ats=ats,
            board=board,
            discovered_via="manual",
            status=status,
            next_read_at=next_read_at,
            india_jobs=india_jobs,
            company_name=company_name,
            **extra,
        )
        session.add(row)
        await session.commit()
        return row.id


async def board_of(factory: OpenSessions, board_id: uuid.UUID) -> JobBoard:
    async with factory() as session:
        row: JobBoard = (
            await session.execute(
                select(JobBoard)
                .where(JobBoard.id == board_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        session.expunge(row)
        return row


async def jobs_of(factory: OpenSessions, source: str | None = None) -> list[JobRow]:
    async with factory() as session:
        stmt = select(JobRow).order_by(JobRow.title)
        if source is not None:
            stmt = stmt.where(JobRow.source == source)
        return list((await session.execute(stmt)).scalars().all())


def naive(value: datetime | None) -> datetime:
    """A stored date (it must be set) on the wall clock: SQLite hands back what it was given without the zone."""
    assert value is not None
    return value.replace(tzinfo=None)


def role(ref: BoardRef, number: int | str, location: str = INDIA) -> Any:
    return build_job(
        ref,
        external_id=f"ats-{number}",
        title=f"Backend Engineer {number}",
        url=f"https://boards.greenhouse.io/{ref.board}/jobs/{number}",
        location=location,
        description="Build and run the services behind the product.",
    )


def ok_read(*roles: Any, complete: bool = True, company_name: str | None = None) -> BoardRead:
    return BoardRead(
        outcome="ok",
        roles=list(roles),
        total=len(roles),
        complete=complete,
        company_name=company_name,
    )


class Fake:
    """A reader for one ATS: ``answers`` maps a board to what it returns (default: an empty board)."""

    def __init__(
        self,
        answers: Mapping[str, BoardRead | Exception] | None = None,
        *,
        time: FakeTime | None = None,
        advance: float = 0.0,
        sessions: OpenSessions | None = None,
    ) -> None:
        self.answers = answers or {}
        self.time = time
        self.advance = advance
        self.sessions = sessions
        self.calls: list[str] = []
        self.open_sessions_during_reads: list[int] = []

    async def __call__(self, client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
        self.calls.append(ref.board)
        if self.time is not None:
            self.time.events.append(f"read:{ref.board}")
            self.time.now += self.advance
        if self.sessions is not None:
            self.open_sessions_during_reads.append(self.sessions.open)
        answer = self.answers.get(ref.board, ok_read())
        if isinstance(answer, Exception):
            raise answer
        return answer


def install(monkeypatch: pytest.MonkeyPatch, **readers: Reader) -> None:
    for ats, reader in readers.items():
        monkeypatch.setitem(READERS, cast("AtsName", ats), reader)


# -- store: adding a board -------------------------------------------------------------------


async def test_upsert_board_inserts_a_candidate_and_says_so(factory: OpenSessions) -> None:
    async with factory() as session:
        inserted = await upsert_board(
            session,
            BoardRef("greenhouse", "northwind"),
            via="open_list",
            company_name="  Northwind  ",
        )
        await session.commit()

    assert inserted is True
    (row,) = await _all_boards(factory)
    assert (row.ats, row.board, row.region) == ("greenhouse", "northwind", None)
    assert row.status == "candidate"
    assert row.discovered_via == "open_list"
    assert row.company_name == "Northwind"
    assert row.next_read_at is None, "a new board is due at once"
    assert (row.failures, row.zero_india_reads, row.not_found_reads) == (0, 0, 0)
    assert row.first_seen_at is not None


async def _all_boards(factory: OpenSessions) -> list[JobBoard]:
    async with factory() as session:
        return list((await session.execute(select(JobBoard))).scalars().all())


async def test_upsert_board_leaves_an_existing_board_alone(factory: OpenSessions) -> None:
    board_id = await add_board(factory, status="blocked", failures=6)
    async with factory() as session:
        inserted = await upsert_board(session, BoardRef("greenhouse", "northwind"), via="yc")
        await session.commit()

    assert inserted is False
    row = await board_of(factory, board_id)
    assert row.status == "blocked" and row.failures == 6, "being found again resets nothing"
    assert row.discovered_via == "manual"
    assert len(await _all_boards(factory)) == 1


async def test_upsert_board_gains_a_company_it_lacked_and_never_swaps_one(
    factory: OpenSessions,
) -> None:
    async with factory() as session:
        first = Company(id=uuid.uuid4(), name="Northwind Labs")
        second = Company(id=uuid.uuid4(), name="Another Company")
        session.add_all([first, second])
        await session.commit()
    board_id = await add_board(factory)

    async with factory() as session:
        assert (
            await upsert_board(
                session, BoardRef("greenhouse", "northwind"), via="yc", company_id=first.id
            )
            is False
        )
        await session.commit()
    assert (await board_of(factory, board_id)).company_id == first.id

    async with factory() as session:
        await upsert_board(
            session, BoardRef("greenhouse", "northwind"), via="yc", company_id=second.id
        )
        await session.commit()
    assert (await board_of(factory, board_id)).company_id == first.id


async def test_upsert_board_keys_on_ats_board_and_region(factory: OpenSessions) -> None:
    refs = [
        BoardRef("personio", "acme", "de"),
        BoardRef("personio", "acme", "com"),
        BoardRef("greenhouse", "acme"),
        BoardRef("lever", "acme"),
    ]
    async with factory() as session:
        assert [await upsert_board(session, ref, via="manual") for ref in refs] == [True] * 4
        assert [await upsert_board(session, ref, via="manual") for ref in refs] == [False] * 4
        await session.commit()

    assert len(await _all_boards(factory)) == 4


async def test_upsert_board_survives_losing_an_insert_race(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is not there when looked for and is there when inserted: the loser says False, and its transaction lives."""
    async with factory() as session:
        company = Company(id=uuid.uuid4(), name="Northwind Labs")
        session.add(company)
        await session.commit()
    await add_board(factory)

    real_find = store._find
    looked = 0

    async def blind_once(session: AsyncSession, ref: BoardRef) -> JobBoard | None:
        nonlocal looked
        looked += 1
        return None if looked == 1 else await real_find(session, ref)

    monkeypatch.setattr(store, "_find", blind_once)
    async with factory() as session:
        inserted = await upsert_board(
            session, BoardRef("greenhouse", "northwind"), via="yc", company_id=company.id
        )
        # The caller's transaction is still good: it can go on to add another board.
        assert await upsert_board(session, BoardRef("greenhouse", "other"), via="yc") is True
        await session.commit()

    assert inserted is False
    boards = await _all_boards(factory)
    assert {row.board for row in boards} == {"northwind", "other"}
    assert next(row for row in boards if row.board == "northwind").company_id == company.id


# -- store: which boards are due -------------------------------------------------------------


async def test_due_boards_selects_by_status_and_date_never_read_first(
    factory: OpenSessions,
) -> None:
    day = timedelta(days=1)
    never = await add_board(factory, board="never")
    old = await add_board(factory, board="old", status="active", next_read_at=NOW - 3 * day)
    older = await add_board(factory, board="older", status="no_india", next_read_at=NOW - 9 * day)
    exact = await add_board(factory, board="exact", next_read_at=NOW)
    await add_board(
        factory, board="later", status="active", next_read_at=NOW + timedelta(minutes=1)
    )
    await add_board(factory, board="gone", status="gone", next_read_at=NOW - 30 * day)
    await add_board(factory, board="blocked", status="blocked")

    async with factory() as session:
        due = await due_boards(session, 10, now=NOW)

    assert [row.id for row in due] == [never, older, old, exact]


async def test_due_boards_is_limited(factory: OpenSessions) -> None:
    for number in range(5):
        await add_board(factory, board=f"b{number}", next_read_at=NOW - timedelta(days=number + 1))

    async with factory() as session:
        due = await due_boards(session, 2, now=NOW)

    assert [row.board for row in due] == ["b4", "b3"], "oldest first"


async def add_many(
    factory: OpenSessions, ats: AtsName, count: int, *, prefix: str = "b", **extra: Any
) -> list[uuid.UUID]:
    """``count`` boards of one ATS in one transaction, first seen in numbered order."""
    async with factory() as session:
        rows = [
            JobBoard(
                ats=ats,
                board=f"{prefix}{number}",
                discovered_via="manual",
                first_seen_at=datetime(2026, 1, 1, tzinfo=UTC)
                + timedelta(seconds=next(_first_seen)),
                **extra,
            )
            for number in range(count)
        ]
        session.add_all(rows)
        await session.commit()
        return [row.id for row in rows]


def test_queue_caps_are_what_the_pace_lets_one_run_read() -> None:
    caps = sweep.queue_caps()

    assert set(caps) == set(ATS)
    assert caps["workable"] == 48
    assert caps["keka"] == 240
    assert caps["lever"] == caps["rippling"] == caps["recruitee"] == 480
    assert caps["greenhouse"] == caps["ashby"] == 960
    assert all(caps[ats] == int(sweep.RUN_BUDGET_SECONDS // PACE_SECONDS[ats]) for ats in ATS)


def test_queue_caps_follow_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep, "RUN_BUDGET_SECONDS", 100)

    assert sweep.queue_caps()["workable"] == 10
    assert sweep.queue_caps()["greenhouse"] == 200


async def test_due_boards_never_starves_other_ats(factory: OpenSessions) -> None:
    await add_many(factory, "workable", 400, prefix="w")
    greens = await add_many(factory, "greenhouse", 5, prefix="g")

    async with factory() as session:
        due = await due_boards(session, 300, now=NOW, per_ats_caps=sweep.queue_caps())

    by_ats = [row.ats for row in due]
    assert {row.id for row in due if row.ats == "greenhouse"} == set(greens)
    assert by_ats.count("greenhouse") == 5
    assert by_ats.count("workable") == 48, "what one run can read at Workable's pace, and no more"
    assert len(due) == 53, "the caps are not made up from other boards"


async def test_due_boards_without_caps_is_one_global_oldest_first_list(
    factory: OpenSessions,
) -> None:
    await add_many(factory, "workable", 400, prefix="w")
    await add_many(factory, "greenhouse", 5, prefix="g")

    async with factory() as session:
        due = await due_boards(session, 300, now=NOW)

    assert len(due) == 300
    assert [row.ats for row in due].count("greenhouse") == 0, (
        "the starvation the caps exist to prevent"
    )


async def test_due_boards_never_exceeds_a_cap_or_the_limit(factory: OpenSessions) -> None:
    await add_many(factory, "greenhouse", 20, prefix="g")
    await add_many(factory, "ashby", 20, prefix="a")
    await add_many(factory, "workable", 20, prefix="w")

    async with factory() as session:
        due = await due_boards(
            session, 25, now=NOW, per_ats_caps={"greenhouse": 15, "ashby": 15, "workable": 15}
        )

    counts = {
        ats: [row.ats for row in due].count(ats) for ats in ("greenhouse", "ashby", "workable")
    }
    assert sum(counts.values()) == len(due) == 25, "exactly the limit, never more"
    assert max(counts.values()) <= 15
    assert min(counts.values()) >= 7, "each of the three has a share"


async def test_due_boards_shares_limit_across_ats(factory: OpenSessions) -> None:
    await add_many(factory, "greenhouse", 1000, prefix="g")
    await add_many(factory, "lever", 100, prefix="l")
    await add_many(factory, "ashby", 100, prefix="a")

    async with factory() as session:
        due = await due_boards(session, 300, now=NOW, per_ats_caps=sweep.queue_caps())

    counts = {ats: [row.ats for row in due].count(ats) for ats in ("greenhouse", "lever", "ashby")}
    assert len(due) == 300
    assert counts == {"greenhouse": 100, "lever": 100, "ashby": 100}, (
        "a backlog takes a share, not the run"
    )


async def test_due_boards_gives_unused_share_to_the_ats_that_has_more(
    factory: OpenSessions,
) -> None:
    await add_many(factory, "greenhouse", 1000, prefix="g")
    await add_many(factory, "lever", 10, prefix="l")

    async with factory() as session:
        due = await due_boards(session, 300, now=NOW, per_ats_caps=sweep.queue_caps())

    counts = {ats: [row.ats for row in due].count(ats) for ats in ("greenhouse", "lever")}
    assert counts == {"greenhouse": 290, "lever": 10}, (
        "the share Lever does not need is filled, oldest first"
    )


async def test_due_boards_filling_the_rest_never_passes_a_cap(factory: OpenSessions) -> None:
    await add_many(factory, "workable", 400, prefix="w")
    await add_many(factory, "greenhouse", 50, prefix="g")

    async with factory() as session:
        due = await due_boards(
            session, 100, now=NOW, per_ats_caps={"workable": 60, "greenhouse": 5}
        )

    counts = {ats: [row.ats for row in due].count(ats) for ats in ("workable", "greenhouse")}
    # Shares of 50: Workable 50, Greenhouse 5; the 45 left go to Workable up to its 60, no further.
    assert counts == {"workable": 60, "greenhouse": 5}


async def test_due_boards_fills_the_rest_with_the_oldest_across_ats(factory: OpenSessions) -> None:
    day = timedelta(days=1)
    for number in range(5):
        await add_board(factory, "greenhouse", f"g{number}", next_read_at=NOW - (50 - number) * day)
        await add_board(factory, "ashby", f"a{number}", next_read_at=NOW - (100 - number) * day)
    await add_board(factory, "lever", "l0", next_read_at=NOW - 5 * day)

    async with factory() as session:
        due = await due_boards(session, 10, now=NOW, per_ats_caps=sweep.queue_caps())

    # Shares of 4: Greenhouse g0-g3, Ashby a0-a3, Lever l0. The one place left goes to the
    # oldest board still due anywhere: Ashby's a4 (96 days), not Greenhouse's g4 (46 days),
    # which would be first in ATS order.
    assert {row.board for row in due} == {
        "g0",
        "g1",
        "g2",
        "g3",
        "a0",
        "a1",
        "a2",
        "a3",
        "a4",
        "l0",
    }


async def test_due_boards_takes_each_atss_oldest_first(factory: OpenSessions) -> None:
    day = timedelta(days=1)
    for name, age in (("w-old", 9), ("w-mid", 5), ("w-new", 1)):
        await add_board(factory, "workable", name, next_read_at=NOW - age * day)
    never = await add_board(factory, "workable", "w-never")

    async with factory() as session:
        due = await due_boards(session, 10, now=NOW, per_ats_caps={"workable": 3})

    assert [row.board for row in due] == ["w-never", "w-old", "w-mid"]
    assert due[0].id == never


async def test_due_boards_an_ats_the_caps_do_not_name_is_not_capped(factory: OpenSessions) -> None:
    await add_many(factory, "lever", 7, prefix="l")
    await add_many(factory, "workable", 7, prefix="w")

    async with factory() as session:
        due = await due_boards(session, 100, now=NOW, per_ats_caps={"workable": 2})

    assert [row.ats for row in due].count("lever") == 7
    assert [row.ats for row in due].count("workable") == 2


async def test_due_boards_caps_still_leave_out_gone_blocked_and_future(
    factory: OpenSessions,
) -> None:
    await add_many(factory, "workable", 3, prefix="gone", status="gone")
    await add_many(factory, "workable", 3, prefix="blocked", status="blocked")
    await add_many(factory, "workable", 3, prefix="later", next_read_at=NOW + timedelta(days=1))
    ready = await add_many(factory, "workable", 2, prefix="ready")

    async with factory() as session:
        due = await due_boards(session, 100, now=NOW, per_ats_caps=sweep.queue_caps())

    assert {row.id for row in due} == set(ready)


# -- store: recording a read -----------------------------------------------------------------


def schedule_after(status: str, hours: int = 24, **counters: int) -> Schedule:
    return Schedule(
        status=status,  # type: ignore[arg-type]
        failures=counters.get("failures", 0),
        zero_india_reads=counters.get("zero", 0),
        not_found_reads=counters.get("not_found", 0),
        next_read_at=NOW + timedelta(hours=hours),
    )


async def test_apply_schedule_writes_the_schedule_and_an_ok_reads_counts(
    factory: OpenSessions,
) -> None:
    board_id = await add_board(factory)
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        await apply_schedule(
            session,
            board,
            schedule_after("active"),
            ok_read(
                *(role(BoardRef("greenhouse", "northwind"), n) for n in range(3)),
                company_name="Northwind Labs",
            ),
            2,
            now=NOW,
        )
        await session.commit()

    row = await board_of(factory, board_id)
    assert row.status == "active"
    assert naive(row.next_read_at) == naive(NOW + timedelta(hours=24))
    assert (row.india_jobs, row.total_jobs) == (2, 3)
    assert naive(row.last_read_at) == naive(NOW) and naive(row.last_india_at) == naive(NOW)
    assert row.company_name == "Northwind Labs"


async def test_apply_schedule_total_only_from_a_complete_read(factory: OpenSessions) -> None:
    board_id = await add_board(factory, total_jobs=90, india_jobs=9)
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        partial = ok_read(role(BoardRef("greenhouse", "northwind"), 1), complete=False)
        await apply_schedule(session, board, schedule_after("active"), partial, 1, now=NOW)
        await session.commit()

    row = await board_of(factory, board_id)
    assert row.total_jobs == 90, "a partial read's total is a floor, not the board's size"
    assert row.india_jobs == 1


async def test_apply_schedule_no_india_roles_leaves_last_india_at(factory: OpenSessions) -> None:
    earlier = NOW - timedelta(days=3)
    board_id = await add_board(factory, status="active", last_india_at=earlier, india_jobs=4)
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        await apply_schedule(
            session, board, schedule_after("candidate", 7 * 24, zero=1), ok_read(), 0, now=NOW
        )
        await session.commit()

    row = await board_of(factory, board_id)
    assert naive(row.last_india_at) == naive(earlier)
    assert naive(row.last_read_at) == naive(NOW)
    assert (row.india_jobs, row.total_jobs, row.zero_india_reads) == (0, 0, 1)


@pytest.mark.parametrize("outcome", ["rate_limited", "error", "not_found"])
async def test_apply_schedule_a_failed_read_keeps_the_counts(
    factory: OpenSessions, outcome: str
) -> None:
    board_id = await add_board(
        factory, status="active", india_jobs=12, total_jobs=40, last_read_at=NOW - timedelta(days=1)
    )
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        await apply_schedule(
            session,
            board,
            schedule_after("active", 2, failures=1),
            failed(outcome),  # type: ignore[arg-type]
            0,
            now=NOW,
        )
        await session.commit()

    row = await board_of(factory, board_id)
    assert (row.india_jobs, row.total_jobs) == (12, 40)
    assert naive(row.last_read_at) == naive(NOW - timedelta(days=1))
    assert row.failures == 1 and naive(row.next_read_at) == naive(NOW + timedelta(hours=2))


async def test_apply_schedule_keeps_a_name_the_board_already_has(factory: OpenSessions) -> None:
    board_id = await add_board(factory, company_name="As Named By The List")
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        await apply_schedule(
            session,
            board,
            schedule_after("active"),
            ok_read(company_name="Another Name"),
            0,
            now=NOW,
        )
        await session.commit()

    assert (await board_of(factory, board_id)).company_name == "As Named By The List"


async def test_apply_schedule_without_a_read_writes_only_the_schedule(
    factory: OpenSessions,
) -> None:
    board_id = await add_board(factory, india_jobs=5)
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        await apply_schedule(
            session, board, schedule_after("candidate", 2, failures=1), None, 0, now=NOW
        )
        await session.commit()

    row = await board_of(factory, board_id)
    assert row.failures == 1 and row.india_jobs == 5 and row.last_read_at is None


# -- store: the boards live search reads -----------------------------------------------------


async def test_top_live_boards_are_active_greenhouse_and_ashby_by_india_jobs(
    factory: OpenSessions,
) -> None:
    await add_board(factory, "greenhouse", "small", status="active", india_jobs=3)
    await add_board(factory, "ashby", "big", status="active", india_jobs=90)
    await add_board(factory, "greenhouse", "medium", status="active", india_jobs=40)
    await add_board(factory, "lever", "lever-huge", status="active", india_jobs=500)
    await add_board(factory, "workable", "workable-huge", status="active", india_jobs=500)
    await add_board(factory, "greenhouse", "idle", status="no_india", india_jobs=200)
    await add_board(factory, "greenhouse", "blocked", status="blocked", india_jobs=200)
    await add_board(factory, "ashby", "unread", status="active", india_jobs=None)

    async with factory() as session:
        top = await top_live_boards(session)
        two = await top_live_boards(session, 2)

    assert [(ref.ats, ref.board) for ref in top] == [
        ("ashby", "big"),
        ("greenhouse", "medium"),
        ("greenhouse", "small"),
        ("ashby", "unread"),
    ]
    assert [ref.board for ref in two] == ["big", "medium"]
    assert all(isinstance(ref, BoardRef) for ref in top)


# -- the sweep: what one run does ------------------------------------------------------------


async def test_sweep_reads_due_boards_and_schedules_next(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    gh_a, gh_b = BoardRef("greenhouse", "alpha"), BoardRef("greenhouse", "beta")
    ash = BoardRef("ashby", "Gamma")
    alpha = await add_board(factory, "greenhouse", "alpha")
    beta = await add_board(factory, "greenhouse", "beta")
    gamma = await add_board(factory, "ashby", "Gamma")
    install(
        monkeypatch,
        greenhouse=Fake(
            {
                "alpha": ok_read(
                    role(gh_a, 1), role(gh_a, 2), role(gh_a, 3, ABROAD), company_name="Alpha Labs"
                ),
                "beta": ok_read(role(gh_b, 1, ABROAD)),
            }
        ),
        ashby=Fake({"Gamma": ok_read(role(ash, 1))}),
    )

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result == {
        "read": 3,
        "active": 2,
        "inserted": 3,
        "updated": 0,
        "closed": 0,
        "rate_limited_ats": "",
        "new_writes_paused": "",
    }
    a = await board_of(factory, alpha)
    assert a.status == "active" and (a.india_jobs, a.total_jobs) == (2, 3)
    assert a.failures == a.zero_india_reads == 0
    assert a.company_name == "Alpha Labs"
    assert a.last_read_at is not None and a.next_read_at is not None
    assert naive(a.next_read_at) - naive(a.last_read_at) == timedelta(hours=24)
    assert naive(a.last_india_at) == naive(a.last_read_at)

    b = await board_of(factory, beta)
    assert b.status == "candidate" and b.zero_india_reads == 1
    assert naive(b.next_read_at) - naive(b.last_read_at) == timedelta(days=7)
    assert b.last_india_at is None and (b.india_jobs, b.total_jobs) == (0, 1)

    assert (await board_of(factory, gamma)).status == "active"
    assert [row.title for row in await jobs_of(factory, "greenhouse:alpha")] == [
        "Backend Engineer 1",
        "Backend Engineer 2",
    ]
    assert len(await jobs_of(factory, "greenhouse:beta")) == 0
    assert sum(len(batch) for batch in embedded) == 3


async def test_a_board_read_again_after_its_date_updates_rather_than_inserts(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    ref = BoardRef("greenhouse", "alpha")
    board_id = await add_board(factory, "greenhouse", "alpha")
    install(monkeypatch, greenhouse=Fake({"alpha": ok_read(role(ref, 1), role(ref, 2))}))

    first = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)
    assert first["inserted"] == 2 and first["read"] == 1
    assert await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep) == {
        **first,
        "read": 0,
        "active": 0,
        "inserted": 0,
    }, "read a minute ago: not due"

    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        board.next_read_at = NOW - timedelta(days=2)
        await session.commit()
    install(monkeypatch, greenhouse=Fake({"alpha": ok_read(role(ref, 1), role(ref, 3))}))

    third = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert (third["inserted"], third["updated"], third["closed"]) == (1, 1, 1)
    open_titles = [row.title for row in await jobs_of(factory) if row.closed_at is None]
    assert open_titles == ["Backend Engineer 1", "Backend Engineer 3"]


async def test_nothing_due_returns_zeros_without_a_client_or_a_size_query(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    async def boom(_session: Any) -> int:
        raise AssertionError("size was asked for with nothing to read")

    monkeypatch.setattr(sweep, "database_size_bytes", boom)
    await add_board(factory, next_read_at=NOW + timedelta(days=1))
    await add_board(factory, "lever", "gone-one", status="gone")

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["read"] == 0 and result["new_writes_paused"] == ""


# -- the sweep: a 429, the pace, the budget, the breaker -------------------------------------


async def test_429_stops_only_that_ats(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    day = timedelta(days=1)
    first = await add_board(factory, "workable", "w1", next_read_at=NOW - 10 * day)
    second = await add_board(factory, "workable", "w2", next_read_at=NOW - 9 * day)
    third = await add_board(factory, "workable", "w3", next_read_at=NOW - 8 * day)
    greens = [
        await add_board(factory, "greenhouse", name, next_read_at=NOW - 8 * day)
        for name in ("g1", "g2", "g3")
    ]
    before = datetime.now(UTC)
    workable = Fake({"w1": failed("rate_limited")})
    greenhouse = Fake(
        {name: ok_read(role(BoardRef("greenhouse", name), 1)) for name in ("g1", "g2", "g3")}
    )
    install(monkeypatch, workable=workable, greenhouse=greenhouse)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["rate_limited_ats"] == "workable"
    assert result["read"] == 1 + 3, "the 429 board and the three Greenhouse boards"
    assert workable.calls == ["w1"], "the rest of the Workable queue is not asked"
    assert greenhouse.calls == ["g1", "g2", "g3"]

    limited = await board_of(factory, first)
    assert limited.failures == 1 and limited.status == "candidate"
    assert (
        timedelta(hours=2)
        <= naive(limited.next_read_at) - naive(before)
        < timedelta(hours=2, minutes=1)
    )
    assert (limited.india_jobs, limited.last_read_at) == (None, None), (
        "a 429 says nothing about the roles"
    )

    untouched_second = await board_of(factory, second)
    untouched_third = await board_of(factory, third)
    assert naive(untouched_second.next_read_at) == naive(NOW - 9 * day)
    assert naive(untouched_third.next_read_at) == naive(NOW - 8 * day)
    assert untouched_second.failures == untouched_third.failures == 0

    for board_id in greens:
        row = await board_of(factory, board_id)
        assert row.status == "active" and naive(row.next_read_at) > naive(NOW)

    health = await SourceHealthService.get("boards:workable")
    assert health.consecutive_failures == 1
    assert (await SourceHealthService.get("boards:greenhouse")).consecutive_failures == 0


async def test_a_429_board_waits_two_to_the_failures_hours(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    board_id = await add_board(factory, "workable", "w1", failures=2)
    install(monkeypatch, workable=Fake({"w1": failed("rate_limited")}))

    before = datetime.now(UTC)
    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    row = await board_of(factory, board_id)
    assert row.failures == 3
    waited = naive(row.next_read_at) - naive(before)
    assert timedelta(hours=8) <= waited < timedelta(hours=8, minutes=1)


async def test_pace_respected(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    for name in ("w1", "w2"):
        await add_board(factory, "workable", name)
    install(monkeypatch, workable=Fake(time=fake_time))

    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert fake_time.events == ["read:w1", "sleep:10", "read:w2"], "the wait is between two reads"
    assert all(seconds >= 10 for seconds in fake_time.sleeps)


@pytest.mark.parametrize("ats", ATS)
async def test_each_ats_keeps_its_own_pace(
    ats: AtsName, factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    for name in ("one", "two", "three"):
        await add_board(factory, ats, name)
    install(monkeypatch, **{ats: Fake(time=fake_time)})

    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert fake_time.sleeps == [PACE_SECONDS[ats]] * 2


async def test_one_board_makes_no_wait(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    await add_board(factory, "workable", "only")
    install(monkeypatch, workable=Fake())

    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert fake_time.sleeps == []


async def test_ats_queues_run_side_by_side(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workable's wait does not hold Greenhouse back: both queues are parked in their sleeps at once."""
    import asyncio

    for name in ("w1", "w2"):
        await add_board(factory, "workable", name)
    for name in ("g1", "g2"):
        await add_board(factory, "greenhouse", name)
    install(monkeypatch, workable=Fake(), greenhouse=Fake())
    waiting: set[float] = set()
    both_waiting = asyncio.Event()
    release = asyncio.Event()

    async def parked(seconds: float) -> None:
        waiting.add(seconds)
        if waiting == {PACE_SECONDS["workable"], PACE_SECONDS["greenhouse"]}:
            both_waiting.set()
        await release.wait()

    task = asyncio.create_task(run_sweep(clock=lambda: 0.0, sleep=parked))
    await asyncio.wait_for(both_waiting.wait(), timeout=5)
    release.set()
    result = await asyncio.wait_for(task, timeout=5)

    assert result["read"] == 4


async def test_no_board_started_after_budget(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = timedelta(days=1)
    ids = [
        await add_board(factory, "greenhouse", f"b{n}", next_read_at=NOW - (10 - n) * day)
        for n in range(8)
    ]
    fake_time = FakeTime()
    install(monkeypatch, greenhouse=Fake(time=fake_time, advance=100.0))

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    # Started at t = 0, 100, 200, 300, 400; the sixth would start at 500, past 480.
    assert result["read"] == 5
    rows = [await board_of(factory, board_id) for board_id in ids]
    assert all(naive(row.next_read_at) > naive(NOW) for row in rows[:5])
    for number, row in enumerate(rows[5:], start=5):
        assert naive(row.next_read_at) == naive(NOW - (10 - number) * day), "not started: untouched"


async def test_the_pacing_wait_counts_against_the_budget(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_board(factory, "workable", "w1")
    await add_board(factory, "workable", "w2")
    fake_time = FakeTime(sleeping_takes_time=True)
    # The first read starts at once and takes 475 s; the 10 s wait after it ends past 480 s.
    workable = Fake(time=fake_time, advance=475.0)
    install(monkeypatch, workable=workable)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["read"] == 1, "the wait took the run past 480 s: the second board is not started"
    assert workable.calls == ["w1"]


async def test_an_exhausted_budget_reads_nothing(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    board_id = await add_board(factory, "greenhouse", "b0")
    times = iter([0.0] + [sweep.RUN_BUDGET_SECONDS + 1.0] * 50)
    reader = Fake()
    install(monkeypatch, greenhouse=reader)

    async def no_sleep(_seconds: float) -> None:
        return None

    result = await run_sweep(clock=lambda: next(times), sleep=no_sleep)

    assert result["read"] == 0 and reader.calls == []
    assert (await board_of(factory, board_id)).next_read_at is None


async def test_open_breaker_skips_ats(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    lever_id = await add_board(factory, "lever", "l1")
    await add_board(factory, "greenhouse", "g1")
    for _ in range(FAILURE_THRESHOLD):
        await SourceHealthService.record_attempt(
            "boards:lever", job_count=0, latency_ms=1.0, status="error: error"
        )
    assert not await SourceHealthService.is_available("boards:lever")
    lever, greenhouse = Fake(), Fake()
    install(monkeypatch, lever=lever, greenhouse=greenhouse)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert lever.calls == [] and greenhouse.calls == ["g1"]
    assert result["read"] == 1
    assert (await board_of(factory, lever_id)).next_read_at is None, "skipped, not failed"


async def test_a_breaker_that_opens_mid_run_stops_its_queue(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    names = [f"r{n}" for n in range(6)]
    for name in names:
        await add_board(factory, "recruitee", name)
    reader = Fake({name: failed("error") for name in names})
    install(monkeypatch, recruitee=reader)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert reader.calls == names[:FAILURE_THRESHOLD]
    assert result["read"] == FAILURE_THRESHOLD
    assert result["rate_limited_ats"] == ""


@pytest.mark.parametrize("status", ["gone", "blocked"])
async def test_gone_and_blocked_not_swept(
    status: str, factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    board_id = await add_board(factory, "greenhouse", "dead", status=status)
    reader = Fake()
    install(monkeypatch, greenhouse=reader)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert reader.calls == [] and result["read"] == 0
    assert (await board_of(factory, board_id)).status == status


@pytest.mark.parametrize("status", ["candidate", "active", "no_india"])
async def test_candidate_active_and_no_india_boards_are_swept(
    status: str, factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    await add_board(
        factory, "greenhouse", "live", status=status, next_read_at=NOW - timedelta(days=40)
    )
    reader = Fake()
    install(monkeypatch, greenhouse=reader)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert reader.calls == ["live"] and result["read"] == 1


async def test_a_future_date_is_not_due(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    await add_board(factory, status="active", next_read_at=datetime.now(UTC) + timedelta(hours=3))
    reader = Fake()
    install(monkeypatch, greenhouse=reader)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert reader.calls == [] and result["read"] == 0


async def test_limit_caps_how_many_boards_are_taken(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    for number in range(6):
        await add_board(
            factory, "greenhouse", f"b{number}", next_read_at=NOW - timedelta(days=number + 1)
        )
    reader = Fake()
    install(monkeypatch, greenhouse=reader)

    result = await run_sweep(2, clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["read"] == 2
    assert reader.calls == ["b5", "b4"], "the two read longest ago"


# -- the sweep: one bad board never stops the run --------------------------------------------


async def test_a_reader_that_raises_is_an_error_for_that_board_only(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    day = timedelta(days=1)
    bad = await add_board(factory, "greenhouse", "bad", next_read_at=NOW - 3 * day)
    good = await add_board(factory, "greenhouse", "good", next_read_at=NOW - 2 * day)
    install(
        monkeypatch,
        greenhouse=Fake(
            {"bad": RuntimeError("boom"), "good": ok_read(role(BoardRef("greenhouse", "good"), 1))}
        ),
    )

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["read"] == 2 and result["active"] == 1
    failed_row = await board_of(factory, bad)
    assert failed_row.failures == 1 and failed_row.next_read_at is not None
    assert (await board_of(factory, good)).status == "active"
    assert (await SourceHealthService.get("boards:greenhouse")).consecutive_failures == 0


async def test_a_failed_write_backs_the_board_off_and_the_run_goes_on(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    day = timedelta(days=1)
    broken = await add_board(factory, "greenhouse", "broken", next_read_at=NOW - 3 * day)
    fine = await add_board(factory, "greenhouse", "fine", next_read_at=NOW - 2 * day)
    install(
        monkeypatch,
        greenhouse=Fake(
            {
                "broken": ok_read(role(BoardRef("greenhouse", "broken"), 1)),
                "fine": ok_read(role(BoardRef("greenhouse", "fine"), 1)),
            }
        ),
    )
    real_write = write_board_roles

    async def write_or_fail(
        session: AsyncSession, board: JobBoard, read: BoardRead, **kw: Any
    ) -> Any:
        if board.board == "broken":
            raise RuntimeError("the database said no")
        return await real_write(session, board, read, **kw)

    monkeypatch.setattr(sweep, "write_board_roles", write_or_fail)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["read"] == 2 and result["inserted"] == 1
    row = await board_of(factory, broken)
    assert row.failures == 1 and row.next_read_at is not None and row.status == "candidate"
    assert row.last_read_at is None
    assert (await board_of(factory, fine)).status == "active"
    assert await jobs_of(factory, "greenhouse:broken") == []
    assert factory.open == 0


# -- the sweep: transactions, embedding, the company -----------------------------------------


async def test_no_session_is_open_while_a_board_is_requested(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    for number in range(3):
        await add_board(factory, "greenhouse", f"b{number}")
    reader = Fake(sessions=factory)
    install(monkeypatch, greenhouse=reader)

    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert reader.open_sessions_during_reads == [0, 0, 0]
    assert factory.open == 0


async def test_new_rows_are_embedded_after_they_are_committed(
    factory: OpenSessions, monkeypatch: pytest.MonkeyPatch, fake_time: FakeTime
) -> None:
    ref = BoardRef("greenhouse", "alpha")
    await add_board(factory, "greenhouse", "alpha")
    install(monkeypatch, greenhouse=Fake({"alpha": ok_read(role(ref, 1), role(ref, 2))}))
    seen_by_embedder: list[int] = []
    open_during: list[int] = []

    async def embed(items: list[dict[str, Any]]) -> None:
        open_during.append(factory.open)
        seen_by_embedder.append(len(await jobs_of(factory, "greenhouse:alpha")))
        assert {item["internal_job_id"] for item in items} == {
            row.id for row in await jobs_of(factory, "greenhouse:alpha")
        }

    monkeypatch.setattr(sweep, "_embed_quietly", embed)

    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert seen_by_embedder == [2], "the rows were already committed when the embedder looked"
    assert open_during == [0], "embedding happens with no session open"


async def test_a_second_read_embeds_nothing_new(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    ref = BoardRef("greenhouse", "alpha")
    board_id = await add_board(factory, "greenhouse", "alpha")
    install(monkeypatch, greenhouse=Fake({"alpha": ok_read(role(ref, 1))}))
    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        board.next_read_at = NOW - timedelta(days=1)
        await session.commit()

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert (result["inserted"], result["updated"]) == (0, 1)
    assert len(embedded) == 1, "only the first read had rows to embed"


async def test_a_stated_company_name_names_the_placeholder_company(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    ref = BoardRef("greenhouse", "xq7")
    await add_board(factory, "greenhouse", "xq7")
    install(
        monkeypatch, greenhouse=Fake({"xq7": ok_read(role(ref, 1), company_name="Quartz Systems")})
    )

    await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    async with factory() as session:
        names = (await session.execute(select(Company.name))).scalars().all()
    assert names == ["Quartz Systems"]


# -- the sweep: size and row caps ------------------------------------------------------------


async def test_size_guard_pauses_new_rows(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    ref = BoardRef("greenhouse", "alpha")
    board_id = await add_board(factory, "greenhouse", "alpha")
    install(monkeypatch, greenhouse=Fake({"alpha": ok_read(role(ref, 1), role(ref, 2))}))
    first = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)
    assert first["inserted"] == 2 and first["new_writes_paused"] == ""

    async def full(_session: Any) -> int:
        return 360 * 1024 * 1024

    monkeypatch.setattr(sweep, "database_size_bytes", full)
    async with factory() as session:
        board = await session.get(JobBoard, board_id)
        assert board is not None
        board.next_read_at = NOW - timedelta(days=1)
        await session.commit()
    install(
        monkeypatch,
        greenhouse=Fake({"alpha": ok_read(role(ref, 1), role(ref, 2), role(ref, 3), role(ref, 4))}),
    )

    second = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert second["inserted"] == 0
    assert second["updated"] == 2, "what is already stored is still refreshed"
    assert second["new_writes_paused"] == "size"
    assert second["read"] == 1 and second["active"] == 1, (
        "the board row itself is still kept current"
    )
    assert len(await jobs_of(factory, "greenhouse:alpha")) == 2


async def test_just_under_the_size_stop_still_writes(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    ref = BoardRef("greenhouse", "alpha")
    await add_board(factory, "greenhouse", "alpha")
    install(monkeypatch, greenhouse=Fake({"alpha": ok_read(role(ref, 1))}))

    async def nearly(_session: Any) -> int:
        return sweep.SIZE_STOP_BYTES - 1

    monkeypatch.setattr(sweep, "database_size_bytes", nearly)

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["inserted"] == 1 and result["new_writes_paused"] == ""
    assert sweep.SIZE_STOP_BYTES == 350 * 1024 * 1024


async def test_new_rows_stop_at_the_per_run_cap(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    monkeypatch.setattr(sweep, "MAX_NEW_PER_RUN", 3)
    day = timedelta(days=1)
    answers = {}
    for number, name in enumerate(("b1", "b2", "b3")):
        await add_board(factory, "greenhouse", name, next_read_at=NOW - (10 - number) * day)
        ref = BoardRef("greenhouse", name)
        answers[name] = ok_read(role(ref, 1), role(ref, 2))
    install(monkeypatch, greenhouse=Fake(answers))

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    # b1 and b2 insert two each (2 < 3 before b2, so b2 is allowed); 4 >= 3 stops b3.
    assert result["inserted"] == 4
    assert result["new_writes_paused"] == "cap"
    assert len(await jobs_of(factory, "greenhouse:b3")) == 0
    assert result["read"] == 3 and result["active"] == 3


async def test_a_full_table_adds_rows_only_for_the_top_boards(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    monkeypatch.setattr(sweep, "MAX_BOARD_ROWS", 1)
    monkeypatch.setattr(sweep, "TOP_BOARDS_WHEN_FULL", 1)
    day = timedelta(days=1)
    # One stored board-sourced row fills the table.
    big = await add_board(
        factory, "greenhouse", "big", status="active", india_jobs=50, next_read_at=NOW - 5 * day
    )
    small = await add_board(
        factory, "greenhouse", "small", status="active", india_jobs=2, next_read_at=NOW - 4 * day
    )
    never = await add_board(factory, "greenhouse", "never", next_read_at=NOW - 3 * day)
    async with factory() as session:
        company = Company(id=uuid.uuid4(), name="Existing")
        session.add(company)
        await session.flush()
        elsewhere = BoardRef("ashby", "elsewhere")
        session.add(
            JobRow(
                id=uuid.uuid4(),
                company_id=company.id,
                external_id="x",
                source=elsewhere.source,
                **build_job_kwargs(role(elsewhere, 1)),
            )
        )
        await session.commit()
    answers = {
        name: ok_read(role(BoardRef("greenhouse", name), 1)) for name in ("big", "small", "never")
    }
    install(monkeypatch, greenhouse=Fake(answers))

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["inserted"] == 1
    assert result["new_writes_paused"] == "cap"
    assert len(await jobs_of(factory, "greenhouse:big")) == 1
    assert len(await jobs_of(factory, "greenhouse:small")) == 0
    assert len(await jobs_of(factory, "greenhouse:never")) == 0
    # Not being in the top only withholds new rows: the boards are read and scheduled as usual.
    assert (await board_of(factory, small)).status == "active"
    assert (await board_of(factory, never)).status == "active"
    assert (await board_of(factory, big)).india_jobs == 1


async def test_a_table_under_its_cap_writes_for_every_board(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    assert sweep.MAX_BOARD_ROWS == 25_000 and sweep.TOP_BOARDS_WHEN_FULL == 500
    assert sweep.MAX_NEW_PER_RUN == 1000 and sweep.RUN_BUDGET_SECONDS == 480
    assert sweep.SWEEP_DEFAULT_LIMIT == 300
    for name in ("a", "b"):
        await add_board(factory, "greenhouse", name)
    install(
        monkeypatch,
        greenhouse=Fake(
            {name: ok_read(role(BoardRef("greenhouse", name), 1)) for name in ("a", "b")}
        ),
    )

    result = await run_sweep(clock=fake_time.clock, sleep=fake_time.sleep)

    assert result["inserted"] == 2 and result["new_writes_paused"] == ""
    assert (await _count_jobs(factory)) == 2


async def _count_jobs(factory: OpenSessions) -> int:
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(JobRow))).scalar_one())


async def test_a_huge_slow_backlog_does_not_keep_the_fast_ats_from_a_run(
    factory: OpenSessions,
    monkeypatch: pytest.MonkeyPatch,
    fake_time: FakeTime,
    embedded: list[list[dict[str, Any]]],
) -> None:
    await add_many(factory, "workable", 400, prefix="w")
    await add_many(factory, "greenhouse", 5, prefix="g")
    workable, greenhouse = Fake(), Fake()
    install(monkeypatch, workable=workable, greenhouse=greenhouse)

    result = await run_sweep(300, clock=fake_time.clock, sleep=fake_time.sleep)

    assert len(greenhouse.calls) == 5
    assert len(workable.calls) == 48
    assert result["read"] == 53
