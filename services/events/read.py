"""
services/events/read.py
~~~~~~~~~~~~~~~~~~~~~~~
What the Events page reads: the list, one event's detail and its ``.ics``.

Reads only. Nothing here calls out to a source (no Luma, GDG or Eventbrite request, no
LinkedIn lookup); the "last read" strip comes from the health counters the refresh steps
already keep, and a person's LinkedIn link is a search URL built from what the user typed.

Who sees what
-------------
* **A public event** is seen by everyone, in lists only when it is *listed* and is not a
  cross-source duplicate (the primary carries the listing and an "also on" link). The
  Saved and Going views are the exception: they list every event the caller saved, listed or
  not, because the caller chose it (a pasted link need not be a listed event).
* **A private event** (``owner_user_id`` set: one a user added by hand) is seen by its
  owner alone, whether or not it is "listed", and every other user gets ``None`` for its
  id exactly as for an id that does not exist.
* **People met and saves** are filtered by ``user_id`` in every query.

Time
----
A window is half-open, ``[lo, hi)``, and an event is in it when it *overlaps* it and has
not ended: a hackathon that began yesterday is still listed today. ``from``/``to`` are IST
dates and ``to`` is inclusive. With no ``to`` the window is :data:`DEFAULT_DAYS` days
counting the first, so today and the 29 days after it. An unstated end is start + 2 h
(:func:`domain.event_time.effective_end`); the same rule is written in SQL by
:func:`_ends_after`, so a list never shows an event the card would call over.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import String, and_, case, cast, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from config.settings import get_settings
from domain.event_time import (
    DEFAULT_DURATION,
    IST,
    Window,
    as_utc,
    effective_end,
    ist_date,
    window,
)
from domain.ical import build_ics
from domain.linkedin_links import person_search_link
from services.source_health import SourceHealthService
from storage.models import Company, Event, EventCompany, EventContact, EventSave

logger = logging.getLogger(__name__)

MAX_LIMIT = 100
MAX_OFFSET = 10_000  # deep paging is not a feature; a huge OFFSET is a 500 on Postgres
DEFAULT_DAYS = 30
SNIPPET_SHOWN = 200
SOURCES: tuple[Literal["luma", "gdg", "eventbrite"], ...] = ("luma", "gdg", "eventbrite")

_Mode = Literal["in_person", "online", "any"]
_Saved = Literal["all", "saved", "going"]

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_FILENAME_MAX = 60


@dataclass(frozen=True)
class EventQuery:
    """One request to the list. ``when`` overrides ``start``/``end``."""

    when: Window | None = None
    start: date | None = None
    end: date | None = None
    mode: _Mode = "in_person"
    topic: str | None = None
    format: str | None = None
    hide_paid: bool = False
    saved: _Saved = "all"
    company_id: str | None = None
    q: str | None = None
    limit: int = 50
    offset: int = 0


# ═════════════════════════════════════════════════════════════════════════════
# the rules, as SQL
# ═════════════════════════════════════════════════════════════════════════════


def _midnight(day: date) -> datetime:
    """The start of an IST day, as an aware UTC instant."""
    return datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)


def _bounds(query: EventQuery, now: datetime) -> tuple[datetime, datetime]:
    """The ``[lo, hi)`` the list covers, as aware UTC instants."""
    if query.when is not None:
        return window(query.when, now)
    first = query.start or ist_date(now)
    last = query.end or first + timedelta(days=DEFAULT_DAYS - 1)
    return _midnight(first), _midnight(last + timedelta(days=1))


def _ends_after(moment: datetime) -> ColumnElement[bool]:
    """The event is still on at ``moment``: its stated end, else its start + 2 h.

    An end that is not after the start is a data error and counts as no end, the same
    as :func:`domain.event_time.effective_end`.
    """
    instant = as_utc(moment)
    has_end = and_(Event.ends_at.is_not(None), Event.ends_at > Event.starts_at)
    return or_(
        and_(has_end, Event.ends_at > instant),
        and_(not_(has_end), Event.starts_at > instant - DEFAULT_DURATION),
    )


def _in_window(lo: datetime, hi: datetime, now: datetime) -> ColumnElement[bool]:
    """Overlaps ``[lo, hi)`` and has not ended."""
    return and_(Event.starts_at < hi, _ends_after(max(as_utc(now), lo)))


def _readable(me: uuid.UUID) -> ColumnElement[bool]:
    """Public events, listed or not, plus the caller's own private ones; never another's."""
    return or_(Event.owner_user_id.is_(None), Event.owner_user_id == me)


def _visible(me: uuid.UUID) -> ColumnElement[bool]:
    """Public, listed, primary events, plus the caller's own private ones."""
    public = and_(
        Event.owner_user_id.is_(None),
        Event.listed.is_(True),
        Event.duplicate_of.is_(None),
    )
    return or_(public, Event.owner_user_id == me)


