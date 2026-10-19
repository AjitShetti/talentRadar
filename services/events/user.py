"""
services/events/user.py
~~~~~~~~~~~~~~~~~~~~~~~
What a signed-in user does with events: save one or mark it going, add one by link or by
hand, and keep notes on the people they met.

Rules that hold throughout:

* **Every write is the caller's own.** A save, a person or a private event of another user is
  ``None`` (or ``False``) exactly as for an id that does not exist, so the API answers both with
  the same 404. Nothing is ever sent on the user's behalf.
* **People are what the user typed.** No guessed e-mail, no lookup, nothing fetched for them;
  LinkedIn is a link the user pasted, and only a search link is built (``read.contact_out``).
  ``company_id`` is set only when the typed name is exactly a tech company on the map.
* **A pasted link is never fetched as given.** :func:`import_link` reads it with
  ``domain.event_urls.parse_event_url`` (strict patterns, exact hosts) and builds any request
  from the captured piece alone: ``GET /v3/events/<digits>/`` on the Eventbrite API, or the
  GDG event page ``/events/details/<slug>/``. A Luma link is never fetched at all: it is looked
  up among the events a Luma feed already brought in. Everything else is "add it by hand".
* **Index first.** An event the index already holds is returned without a request, so a
  re-pasted link costs nothing; only an unknown Eventbrite or GDG event is read.
* **Nothing is committed here.** Writes are flushed; the request's session commits.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Literal

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.event_schemas import EventContactIn, EventContactPatch, ManualEventIn
from config.settings import get_settings
from domain.event_facts import EventFacts, SourceScope
from domain.event_time import as_utc, ist_date
from domain.event_urls import parse_event_url
from services.cache_backend import CacheBackend
from services.company_map.store import find_existing
from services.events import eventbrite, gdg, read
from services.events.refresh import EVENTBRITE_KEY, GDG_KEY
from services.events.sources import scope_for
from services.events.store import upsert_event
from services.source_health import SourceHealthService
from storage.models import Event, EventContact, EventSave

logger = logging.getLogger(__name__)

IMPORT_DAILY_CAP = 10
_IMPORT_TTL_S = 48 * 3600

#: What one user may keep. Soft limits: they are counted before the insert, not enforced by the
#: database, so two simultaneous requests can overshoot by one.
MAX_MANUAL_EVENTS = 200
MAX_PEOPLE_PER_EVENT = 100
MAX_PEOPLE_TOTAL = 2000

Reason = Literal[
    "unrecognised",
    "luma_not_indexed",
    "eventbrite_off",
    "not_readable",
    "daily_cap",
    "not_in_bengaluru",
]

#: A pasted GDG page names no chapter, and GDG chapters exist worldwide: nothing about it
#: vouches for India, so an online event needs an India signal of its own.
_PASTED_GDG = SourceScope(key="gdg", india=False, trusted_tech=True)

#: What a source says about one link and not about itself. Such a failure is the link's
#: fault, so a typo cannot open the breaker the scheduled refresh also sits behind.
_ABOUT_THE_LINK = frozenset({400, 403, 404, 410})

_SAVE_CONSTRAINT = "uq_event_saves_user_event"
_SQLITE_SAVE_KEY = "UNIQUE constraint failed: event_saves.user_id, event_saves.event_id"


class NotStartedError(ValueError):
    """People can be added to an event only once it has started."""


class LimitReachedError(ValueError):
    """The caller has used one of their allowances; the message says which and what to do."""


def _needs(reason: Reason) -> dict[str, Any]:
    return {"needs_details": True, "reason": reason}


# ═════════════════════════════════════════════════════════════════════════════
# save / going
# ═════════════════════════════════════════════════════════════════════════════


async def _find_save(session: AsyncSession, me: uuid.UUID, event_id: uuid.UUID) -> EventSave | None:
    found = await session.execute(
        select(EventSave).where(EventSave.user_id == me, EventSave.event_id == event_id)
    )
    return found.scalar_one_or_none()


def _is_save_conflict(error: IntegrityError) -> bool:
    """The unique ``(user_id, event_id)`` failed, and nothing else (a vanished user or event is not)."""
    constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
    message = str(error.orig)
    return (
        constraint == _SAVE_CONSTRAINT or _SAVE_CONSTRAINT in message or _SQLITE_SAVE_KEY in message
    )


async def _create_save(
    session: AsyncSession, me: uuid.UUID, event_id: uuid.UUID, status: str, note: str | None
) -> EventSave:
    """Insert a save, or return the one a concurrent request inserted first (a savepoint)."""
    save = EventSave(user_id=me, event_id=event_id, status=status, note=note)
    try:
        async with session.begin_nested():
            session.add(save)
            await session.flush()
    except IntegrityError as error:
        if not _is_save_conflict(error):
            raise
        winner = await _find_save(session, me, event_id)
        if winner is None:
            raise
        return winner
    return save


async def _ensure_save(
    session: AsyncSession, me: uuid.UUID, event_id: uuid.UUID, status: str
) -> None:
    """The caller has a save for this event: the one they had, else a new ``status`` one."""
    if await _find_save(session, me, event_id) is None:
        await _create_save(session, me, event_id, status, None)


async def save_event(
    session: AsyncSession,
    user_id: str,
    event_id: uuid.UUID,
    status: Literal["saved", "going"],
    note: str | None,
) -> EventSave | None:
    """Save the event or mark it going: an upsert. ``None`` if the caller cannot see it.

    ``note=None`` leaves an existing note alone (so moving from saved to going keeps it);
    an empty string clears it.
    """
    me = uuid.UUID(user_id)
    if await read.visible_event(session, me, event_id) is None:
        return None
    save = await _find_save(session, me, event_id)
    if save is None:
        return await _create_save(session, me, event_id, status, note or None)
    save.status = status
    if note is not None:
        save.note = note or None
    await session.flush()
    return save


async def unsave_event(session: AsyncSession, user_id: str, event_id: uuid.UUID) -> bool:
    """Remove the caller's save. ``False`` if they had none (which says nothing of the event)."""
    save = await _find_save(session, uuid.UUID(user_id), event_id)
    if save is None:
        return False
    await session.delete(save)
    await session.flush()
    return True


