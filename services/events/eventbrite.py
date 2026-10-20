"""
services/events/eventbrite.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Eventbrite, read only through its official API v3.

Eventbrite's terms forbid scraping, so this module never requests a
``www.eventbrite.*`` page and never reads a page's JSON-LD. It builds exactly
three kinds of address on ``https://www.eventbriteapi.com/v3`` and nothing else:

* ``/events/<id>/`` : one event (a pasted link, or an id found in a list);
* ``/organizers/<id>/events/`` : a curated organiser's live events;
* ``/venues/<id>/events/`` : a curated venue's live events.

Event search was shut down in 2019, which is why discovery is a curated list of
organisers and venues (``data/events/sources.json``).

Every request carries ``Authorization: Bearer <token>`` and goes through
:func:`services.events.http.get_bounded` (project User-Agent, 15 s, 1 MB cap, no
redirects). The token lives only in that header: it is checked against a plain
token pattern before use, is never part of an address, and no message raised
here contains it. Every id is all digits and a continuation cursor matches a
strict token pattern before a request is built from it, so a corrupted list or a
stray argument cannot steer the token anywhere but the API.

:func:`parse_event` and :func:`parse_event_list` are pure and total: what the API
sends is data, so a malformed field is ``None`` and a malformed event is skipped,
never an exception. They are cheap enough (one page is at most 1 MB of JSON) to
run on the event loop.

What an event states, and what becomes of it:

* ``start.utc`` is the start. A start that is missing, has no UTC offset, or falls
  outside the years 2000 to 2100 drops the event: a time is never guessed, and the
  store converts every start to IST, which cannot overflow inside that range.
* The event ``url`` must be https on ``eventbrite.com`` or ``eventbrite.co.in``
  (optionally ``www.``): the link a reader is sent to is never another site.
* ``status == "canceled"`` is the only cancellation.
* The venue gives the name, the printed address, the city and a coordinate pair
  (both numbers, finite and in range, or neither).
* ``category_id`` / ``subcategory_id`` / ``format_id`` are kept as opaque ids for
  the relevance rules (category ``102``, subcategory ``2004``).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import SplitResult, quote, urlsplit

import httpx

from domain.event_facts import EventFacts, flatten
from services.events.http import get_bounded

API = "https://www.eventbriteapi.com/v3"
HOST = "www.eventbriteapi.com"
EXPAND = "venue,organizer,format"

TITLE_MAX = 300
SNIPPET_MAX = 600
VENUE_MAX = 200
ADDRESS_MAX = 300
LOCALITY_MAX = 100
ORGANISER_MAX = 200
EXTERNAL_ID_MAX = 32
TIMEZONE_MAX = 64
URL_MAX = 1024
CONTINUATION_MAX = 512
TOKEN_MAX = 512
YEAR_MIN = 2000
YEAR_MAX = 2100

_LIST_QUERY = f"status=live&order_by=start_asc&expand={EXPAND}"
# The venue endpoint's order_by is unconfirmed: the refresh step asks again without it on a 400.
_UNORDERED_LIST_QUERY = f"status=live&expand={EXPAND}"

# ASCII digits only: str.isdigit() and \d also accept "٣" and "²".
_ID = re.compile(rf"[0-9]{{1,{EXTERNAL_ID_MAX}}}")
_TAXONOMY_ID = re.compile(r"[0-9]{1,12}")
# A cursor is base64 (standard or URL-safe alphabet, with padding), nothing a URL
# gives meaning to beyond what quote() escapes.
_CONTINUATION = re.compile(rf"[A-Za-z0-9_+/=-]{{1,{CONTINUATION_MAX}}}")
# A personal OAuth token is letters and digits; the extra characters cover other token
# alphabets. Nothing that could end a header value or add a header can pass.
_TOKEN = re.compile(rf"[A-Za-z0-9._~+/=-]{{1,{TOKEN_MAX}}}")
_TIMEZONE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+){0,2}")
# The documented shape of start.utc / end.utc, with the offset required.
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_COORDINATE = re.compile(r"-?[0-9]{1,3}(?:\.[0-9]{1,20})?")
_EVENT_HOST = re.compile(r"(?:www\.)?eventbrite\.(?:com|co\.in)")
_UNSAFE_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")


# ── small readers ────────────────────────────────────────────────────────────


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, limit: int) -> str | None:
    return flatten(value, limit) if isinstance(value, str) else None


def _digits(value: object, pattern: re.Pattern[str]) -> str | None:
    """``value`` as digit-only text matching ``pattern`` (an int counts, a bool does not)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        # Bounded before str(): converting an int of thousands of digits raises.
        if not 0 <= value < 10**EXTERNAL_ID_MAX:
            return None
        value = str(value)
    if isinstance(value, str) and pattern.fullmatch(value) is not None:
        return value
    return None