def topic_clause(dialect: str, topic: str) -> ColumnElement[bool]:
    """The event carries ``topic``: array containment on Postgres, JSON text on SQLite."""
    if dialect == "postgresql":
        return Event.topics.contains([topic])
    return cast(Event.topics, String).contains(f'"{topic}"', autoescape=True)


def _search_text(raw: str | None) -> str:
    return " ".join(_CONTROL.sub(" ", raw or "").split())


def _filters(
    query: EventQuery, me: uuid.UUID, now: datetime, dialect: str
) -> list[ColumnElement[bool]]:
    lo, hi = _bounds(query, now)
    # What the caller saved is theirs to see whether or not the relevance verdict listed it.
    where = [_readable(me) if query.saved != "all" else _visible(me), _in_window(lo, hi, now)]
    if query.mode == "in_person":
        where.append(Event.is_online.is_(False))
    elif query.mode == "online":
        where.append(Event.is_online.is_(True))
    if query.topic:
        where.append(topic_clause(dialect, query.topic))
    if query.format:
        where.append(Event.format == query.format)
    if query.hide_paid:
        where.append(or_(Event.is_free.is_(None), Event.is_free.is_(True)))
    if query.saved != "all":
        kept = ("going",) if query.saved == "going" else ("saved", "going")
        where.append(
            Event.id.in_(
                select(EventSave.event_id).where(
                    EventSave.user_id == me, EventSave.status.in_(kept)
                )
            )
        )
    if query.company_id:
        where.append(
            Event.id.in_(
                select(EventCompany.event_id).where(
                    EventCompany.company_id == uuid.UUID(query.company_id)
                )
            )
        )
    text = _search_text(query.q)
    if text:
        where.append(
            or_(
                Event.title.icontains(text, autoescape=True),
                Event.organiser_name.icontains(text, autoescape=True),
            )
        )
    return where


# ═════════════════════════════════════════════════════════════════════════════
# shapes
# ═════════════════════════════════════════════════════════════════════════════


def _utc(moment: datetime | None) -> datetime | None:
    return as_utc(moment) if moment is not None else None


def _card(event: Event, companies: list[dict[str, Any]], my_status: str | None) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "title": event.title,
        "starts_at": as_utc(event.starts_at),
        "ends_at": _utc(event.ends_at),
        "is_online": bool(event.is_online),
        "venue_name": event.venue_name,
        "area": event.area,
        "lat": event.lat,
        "lng": event.lng,
        "geo_precision": event.geo_precision,
        "is_free": event.is_free,
        "organiser_name": event.organiser_name,
        "format": event.format,
        "topics": list(event.topics or []),
        "source": event.source,
        "url": event.url,
        "companies": [{"id": c["id"], "name": c["name"]} for c in companies],
        "my_status": my_status,
        "is_cancelled": bool(event.is_cancelled),
    }


def contact_out(contact: EventContact) -> dict[str, Any]:
    """One person the caller met. A search link stands in when no profile was typed."""
    return {
        "id": str(contact.id),
        "event_id": str(contact.event_id),
        "name": contact.name,
        "title": contact.title,
        "company_name": contact.company_name,
        "company_id": str(contact.company_id) if contact.company_id else None,
        "linkedin_url": contact.linkedin_url,
        "x_url": contact.x_url,
        "github_url": contact.github_url,
        "email": contact.email,
        "note": contact.note,
        "created_at": _utc(contact.created_at),
        "linkedin_search_url": (
            None
            if contact.linkedin_url
            else person_search_link(contact.name, contact.company_name or "")
        ),
    }


