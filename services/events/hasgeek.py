"""
services/events/hasgeek.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
A Hasgeek event, read from the link a person pasted.

Hasgeek's terms forbid crawling ("spider, crawl, or scrape"), and it publishes no
list of upcoming events, so it is **never** read on a schedule. What it does publish
for every project is an iCal schedule, ``https://hasgeek.com/<org>/<project>/schedule/ical``,
built for calendar apps to subscribe to (it declares ``REFRESH-INTERVAL: PT12H``). That
feed is the one address requested, built from the key ``domain.event_urls`` lifted out
of the pasted link, never the link itself.

A schedule is one VEVENT per session. The project is one event: the calendar's own
``NAME`` (else the first session's summary), the earliest start and the latest end.
The first session's ``LOCATION`` reads ``"Bengaluru - https://hasgeek.com/…"``; the text
before the link is the place. A project that has published no schedule has no session,
and is not an event we can read.
"""

from __future__ import annotations

import re

import httpx

from domain.event_facts import EventFacts, flatten
from domain.event_urls import HASGEEK_KEY_PATTERN
from domain.ical import IcalEvent, calendar_property, parse_ical
from services.events.http import get_bounded

HASGEEK_KEY = "events:hasgeek"
BASE = "https://hasgeek.com"

TITLE_MAX = 300
SNIPPET_MAX = 600
LOCALITY_MAX = 120
ORGANISER_MAX = 200

# "Bengaluru - https://hasgeek.com/org/project/": the place, then a link back.
_LINK_TAIL = re.compile(r"\s*-?\s*https?://\S*\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_EMPHASIS = re.compile(r"[*_]{1,3}(?=\S)|(?<=\S)[*_]{1,3}")
_ONLINE = frozenset({"online", "virtual", "zoom", "online event"})


def schedule_url(key: str) -> str:
    """The schedule address of ``"<org>/<project>"``; ``ValueError`` for anything else."""
    if HASGEEK_KEY_PATTERN.fullmatch(key) is None:
        raise ValueError("not a Hasgeek project key")
    return f"{BASE}/{key}/schedule/ical"


def project_url(key: str) -> str:
    """The project's public page, which is where a person registers."""
    schedule_url(key)  # validates
    return f"{BASE}/{key}/"


async def fetch_schedule(client: httpx.AsyncClient, key: str) -> bytes:
    """The raw schedule of one project: bounded, no redirect followed.

    Raises ``ValueError`` for a malformed key (before any request) or an oversized
    body, and ``httpx.HTTPError`` on a transport error or non-2xx.
    """
    return await get_bounded(
        client,
        schedule_url(key),
        headers={"Accept": "text/calendar"},
        follow_redirects=False,
    )


def _place(location: str | None) -> str | None:
    if not location:
        return None
    return flatten(_LINK_TAIL.sub("", location), LOCALITY_MAX)


def _snippet(description: str | None) -> str | None:
    if not description:
        return None
    return flatten(_EMPHASIS.sub("", _HEADING.sub("", description)), SNIPPET_MAX)


def _first(sessions: list[IcalEvent], attribute: str) -> str | None:
    for session in sessions:
        value = getattr(session, attribute)
        if value:
            return str(value)
    return None


def parse_schedule(data: bytes, key: str) -> EventFacts | None:
    """The project a schedule describes, as one :class:`EventFacts`; ``None`` without sessions."""
    sessions = sorted(parse_ical(data), key=lambda session: session.dtstart)
    if not sessions:
        return None
    title = (
        flatten(calendar_property(data, "NAME"), TITLE_MAX)
        or flatten(calendar_property(data, "X-WR-CALNAME"), TITLE_MAX)
        or flatten(sessions[0].summary, TITLE_MAX)
    )
    if title is None:
        return None
    ends = [session.dtend for session in sessions if session.dtend is not None]
    place = _place(_first(sessions, "location"))
    geo = next((session.geo for session in sessions if session.geo is not None), None)
    organiser = _first(sessions, "organizer_cn") or key.split("/", 1)[0]
    online = place is not None and place.lower() in _ONLINE
    return EventFacts(
        source="hasgeek",
        external_id=key,
        url=project_url(key),
        title=title,
        starts_at=sessions[0].dtstart,
        ends_at=max(ends) if ends else None,
        snippet=_snippet(_first(sessions, "description")),
        locality=None if online else place,
        lat=geo[0] if geo else None,
        lng=geo[1] if geo else None,
        is_online=online,
        organiser_name=flatten(organiser, ORGANISER_MAX),
        is_cancelled=all(session.status == "CANCELLED" for session in sessions),
    )