def _moment(value: object) -> datetime | None:
    """An API timestamp as an aware UTC datetime, or ``None`` when it is not a clean one in range."""
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        moment = datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    return moment if YEAR_MIN <= moment.year <= YEAR_MAX else None


def _coordinate(value: object, limit: float) -> float | None:
    """A latitude or longitude as a finite float within ``limit``, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    if isinstance(value, str) and _COORDINATE.fullmatch(value) is None:
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):  # an int too large for a float
        return None
    return number if math.isfinite(number) and -limit <= number <= limit else None


def _position(*places: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """The first place that states a complete coordinate pair; a half pair is no position."""
    for place in places:
        lat = _coordinate(place.get("latitude"), 90.0)
        lng = _coordinate(place.get("longitude"), 180.0)
        if lat is not None and lng is not None:
            return lat, lng
    return None, None


def _split(value: object) -> SplitResult | None:
    """``value`` as a URL with no control characters, credentials or odd port, else ``None``."""
    if not isinstance(value, str) or not value or len(value) > URL_MAX:
        return None
    if _UNSAFE_URL_CHARS.search(value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.hostname is None or parts.username is not None or parts.password is not None:
        return None
    return parts if port in (None, 443, 80) else None


def _event_url(value: object) -> str | None:
    """The event's own page: https on an Eventbrite host, nowhere else."""
    parts = _split(value)
    if parts is None or parts.scheme != "https" or parts.port not in (None, 443):
        return None
    if _EVENT_HOST.fullmatch(parts.hostname or "") is None:
        return None
    return str(value)


def _site_url(value: object) -> str | None:
    """An organiser's website or page: http(s) on a dotted host, not an address to be fetched."""
    parts = _split(value)
    if parts is None or parts.scheme not in ("http", "https") or "." not in (parts.hostname or ""):
        return None
    return str(value)


def _timezone(value: object) -> str | None:
    if isinstance(value, str) and len(value) <= TIMEZONE_MAX and _TIMEZONE.fullmatch(value):
        return value
    return None


# ── parsing ──────────────────────────────────────────────────────────────────


def parse_event(payload: Mapping[str, Any]) -> EventFacts | None:
    """One v3 event object (with ``expand=venue,organizer,format``) as :class:`EventFacts`.

    Total: ``None`` for anything that is not a usable event (no digit id, no title,
    no aware start within 2000 to 2100, no https Eventbrite url), and ``None`` for
    each individual field it cannot read. Unknown keys, such as a hand-written
    ``_comment``, are ignored.
    """
    if not isinstance(payload, Mapping):
        return None
    external_id = _digits(payload.get("id"), _ID)
    url = _event_url(payload.get("url"))
    title = _text(_map(payload.get("name")).get("text"), TITLE_MAX)
    start = _map(payload.get("start"))
    starts_at = _moment(start.get("utc"))
    if external_id is None or url is None or title is None or starts_at is None:
        return None

    ends_at = _moment(_map(payload.get("end")).get("utc"))
    if ends_at is not None and ends_at <= starts_at:
        ends_at = None

    venue = _map(payload.get("venue"))
    address = _map(venue.get("address"))
    lat, lng = _position(venue, address)
    organizer = _map(payload.get("organizer"))
    organiser_url = _site_url(organizer.get("website")) or _site_url(organizer.get("url"))

    format_id = payload.get("format_id")
    if format_id is None:  # asked for with expand=format
        format_id = _map(payload.get("format")).get("id")
    taxonomy: dict[str, str] = {}
    for key, value in (
        ("category", payload.get("category_id")),
        ("subcategory", payload.get("subcategory_id")),
        ("format", format_id),
    ):
        ident = _digits(value, _TAXONOMY_ID)
        if ident is not None:
            taxonomy[key] = ident

    is_free = payload.get("is_free")
    return EventFacts(
        source="eventbrite",
        external_id=external_id,
        url=url,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        snippet=_text(payload.get("summary"), SNIPPET_MAX),
        venue_name=_text(venue.get("name"), VENUE_MAX),
        address=_text(address.get("localized_address_display"), ADDRESS_MAX),
        locality=_text(address.get("city"), LOCALITY_MAX),
        lat=lat,
        lng=lng,
        is_online=payload.get("online_event") is True,
        is_free=is_free if isinstance(is_free, bool) else None,
        organiser_name=_text(organizer.get("name"), ORGANISER_MAX),
        organiser_url=organiser_url,
        is_cancelled=payload.get("status") == "canceled",
        timezone=_timezone(start.get("timezone")),
        taxonomy=taxonomy,
    )


