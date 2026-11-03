"""
services/routine.py
~~~~~~~~~~~~~~~~~~~
A user's daily routine and the first-run tour, both kept on the ``profiles`` row.

The rules (what a block is, what a tick is, when a tick expires) are ``domain/routine.py``.
This module reads and writes the row and shapes the answer; it calls no model and makes no
outside request. Every function returns the same dict, so a page can replace what it holds
with whatever a write answers.

    read_routine()     the routine, today's ticks, and whether the tour is still to show
    save_routine()     replace the blocks (today's ticks of blocks that remain are kept)
    set_tick()         tick or clear one block for today
    mark_tour_seen()   stamp the tour as seen, once

A write creates the profile row if the user has none, except a tick, which needs a routine
to tick. A read never creates one. ``routine`` is always assigned a new dict and never
changed in place, which is how SQLAlchemy comes to see the change.

Another user's block can never be named: every query is for the caller's own row, and an id
that is not in it is ``RoutineBlockNotFound``. Logs carry ids and counts only.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.routine import (
    ist_day,
    ticks_today,
    validate_blocks,
    with_blocks,
    with_tick,
)
from services.base import parse_uuid
from storage.database import AsyncSessionLocal
from storage.models import Profile

logger = logging.getLogger(__name__)


class RoutineBlockNotFound(LookupError):  # noqa: N818 - reads as the condition
    """The id names no block in the caller's routine."""


def _now() -> datetime:
    """The clock for every function here; a test replaces it."""
    return datetime.now(UTC)


def _stored_blocks(routine: Mapping[str, object] | None) -> list[dict[str, object]]:
    blocks = routine.get("blocks") if routine is not None else None
    if not isinstance(blocks, list):
        return []
    return [dict(block) for block in blocks if isinstance(block, Mapping)]


def _answer(
    *,
    routine: Mapping[str, object] | None,
    tour_seen: bool,
    onboarding_completed: bool,
    today: date,
) -> dict[str, Any]:
    return {
        "has_routine": routine is not None,
        "blocks": _stored_blocks(routine),
        "done_today": ticks_today(routine, today),
        "day": today.isoformat(),
        "tour_seen": tour_seen,
        "onboarding_completed": onboarding_completed,
    }


def _answer_for(profile: Profile | None, today: date) -> dict[str, Any]:
    """What a read says about ``profile`` (``None``: the user has none yet)."""
    if profile is None:
        return _answer(routine=None, tour_seen=False, onboarding_completed=False, today=today)
    return _answer(
        routine=profile.routine,
        tour_seen=profile.tour_seen_at is not None,
        onboarding_completed=bool(profile.onboarding_completed),
        today=today,
    )


async def _profile(session: AsyncSession, user_id: str, *, lock: bool = False) -> Profile | None:
    """The user's profile row, or ``None``. ``lock`` holds it for this transaction.

    The routine is one JSON value that a write reads, changes and writes back, so two
    quick ticks must queue behind one another or the second would undo the first.
    """
    user_uuid = parse_uuid(user_id)
    if user_uuid is None:
        return None
    query = select(Profile).where(Profile.user_id == user_uuid)
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def _profile_for_write(session: AsyncSession, user_id: str) -> Profile:
    """The user's profile row, added to the session when the user has none yet."""
    profile = await _profile(session, user_id, lock=True)
    if profile is not None:
        return profile
    user_uuid = parse_uuid(user_id)
    if user_uuid is None:
        raise ValueError("Invalid user_id")
    profile = Profile(user_id=user_uuid)
    session.add(profile)
    return profile


async def read_routine(*, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    """The user's routine, today's ticks and the tour flag. A missing profile reads empty."""
    today = ist_day(now or _now())
    async with AsyncSessionLocal() as session:
        return _answer_for(await _profile(session, user_id), today)


async def save_routine(
    *, user_id: str, blocks: list[dict[str, object]], now: datetime | None = None
) -> dict[str, Any]:
    """Replace the blocks with ``blocks`` (cleaned, checked and put in clock order).

    Raises ``domain.routine.RoutineInvalid`` with the sentence to show the user.
    """
    today = ist_day(now or _now())
    checked = validate_blocks(blocks)
    async with AsyncSessionLocal() as session:
        profile = await _profile_for_write(session, user_id)
        routine = with_blocks(profile.routine, checked, today)
        profile.routine = routine
        tour_seen = profile.tour_seen_at is not None
        onboarding_completed = bool(profile.onboarding_completed)
        await session.commit()
    logger.info("routine saved user=%s blocks=%d", user_id, len(checked))
    return _answer(
        routine=routine,
        tour_seen=tour_seen,
        onboarding_completed=onboarding_completed,
        today=today,
    )


async def set_tick(
    *, user_id: str, block_id: str, done: bool, now: datetime | None = None
) -> dict[str, Any]:
    """Tick (``done``) or clear ``block_id`` for today.

    Raises ``RoutineBlockNotFound`` when the user's routine has no such block, which is
    also what a user with no routine gets, and creates no row.
    """
    today = ist_day(now or _now())
    async with AsyncSessionLocal() as session:
        profile = await _profile(session, user_id, lock=True)
        if profile is None or not any(
            block.get("id") == block_id for block in _stored_blocks(profile.routine)
        ):
            raise RoutineBlockNotFound(block_id)
        routine = with_tick(profile.routine, block_id, done, today)
        profile.routine = routine
        tour_seen = profile.tour_seen_at is not None
        onboarding_completed = bool(profile.onboarding_completed)
        await session.commit()
    logger.info("routine tick user=%s done=%s", user_id, done)
    return _answer(
        routine=routine,
        tour_seen=tour_seen,
        onboarding_completed=onboarding_completed,
        today=today,
    )


async def mark_tour_seen(*, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Stamp the tour as seen. The first stamp stays: a second call changes nothing."""
    moment = now or _now()
    today = ist_day(moment)
    async with AsyncSessionLocal() as session:
        profile = await _profile_for_write(session, user_id)
        if profile.tour_seen_at is None:
            profile.tour_seen_at = moment
        routine = profile.routine
        onboarding_completed = bool(profile.onboarding_completed)
        await session.commit()
    logger.info("tour seen user=%s", user_id)
    return _answer(
        routine=routine,
        tour_seen=True,
        onboarding_completed=onboarding_completed,
        today=today,
    )