# ═════════════════════════════════════════════════════════════════════════════
# add by link
# ═════════════════════════════════════════════════════════════════════════════


async def _within_import_cap(me: uuid.UUID, now: datetime) -> bool:
    """Count this attempt against the caller's day; fail open if the counter is unavailable."""
    key = f"tr:events:import:{me}:{ist_date(now)}"
    try:
        used = await CacheBackend.incr(key, _IMPORT_TTL_S)
    except Exception as exc:
        logger.debug("events import counter unavailable: %s", type(exc).__name__)
        return True
    return used <= IMPORT_DAILY_CAP


def _describe(failure: BaseException) -> str:
    """The status or the exception's class, never its text (which can quote a URL or a token)."""
    if isinstance(failure, httpx.HTTPStatusError):
        return f"HTTP {failure.response.status_code}"
    return type(failure).__name__


async def _record(
    key: str, jobs: int, started: float, failure: BaseException | None = None
) -> None:
    """Record one read against the source's health. A refused link is not the source failing."""
    if (
        isinstance(failure, httpx.HTTPStatusError)
        and failure.response.status_code in _ABOUT_THE_LINK
    ):
        return
    try:
        await SourceHealthService.record_attempt(
            key,
            job_count=jobs,
            latency_ms=(time.monotonic() - started) * 1000,
            status="success" if failure is None else f"error: {_describe(failure)}",
        )
    except Exception as exc:
        logger.debug("events import could not record source health: %s", type(exc).__name__)


