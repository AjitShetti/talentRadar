"""
services/job_boards/sweep.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled loop that reads due boards from the nine ATSes and writes their India
roles. Design: docs/superpowers/specs/2026-11-23-job-board-roster-design.md, "The sweep".

How a run goes
--------------
* The boards due now (``store.due_boards``: never-read first, then oldest read first) are taken,
  at most ``limit`` of them and at most :func:`queue_caps` per ATS (what its pace lets one run
  read). The run is shared out: each ATS with due boards first takes an equal share of
  ``limit``, and the room left is filled oldest first, so neither a slow ATS nor a big backlog
  at one ATS crowds the others out; they are split into **one queue per ATS**. The queues run
  concurrently; each keeps its ATS's pace (``PACE_SECONDS``) between two requests, so the
  slow ones (Workable answers a faster client with an HTML 429 page) never hold the fast ones.
* A queue whose breaker (``boards:<ats>``) is open is skipped. The first ``rate_limited``
  read stops that queue for the rest of the run: that board's failure is recorded, and the
  boards behind it are not touched (their ``next_read_at`` stays where it was, so the next
  run takes them first). A breaker that opens mid-run stops its queue too.
* No board is started once :data:`RUN_BUDGET_SECONDS` have passed, and every board's work is
  its own short transaction opened after the request returned: no session is held across a
  network call. A board that raises, or whose write fails, is backed off like a failed read
  and the run goes on.
* Writes are one at a time (a lock held only around database work, never a request), which
  keeps the per-run new-row cap exact; the new rows' embeddings are computed after the commit.

What stops a board writing *new* rows (it still refreshes the ones it holds)
----------------------------------------------------------------------------
* the database is at or over :data:`SIZE_STOP_BYTES` (``new_writes_paused == "size"``), below
  the prune's own high-water mark so the two do not fight;
* :data:`MAX_NEW_PER_RUN` rows have been inserted this run (``"cap"``): checked before each
  board, so the run can pass it by at most one board's worth (200 rows);
* :data:`MAX_BOARD_ROWS` board-sourced rows are stored and the board is not among the
  :data:`TOP_BOARDS_WHEN_FULL` boards with the most India roles (``"cap"``).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.job_boards import (
    ATS,
    PACE_SECONDS,
    AtsName,
    BoardRef,
    BoardStatus,
    Schedule,
    is_india_role,
    next_read,
)
from ingestion.boards import READERS
from ingestion.boards.base import BoardRead, failed
from services.job_boards.store import apply_schedule, due_boards
from services.job_boards.write import WriteSummary, write_board_roles
from services.job_persistence import _embed_quietly
from services.job_retention import database_size_bytes
from services.source_health import SourceHealthService
from storage.database import AsyncSessionLocal
from storage.models import Job as JobRow
from storage.models import JobBoard

logger = logging.getLogger(__name__)

#: Boards one run takes by default.
SWEEP_DEFAULT_LIMIT = 300

#: No board is started after this many seconds (the Events rule).
RUN_BUDGET_SECONDS = 480

#: New ``jobs`` rows one run may insert, over all boards.
MAX_NEW_PER_RUN = 1000

#: Above this database size no new row is written. Below the prune's 400 MB.
SIZE_STOP_BYTES = 350 * 1024 * 1024

#: Board-sourced ``jobs`` rows in all; over it only the top boards get new rows.
MAX_BOARD_ROWS = 25_000

#: How many boards (by ``india_jobs``) may still add rows when the table is full.
TOP_BOARDS_WHEN_FULL = 500

#: One request's timeout. The readers send the User-Agent and refuse redirects themselves.
_REQUEST_TIMEOUT_SECONDS = 20.0

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class _Due:
    """A due board, kept as plain values: the session it was loaded in is closed."""

    id: uuid.UUID
    ref: BoardRef


@dataclass
class _Run:
    """What one run shares between its queues."""

    started: float
    clock: Callable[[], float]
    sleep: Sleep
    client: httpx.AsyncClient
    size_stop: bool
    table_full: bool
    top_board_ids: set[uuid.UUID]
    #: Held around database work only, never a request.
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    read: int = 0
    active: int = 0
    inserted: int = 0
    updated: int = 0
    closed: int = 0
    rate_limited: set[AtsName] = field(default_factory=set)
    paused: str = ""

    def out_of_time(self) -> bool:
        return self.clock() - self.started >= RUN_BUDGET_SECONDS

    def allow_new_for(self, board_id: uuid.UUID) -> bool:
        """Whether this board may insert rows now. Records why not in ``paused``."""
        if self.size_stop:
            return False
        if self.inserted >= MAX_NEW_PER_RUN or (
            self.table_full and board_id not in self.top_board_ids
        ):
            self.paused = "cap"
            return False
        return True


def queue_caps() -> dict[AtsName, int]:
    """
    How many boards one run can read from each ATS: its budget divided by its pace, rounded down.

    Workable's 10 s pace allows 48 in the 480 s budget, Keka's 2 s allows 240. A longer queue
    could not finish in the run, so :func:`~services.job_boards.store.due_boards` is given
    these and never lets a slow ATS's backlog crowd the fast ones out of the run's limit.
    """
    return {ats: int(RUN_BUDGET_SECONDS // PACE_SECONDS[ats]) for ats in ATS}


def _ordered(ats_names: set[AtsName]) -> list[AtsName]:
    return [ats for ats in ATS if ats in ats_names]


async def _board_rows(session: AsyncSession) -> int:
    """Board-sourced ``jobs`` rows: those whose ``source`` starts with one of the nine ATS names."""
    stmt = (
        select(func.count())
        .select_from(JobRow)
        .where(or_(*[JobRow.source.like(f"{ats}:%") for ats in ATS]))
    )
    return int((await session.execute(stmt)).scalar_one())


async def _top_board_ids(session: AsyncSession) -> set[uuid.UUID]:
    """The boards with the most India roles at their last read: the only ones that add rows when the table is full."""
    rows = await session.execute(
        select(JobBoard.id)
        .where(JobBoard.india_jobs > 0)
        .order_by(JobBoard.india_jobs.desc(), JobBoard.id.asc())
        .limit(TOP_BOARDS_WHEN_FULL)
    )
    return set(rows.scalars().all())


def _schedule_of(board: JobBoard, now: datetime) -> Schedule:
    return Schedule(
        status=cast("BoardStatus", board.status),
        failures=board.failures,
        zero_india_reads=board.zero_india_reads,
        not_found_reads=board.not_found_reads,
        next_read_at=board.next_read_at or now,
    )


async def _back_off(due: _Due, *, now: datetime) -> None:
    """Best effort: after a failed write, schedule the board as an ``error`` read so it is not retried at once."""
    try:
        async with AsyncSessionLocal() as session:
            board = await session.get(JobBoard, due.id)
            if board is None:
                return
            schedule = next_read(_schedule_of(board, now), "error", 0, now=now)
            await apply_schedule(session, board, schedule, None, 0, now=now)
            await session.commit()
    except Exception:
        logger.warning("Could not back off board %s after a failed write", due.ref.source)


async def _persist(run: _Run, due: _Due, read: BoardRead) -> None:
    """
    Write one board's read and its new schedule in one short transaction, then embed what was inserted.

    Never raises: a board whose write fails is backed off and the queue goes on.
    """
    now = datetime.now(UTC)
    ok = read.outcome == "ok"
    india = sum(1 for role in read.roles if is_india_role(role.location_raw)) if ok else 0
    summary = WriteSummary()
    items: list[dict[str, Any]] = []
    status = ""
    async with run.write_lock:
        try:
            async with AsyncSessionLocal() as session:
                board = await session.get(JobBoard, due.id)
                if board is None:  # deleted since the run began
                    return
                if ok:
                    stated = (read.company_name or "").strip()
                    if stated and not board.company_name:
                        # Before the write, so a placeholder company is named for the company.
                        board.company_name = stated
                    summary, items = await write_board_roles(
                        session, board, read, allow_new=run.allow_new_for(due.id)
                    )
                schedule = next_read(_schedule_of(board, now), read.outcome, india, now=now)
                await apply_schedule(session, board, schedule, read, india, now=now)
                await session.commit()
                status = schedule.status
        except Exception:
            logger.exception("Writing board %s failed", due.ref.source)
            await _back_off(due, now=now)
            run.read += 1
            return
        run.read += 1
        run.active += status == "active"
        run.inserted += summary.inserted
        run.updated += summary.updated
        run.closed += summary.closed
    if items:
        # After the commit and outside the lock: embedding is slow, and a failure here
        # (no vector store, a model error) leaves the rows stored and searchable.
        await _embed_quietly(items)


async def _run_queue(run: _Run, ats: AtsName, boards: list[_Due]) -> None:
    """Read one ATS's boards one after another, ``PACE_SECONDS`` apart, until done, out of time or stopped."""
    source = f"boards:{ats}"
    if not await SourceHealthService.is_available(source):
        logger.info("Skipping %d %s boards: breaker open", len(boards), ats)
        return
    reader = READERS[ats]

    for position, due in enumerate(boards):
        if run.out_of_time():
            break
        if position:
            await run.sleep(PACE_SECONDS[ats])
            if run.out_of_time():  # the wait is part of the budget
                break

        began = run.clock()
        try:
            read = await reader(run.client, due.ref)
        except Exception as exc:
            logger.warning("Reading %s raised %s", due.ref.source, type(exc).__name__)
            read = failed("error")
        latency_ms = (run.clock() - began) * 1000

        await _persist(run, due, read)

        # The ATS answered with either; a board that is gone is that board's news, not the ATS's.
        answered = read.outcome in ("ok", "not_found")
        try:
            health = await SourceHealthService.record_attempt(
                source,
                job_count=len(read.roles),
                latency_ms=latency_ms,
                status="success" if answered else f"error: {read.outcome}",
            )
        except Exception as exc:  # health is a signal, never a reason to stop reading
            logger.debug("Could not record health for %s: %s", source, exc)
            health = None
        if read.outcome == "rate_limited":
            run.rate_limited.add(ats)
            logger.warning("%s rate-limited the sweep; its queue stops for this run", ats)
            break
        if health is not None and health.is_circuit_open():
            logger.warning("%s breaker opened; its queue stops for this run", ats)
            break


