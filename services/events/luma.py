"""
services/events/luma.py
~~~~~~~~~~~~~~~~~~~~~~~
Luma, read only through its published iCal subscriptions.

Luma documents an iCal feed per Discover city and per calendar as a supported
interface for machines. This module builds those two addresses
(``https://api.lu.ma/ics/get?entity=discover|calendar&id=<id>``) and nothing
else, reads them, and turns each ``VEVENT`` into :class:`EventFacts`. It never
requests a ``luma.com`` page, Luma's internal API or an event link.

:func:`parse_feed` is pure and total: bad input is an empty list. It is
synchronous, so an async caller runs it off the event loop
(``await asyncio.to_thread(parse_feed, data)``). :func:`fetch_feed` is the one
network call.

What a feed states, and what becomes of it:

* ``UID`` ``evt-…@events.lu.ma`` is the identity, minus the suffix.
* The event page is the first ``https://luma.com/…`` link in the description
  (Luma opens every description with one), else a ``luma.com`` ``LOCATION``.
  An event with neither has nowhere to send a reader, so it is dropped, as is
  one with no title.
* The description is a header line, an ``Address:`` block, the host's own text
  and a closing ``Hosted by A, B & C``. The snippet is the host's text alone.
  Only a *closing* ``Hosted by`` line names hosts; the same words inside the
  host's text are theirs.
* Luma withholds the street address ("Check event page for more details", or an
  ``Address:`` block of an area and "The exact location will be shared once the
  invite is accepted"); ``GEO`` is still given, so ``address_withheld`` tells a
  reader the pin may only be near the venue. The second sentence is kept, after
  the host's own words, in the snippet: it tells a reader why there is no street
  address, and it is the text ``services.events.store.refine_facts`` looks for.
* ``STATUS`` is ``TENTATIVE`` on every live event; only ``CANCELLED`` counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

import httpx

from domain.event_facts import EventFacts, flatten
from domain.ical import IcalEvent, parse_ical
from services.events.http import get_bounded

FEED_BASE = "https://api.lu.ma/ics/get"
UID_SUFFIX = "@events.lu.ma"

TITLE_MAX = 300
SNIPPET_MAX = 600
VENUE_MAX = 200
ADDRESS_MAX = 300
ORGANISER_MAX = 200
HOST_MAX = 200
NOTICE_MAX = 160
HOSTS_MAX = 10
EXTERNAL_ID_MAX = 160
URL_MAX = 1024

FeedKind = Literal["discover", "calendar"]

_FEED_ID = re.compile(r"(?:discplace|cal)-[A-Za-z0-9]{6,40}")
_ID_CHARS = re.compile(r"[^\s\x00-\x1f\x7f]+")

# An absolute luma.com link that starts at a word edge ("(", a quote or white
# space before it, never "?next=" or a longer host in front) and uses only
# characters a link may carry: no backslash, quote, angle bracket or space.
_LUMA_URL = re.compile(r"(?<![^\s(<\[\"'])https://luma\.com/[A-Za-z0-9][A-Za-z0-9._~/?=&%#+-]*")
_URL_TRAILING = ".,;:!?)"
_ANY_URL = re.compile(r"https?://", re.IGNORECASE)

# A meeting-room link: the domain on a label boundary (not "notzoom.us", not
# "zoom.us.example"), single pass so a hostile description cannot make it slow.
_MEETING_LINK = re.compile(
    r"(?<![A-Za-z0-9-])(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com)(?![A-Za-z0-9-]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)

_UP_TO_DATE = "get up-to-date information at"
_ADDRESS_HEADER = "address:"
_WITHHELD = "check event page for more details"
# A line of the address block that talks about the exact location ("The exact location
# will be shared once the invite is accepted"). Matched on its subject, not its verbs:
# a host's typo in "shared" must not make the address look public.
_LOCATION_NOTICE = re.compile(r"\bexact\s+(?:location|address|venue)\b", re.IGNORECASE)
_HOSTED_BY = "Hosted by "
_HOST_SEPARATOR = re.compile(r"\s*,\s*|\s+&\s+")
_OTHERS_COUNT = re.compile(r"\d+\s+others?", re.IGNORECASE)


# ── addresses ────────────────────────────────────────────────────────────────


def _kind(value: str | None) -> FeedKind | None:
    if value == "discover":
        return "discover"
    if value == "calendar":
        return "calendar"
    return None


def feed_url(kind: FeedKind, ident: str) -> str:
    """The iCal subscription address of a Discover city or a calendar.

    Raises ``ValueError`` for an unknown ``kind`` or an ``ident`` that is not
    ``discplace-…`` / ``cal-…`` followed by 6 to 40 letters and digits, so
    nothing but a vetted id is ever put into the query.
    """
    if _kind(kind) is None:
        raise ValueError(f"unknown Luma feed kind: {str(kind)[:40]!r}")
    if not isinstance(ident, str) or _FEED_ID.fullmatch(ident) is None:
        raise ValueError(f"not a Luma feed id: {str(ident)[:60]!r}")
    return f"{FEED_BASE}?entity={kind}&id={ident}"


# ── parsing ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Description:
    """A Luma description taken apart."""

    text: str  # the host's own words: header, address block and closing line removed
    venue: str | None
    hosts: tuple[str, ...]
    withheld: bool
    notice: str | None  # the address block's "exact location will be shared …" sentence


def _hosts(listed: str) -> tuple[str, ...]:
    """``A, B & C`` or ``A & 3 others`` as names; the count of others is not a name."""
    names: list[str] = []
    for part in _HOST_SEPARATOR.split(listed):
        name = flatten(part, HOST_MAX)
        if name and _OTHERS_COUNT.fullmatch(name) is None and name not in names:
            names.append(name)
    return tuple(names[:HOSTS_MAX])


def _split_description(description: str) -> _Description:
    lines = description.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()

    hosts: tuple[str, ...] = ()
    if lines and lines[-1].startswith(_HOSTED_BY):
        hosts = _hosts(lines.pop()[len(_HOSTED_BY) :])

    index = 0

    def skip_blank() -> None:
        nonlocal index
        while index < len(lines) and not lines[index].strip():
            index += 1

    skip_blank()
    if index < len(lines) and lines[index].strip().lower().startswith(_UP_TO_DATE):
        index += 1
    skip_blank()
    block: list[str] = []
    if index < len(lines) and lines[index].strip().lower().startswith(_ADDRESS_HEADER):
        first = lines[index].strip()[len(_ADDRESS_HEADER) :].strip()
        if first:
            block.append(first)
        index += 1
        while index < len(lines) and lines[index].strip():
            block.append(lines[index].strip())
            index += 1

    # A place is named only when the block gives a name and then where it is.
    withheld_block = bool(block) and block[0].lower().startswith(_WITHHELD)
    venue = flatten(block[0], VENUE_MAX) if len(block) >= 2 and not withheld_block else None
    notice = flatten(
        " ".join(line for line in block[1:] if _LOCATION_NOTICE.search(line)), NOTICE_MAX
    )
    return _Description(
        text="\n".join(lines[index:]),
        venue=venue,
        hosts=hosts,
        withheld=_WITHHELD in description.lower() or notice is not None,
        notice=notice,
    )


def _snippet(parts: _Description) -> str | None:
    """The host's words, then the address notice if the block had one; at most ``SNIPPET_MAX``."""
    if parts.notice is None:
        return flatten(parts.text, SNIPPET_MAX)
    body = flatten(parts.text, SNIPPET_MAX - len(parts.notice) - 1)
    return f"{body} {parts.notice}" if body else parts.notice


