"""
services/events/gdg.py
~~~~~~~~~~~~~~~~~~~~~~
Google Developer Groups, read only from the public chapter and event pages that
``gdg.community.dev/robots.txt`` allows.

The site's robots.txt disallows ``/health/``, ``/accounts/``, ``/api/``,
``/newsletter/``, ``/gql/``, ``/triggers/``, ``/u/`` and ``/viewserver-health/``
for every agent and asks for a two second pause between requests. The terms
tie automated access to those instructions, so this module reads exactly two
kinds of page and nothing else:

* a chapter page, ``/<chapter-slug>/``: server-rendered, with the upcoming
  events in the Next.js data (``props.pageProps.prerenderData.upcomingEvents``);
* an event page, ``/events/details/<slug>/``: a schema.org ``Event`` JSON-LD
  block holding where and when.

There is no iCal feed, and the JSON endpoints under ``/api/`` are off limits,
so :func:`assert_allowed` refuses them (and any address it cannot vouch for)
before :func:`fetch_page` makes a request. The caller keeps to
:data:`CRAWL_DELAY_S` between requests.

The parsers are pure and total: bad input is an empty list or ``None``. They
are synchronous, so an async caller runs them off the event loop
(``await asyncio.to_thread(parse_chapter, data)``). :func:`fetch_page` is the
one network call.

What a page states, and what becomes of it:

* The event's address is built from the slug, never from a link in the page.
* ``addressCountry`` is never read: GDG prints ``"US"`` for a Bengaluru venue.
  Where the event is comes from ``addressLocality`` and ``postalCode``.
* A start time without an offset is not guessed at: the event is not built.
  (A chapter listing's ``start_date`` is documented as UTC, so there a bare
  time is read as UTC.)
* ``Cancelled`` only from an explicit ``eventStatus`` of ``EventCancelled``;
  online only from an ``OnlineEventAttendanceMode``.
* The organiser is the chapter that listed the event, not the people the page
  may name.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

from domain.event_facts import EventFacts, flatten
from services.events.http import get_bounded

BASE = "https://gdg.community.dev"
HOST = "gdg.community.dev"
CRAWL_DELAY_S = 2.0
DISALLOWED = (
    "/health/",
    "/accounts/",
    "/api/",
    "/newsletter/",
    "/gql/",
    "/triggers/",
    "/u/",
    "/viewserver-health/",
)

TITLE_MAX = 300
SNIPPET_MAX = 600
VENUE_MAX = 200
ADDRESS_MAX = 300
LOCALITY_MAX = 100
POSTAL_CODE_MAX = 20
ORGANISER_MAX = 200
EVENT_TYPE_MAX = 80
URL_MAX = 2048

#: Top-level paths that look like a chapter slug but are not a chapter: the site's own
#: events listing, below which the event pages live.
_NOT_CHAPTERS = frozenset({"events"})

_SLUG = re.compile(r"[a-z0-9-]{1,160}")
_CHAPTER_ADDRESS = re.compile(rf"{re.escape(BASE)}/([a-z0-9-]{{1,160}})/")
_EVENT_ADDRESS = re.compile(rf"{re.escape(BASE)}/events/details/([a-z0-9-]{{1,160}})/")
_LISTED_EVENT_URL = re.compile(rf"{re.escape(BASE)}/events/details/([a-z0-9-]{{1,160}})/?")
_UNSAFE_URL_CHARS = re.compile(r"[\x00-\x20\x7f]")
_UNSAFE_PATH_CHARS = re.compile(r"[\\%;]")
# An ISO 8601 date *and* time, extended format, so a bare date is not read as midnight.
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}")
_ONLINE_MODE = "OnlineEventAttendanceMode"
_CANCELLED = "EventCancelled"


# ── the robots.txt guard ─────────────────────────────────────────────────────


def assert_allowed(url: str) -> None:
    """Raise ``ValueError`` unless ``url`` is a page robots.txt lets this client read.

    The scheme must be https and the host exactly ``gdg.community.dev``, with no
    credentials and no port but 443, and the path must start with none of
    :data:`DISALLOWED`. A path that would have to be normalised to be understood
    (``..`` or ``.`` segments, an empty segment, a backslash, a percent escape, a
    ``;``) is refused rather than normalised: what is checked is what is requested.
    The comparison ignores case, so ``/API/`` is refused too.
    """
    if not isinstance(url, str) or len(url) > URL_MAX or _UNSAFE_URL_CHARS.search(url):
        raise ValueError(f"not a GDG address: {str(url)[:80]!r}")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"not a GDG address: {url[:80]!r}") from exc
    if (
        parts.scheme != "https"
        or parts.hostname != HOST
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        raise ValueError(f"not a GDG address: {url[:80]!r}")
    path = parts.path
    segments = path.split("/")
    if (
        not path.startswith("/")
        or "//" in path
        or _UNSAFE_PATH_CHARS.search(path)
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ValueError(f"not a path this client will request: {path[:80]!r}")
    folded = path.casefold()
    if any(folded.startswith(prefix) for prefix in DISALLOWED):
        raise ValueError(f"robots.txt disallows {path[:80]!r}")


def _slug(value: str) -> str:
    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise ValueError(f"not a GDG slug: {str(value)[:60]!r}")
    return value


def chapter_url(slug: str) -> str:
    """The address of a chapter page; ``ValueError`` for a slug that is not a chapter's."""
    if _slug(slug) in _NOT_CHAPTERS:
        raise ValueError(f"not a chapter slug: {slug!r}")
    url = f"{BASE}/{slug}/"
    assert_allowed(url)
    return url


