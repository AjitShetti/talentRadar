"""
services/outreach/touches.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
What the user did, and what is owed because of it: recording a touch, the due
list, the daily reach count, the Outreach page and the daily target.

A touch is the user saying "I sent it" or "they replied". Nothing here sends
anything; the app only keeps the clock. Creating threads is ``threads.py``, the
people in them ``people.py``, and the prune of dead threads ``retention.py``.

Rules this module keeps:

* **The clock comes only from ``domain.outreach.apply_touch``.** This module stores
  what it returns and names the touch's ``kind`` from it; it never decides a
  transition itself. A thread-level touch (no person, such as a GitHub comment) has
  no clock: its first outbound is kind ``first`` (and counts as a reach), every
  other thread-level touch is ``other``, and no person changes.
* **Every touch sets ``thread.updated_at``** to the moment it was recorded, which is
  what the nightly prune reads as "last activity".
* **Touches are logged in the order the user remembers them.** A touch is stored
  whenever it happened, up to 30 days back and never in the future; an outbound one
  dated before the person's last message leaves the clock alone, and a reply always
  replies (see ``apply_touch``).
* **"Due" is a day in India, not a minute.** A follow-up dated any time today in IST
  is due from 00:00 IST. The due list and the thread rows both use
  ``threads.follow_up_due``, so the two cannot disagree.
* **Ownership first.** Every function takes the caller's ``user_id`` and filters on
  it; another user's thread or person is a ``NotFound`` (404). A touch is recorded
  under a lock on the thread row, so two simultaneous "I sent it" clicks queue and
  the second sees the first.
* **No model call and no outside call.**
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.outreach import (
    BODY_MAX,
    CHANNELS,
    DEFAULT_DAILY_TARGET,
    DIRECTIONS,
    MAX_DAILY_TARGET,
    MAX_TOUCHES_PER_THREAD,
    NEXT_STEP_MAX,
    NOTE_MAX,
    DueItem,
    PersonClock,
    TouchKind,
    apply_touch,
    aware,
    ist_day_bounds,
    ist_today,
    sort_due,
    validate_happened_at,
)
from services.base import parse_uuid
from services.outreach.errors import Conflict, Invalid, NotFound
from services.outreach.people import _find_person, get_thread
from services.outreach.threads import (
    _one_line,
    _owner,
    _text,
    follow_up_due,
    load_owned_thread,
    person_clock,
    summarise_many,
)
from storage.database import AsyncSessionLocal
from storage.models import (
    OutreachPerson,
    OutreachSettings,
    OutreachThread,
    OutreachTouch,
)

logger = logging.getLogger(__name__)


# --- recording a touch --------------------------------------------------------------


def locked_thread_statement(user_id: uuid.UUID, thread_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Lock the caller's thread row (``FOR UPDATE``) and nothing else.

    Taken before the people and touches are read, so a second request for the same
    thread waits here until the first has committed, then reads what the first wrote.
    SQLite ignores the lock; the tests run on it.
    """
    return (
        select(OutreachThread.id)
        .where(OutreachThread.id == thread_id, OutreachThread.user_id == user_id)
        .with_for_update()
    )


async def _lock_owned_thread(session: AsyncSession, user_id: str, thread_id: str) -> None:
    uid, tid = parse_uuid(user_id), parse_uuid(thread_id)
    if uid is None or tid is None:
        raise NotFound("Thread not found")
    found = (await session.execute(locked_thread_statement(uid, tid))).scalar_one_or_none()
    if found is None:
        raise NotFound("Thread not found")


def _thread_level_kind(thread: OutreachThread, direction: str) -> TouchKind:
    """The kind of a touch with no person: ``first`` for the thread's first outbound
    one, ``other`` for everything else. There is no clock to move.

    A person who was removed leaves their touches behind with no person (the database
    sets them NULL), and those cannot be told from a GitHub comment, so they count as
    the thread's outbound touches too."""
    if direction != "out":
        return "other"
    started = any(t.person_id is None and t.direction == "out" for t in thread.touches)
    return "other" if started else "first"


def _store_clock(person: OutreachPerson, clock: PersonClock) -> None:
    person.state = clock.state
    person.first_sent_at = clock.first_sent_at
    person.last_outbound_at = clock.last_outbound_at
    person.follow_ups_sent = clock.follow_ups_sent
    person.next_follow_up_at = clock.next_follow_up_at
    person.replied_at = clock.replied_at


