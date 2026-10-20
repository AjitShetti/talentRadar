"""
services/events/met.py
~~~~~~~~~~~~~~~~~~~~~~
People met at events, published for Outreach: the read-only contract of spec section 8.

Outreach is built in parallel on its own branch and its tables are not known here, so Events
*publishes* this contract and imports nothing from Outreach. Outreach pulls
:func:`people_met` (or ``GET /api/v1/events/people-met``) and decides what to copy. Its touch
for one of these is the channel "met at an event", with ``occurred_at = met_on`` and
:func:`context_line`. Re-reading is safe: ``source_ref`` is the idempotency key.

Private by construction: every row is the caller's own (``user_id`` filter), and nothing here
enriches a person, looks one up or reaches outside the database. The contact fields are what
the user typed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.event_time import as_utc, ist_date
from services.events.read import readable
from storage.models import Event, EventContact

MAX_LIMIT = 500

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class MetAtEvent:
    """One person the user met at one event."""

    source_ref: str  # "event_contact:<uuid>", Outreach's idempotency key
    name: str
    title: str | None
    company_name: str | None
    company_id: str | None  # a company on the map, when the typed name matched one
    linkedin_url: str | None
    x_url: str | None
    github_url: str | None
    email: str | None  # only what the person gave the user
    note: str | None
    event_id: str
    event_title: str
    event_url: str  # empty for a manual event with no link
    met_on: date  # the IST date of the event's start
    created_at: datetime


async def people_met(
    session: AsyncSession,
    user_id: str,
    *,
    since: datetime | None = None,
    limit: int = 100,
) -> list[MetAtEvent]:
    """The caller's people, oldest first, so a reader can page forward by ``since``.

    ``since`` keeps the rows added at or after that instant (inclusive: a repeat is safe, a
    gap is not). ``limit`` is held between 1 and :data:`MAX_LIMIT`.
    """
    me = uuid.UUID(user_id)
    statement = (
        select(EventContact, Event)
        .join(Event, Event.id == EventContact.event_id)
        .where(EventContact.user_id == me, readable(me))
        .order_by(EventContact.created_at, EventContact.id)
        .limit(max(1, min(limit, MAX_LIMIT)))
    )
    if since is not None:
        statement = statement.where(EventContact.created_at >= as_utc(since))
    rows = await session.execute(statement)
    return [
        MetAtEvent(
            source_ref=f"event_contact:{contact.id}",
            name=contact.name,
            title=contact.title,
            company_name=contact.company_name,
            company_id=str(contact.company_id) if contact.company_id else None,
            linkedin_url=contact.linkedin_url,
            x_url=contact.x_url,
            github_url=contact.github_url,
            email=contact.email,
            note=contact.note,
            event_id=str(event.id),
            event_title=event.title,
            event_url=event.url or "",
            met_on=ist_date(event.starts_at),
            created_at=as_utc(contact.created_at),
        )
        for contact, event in rows
    ]


def context_line(met: MetAtEvent) -> str:
    """``Met at Swift Bengaluru × Okta on 16 Oct``: the day without a leading zero."""
    return f"Met at {met.event_title} on {met.met_on.day} {_MONTHS[met.met_on.month - 1]}"