def _event_url(description: str, location: str | None) -> str | None:
    for match in _LUMA_URL.finditer(description):
        url = match.group(0).rstrip(_URL_TRAILING)
        if len(url) <= URL_MAX:
            return url
    if location is not None:
        url = location.strip()
        if _LUMA_URL.fullmatch(url) is not None and len(url) <= URL_MAX:
            return url
    return None


def _external_id(uid: str) -> str | None:
    ident = uid[: -len(UID_SUFFIX)] if uid.lower().endswith(UID_SUFFIX) else uid
    if len(ident) > EXTERNAL_ID_MAX or _ID_CHARS.fullmatch(ident) is None:
        return None
    return ident


def _to_facts(event: IcalEvent) -> EventFacts | None:
    title = flatten(event.summary, TITLE_MAX)
    external_id = _external_id(event.uid)
    description = event.description or ""
    url = _event_url(description, event.location)
    if title is None or external_id is None or url is None:
        return None

    parts = _split_description(description)
    location = event.location or ""
    address = None if _ANY_URL.match(location) else flatten(location, ADDRESS_MAX)
    lat, lng = event.geo if event.geo is not None else (None, None)
    online = event.geo is None and (
        _MEETING_LINK.search(location) is not None or _MEETING_LINK.search(description) is not None
    )
    return EventFacts(
        source="luma",
        external_id=external_id,
        url=url,
        title=title,
        starts_at=event.dtstart,
        ends_at=event.dtend,
        snippet=_snippet(parts),
        venue_name=parts.venue,
        address=address,
        lat=lat,
        lng=lng,
        address_withheld=parts.withheld,
        is_online=online,
        organiser_name=flatten(event.organizer_cn, ORGANISER_MAX),
        hosts=parts.hosts,
        is_cancelled=event.status == "CANCELLED",
    )


def parse_feed(data: bytes) -> list[EventFacts]:
    """Every readable event in a Luma iCal feed, in feed order; ``[]`` for bad input."""
    events = (_to_facts(event) for event in parse_ical(data))
    return [event for event in events if event is not None]


# ── fetching ─────────────────────────────────────────────────────────────────


async def fetch_feed(client: httpx.AsyncClient, url: str) -> bytes:
    """The raw bytes of one feed address built by :func:`feed_url`.

    Any other address is refused with ``ValueError`` before a request is made,
    so a corrupted list or a stray argument cannot send this client anywhere
    but ``api.lu.ma/ics/get``. Raises ``httpx.HTTPError`` on a transport error
    or non-2xx (a redirect is not followed), and ``ValueError`` when the body
    passes the size cap. The caller records the outcome against the source's
    health counter.
    """
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    kind = _kind(query.get("entity"))
    ident = query.get("id")
    if kind is None or ident is None or feed_url(kind, ident) != url:
        raise ValueError(f"not a Luma feed address: {url[:80]!r}")
    return await get_bounded(client, url, headers={"Accept": "text/calendar"})