async def _held(session: AsyncSession, source: str, external_id: str) -> Event | None:
    """The public event the index already holds under this identity."""
    found = await session.execute(
        select(Event)
        .where(
            Event.source == source,
            Event.external_id == external_id,
            Event.owner_user_id.is_(None),
        )
        .limit(1)
    )
    return found.scalars().first()


async def _indexed_luma_event(session: AsyncSession, key: str) -> Event | None:
    """An event a Luma feed brought in: by event id, or by the page address its feed named."""
    if key.startswith("evt-"):
        return await _held(session, "luma", key)
    page = f"https://luma.com/{key}"
    found = await session.execute(
        select(Event)
        .where(
            Event.source == "luma",
            Event.owner_user_id.is_(None),
            or_(
                Event.url.in_([page, f"{page}/"]),
                Event.url.startswith(f"{page}?", autoescape=True),
                Event.url.startswith(f"{page}#", autoescape=True),
            ),
        )
        .order_by(Event.starts_at, Event.id)
        .limit(1)
    )
    return found.scalars().first()


async def _store(
    session: AsyncSession, facts: EventFacts, scope: SourceScope, now: datetime
) -> Event | Reason:
    """Store what one page stated, through the same upsert the scheduled refresh uses."""
    result = await upsert_event(session, facts, scope, now=now)
    if result.event is None:
        return "not_in_bengaluru" if result.skipped == "location" else "not_readable"
    result.event.detail_fetched_at = as_utc(now)
    return result.event


async def _import_eventbrite(session: AsyncSession, digits: str, now: datetime) -> Event | Reason:
    held = await _held(session, "eventbrite", digits)
    if held is not None:
        return held
    token = get_settings().eventbrite_token
    if not token:
        return "eventbrite_off"
    if not await SourceHealthService.is_available(EVENTBRITE_KEY):
        return "not_readable"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            payload = await eventbrite.get_event(client, token, digits)
    except (httpx.HTTPError, ValueError) as exc:
        await _record(EVENTBRITE_KEY, 0, started, exc)
        logger.info("Eventbrite event unavailable: %s", _describe(exc))
        return "not_readable"
    # An event that is not publicly listed is not ours to list, as in the scheduled refresh.
    public = payload.get("listed") is not False and payload.get("status") != "draft"
    facts = eventbrite.parse_event(payload) if public else None
    if facts is not None and facts.external_id != digits:
        facts = None
    await _record(EVENTBRITE_KEY, 0 if facts is None else 1, started)
    if facts is None:
        return "not_readable"
    return await _store(session, facts, scope_for("eventbrite"), now)


async def _import_gdg(session: AsyncSession, slug: str, now: datetime) -> Event | Reason:
    held = await _held(session, "gdg", slug)
    if held is not None:
        return held
    if not await SourceHealthService.is_available(GDG_KEY):
        return "not_readable"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            body = await gdg.fetch_page(client, gdg.event_page_url(slug))
    except (httpx.HTTPError, ValueError) as exc:
        await _record(GDG_KEY, 0, started, exc)
        logger.info("GDG event page unavailable: %s", _describe(exc))
        return "not_readable"
    try:
        facts = await asyncio.to_thread(gdg.parse_event_page, body, slug=slug, chapter="")
    except Exception as exc:  # our parser failing is not the page failing
        logger.warning("GDG event page could not be parsed: %s", type(exc).__name__)
        return "not_readable"
    await _record(GDG_KEY, 0 if facts is None else 1, started)
    if facts is None:
        return "not_readable"
    return await _store(session, facts, _PASTED_GDG, now)


async def _primary(session: AsyncSession, event: Event) -> Event:
    """The event the lists show: a cross-source duplicate stands behind its primary."""
    if event.duplicate_of is None:
        return event
    primary = await session.get(Event, event.duplicate_of)
    return primary if primary is not None and primary.owner_user_id is None else event