async def _guarded_queue(run: _Run, ats: AtsName, boards: list[_Due]) -> None:
    try:
        await _run_queue(run, ats, boards)
    except Exception:
        # One ATS's bug must not take the other eight queues down with it.
        logger.exception("The %s queue failed", ats)


async def sweep(
    limit: int = SWEEP_DEFAULT_LIMIT,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, int | str]:
    """
    Read the boards that are due and write their India roles. Returns what the run did.

    ``read``: boards a request was made for (whatever it answered); ``active``: of those, the ones
    that now have India roles; ``inserted`` / ``updated`` / ``closed``: ``jobs`` rows written;
    ``rate_limited_ats``: the ATSes that answered 429 (comma-separated); ``new_writes_paused``:
    ``"size"``, ``"cap"`` or ``""``, see the module docstring. ``clock`` and ``sleep`` are
    injectable so a test can run a day's pacing without waiting.

    A board's own failure never raises out of here (it is backed off and the run goes on); only
    a roster that cannot be read at all does, and ``steps.run_step`` turns that into an error result.
    """
    started = clock()
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        due_rows = await due_boards(session, limit, now=now, per_ats_caps=queue_caps())
        queues: dict[AtsName, list[_Due]] = {}
        for row in due_rows:
            if row.ats in READERS:
                queues.setdefault(row.ats, []).append(_Due(row.id, row.ref))
    if not queues:
        return _summary(None)

    async with AsyncSessionLocal() as session:
        size_stop = await database_size_bytes(session) >= SIZE_STOP_BYTES
    table_full = False
    top_ids: set[uuid.UUID] = set()
    if not size_stop:
        async with AsyncSessionLocal() as session:
            table_full = await _board_rows(session) >= MAX_BOARD_ROWS
            if table_full:
                top_ids = await _top_board_ids(session)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS), follow_redirects=False
    ) as client:
        run = _Run(
            started=started,
            clock=clock,
            sleep=sleep,
            client=client,
            size_stop=size_stop,
            table_full=table_full,
            top_board_ids=top_ids,
            paused="size" if size_stop else "",
        )
        await asyncio.gather(*(_guarded_queue(run, ats, boards) for ats, boards in queues.items()))

    result = _summary(run)
    logger.info("Board sweep: %s", result)
    return result


def _summary(run: _Run | None) -> dict[str, int | str]:
    if run is None:
        return {
            "read": 0,
            "active": 0,
            "inserted": 0,
            "updated": 0,
            "closed": 0,
            "rate_limited_ats": "",
            "new_writes_paused": "",
        }
    return {
        "read": run.read,
        "active": run.active,
        "inserted": run.inserted,
        "updated": run.updated,
        "closed": run.closed,
        "rate_limited_ats": ",".join(_ordered(run.rate_limited)),
        "new_writes_paused": run.paused,
    }
