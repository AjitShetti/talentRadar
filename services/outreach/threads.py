"""
services/outreach/threads.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A user's threads: one conversation with one company, and the people in it.

This module creates, updates and deletes a thread, finds the thread a company
already has, searches stored companies for the "Add a company" box, and turns a
loaded thread into the ``ThreadSummary`` every list row is built from. The
people inside a thread, their channels and the full detail live in
``people.py``; touches and the follow-up clock in ``touches.py``.

Rules this module keeps:

* **Every function takes the caller's ``user_id`` and filters on it.** A thread
  that belongs to someone else is a ``NotFound`` (404), exactly like one that
  does not exist, so an id reveals nothing. Nothing is touched before that check.
* **A company is opened at most once per user.** Adding it again returns the
  existing thread, reopening it if it was closed; a request that races past the
  check loses to the unique index and gets the winner back, not an error.
* **Typing a name never writes to ``companies``.** That table is shared and the
  map lists it; a name nobody on the map knows becomes a private, name-only
  thread (``company_id`` NULL).
* **The names are snapshots.** ``company_name`` and ``job_title`` are copied onto
  the thread, so it stays readable after the company is renamed or the posting
  is pruned.
* **No model call and no outside call.** Everything here is a database read or
  write.

``people`` and ``touches`` are ``passive_deletes`` and ``lazy="raise"``: the
database deletes them with the thread, and a caller that needs them loads them
explicitly (``load_owned_thread`` does).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, TypedDict, cast

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.outreach import (
    MAX_OPEN_THREADS,
    NEXT_STEP_MAX,
    NOTE_MAX,
    OUTCOMES,
    SOURCES,
    PersonClock,
    StoredState,
    aware,
    clean_text,
    effective_state,
    ist_day_bounds,
    ist_today,
    thread_stage,
)
from services.base import parse_uuid
from services.outreach.errors import Conflict, Invalid, NotFound
from storage.database import AsyncSessionLocal
from storage.models import Company, Job, JobApplication, OutreachPerson, OutreachThread

logger = logging.getLogger(__name__)

# Both are the widths of the columns the snapshots are copied into.
COMPANY_NAME_MAX = 256
JOB_TITLE_MAX = 256

MAX_SEARCH_RESULTS = 5
MAX_SEARCH_LENGTH = 100

_EPOCH = datetime.min.replace(tzinfo=UTC)

# Which person leads a thread: the first rank that anyone holds wins, and among
# several people at that rank, the one added first.
_LEAD_RANK = {"replied": 0, "waiting": 2, "cold": 3, "planned": 4}
_LEAD_RANK_DUE = 1


class ThreadPatch(TypedDict, total=False):
    """The changes ``update_thread`` accepts. A key that is absent is left alone.

    ``None`` clears ``note``, ``next_step``, ``next_step_at``, ``job_id`` (and
    with it the job title snapshot) and ``application_id``.
    ``close_outcome`` closes the thread; ``reopen`` undoes that. They cannot be
    sent together.
    """

    note: str | None
    next_step: str | None
    next_step_at: date | None
    job_id: str | None
    application_id: str | None
    close_outcome: str
    reopen: bool


# --- small helpers -----------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return None if value is None else aware(value).isoformat()


def _id(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _owner(user_id: str) -> uuid.UUID:
    uid = parse_uuid(user_id)
    if uid is None:
        raise NotFound("User not found")
    return uid


def _text(value: str | None, limit: int, label: str) -> str | None:
    """Cleaned, bounded free text; ``None`` when nothing is left. Too long is a 422."""
    try:
        return clean_text(value, limit)
    except ValueError as exc:
        raise Invalid(f"Keep the {label} to {limit} characters or fewer.") from exc


def _one_line(value: str | None, limit: int, label: str) -> str | None:
    text = _text(value, limit, label)
    return None if text is None else " ".join(text.split())


def _like_escape(value: str) -> str:
    """Make ``value`` match itself literally inside a LIKE pattern (escape is ``\\``)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- the clock, read from a stored person -----------------------------------------


def person_clock(person: OutreachPerson) -> PersonClock:
    """A stored person as the pure follow-up clock, with every time made UTC-aware."""
    return PersonClock(
        state=cast("StoredState", person.state),
        first_sent_at=aware(person.first_sent_at),
        last_outbound_at=aware(person.last_outbound_at),
        follow_ups_sent=person.follow_ups_sent,
        next_follow_up_at=aware(person.next_follow_up_at),
        replied_at=aware(person.replied_at),
    )