async def import_link(
    session: AsyncSession, user_id: str, url: str, *, now: datetime
) -> dict[str, Any]:
    """``{"event": card}``, or ``{"needs_details": True, "reason": …}`` (spec section 4.4).

    Every call counts against :data:`IMPORT_DAILY_CAP` a day (IST), recognised or not. A link
    that is not exactly an event page on a known source is ``unrecognised`` and nothing is
    requested. The event found is saved for the caller as ``saved`` (an existing save is left
    as it is), so an imported event that the relevance rules left unlisted is still theirs to find.
    """
    me = uuid.UUID(user_id)
    if not await _within_import_cap(me, now):
        return _needs("daily_cap")
    pasted = parse_event_url(url)
    if pasted is None:
        return _needs("unrecognised")

    found: Event | Reason
    if pasted.source == "luma":
        found = await _indexed_luma_event(session, pasted.key) or "luma_not_indexed"
    elif pasted.source == "eventbrite":
        found = await _import_eventbrite(session, pasted.key, now)
    else:
        found = await _import_gdg(session, pasted.key, now)
    if not isinstance(found, Event):
        return _needs(found)

    event = await _primary(session, found)
    await _ensure_save(session, me, event.id, "saved")
    return {"event": await read.event_card(session, me, event)}


# ═════════════════════════════════════════════════════════════════════════════
# add by hand
# ═════════════════════════════════════════════════════════════════════════════


async def create_manual(session: AsyncSession, user_id: str, payload: ManualEventIn) -> Event:
    """A private event the caller added by hand, saved as ``going``.

    Raises :class:`LimitReachedError` once they hold :data:`MAX_MANUAL_EVENTS` of them.
    """
    me = uuid.UUID(user_id)
    held = await session.scalar(
        select(func.count()).select_from(Event).where(Event.owner_user_id == me)
    )
    if (held or 0) >= MAX_MANUAL_EVENTS:
        raise LimitReachedError(
            f"You've added {MAX_MANUAL_EVENTS:,} events. Remove one to add another."
        )
    event_id = uuid.uuid4()
    event = Event(
        id=event_id,
        source="user",
        external_id=str(event_id),
        url=payload.url,
        title=payload.title,
        starts_at=as_utc(payload.starts_at),
        ends_at=as_utc(payload.ends_at) if payload.ends_at is not None else None,
        venue_name=payload.venue_name,
        is_online=payload.is_online,
        geo_precision="none",
        topics=[],
        listed=True,
        reasons=["added by you"],
        owner_user_id=me,
    )
    session.add(event)
    await session.flush()
    await _create_save(session, me, event_id, "going", None)
    return event


async def delete_manual(session: AsyncSession, user_id: str, event_id: uuid.UUID) -> bool:
    """Remove an event the caller added by hand, with their save and people at it.

    The save and the people go with the row (``ON DELETE CASCADE``). ``False`` for any other
    event, a source's or another user's, and for an id that does not exist: the three are not
    told apart.
    """
    found = await session.execute(
        select(Event).where(
            Event.id == event_id, Event.source == "user", Event.owner_user_id == uuid.UUID(user_id)
        )
    )
    event = found.scalar_one_or_none()
    if event is None:
        return False
    await session.delete(event)
    await session.flush()
    return True


# ═════════════════════════════════════════════════════════════════════════════
# people met
# ═════════════════════════════════════════════════════════════════════════════


async def _tech_company_id(session: AsyncSession, name: str | None) -> uuid.UUID | None:
    """The tech company on the map whose name is exactly ``name``, else ``None``. Creates nothing."""
    if not name:
        return None
    company = await find_existing(session, domain=None, wikidata_id=None, name=name)
    return company.id if company is not None and company.is_tech is True else None


async def _own_contact(
    session: AsyncSession, me: uuid.UUID, person_id: uuid.UUID
) -> EventContact | None:
    found = await session.execute(
        select(EventContact).where(EventContact.id == person_id, EventContact.user_id == me)
    )
    return found.scalar_one_or_none()