def event_page_url(slug: str) -> str:
    """The address of an event page; ``ValueError`` for a malformed slug."""
    url = f"{BASE}/events/details/{_slug(slug)}/"
    assert_allowed(url)
    return url


def event_slug(url: object) -> str | None:
    """The slug of an event page address (with or without the trailing slash), else ``None``."""
    if not isinstance(url, str):
        return None
    match = _LISTED_EVENT_URL.fullmatch(url)
    return match.group(1) if match else None


# ── reading the page ─────────────────────────────────────────────────────────


def _scripts(html: bytes, script_type: str) -> list[Any]:
    """The JSON in every ``<script type=script_type>`` of the page, in page order.

    A block that is not valid JSON, or nests deeper than the parser allows, is skipped.
    """
    try:
        tags = BeautifulSoup(html, "html.parser").find_all("script", attrs={"type": script_type})
    except (RecursionError, ParserRejectedMarkup):
        return []
    found: list[Any] = []
    for tag in tags:
        text = tag.string
        if not text:
            continue
        try:
            found.append(json.loads(text))
        except (ValueError, RecursionError):
            continue
    return found


def chapter_blob(html: bytes) -> dict[str, Any] | None:
    """The Next.js data of a chapter page (the JSON block with ``props``), else ``None``.

    Other JSON scripts come first on some pages (a bare list of tags), so the block is
    recognised by its shape, not its position.
    """
    for data in _scripts(html, "application/json"):
        if isinstance(data, dict) and isinstance(data.get("props"), dict):
            return data
    return None


def _is_event_type(value: object) -> bool:
    kinds = value if isinstance(value, list) else [value]
    return any(isinstance(kind, str) and kind.endswith("Event") for kind in kinds)


def _event_nodes(data: Any) -> Iterable[Any]:
    if isinstance(data, list):
        yield from data
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            yield from graph
        yield data


def event_json_ld(html: bytes) -> dict[str, Any] | None:
    """The schema.org ``Event`` block of an event page, else ``None``.

    The block may stand alone, in a list or in a ``@graph``, and its ``@type`` may be a
    subtype (``EducationEvent``) or a list.
    """
    for data in _scripts(html, "application/ld+json"):
        for node in _event_nodes(data):
            if isinstance(node, dict) and _is_event_type(node.get("@type")):
                return node
    return None


def _text(value: object, limit: int) -> str | None:
    return flatten(value, limit) if isinstance(value, str) else None


def _moment(value: object, *, assume: tzinfo | None) -> datetime | None:
    """An ISO 8601 date-time as an aware ``datetime``; ``assume`` is the zone of a bare one."""
    if not isinstance(value, str) or len(value) > 40 or _DATE_TIME.match(value.strip()) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None if assume is None else parsed.replace(tzinfo=assume)
    return parsed


# ── the chapter page ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GdgListing:
    """One upcoming event as a chapter page lists it."""

    slug: str
    title: str
    starts_at: datetime
    url: str
    event_type: str | None
    description: str | None


def _upcoming(blob: dict[str, Any]) -> list[Any]:
    node: Any = blob
    for key in ("props", "pageProps", "prerenderData", "upcomingEvents", "results"):
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def _listing(entry: Any) -> GdgListing | None:
    if not isinstance(entry, dict):
        return None
    slug = event_slug(entry.get("url"))
    title = _text(entry.get("title"), TITLE_MAX)
    starts_at = _moment(entry.get("start_date"), assume=UTC)
    if slug is None or title is None or starts_at is None:
        return None
    return GdgListing(
        slug=slug,
        title=title,
        starts_at=starts_at,
        url=event_page_url(slug),
        event_type=_text(entry.get("event_type_title"), EVENT_TYPE_MAX),
        description=_text(entry.get("description_short"), SNIPPET_MAX),
    )


def past_event_slugs(blob: dict[str, Any]) -> list[str]:
    """The slugs of the earlier events a chapter page lists, in page order, once each.

    The parsers read only upcoming events; the capture script uses these to record an
    event page when a chapter has nothing upcoming. ``[]`` for a missing key.
    """
    node: Any = blob
    for key in ("props", "pageProps", "prerenderData", "pastEvents", "results"):
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    slugs: list[str] = []
    for entry in node if isinstance(node, list) else []:
        slug = event_slug(entry.get("url")) if isinstance(entry, dict) else None
        if slug is not None and slug not in slugs:
            slugs.append(slug)
    return slugs