def _day_start(moment: datetime) -> datetime:
    """00:00 IST on the IST day ``moment`` falls on, as a UTC-aware instant."""
    return ist_day_bounds(ist_today(moment))[0]


def follow_up_due_from(clock: PersonClock) -> datetime | None:
    """The instant a waiting person's follow-up becomes owed: 00:00 IST on the day it
    is dated, or ``None`` when no follow-up is pending."""
    if clock.state != "waiting" or clock.next_follow_up_at is None:
        return None
    return _day_start(clock.next_follow_up_at)


def follow_up_due(clock: PersonClock, now: datetime) -> bool:
    """Is a follow-up owed to this person today in India?

    A follow-up dated any time today (IST) is due from 00:00 IST, not from the
    minute it was set for, so a Bengaluru morning shows what the day holds.
    ``domain.outreach.is_due`` compares instants; this is the day-granular rule
    every surface that says "due" must share.
    """
    owed_from = follow_up_due_from(clock)
    return owed_from is not None and owed_from <= aware(now)


# --- the summary -------------------------------------------------------------


def _in_added_order(people: Sequence[OutreachPerson]) -> list[OutreachPerson]:
    return sorted(people, key=lambda person: (aware(person.created_at) or _EPOCH, str(person.id)))


def _lead(
    entries: Sequence[tuple[OutreachPerson, PersonClock]], states: Sequence[str], now: datetime
) -> dict[str, str] | None:
    """The person to show for the thread. ``entries`` are in the order added, and
    a later person only displaces the lead by holding a strictly better rank."""
    best: tuple[int, OutreachPerson, str] | None = None
    for (person, clock), state in zip(entries, states, strict=True):
        if state == "dropped":
            continue
        if state == "waiting" and follow_up_due(clock, now):
            rank = _LEAD_RANK_DUE
        else:
            rank = _LEAD_RANK[state]
        if best is None or rank < best[0]:
            best = (rank, person, state)
    return None if best is None else {"name": best[1].name, "state": best[2]}


def summarise(
    thread: OutreachThread,
    now: datetime,
    *,
    company_summary: str | None = None,
    application_status: str | None = None,
) -> dict[str, Any]:
    """One thread as a ``ThreadSummary``.

    The thread's people and touches must be loaded. ``company_summary`` and
    ``application_status`` live on other rows; a caller with a session passes
    them in, or uses ``summarise_many`` which fetches them for a whole list.

    * ``stage`` is ``thread_stage`` over each person's effective state, so a
      waiting person who has gone quiet reads as ``cold`` without any job.
    * ``lead`` is the person worth naming on the row, in the order the thread
      stage uses: replied, then a waiting person who is due, then waiting, then
      cold, then planned; the earliest added wins a tie. A dropped person never
      leads, and the summary carries only a name and a state - never an email
      or a link.
    * ``people_count`` is the people still in the plan: a dropped person is not
      counted.
    * ``next_due_at`` is the earliest moment something falls owed, always the
      start of an IST day (a follow-up from the day it is dated, a replied
      thread's next step from its day). ``due`` is exactly "that moment has
      arrived", so the two cannot disagree. A closed thread has neither.
    """
    moment = aware(now)
    closed = thread.closed_at is not None
    entries = [(person, person_clock(person)) for person in _in_added_order(thread.people)]
    states = [effective_state(clock, moment) for _, clock in entries]
    stage = thread_stage(states, closed=closed)

    deadlines: list[datetime] = []
    if not closed:
        for _, clock in entries:
            owed_from = follow_up_due_from(clock)
            if owed_from is not None:
                deadlines.append(owed_from)
        if stage == "replied" and thread.next_step_at is not None:
            deadlines.append(ist_day_bounds(thread.next_step_at)[0])
    next_due_at = min(deadlines) if deadlines else None

    touched = [aware(touch.happened_at) for touch in thread.touches]
    has_job = thread.job_id is not None or thread.job_title is not None

    return {
        "id": str(thread.id),
        "company_id": _id(thread.company_id),
        "company_name": thread.company_name,
        "company_summary": company_summary,
        "stage": stage,
        "outcome": thread.outcome,
        "people_count": sum(1 for state in states if state != "dropped"),
        "lead": _lead(entries, states, moment),
        "due": next_due_at is not None and next_due_at <= moment,
        "next_due_at": _iso(next_due_at),
        "last_touch_at": _iso(max(touched)) if touched else None,
        "job": {"id": _id(thread.job_id), "title": thread.job_title} if has_job else None,
        "application": (
            None
            if thread.application_id is None
            else {"id": str(thread.application_id), "status": application_status}
        ),
        "source": thread.source,
        "created_at": _iso(thread.created_at),
    }


