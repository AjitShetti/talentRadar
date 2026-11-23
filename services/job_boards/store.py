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
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.job_boards import ATS, LIVE_SEARCH_ATS, SWEPT_STATUSES, AtsName, BoardRef, Schedule
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


def _due_clause(now: datetime) -> list[ColumnElement[bool]]:
    return [
        JobBoard.status.in_(sorted(SWEPT_STATUSES)),
        or_(JobBoard.next_read_at.is_(None), JobBoard.next_read_at <= now),
    ]


async def _due(
    session: AsyncSession,
    limit: int,
    *,
    now: datetime,
    ats: str | None = None,
    exclude: Collection[uuid.UUID] = (),
) -> list[JobBoard]:
    """Up to ``limit`` due boards (of one ATS when given), never-read first and then oldest first."""
    stmt = select(JobBoard).where(*_due_clause(now))
    if ats is not None:
        stmt = stmt.where(JobBoard.ats == ats)
    if exclude:
        stmt = stmt.where(JobBoard.id.not_in(list(exclude)))
    stmt = stmt.order_by(
        JobBoard.next_read_at.asc().nulls_first(), JobBoard.first_seen_at.asc(), JobBoard.id.asc()
    ).limit(max(limit, 0))
    return list((await session.execute(stmt)).scalars().all())


def _oldest_first(board: JobBoard) -> tuple[bool, datetime, datetime, uuid.UUID]:
    """The order ``_due`` sorts in, for merging lists already read from the database."""
    return (
        board.next_read_at is not None,
        board.next_read_at or datetime.min,
        board.first_seen_at,
        board.id,
    )


async def due_boards(
    session: AsyncSession,
    limit: int,
    *,
    now: datetime,
    per_ats_caps: Mapping[AtsName, int] | None = None,
) -> list[JobBoard]:
    """
    The boards the sweep should read now, at most ``limit``, never-read first and then oldest first.

    A board is due when its status is one the sweep reads (``candidate``, ``active``,
    ``no_india``; never ``gone`` or ``blocked``) and its ``next_read_at`` is unset or has
    passed.

    With ``per_ats_caps`` (how many boards one run can read from each ATS) the run is shared
    out, so neither a slow ATS nor a large backlog at one ATS can take the whole ``limit``:

    1. each ATS with due boards takes up to ``min(its cap, ceil(limit / how many ATSes have
       due boards))``, oldest first;
    2. the room left under ``limit`` is then filled oldest first across all of them, still
       never past an ATS's cap. A cap is not made up from other boards: they could not be
       read in the run anyway.

    An ATS the mapping does not name is not capped.
    """
    if per_ats_caps is None:
        return await _due(session, limit, now=now)
    if limit <= 0:
        return []

    def cap_of(ats: AtsName) -> int:
        return max(per_ats_caps.get(ats, limit), 0)

    present = set(
        (await session.execute(select(JobBoard.ats).where(*_due_clause(now)).distinct()))
        .scalars()
        .all()
    )
    sharing = [ats for ats in ATS if ats in present and cap_of(ats) > 0]
    if not sharing:
        return []
    share = -(-limit // len(sharing))

    picked: list[JobBoard] = []
    taken = dict.fromkeys(sharing, 0)
    for ats in sharing:
        room = min(limit - len(picked), cap_of(ats), share)
        if room > 0:
            rows = await _due(session, room, now=now, ats=ats)
            picked.extend(rows)
            taken[ats] = len(rows)

    room = limit - len(picked)
    if room > 0:
        chosen = {board.id for board in picked}
        candidates: list[JobBoard] = []
        for ats in sharing:
            left = min(room, cap_of(ats) - taken[ats])
            if left > 0:
                candidates.extend(await _due(session, left, now=now, ats=ats, exclude=chosen))
        for board in sorted(candidates, key=_oldest_first):
            if room <= 0:
                break
            ats = cast("AtsName", board.ats)
            if taken[ats] < cap_of(ats):
                picked.append(board)
                taken[ats] += 1
                room -= 1
    return picked


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