def parse_chapter(html: bytes) -> list[GdgListing]:
    """The upcoming events a chapter page lists, in page order; ``[]`` for bad input.

    A missing key anywhere on the way to ``upcomingEvents.results`` is an empty list. An
    entry without a title, a start or an event address of ``gdg.community.dev`` is
    skipped, and a repeated slug keeps its first entry.
    """
    blob = chapter_blob(html)
    if blob is None:
        return []
    seen: set[str] = set()
    listings: list[GdgListing] = []
    for entry in _upcoming(blob):
        listing = _listing(entry)
        if listing is not None and listing.slug not in seen:
            seen.add(listing.slug)
            listings.append(listing)
    return listings


# ── the event page ───────────────────────────────────────────────────────────


def _type_names(node: dict[str, Any]) -> list[str]:
    kinds = node.get("@type")
    return [
        kind for kind in (kinds if isinstance(kinds, list) else [kinds]) if isinstance(kind, str)
    ]


def _venue(location: Any) -> dict[str, Any] | None:
    """The physical place among an event's ``location`` values (one, or a list of them)."""
    for node in location if isinstance(location, list) else [location]:
        if isinstance(node, dict) and "VirtualLocation" not in _type_names(node):
            return node
    return None


def _join_address(parts: Iterable[str | None]) -> str | None:
    """The parts in order, leaving out one an earlier part already says ("…, Bengaluru")."""
    kept: list[str] = []
    for part in parts:
        if part and not any(part.casefold() in earlier.casefold() for earlier in kept):
            kept.append(part)
    return flatten(", ".join(kept), ADDRESS_MAX)


def _last_segment(value: object) -> str:
    """``https://schema.org/EventCancelled`` -> ``EventCancelled``."""
    return value.rstrip("/").rsplit("/", 1)[-1] if isinstance(value, str) else ""


def parse_event_page(html: bytes, *, slug: str, chapter: str) -> EventFacts | None:
    """The event an event page states, as :class:`EventFacts`; ``None`` if it states none.

    ``slug`` is the page's address, so it is validated (``ValueError`` when malformed) and
    becomes both the ``external_id`` and, through :func:`event_page_url`, the ``url``.
    ``chapter`` is the chapter's name, which is the organiser. Without a title and a start
    time that carries an offset there is no event. An end that is not after the start is
    dropped and the event stands.
    """
    url = event_page_url(slug)
    block = event_json_ld(html)
    if block is None:
        return None
    title = _text(block.get("name"), TITLE_MAX)
    starts_at = _moment(block.get("startDate"), assume=None)
    if title is None or starts_at is None:
        return None
    ends_at = _moment(block.get("endDate"), assume=None)
    if ends_at is not None and ends_at <= starts_at:
        ends_at = None

    venue = _venue(block.get("location"))
    place = venue if venue is not None else {}
    postal = place.get("address")
    if isinstance(postal, str):
        address = flatten(postal, ADDRESS_MAX)
        locality = None
    elif isinstance(postal, dict):
        locality = _text(postal.get("addressLocality"), LOCALITY_MAX)
        address = _join_address(
            [
                _text(postal.get("streetAddress"), ADDRESS_MAX),
                locality,
                _text(postal.get("postalCode"), POSTAL_CODE_MAX),
            ]
        )
    else:
        address = locality = None
    return EventFacts(
        source="gdg",
        external_id=slug,
        url=url,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        snippet=_text(block.get("description"), SNIPPET_MAX),
        venue_name=_text(place.get("name"), VENUE_MAX),
        address=address,
        locality=locality,
        is_online=_last_segment(block.get("eventAttendanceMode")) == _ONLINE_MODE,
        organiser_name=flatten(chapter, ORGANISER_MAX),
        is_cancelled=_last_segment(block.get("eventStatus")) == _CANCELLED,
    )


# ── fetching ─────────────────────────────────────────────────────────────────


def _is_page_address(url: str) -> bool:
    chapter = _CHAPTER_ADDRESS.fullmatch(url)
    if chapter is not None:
        return chapter.group(1) not in _NOT_CHAPTERS
    return _EVENT_ADDRESS.fullmatch(url) is not None


async def fetch_page(client: httpx.AsyncClient, url: str) -> bytes:
    """The raw bytes of one chapter or event page, as built by the URL helpers above.

    :func:`assert_allowed` runs first, and then the address must be exactly a chapter page
    or an event page (no query, no fragment), so a corrupted list or a stray argument cannot
    send this client anywhere else on the site. Either failure is a ``ValueError`` raised
    before any request. Raises ``httpx.HTTPError`` on a transport error or non-2xx (a
    redirect is not followed), and ``ValueError`` when the body passes the size cap. The
    caller waits :data:`CRAWL_DELAY_S` between requests and records the outcome against the
    source's health counter.
    """
    assert_allowed(url)
    if not _is_page_address(url):
        raise ValueError(f"not a GDG chapter or event page: {url[:80]!r}")
    return await get_bounded(client, url, headers={"Accept": "text/html"}, follow_redirects=False)