async def summarise_many(
    session: AsyncSession, threads: Sequence[OutreachThread], now: datetime
) -> list[dict[str, Any]]:
    """``summarise`` for several loaded threads, fetching the company summaries and
    application statuses in two queries rather than two per thread."""
    company_ids = {t.company_id for t in threads if t.company_id is not None}
    application_ids = {t.application_id for t in threads if t.application_id is not None}

    summaries: dict[uuid.UUID, str | None] = {}
    if company_ids:
        rows = await session.execute(
            select(Company.id, Company.summary).where(Company.id.in_(company_ids))
        )
        summaries = {row.id: row.summary for row in rows}

    statuses: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if application_ids:
        rows = await session.execute(
            select(JobApplication.id, JobApplication.user_id, JobApplication.status).where(
                JobApplication.id.in_(application_ids)
            )
        )
        statuses = {row.id: (row.user_id, row.status.value) for row in rows}

    out: list[dict[str, Any]] = []
    for thread in threads:
        tracked = statuses.get(thread.application_id) if thread.application_id else None
        out.append(
            summarise(
                thread,
                now,
                company_summary=summaries.get(thread.company_id) if thread.company_id else None,
                # Another user's tracker row is never described, even by status.
                application_status=(
                    tracked[1] if tracked is not None and tracked[0] == thread.user_id else None
                ),
            )
        )
    return out


# --- loading -----------------------------------------------------------------


async def load_owned_thread(
    session: AsyncSession, user_id: str, thread_id: str, *, with_children: bool = True
) -> OutreachThread:
    """The caller's thread, or ``NotFound``.

    A malformed id, a missing thread and another user's thread are the same
    answer. ``with_children`` also loads the people and touches, which
    ``summarise`` needs; leave it off to change or delete the thread itself.
    """
    uid, tid = parse_uuid(user_id), parse_uuid(thread_id)
    if uid is None or tid is None:
        raise NotFound("Thread not found")
    stmt = select(OutreachThread).where(OutreachThread.id == tid, OutreachThread.user_id == uid)
    if with_children:
        stmt = stmt.options(
            selectinload(OutreachThread.people), selectinload(OutreachThread.touches)
        )
    thread = (await session.execute(stmt)).scalar_one_or_none()
    if thread is None:
        raise NotFound("Thread not found")
    return thread


async def _thread_for(
    session: AsyncSession, uid: uuid.UUID, company_id: uuid.UUID
) -> OutreachThread | None:
    """The user's thread with this company, with its people and touches."""
    return (
        await session.execute(
            select(OutreachThread)
            .where(OutreachThread.user_id == uid, OutreachThread.company_id == company_id)
            .options(selectinload(OutreachThread.people), selectinload(OutreachThread.touches))
        )
    ).scalar_one_or_none()


def _name_key(name: str) -> str:
    """A company name as compared for "the same one typed again": whitespace
    collapsed, case folded."""
    return " ".join(name.split()).casefold()


async def _open_typed_thread(
    session: AsyncSession, uid: uuid.UUID, name: str
) -> OutreachThread | None:
    """The user's open name-only thread for ``name``, if they already have one.

    Compared in Python so the case folding is Unicode's; the scan is bounded by
    ``MAX_OPEN_THREADS``. A closed thread is not matched: typing the name again
    after closing it starts a fresh conversation.
    """
    rows = await session.execute(
        select(OutreachThread)
        .where(
            OutreachThread.user_id == uid,
            OutreachThread.company_id.is_(None),
            OutreachThread.closed_at.is_(None),
        )
        .order_by(OutreachThread.created_at, OutreachThread.id)
    )
    key = _name_key(name)
    return next((t for t in rows.scalars() if _name_key(t.company_name) == key), None)


async def _summary_of(
    session: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID, now: datetime
) -> dict[str, Any]:
    """Read a thread back after a write, so the summary shows what is stored."""
    thread = await load_owned_thread(session, str(user_id), str(thread_id))
    return (await summarise_many(session, [thread], now))[0]


async def _ensure_room(session: AsyncSession, uid: uuid.UUID) -> None:
    """Refuse a new or reopened thread at the cap. The cap is soft: it counts, then the
    caller inserts, so two simultaneous requests can pass one over the limit."""
    open_threads = (
        await session.execute(
            select(func.count())
            .select_from(OutreachThread)
            .where(OutreachThread.user_id == uid, OutreachThread.closed_at.is_(None))
        )
    ).scalar_one()
    if open_threads >= MAX_OPEN_THREADS:
        raise Conflict("Close a thread before adding another")