def _iso_utc(epoch: float | None) -> str | None:
    """``SourceHealth.last_success_at`` (epoch seconds) as ``2026-10-15T00:01:02Z``."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError, TypeError):
        return None


# ═════════════════════════════════════════════════════════════════════════════
# batched lookups for a page of events
# ═════════════════════════════════════════════════════════════════════════════


async def _companies(
    session: AsyncSession, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Linked companies per event, the organiser first."""
    if not event_ids:
        return {}
    rows = await session.execute(
        select(EventCompany.event_id, Company.id, Company.name, EventCompany.role)
        .join(Company, Company.id == EventCompany.company_id)
        .where(EventCompany.event_id.in_(event_ids))
        .order_by(Company.name, Company.id)
    )
    found: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for event_id, company_id, name, role in rows:
        found.setdefault(event_id, []).append({"id": str(company_id), "name": name, "role": role})
    for linked in found.values():
        linked.sort(key=lambda c: c["role"] != "organiser")
    return found


async def _statuses(
    session: AsyncSession, me: uuid.UUID, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not event_ids:
        return {}
    rows = await session.execute(
        select(EventSave.event_id, EventSave.status).where(
            EventSave.user_id == me, EventSave.event_id.in_(event_ids)
        )
    )
    return {event_id: status for event_id, status in rows}


async def _counts(session: AsyncSession, me: uuid.UUID, now: datetime) -> dict[str, int]:
    """The ledger line: upcoming, in the next 7 days, and the caller's going.

    Independent of the list's filters and of its mode (an online event counts), and
    leaving out what is over or cancelled.
    """
    _, week_end = window("week", now)
    not_over = and_(Event.is_cancelled.is_(False), _ends_after(now))
    upcoming, this_week = (
        await session.execute(
            select(
                func.count(Event.id),
                func.count(case((Event.starts_at < week_end, Event.id))),
            ).where(_visible(me), not_over)
        )
    ).one()
    # The Going view lists everything the caller is going to, so its count does too.
    going = await session.scalar(
        select(func.count(Event.id))
        .join(EventSave, EventSave.event_id == Event.id)
        .where(_readable(me), not_over, EventSave.user_id == me, EventSave.status == "going")
    )
    return {"upcoming": int(upcoming), "this_week": int(this_week), "going": int(going or 0)}


async def _sources() -> list[dict[str, Any]]:
    """When each source was last read. A cache that is down reads as "unknown", not an error."""
    has_token = bool(get_settings().eventbrite_token)
    found: list[dict[str, Any]] = []
    for source in SOURCES:
        status = "unknown"
        last_read_at: str | None = None
        try:
            health = await SourceHealthService.get(f"events:{source}")
            status = health.status
            last_read_at = _iso_utc(health.last_success_at)
        except Exception:
            logger.warning("events: source health for %s unavailable", source, exc_info=True)
        if source == "eventbrite" and not has_token:
            status = "off"
        found.append({"source": source, "last_read_at": last_read_at, "status": status})
    return found


# ═════════════════════════════════════════════════════════════════════════════
# the list
# ═════════════════════════════════════════════════════════════════════════════


async def list_events(
    session: AsyncSession, user_id: str, query: EventQuery, *, now: datetime
) -> dict[str, Any]:
    """``{total, events, counts, sources}`` for the caller.

    ``events`` is one page, soonest first. ``total`` counts every match, not the page.
    """
    me = uuid.UUID(user_id)
    instant = as_utc(now)
    # The cache first: the session takes its connection at its first query, and that
    # connection must not wait on Redis round trips.
    sources = await _sources()
    where = _filters(query, me, instant, session.get_bind().dialect.name)
    limit = max(1, min(query.limit, MAX_LIMIT))
    offset = max(0, min(query.offset, MAX_OFFSET))

    total = int(await session.scalar(select(func.count(Event.id)).where(*where)) or 0)
    events = (
        (
            await session.execute(
                select(Event)
                .where(*where)
                .order_by(Event.starts_at, Event.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    ids = [event.id for event in events]
    companies = await _companies(session, ids)
    statuses = await _statuses(session, me, ids)

    return {
        "total": total,
        "events": [
            _card(event, companies.get(event.id, []), statuses.get(event.id)) for event in events
        ],
        "counts": await _counts(session, me, instant),
        "sources": sources,
    }


# ═════════════════════════════════════════════════════════════════════════════
# one event
# ═════════════════════════════════════════════════════════════════════════════


async def _visible_event(session: AsyncSession, me: uuid.UUID, event_id: uuid.UUID) -> Event | None:
    """The event, unless it does not exist or is another user's private event."""
    result = await session.execute(select(Event).where(Event.id == event_id, _readable(me)))
    return result.scalar_one_or_none()


async def event_detail(
    session: AsyncSession, user_id: str, event_id: uuid.UUID
) -> dict[str, Any] | None:
    """The card plus snippet, reasons, hosts, other sources, companies, the caller's save and
    the caller's people. ``None`` for an unknown event or another user's private one."""
    me = uuid.UUID(user_id)
    event = await _visible_event(session, me, event_id)
    if event is None:
        return None

    linked = (await _companies(session, [event.id])).get(event.id, [])
    save = (
        await session.execute(
            select(EventSave).where(EventSave.user_id == me, EventSave.event_id == event.id)
        )
    ).scalar_one_or_none()
    others = await session.execute(
        select(Event.source, Event.url)
        .where(
            Event.duplicate_of == event.id,
            Event.owner_user_id.is_(None),
            Event.url.is_not(None),
        )
        .order_by(Event.source, Event.url)
    )
    contacts = (
        (
            await session.execute(
                select(EventContact)
                .where(EventContact.user_id == me, EventContact.event_id == event.id)
                .order_by(EventContact.created_at, EventContact.id)
            )
        )
        .scalars()
        .all()
    )

    detail = _card(event, linked, save.status if save else None)
    detail["companies"] = linked
    detail.update(
        {
            "snippet": event.snippet[:SNIPPET_SHOWN] if event.snippet else None,
            "reasons": list(event.reasons or []),
            "hosts": list(event.hosts or []),
            "also_on": [{"source": source, "url": url} for source, url in others],
            "my": {"status": save.status, "note": save.note} if save else None,
            "people": [contact_out(contact) for contact in contacts],
        }
    )
    return detail


# ═════════════════════════════════════════════════════════════════════════════
# .ics
# ═════════════════════════════════════════════════════════════════════════════


def _filename(title: str) -> str:
    """``rust-night.ics``: ASCII, so the header needs no encoding. ``event`` if nothing is left."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = _NOT_SLUG.sub("-", ascii_title).strip("-")[:_FILENAME_MAX].rstrip("-")
    return f"{slug or 'event'}.ics"


def _location(event: Event) -> str | None:
    venue = (event.venue_name or "").strip()
    address = (event.address or "").strip()
    if venue and address and venue.casefold() not in address.casefold():
        return f"{venue}, {address}"
    return address or venue or ("Online" if event.is_online else None)


async def event_ics(
    session: AsyncSession, user_id: str, event_id: uuid.UUID, *, now: datetime
) -> tuple[str, str] | None:
    """``(filename, body)`` of a one-event calendar file, or ``None`` as for :func:`event_detail`."""
    event = await _visible_event(session, uuid.UUID(user_id), event_id)
    if event is None:
        return None
    body = build_ics(
        uid=f"{event.id}@talentradar",
        title=event.title,
        starts_at=as_utc(event.starts_at),
        ends_at=effective_end(event.starts_at, event.ends_at),
        location=_location(event),
        url=event.url or "",
        description=event.snippet,
        cancelled=bool(event.is_cancelled),
        stamp=as_utc(now),
    )
    return _filename(event.title), body