async def record_touch(
    user_id: str,
    thread_id: str,
    *,
    person_id: str | None,
    channel: str,
    direction: str,
    note: str | None = None,
    body: str | None = None,
    happened_at: datetime | None = None,
    next_step: str | None = None,
    next_step_at: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record what the user did, move the person's clock, and return the ``ThreadDetail``.

    ``direction`` is ``out`` (the user wrote) or ``in`` (they answered); ``channel`` is
    one of ``CHANNELS``. ``person_id`` is the person it concerns, or ``None`` for a
    thread-level touch. ``happened_at`` defaults to now, may be up to 30 days back and
    never the future. The touch's kind is set here from the clock, never by the client:

    * a first message starts the person's clock; a second one is a follow-up, and
      "I sent it" clicked twice is therefore a follow-up, not a second reach;
    * a reply stops the clock and keeps the first ``replied_at``;
    * anything the clock has no use for (a third nudge, a message after a reply, one
      dated before the last) is ``other``: logged, no clock change.

    ``note`` (500), ``body`` (2,000, the kept copy of the message) and ``next_step``
    (140) are cleaned and bounded; a ``next_step`` and ``next_step_at`` are stored on
    the thread when given. Every touch sets the thread's ``updated_at``.

    Raises ``Invalid`` for an unknown channel or direction, a bad date or text over its
    limit; ``NotFound`` for a thread that is not the caller's or a person who is not in
    it; ``Conflict`` for the 61st touch.
    """
    moment = datetime.now(UTC) if now is None else aware(now)
    if channel not in CHANNELS:
        raise Invalid(f"Unknown channel: {channel!r}")
    if direction not in DIRECTIONS:
        raise Invalid(f"Unknown direction: {direction!r}")
    try:
        happened = validate_happened_at(happened_at, moment)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    clean_note = _text(note, NOTE_MAX, "note")
    clean_body = _text(body, BODY_MAX, "message copy")
    step = _one_line(next_step, NEXT_STEP_MAX, "next step")

    async with AsyncSessionLocal() as session:
        await _lock_owned_thread(session, user_id, thread_id)
        thread = await load_owned_thread(session, user_id, thread_id)
        person = None if person_id is None else _find_person(thread, person_id)
        if len(thread.touches) >= MAX_TOUCHES_PER_THREAD:
            raise Conflict(f"A thread keeps at most {MAX_TOUCHES_PER_THREAD} touches")

        if person is None:
            kind = _thread_level_kind(thread, direction)
        else:
            effect = apply_touch(
                person_clock(person), direction=direction, channel=channel, at=happened
            )
            _store_clock(person, effect.clock)
            kind = effect.kind

        touch = OutreachTouch(
            id=uuid.uuid4(),
            thread_id=thread.id,
            person_id=None if person is None else person.id,
            user_id=thread.user_id,
            channel=channel,
            direction=direction,
            kind=kind,
            note=clean_note,
            body=clean_body,
            happened_at=happened,
            created_at=moment,
        )
        session.add(touch)
        if step is not None:
            thread.next_step = step
        if next_step_at is not None:
            thread.next_step_at = next_step_at
        thread.updated_at = moment

        touch_uuid, thread_uuid = touch.id, thread.id
        await session.commit()
        logger.info("outreach touch %s (%s) recorded on thread %s", touch_uuid, kind, thread_uuid)
    return await get_thread(user_id, thread_id, now=moment)


# --- the due list -------------------------------------------------------------------


def _due_from(threads: Sequence[OutreachThread], now: datetime) -> list[DueItem]:
    """What is owed at ``now`` across ``threads`` (people loaded), next steps first.

    * A **follow-up** is owed to every ``waiting`` person whose follow-up is dated today
      in IST or earlier (``follow_up_due``), in a thread that is not closed.
    * A **next step** is owed by a thread where someone has replied, once its date
      (IST) has come. It belongs to the thread, so it names no person.

    The thread's ``due`` flag and ``next_due_at`` are built from the same two rules.
    """
    moment = aware(now)
    items: list[DueItem] = []
    for thread in threads:
        if thread.closed_at is not None:
            continue
        replied = False
        for person in thread.people:
            clock = person_clock(person)
            replied = replied or clock.state == "replied"
            if clock.next_follow_up_at is not None and follow_up_due(clock, moment):
                items.append(
                    DueItem(
                        thread_id=str(thread.id),
                        person_id=str(person.id),
                        kind="follow_up",
                        due_at=clock.next_follow_up_at,
                        company_name=thread.company_name,
                        person_name=person.name,
                        follow_up_number=clock.follow_ups_sent + 1,
                        last_outbound_at=clock.last_outbound_at,
                        next_step=None,
                    )
                )
        if replied and thread.next_step_at is not None:
            owed_from = ist_day_bounds(thread.next_step_at)[0]
            if owed_from <= moment:
                items.append(
                    DueItem(
                        thread_id=str(thread.id),
                        person_id=None,
                        kind="next_step",
                        due_at=owed_from,
                        company_name=thread.company_name,
                        person_name=None,
                        follow_up_number=None,
                        last_outbound_at=None,
                        next_step=thread.next_step,
                    )
                )
    return sort_due(items)


async def due_items(user_id: str, *, now: datetime | None = None) -> list[DueItem]:
    """Everything the caller owes today (IST), next steps first, then follow-ups oldest
    first. A closed thread, a dropped person and a follow-up dated tomorrow are absent."""
    uid = _owner(user_id)
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(OutreachThread)
            .where(OutreachThread.user_id == uid, OutreachThread.closed_at.is_(None))
            .options(selectinload(OutreachThread.people))
        )
        return _due_from(list(rows.scalars()), moment)


# --- the daily count ----------------------------------------------------------------


async def _reached_today(session: AsyncSession, uid: uuid.UUID, moment: datetime) -> int:
    start, end = ist_day_bounds(ist_today(moment))
    return (
        await session.execute(
            select(func.count())
            .select_from(OutreachTouch)
            .where(
                OutreachTouch.user_id == uid,
                OutreachTouch.kind == "first",
                OutreachTouch.happened_at >= start,
                OutreachTouch.happened_at < end,
            )
        )
    ).scalar_one()


async def reached_today(user_id: str, *, now: datetime | None = None) -> int:
    """How many people the caller started a conversation with today (IST).

    Counts touches of kind ``first`` whose ``happened_at`` falls on today in India:
    a first message to a person, or a thread's first GitHub comment. A follow-up
    does not count - those are already surfaced as due work, and counting nudges
    would reward nagging.
    """
    uid = _owner(user_id)
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        return await _reached_today(session, uid, moment)


async def _conversations_started(session: AsyncSession, uid: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(OutreachTouch.thread_id.distinct())).where(
                OutreachTouch.user_id == uid, OutreachTouch.kind == "reply"
            )
        )
    ).scalar_one()


async def conversations_started(user_id: str) -> int:
    """How many of the caller's threads have a recorded reply, closed ones included.

    "They replied" is a conversation whatever they said, so a "not now" counts. It is
    read from the reply on the timeline, not from who is in the thread now: taking a
    person out leaves their touches behind, so tidying a thread never lowers the count.
    """
    uid = _owner(user_id)
    async with AsyncSessionLocal() as session:
        return await _conversations_started(session, uid)


# --- the daily target ---------------------------------------------------------------


async def _target(session: AsyncSession, uid: uuid.UUID) -> int:
    row = await session.get(OutreachSettings, uid)
    return DEFAULT_DAILY_TARGET if row is None else row.daily_target


async def get_target(user_id: str) -> int:
    """The caller's daily target; no row means ``DEFAULT_DAILY_TARGET``."""
    uid = _owner(user_id)
    async with AsyncSessionLocal() as session:
        return await _target(session, uid)


async def set_target(user_id: str, daily_target: int) -> int:
    """Set the caller's daily target, 0 to ``MAX_DAILY_TARGET`` (0 turns the Overview
    suggestion off), and return it. Anything else is ``Invalid``."""
    if not 0 <= daily_target <= MAX_DAILY_TARGET:
        raise Invalid(f"Choose a daily target between 0 and {MAX_DAILY_TARGET}.")
    uid = _owner(user_id)
    async with AsyncSessionLocal() as session:
        row = await session.get(OutreachSettings, uid)
        if row is None:
            session.add(OutreachSettings(user_id=uid, daily_target=daily_target))
        else:
            row.daily_target = daily_target
        try:
            await session.commit()
        except IntegrityError as exc:
            # A first-time save raced another one and lost, or the user is gone.
            await session.rollback()
            row = await session.get(OutreachSettings, uid)
            if row is None:
                raise NotFound("User not found") from exc
            row.daily_target = daily_target
            await session.commit()
    return daily_target


# --- the page -----------------------------------------------------------------------


def _attention(thread: OutreachThread, summary: dict[str, Any]) -> tuple[bool, bool, float, str]:
    """Sort key for the thread list: open before closed, due first, then the most
    recently active."""
    return (
        summary["stage"] == "closed",
        not summary["due"],
        -aware(thread.updated_at).timestamp(),
        str(thread.id),
    )


async def home(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """The Outreach page: ``{target, conversations_started, due, threads}``.

    ``target`` is ``{daily, reached_today}``. ``due`` is the due list as dicts.
    ``threads`` is every thread of the caller's as a ``ThreadSummary``: open before
    closed, those with something due first, then the most recently active. One
    session, so the list, the due list and the counts are read together.
    """
    uid = _owner(user_id)
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(OutreachThread)
            .where(OutreachThread.user_id == uid)
            .options(selectinload(OutreachThread.people), selectinload(OutreachThread.touches))
        )
        threads = list(rows.scalars())
        summaries = await summarise_many(session, threads, moment)
        reached = await _reached_today(session, uid, moment)
        started = await _conversations_started(session, uid)
        daily = await _target(session, uid)

    ordered = sorted(zip(threads, summaries, strict=True), key=lambda pair: _attention(*pair))
    return {
        "target": {"daily": daily, "reached_today": reached},
        "conversations_started": started,
        "due": [item.to_dict() for item in _due_from(threads, moment)],
        "threads": [summary for _, summary in ordered],
    }