def parse_event_list(payload: Mapping[str, Any]) -> tuple[list[EventFacts], str | None]:
    """The readable events of one list page, and the cursor of the next page.

    Total. An unreadable event is skipped and an id already seen on the page is
    kept once. The cursor is returned only while ``pagination.has_more_items`` is
    true and the cursor is a plain token, so ``None`` means "stop paging".
    """
    if not isinstance(payload, Mapping):
        return [], None
    events: list[EventFacts] = []
    seen: set[str] = set()
    raw = payload.get("events")
    for item in raw if isinstance(raw, list) else []:
        facts = parse_event(item)
        if facts is not None and facts.external_id not in seen:
            seen.add(facts.external_id)
            events.append(facts)
    pagination = _map(payload.get("pagination"))
    cursor = pagination.get("continuation")
    more = pagination.get("has_more_items") is True
    if more and isinstance(cursor, str) and _CONTINUATION.fullmatch(cursor) is not None:
        return events, cursor
    return events, None


# ── fetching ─────────────────────────────────────────────────────────────────


def _checked_id(kind: str, value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"not an Eventbrite {kind} id: {str(value)[:40]!r}")
    return value


def _checked_token(token: object) -> str:
    # The message names the rule, never the value.
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise ValueError("the Eventbrite token is empty or not a plain token")
    return token


def _checked_continuation(value: object) -> str:
    if not isinstance(value, str) or _CONTINUATION.fullmatch(value) is None:
        raise ValueError("not an Eventbrite continuation cursor")
    return value


def _list_url(
    kind: Literal["organizers", "venues"],
    ident: str,
    continuation: str | None,
    *,
    ordered: bool = True,
) -> str:
    query = _LIST_QUERY if ordered else _UNORDERED_LIST_QUERY
    url = f"{API}/{kind}/{_checked_id(kind[:-1], ident)}/events/?{query}"
    if continuation is not None:
        url += f"&continuation={quote(_checked_continuation(continuation), safe='')}"
    return url


async def _get_object(client: httpx.AsyncClient, token: str, url: str) -> dict[str, Any]:
    """``GET url`` with the bearer token, as a JSON object.

    The address must be on the API host before the token is attached. Raises
    ``httpx.HTTPError`` on a transport error or non-2xx (a redirect included), and
    ``ValueError`` when the body is over the size cap, not JSON, or not an object.
    No message here quotes the body or the token.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST or not parts.path.startswith("/v3/"):
        raise ValueError("not an Eventbrite API address")
    body = await get_bounded(
        client,
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        follow_redirects=False,
    )
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):  # bad JSON, bad UTF-8, or nested past the stack
        raise ValueError(f"Eventbrite {parts.path} did not return JSON") from None
    if not isinstance(data, dict):
        raise ValueError(f"Eventbrite {parts.path} did not return a JSON object")
    return data


async def get_event(client: httpx.AsyncClient, token: str, event_id: str) -> dict[str, Any]:
    """``GET /events/<id>/?expand=venue,organizer,format`` as the API's JSON object.

    ``event_id`` must be all digits and ``token`` a plain token, else ``ValueError``
    before any request. Feed the result to :func:`parse_event`.
    """
    token = _checked_token(token)
    url = f"{API}/events/{_checked_id('event', event_id)}/?expand={EXPAND}"
    return await _get_object(client, token, url)


async def organizer_events(
    client: httpx.AsyncClient, token: str, organizer_id: str, continuation: str | None = None
) -> dict[str, Any]:
    """``GET /organizers/<id>/events/?status=live&order_by=start_asc&expand=…`` (one page).

    ``continuation`` is the cursor :func:`parse_event_list` returned for the next page.
    Bad ids, cursors and tokens are a ``ValueError`` before any request. This
    endpoint may refuse a token that is not a member of the organisation (403 or
    404); the caller then falls back to :func:`venue_events`.
    """
    token = _checked_token(token)
    return await _get_object(client, token, _list_url("organizers", organizer_id, continuation))


async def venue_events(
    client: httpx.AsyncClient,
    token: str,
    venue_id: str,
    continuation: str | None = None,
    *,
    ordered: bool = True,
) -> dict[str, Any]:
    """``GET /venues/<id>/events/?status=live&order_by=start_asc&expand=…`` (one page).

    ``ordered=False`` leaves out ``order_by``: the venue endpoint's support for it is
    unconfirmed, and the refresh step asks again without it when the API answers 400.
    """
    token = _checked_token(token)
    return await _get_object(
        client, token, _list_url("venues", venue_id, continuation, ordered=ordered)
    )