async def _get_company(session: AsyncSession, company_id: str) -> Company:
    cid = parse_uuid(company_id)
    company = None if cid is None else await session.get(Company, cid)
    if company is None:
        raise NotFound("Company not found")
    return company


async def _get_job(session: AsyncSession, job_id: str) -> Job:
    jid = parse_uuid(job_id)
    job = None if jid is None else await session.get(Job, jid)
    if job is None:
        raise NotFound("Role not found")
    return job


async def _application_for(
    session: AsyncSession, uid: uuid.UUID, job_id: uuid.UUID
) -> uuid.UUID | None:
    """The user's own tracker row for this posting, if they track it."""
    return (
        await session.execute(
            select(JobApplication.id).where(
                JobApplication.user_id == uid, JobApplication.job_id == job_id
            )
        )
    ).scalar_one_or_none()


# --- create -----------------------------------------------------------------


async def create_thread(
    user_id: str,
    *,
    company_id: str | None,
    company_name: str | None,
    job_id: str | None,
    source: str,
) -> tuple[dict[str, Any], bool]:
    """Open a thread with a company, or return the one already open.

    Returns ``(ThreadSummary, created)``. A stored company (``company_id``) is
    opened once per user: a second call returns the same thread with
    ``created=False``, reopening it if it had been closed. Otherwise
    ``company_name`` is a typed name and starts a private, name-only thread; typing
    the same name again (ignoring case and spacing) while that thread is open
    returns it too.
    ``job_id`` attaches a posting as context and links the user's own tracker
    row for it, if any; on a thread that already exists it is attached only if the
    thread has no role yet. Opening a thread, or reopening one, is refused with a
    ``Conflict`` once the user has ``MAX_OPEN_THREADS`` open; returning one that is
    already open never is.
    """
    if source not in SOURCES:
        raise Invalid(f"Unknown source: {source!r}")
    uid = _owner(user_id)
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        company_uuid: uuid.UUID | None = None
        if company_id is not None:
            company = await _get_company(session, company_id)
            company_uuid, name = company.id, company.name[:COMPANY_NAME_MAX]
        else:
            typed = _one_line(company_name, COMPANY_NAME_MAX, "company name")
            if typed is None:
                raise Invalid("Choose a company or type its name")
            name = typed
        job = None if job_id is None else await _get_job(session, job_id)

        if company_uuid is None:
            thread = await _open_typed_thread(session, uid, name)
        else:
            thread = await _thread_for(session, uid, company_uuid)
        created = thread is None
        thread_uuid = uuid.uuid4()  # the new thread's id; replaced by an existing thread's
        if thread is None:
            await _ensure_room(session, uid)
            application_id = None if job is None else await _application_for(session, uid, job.id)
            session.add(
                OutreachThread(
                    id=thread_uuid,
                    user_id=uid,
                    company_id=company_uuid,
                    company_name=name,
                    job_id=None if job is None else job.id,
                    job_title=None if job is None else job.title[:JOB_TITLE_MAX],
                    application_id=application_id,
                    source=source,
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                # Two requests for one company both passed the check above and the
                # unique index let one in. Answer with the thread that won.
                await session.rollback()
                if company_uuid is not None:
                    thread = await _thread_for(session, uid, company_uuid)
                if thread is None:
                    raise
                created = False

        if thread is not None:
            thread_uuid = thread.id
            if thread.closed_at is not None:
                await _ensure_room(session, uid)
                thread.closed_at = None
                thread.outcome = None
            if job is not None and thread.job_id is None:
                # Adding the company again, this time from a role: attach it. A role
                # that is already attached is left alone.
                await _set_job(session, thread, job_id, link_application=True)
        await session.commit()

        logger.info("outreach thread %s %s", thread_uuid, "created" if created else "reused")
        return await _summary_of(session, uid, thread_uuid, now), created


# --- update -----------------------------------------------------------------


async def _set_job(
    session: AsyncSession, thread: OutreachThread, job_id: str | None, *, link_application: bool
) -> None:
    if job_id is None:
        thread.job_id = None
        thread.job_title = None
        return
    job = await _get_job(session, job_id)
    thread.job_id = job.id
    thread.job_title = job.title[:JOB_TITLE_MAX]
    if link_application and thread.application_id is None:
        thread.application_id = await _application_for(session, thread.user_id, job.id)


async def _set_application(
    session: AsyncSession, thread: OutreachThread, application_id: str | None
) -> None:
    if application_id is None:
        thread.application_id = None
        return
    aid = parse_uuid(application_id)
    found = (
        None
        if aid is None
        else (
            await session.execute(
                select(JobApplication.id).where(
                    JobApplication.id == aid, JobApplication.user_id == thread.user_id
                )
            )
        ).scalar_one_or_none()
    )
    if found is None:
        raise NotFound("Application not found")
    thread.application_id = found


async def update_thread(user_id: str, thread_id: str, changes: ThreadPatch) -> None:
    """Apply ``changes`` to the caller's thread, all of them or none.

    Text is cleaned and bounded (``NOTE_MAX``, ``NEXT_STEP_MAX``); a role must
    exist and an application must be the caller's own (otherwise ``NotFound``).
    Attaching a role links the caller's tracker row for it unless the same patch
    names an application. ``close_outcome`` must be one of ``OUTCOMES``.
    Reopening a closed thread needs room under ``MAX_OPEN_THREADS``.
    """
    if "close_outcome" in changes and changes.get("reopen"):
        raise Invalid("A thread cannot be closed and reopened at once")
    if "close_outcome" in changes and changes["close_outcome"] not in OUTCOMES:
        raise Invalid(f"Unknown outcome: {changes['close_outcome']!r}")
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        thread = await load_owned_thread(session, user_id, thread_id, with_children=False)

        if "note" in changes:
            thread.note = _text(changes["note"], NOTE_MAX, "note")
        if "next_step" in changes:
            thread.next_step = _one_line(changes["next_step"], NEXT_STEP_MAX, "next step")
        if "next_step_at" in changes:
            thread.next_step_at = changes["next_step_at"]
        if "job_id" in changes:
            await _set_job(
                session,
                thread,
                changes["job_id"],
                link_application="application_id" not in changes,
            )
        if "application_id" in changes:
            await _set_application(session, thread, changes["application_id"])
        if "close_outcome" in changes:
            thread.closed_at = thread.closed_at or now
            thread.outcome = changes["close_outcome"]
        elif changes.get("reopen") and thread.closed_at is not None:
            await _ensure_room(session, thread.user_id)
            thread.closed_at = None
            thread.outcome = None

        thread_uuid = thread.id
        await session.commit()
        logger.info("outreach thread %s updated (%d fields)", thread_uuid, len(changes))


# --- delete -----------------------------------------------------------------


async def delete_thread(user_id: str, thread_id: str) -> None:
    """Delete the caller's thread. Its people and touches go with it, by the
    database's ``ON DELETE CASCADE``; they are never loaded."""
    async with AsyncSessionLocal() as session:
        thread = await load_owned_thread(session, user_id, thread_id, with_children=False)
        thread_uuid = thread.id
        await session.delete(thread)
        await session.commit()
        logger.info("outreach thread %s deleted", thread_uuid)


# --- lookups ----------------------------------------------------------------


async def thread_for_company(user_id: str, company_id: str) -> dict[str, Any] | None:
    """The caller's thread with this company (open or closed), or ``None``.

    Another user's thread is simply not found, so the company drawer can ask
    for any company without learning who else has opened it.
    """
    uid, cid = parse_uuid(user_id), parse_uuid(company_id)
    if uid is None or cid is None:
        return None
    async with AsyncSessionLocal() as session:
        thread = await _thread_for(session, uid, cid)
        if thread is None:
            return None
        return (await summarise_many(session, [thread], datetime.now(UTC)))[0]


async def search_companies(q: str, limit: int = MAX_SEARCH_RESULTS) -> list[dict[str, Any]]:
    """Up to five stored companies whose name contains ``q``, ignoring case.

    Every stored company matches, not only the ones the map lists: the point is
    to find "the company I mean" before falling back to a typed name. The exact
    name comes first, then names that start with ``q``, then the rest; shorter
    names before longer ones. ``%`` and ``_`` in ``q`` match themselves.
    """
    needle = " ".join(q.split())[:MAX_SEARCH_LENGTH].lower()
    count = min(limit, MAX_SEARCH_RESULTS)
    if not needle or count < 1:
        return []
    escaped = _like_escape(needle)
    name = func.lower(Company.name)
    rank = case(
        (name == needle, 0),
        (name.like(f"{escaped}%", escape="\\"), 1),
        else_=2,
    )
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Company.id, Company.name, Company.summary, Company.area)
            .where(name.like(f"%{escaped}%", escape="\\"))
            .order_by(rank, func.length(Company.name), Company.name)
            .limit(count)
        )
        return [
            {"id": str(row.id), "name": row.name, "summary": row.summary, "area": row.area}
            for row in rows
        ]
