"""
services/job_boards/store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The roster's reads and writes of its own table: adding a board, finding the ones
due to be read, recording what a read came to, and naming the boards live search
reads on demand.

The rules that decide *when* a board is read again are pure and live in
``domain.job_boards.next_read``; this module only stores what they return. It
commits nothing: the caller owns the transaction, and no function here makes a
request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.job_boards import LIVE_SEARCH_ATS, SWEPT_STATUSES, BoardRef, Schedule
from ingestion.boards.base import BoardRead
from storage.models import JobBoard


async def _find(session: AsyncSession, ref: BoardRef) -> JobBoard | None:
    """The row for ``ref``, matched on the table's unique key: ats, board and region (``None`` is ``''``)."""
    return (
        await session.execute(
            select(JobBoard).where(
                JobBoard.ats == ref.ats,
                JobBoard.board == ref.board,
                func.coalesce(JobBoard.region, "") == (ref.region or ""),
            )
        )
    ).scalar_one_or_none()


def _gain_company(row: JobBoard, company_id: uuid.UUID | None) -> None:
    """A row that knew no company takes the one it is given; one that knew a company keeps it."""
    if row.company_id is None and company_id is not None:
        row.company_id = company_id


async def upsert_board(
    session: AsyncSession,
    ref: BoardRef,
    *,
    via: str,
    company_id: uuid.UUID | None = None,
    company_name: str | None = None,
) -> bool:
    """
    Add a board to the roster as a ``candidate``; True when this call inserted it.

    A board already on the roster is left as it is: its ``status`` and schedule are never
    reset by being found again (a ``blocked`` or ``gone`` board stays so), and the only
    thing it can gain is a ``company_id`` it lacked. Two discoverers adding the same board
    at once is safe: the loser's insert is rolled back on its own savepoint, so the caller's
    transaction survives, and it reports False. Does not commit.
    """
    existing = await _find(session, ref)
    if existing is not None:
        _gain_company(existing, company_id)
        return False

    row = JobBoard(
        id=uuid.uuid4(),
        ats=ref.ats,
        board=ref.board,
        region=ref.region or None,
        company_id=company_id,
        company_name=(company_name or "").strip() or None,
        discovered_via=via,
        status="candidate",
        first_seen_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        # Somebody else's insert of the same board landed first.
        winner = await _find(session, ref)
        if winner is not None:
            _gain_company(winner, company_id)
        return False
    return True


async def due_boards(session: AsyncSession, limit: int, *, now: datetime) -> list[JobBoard]:
    """
    The boards the sweep should read now, at most ``limit``, never-read first and then oldest first.

    A board is due when its status is one the sweep reads (``candidate``, ``active``,
    ``no_india``; never ``gone`` or ``blocked``) and its ``next_read_at`` is unset or has
    passed.
    """
    stmt = select(JobBoard).where(
        JobBoard.status.in_(sorted(SWEPT_STATUSES)),
        or_(JobBoard.next_read_at.is_(None), JobBoard.next_read_at <= now),
    )
    stmt = stmt.order_by(
        JobBoard.next_read_at.asc().nulls_first(), JobBoard.first_seen_at.asc(), JobBoard.id.asc()
    ).limit(max(limit, 0))
    return list((await session.execute(stmt)).scalars().all())


async def apply_schedule(
    session: AsyncSession,
    board: JobBoard,
    s: Schedule,
    read: BoardRead | None,
    india: int,
    *,
    now: datetime,
) -> None:
    """
    Store what a read of ``board`` came to: the schedule ``next_read`` returned, and the read's counts.

    The status and the four schedule fields are always written. The counts are written only for
    an ``ok`` read (an error or a 429 says nothing about the board's roles, so they keep their
    last values): ``india_jobs``, ``total_jobs`` (only when the read was ``complete``; a partial
    read's total is a floor, not the board's size), ``last_read_at``, ``last_india_at`` when it
    found India roles, and the company's name when the board had none and the ATS states one.
    ``read`` is ``None`` for a board that was not read (a failed write backs it off). Flushes;
    does not commit.
    """
    board.status = s.status
    board.failures = s.failures
    board.zero_india_reads = s.zero_india_reads
    board.not_found_reads = s.not_found_reads
    board.next_read_at = s.next_read_at

    if read is not None and read.outcome == "ok":
        board.india_jobs = india
        if read.complete:
            board.total_jobs = read.total
        board.last_read_at = now
        if india > 0:
            board.last_india_at = now
        stated = (read.company_name or "").strip()
        if stated and not board.company_name:
            board.company_name = stated
    await session.flush()


async def top_live_boards(session: AsyncSession, n: int = 25) -> list[BoardRef]:
    """
    The ``n`` active boards live search reads on demand: Greenhouse and Ashby only, most India roles first.

    Ties are broken by the board's name, so the answer is the same on every call.
    """
    rows = (
        (
            await session.execute(
                select(JobBoard)
                .where(JobBoard.status == "active", JobBoard.ats.in_(sorted(LIVE_SEARCH_ATS)))
                .order_by(JobBoard.india_jobs.desc().nulls_last(), JobBoard.board.asc())
                .limit(max(n, 0))
            )
        )
        .scalars()
        .all()
    )
    return [row.ref for row in rows]
