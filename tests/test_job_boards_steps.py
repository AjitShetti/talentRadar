"""
tests/test_job_boards_steps.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled job-board steps (``services/job_boards/steps.py``), their endpoint, and the
roster's line in ``/sources/health``.

* a step never raises: a failure comes back as ``{"step": ..., "error": "<ClassName>"}``
* ``limit`` is clamped to the step's ceiling and to at least 1
* ``prune`` deletes ``gone`` boards that were not read for 90 days (the last read, or when
  the board was first seen if it was never read), at most ``limit``, and nothing else
* the endpoint is closed like the other cron endpoints: no token, no entry
* ``/sources/health`` gains a ``job_boards`` block, and a failure there leaves the rest intact

The database is an in-memory SQLite built from the real models, wired in where ``run_step``
opens its session. The discovery functions and the sweep are replaced, so no test requests a
website. Every board here is fictional.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import storage.database as database
from config.settings import get_settings
from services.cache_backend import CacheBackend
from services.job_boards import steps
from services.job_boards.discover import OpenListError, YcListError
from storage.database import Base
from storage.models import JobBoard

NOW = datetime.now(UTC)
STEP_URL = "/api/v1/ingest/job-boards"
HEALTH_URL = "/api/v1/ingest/sources/health"

#: The name of the callable each step runs, so a test can replace it.
CALLABLE = {
    "open_list": "discover_open_list",
    "yc": "discover_yc",
    "probe": "discover_probe",
    "sweep": "run_sweep",
    "prune": "prune_gone_boards",
}


# -- the database and the cache ---------------------------------------------------------


@pytest.fixture(autouse=True)
async def memory_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


@pytest_asyncio.fixture
async def sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory database, wired in where ``run_step`` opens its session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


def make_board(
    name: str,
    *,
    ats: str = "greenhouse",
    status: str = "gone",
    read_days_ago: float | None = None,
    seen_days_ago: float = 0.0,
) -> JobBoard:
    return JobBoard(
        ats=ats,
        board=name,
        discovered_via="open_list",
        status=status,
        first_seen_at=NOW - timedelta(days=seen_days_ago),
        last_read_at=None if read_days_ago is None else NOW - timedelta(days=read_days_ago),
    )


async def store(factory: async_sessionmaker[AsyncSession], *boards: JobBoard) -> None:
    async with factory() as session:
        session.add_all(boards)
        await session.commit()


async def board_names(factory: async_sessionmaker[AsyncSession]) -> set[str]:
    async with factory() as session:
        return set((await session.scalars(select(JobBoard.board))).all())


# -- run_step: unknown steps, limits, failures --------------------------------------------


async def test_run_step_unknown_step_is_an_error_not_a_raise() -> None:
    assert await steps.run_step("everything", 5) == {"error": "unknown step"}


@pytest.mark.parametrize(
    ("step", "limit", "expected"),
    [
        ("sweep", 10_000, 600),
        ("sweep", 300, 300),
        ("open_list", 99_999, 20_000),
        ("yc", 500, 100),
        ("probe", 61, 60),
        ("probe", 0, 1),
        ("prune", -4, 1),
        ("prune", 5_000, 5_000),
    ],
)
async def test_run_step_caps_limit(
    step: str, limit: int, expected: int, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # prune returns a count; the others return their counts as a mapping.
    fake = AsyncMock(return_value=0 if step == "prune" else {})
    with patch.object(steps, CALLABLE[step], fake):
        await steps.run_step(step, limit)
    assert fake.await_args is not None
    assert fake.await_args.kwargs["limit"] == expected


@pytest.mark.parametrize(
    ("step", "failure"),
    [
        ("sweep", RuntimeError("https://secret.example/x?token=abc")),
        ("open_list", OpenListError("the list did not load")),
        ("yc", YcListError("the list did not load")),
        ("probe", OSError("network down")),
        ("prune", RuntimeError("database gone")),
    ],
)
async def test_run_step_never_raises(
    step: str, failure: Exception, sessions: async_sessionmaker[AsyncSession]
) -> None:
    fake = AsyncMock(side_effect=failure)
    with patch.object(steps, CALLABLE[step], fake):
        result = await steps.run_step(step, 5)
    assert result == {"step": step, "error": type(failure).__name__}
    # The message can carry a URL or a token; it never reaches the caller.
    assert "secret" not in str(result)


async def test_run_step_sweep_names_its_step_and_counts(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    counts = {
        "read": 12,
        "active": 3,
        "inserted": 4,
        "rate_limited_ats": "",
        "new_writes_paused": "",
    }
    with patch.object(steps, "run_sweep", AsyncMock(return_value=counts)) as fake:
        result = await steps.run_step("sweep", 300)
    assert result == {"step": "sweep", **counts}
    assert fake.await_args is not None
    assert fake.await_args.kwargs["limit"] == 300


@pytest.mark.parametrize(
    ("step", "counts"),
    [
        ("open_list", {"rows": 3, "kept": 2, "inserted": 1}),
        ("yc", {"companies": 4, "boards": 1}),
        ("probe", {"companies": 2, "boards": 0}),
    ],
)
async def test_run_step_discovery_names_its_step_and_counts(
    step: str, counts: dict[str, int], sessions: async_sessionmaker[AsyncSession]
) -> None:
    fake = AsyncMock(return_value=counts)
    with patch.object(steps, CALLABLE[step], fake):
        result = await steps.run_step(step, 5)
    assert result == {"step": step, **counts}


# -- prune ----------------------------------------------------------------------------------


async def test_prune_removes_old_gone_boards_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await store(
        sessions,
        # deleted: gone, and the last read (or, never read, the first sighting) is over 90 days old
        make_board("old-read", read_days_ago=120, seen_days_ago=400),
        make_board("old-unread", read_days_ago=None, seen_days_ago=120),
        # kept: gone, but read or first seen within 90 days
        make_board("recent-read", read_days_ago=10, seen_days_ago=400),
        make_board("recent-unread", read_days_ago=None, seen_days_ago=10),
        # kept: every other status, however old
        make_board("old-active", status="active", read_days_ago=400, seen_days_ago=400),
        make_board("old-no-india", status="no_india", read_days_ago=400, seen_days_ago=400),
        make_board("old-candidate", status="candidate", read_days_ago=None, seen_days_ago=400),
        make_board("old-blocked", status="blocked", read_days_ago=400, seen_days_ago=400),
    )

    result = await steps.run_step("prune", 5000)

    assert result == {"step": "prune", "deleted": 2}
    assert await board_names(sessions) == {
        "recent-read",
        "recent-unread",
        "old-active",
        "old-no-india",
        "old-candidate",
        "old-blocked",
    }


async def test_prune_takes_at_most_its_limit(sessions: async_sessionmaker[AsyncSession]) -> None:
    await store(
        sessions,
        *(make_board(f"old-{n}", read_days_ago=200) for n in range(3)),
    )

    result = await steps.run_step("prune", 2)

    assert result == {"step": "prune", "deleted": 2}
    async with sessions() as session:
        left = await session.scalar(select(func.count()).select_from(JobBoard))
    assert left == 1


# -- the endpoint -----------------------------------------------------------------------------


@pytest.fixture
def cron_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "cron_token", "a-long-shared-secret")
    return "a-long-shared-secret"


async def test_endpoint_requires_cron_token(api_client: AsyncClient, cron_token: str) -> None:
    with patch.object(steps, "run_step", AsyncMock(return_value={"step": "sweep"})) as run:
        response = await api_client.post(f"{STEP_URL}?step=sweep&limit=300")
    assert response.status_code in (401, 403)
    assert run.await_count == 0


async def test_endpoint_refuses_a_wrong_token(api_client: AsyncClient, cron_token: str) -> None:
    with patch.object(steps, "run_step", AsyncMock(return_value={"step": "sweep"})) as run:
        response = await api_client.post(
            f"{STEP_URL}?step=sweep&limit=300", headers={"X-Cron-Token": "nope"}
        )
    assert response.status_code == 403
    assert run.await_count == 0


async def test_endpoint_with_the_token_runs_the_step(
    api_client: AsyncClient, cron_token: str
) -> None:
    counts = {"step": "sweep", "read": 0}
    with patch.object(steps, "run_step", AsyncMock(return_value=counts)) as run:
        response = await api_client.post(
            f"{STEP_URL}?step=sweep&limit=300", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == counts
    assert run.await_args is not None
    assert run.await_args.args == ("sweep", 300)


async def test_endpoint_rejects_an_unknown_step_and_an_oversized_limit(
    api_client: AsyncClient, cron_token: str
) -> None:
    headers = {"X-Cron-Token": cron_token}
    with patch.object(steps, "run_step", AsyncMock(return_value={})) as run:
        unknown = await api_client.post(f"{STEP_URL}?step=everything", headers=headers)
        oversized = await api_client.post(f"{STEP_URL}?step=sweep&limit=20001", headers=headers)
    assert unknown.status_code == 422
    assert oversized.status_code == 422
    assert run.await_count == 0


# -- /sources/health --------------------------------------------------------------------------


async def test_health_counts_boards_by_status_and_ats(
    admin_client: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await store(
        sessions,
        make_board("a", ats="greenhouse", status="active", read_days_ago=0),
        make_board("b", ats="greenhouse", status="candidate", read_days_ago=None),
        make_board("c", ats="lever", status="candidate", read_days_ago=None),
    )

    response = await admin_client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response.json()["job_boards"] == {
        "by_status": {"active": 1, "candidate": 2},
        "by_ats": {"greenhouse": 2, "lever": 1},
    }


async def test_health_keeps_the_report_when_the_board_counts_fail(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise RuntimeError("no database")

    monkeypatch.setattr(database, "AsyncSessionLocal", broken)

    response = await admin_client.get(HEALTH_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["job_boards"] == {}
    assert "sources" in body
    assert "healthy" in body