async def list_contacts(
    session: AsyncSession, user_id: str, event_id: uuid.UUID
) -> list[EventContact] | None:
    """The caller's people at the event, oldest first; ``None`` if they cannot see the event."""
    me = uuid.UUID(user_id)
    if await read.visible_event(session, me, event_id) is None:
        return None
    found = await session.execute(
        select(EventContact)
        .where(EventContact.user_id == me, EventContact.event_id == event_id)
        .order_by(EventContact.created_at, EventContact.id)
    )
    return list(found.scalars())


async def _check_room_for_a_person(
    session: AsyncSession, me: uuid.UUID, event_id: uuid.UUID
) -> None:
    """Raise :class:`LimitReachedError` if the caller has no room for one more person."""
    here = await session.scalar(
        select(func.count())
        .select_from(EventContact)
        .where(EventContact.user_id == me, EventContact.event_id == event_id)
    )
    if (here or 0) >= MAX_PEOPLE_PER_EVENT:
        raise LimitReachedError(
            f"You've added {MAX_PEOPLE_PER_EVENT:,} people to this event. "
            "Remove one to add another."
        )
    overall = await session.scalar(
        select(func.count()).select_from(EventContact).where(EventContact.user_id == me)
    )
    if (overall or 0) >= MAX_PEOPLE_TOTAL:
        raise LimitReachedError(
            f"You've added {MAX_PEOPLE_TOTAL:,} people in total. Remove one to add another."
        )


async def add_contact(
    session: AsyncSession,
    user_id: str,
    event_id: uuid.UUID,
    payload: EventContactIn,
    *,
    now: datetime,
) -> EventContact | None:
    """Note a person met at an event. ``None`` if the caller cannot see the event.

    Raises :class:`NotStartedError` (a ``ValueError``) before the event starts: nobody has been
    met yet. Raises :class:`LimitReachedError` past :data:`MAX_PEOPLE_PER_EVENT` at this event
    or :data:`MAX_PEOPLE_TOTAL` overall. Marks the caller as going if they had no save.
    """
    me = uuid.UUID(user_id)
    event = await read.visible_event(session, me, event_id)
    if event is None:
        return None
    if as_utc(event.starts_at) > as_utc(now):
        raise NotStartedError("people can be added once the event has started")
    await _check_room_for_a_person(session, me, event_id)
    await _ensure_save(session, me, event_id, "going")
    stamp = as_utc(now)
    contact = EventContact(
        id=uuid.uuid4(),
        user_id=me,
        event_id=event_id,
        name=payload.name,
        title=payload.title,
        company_name=payload.company_name,
        company_id=await _tech_company_id(session, payload.company_name),
        linkedin_url=payload.linkedin_url,
        x_url=payload.x_url,
        github_url=payload.github_url,
        email=payload.email,
        note=payload.note,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(contact)
    await session.flush()
    return contact


_PATCHABLE = (
    "name",
    "title",
    "company_name",
    "linkedin_url",
    "x_url",
    "github_url",
    "email",
    "note",
)


async def update_contact(
    session: AsyncSession,
    user_id: str,
    person_id: uuid.UUID,
    payload: EventContactPatch,
    *,
    now: datetime,
) -> EventContact | None:
    """Change the fields the caller sent on one of their own people; ``None`` if not theirs."""
    contact = await _own_contact(session, uuid.UUID(user_id), person_id)
    if contact is None:
        return None
    sent = payload.model_fields_set
    for field in _PATCHABLE:
        if field in sent:
            setattr(contact, field, getattr(payload, field))
    if "company_name" in sent:
        contact.company_id = await _tech_company_id(session, contact.company_name)
    contact.updated_at = as_utc(now)
    await session.flush()
    return contact


async def delete_contact(session: AsyncSession, user_id: str, person_id: uuid.UUID) -> bool:
    """Remove one of the caller's own people. ``False`` if it is not theirs or not there."""
    contact = await _own_contact(session, uuid.UUID(user_id), person_id)
    if contact is None:
        return False
    await session.delete(contact)
    await session.flush()
    return True
