"""
domain/event_urls.py
~~~~~~~~~~~~~~~~~~~~
Recognising a link a person pasted to "add an event by link".

The URL is never fetched. It is only *read*: if it is an event page on one of
the three sources we know, the identifier is lifted out of it - matched against
a strict pattern - and the caller builds any request from that piece alone, as
``domain/posting_urls.py`` does for the posting check. A URL that does not match
exactly is not something we can resolve, and the answer is ``None`` (the caller
offers the manual form).

What a link must be, all at once:

* ``https`` - never ``http``, never any other scheme;
* a host that is **exactly** one of the listed ones, so ``eventbrite.com.evil.io``
  and ``www.gdg.community.dev`` never reach a path pattern;
* no userinfo and no port, written or empty;
* visible ASCII only, at most :data:`MAX_URL_LENGTH` characters;
* a path that fully matches the source's one event-page shape.

A query string or fragment (``?aff=…``, ``#top``) is ignored: it carries
nothing the request needs and never reaches the key.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

MAX_URL_LENGTH = 512

EventSource = Literal["eventbrite", "gdg", "luma"]


@dataclass(frozen=True)
class PastedEvent:
    """An event page on a known source: enough to look the event up, nothing more."""

    source: EventSource
    key: str


#: Visible ASCII only. Whitespace, control characters (a NUL, a tab that
#: ``urlsplit`` would silently drop) and look-alike Unicode never get this far.
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")

# host -> (source, pattern over the path). Hosts are matched exactly, never by
# suffix. Each pattern captures the key; ``fullmatch`` is used throughout, as ``$``
# would also accept a trailing newline.
_EVENTBRITE_PATH = re.compile(r"/e/[A-Za-z0-9_-]{1,200}-(\d{1,20})/?")
_GDG_PATH = re.compile(r"/events/details/([a-z0-9-]{1,160})/?")
# "/event" is the prefix of the event-id route, never a calendar or event slug.
_LUMA_PATH = re.compile(r"/(?:event/(evt-[A-Za-z0-9]{3,64})|(?!event/?\Z)([A-Za-z0-9-]{3,64}))/?")

_EVENTBRITE_TLDS = ("com", "co.in", "co.uk", "com.au", "ca", "ie")

_PATTERNS: dict[str, tuple[EventSource, re.Pattern[str]]] = {
    **{f"www.eventbrite.{tld}": ("eventbrite", _EVENTBRITE_PATH) for tld in _EVENTBRITE_TLDS},
    "gdg.community.dev": ("gdg", _GDG_PATH),
    "lu.ma": ("luma", _LUMA_PATH),
    "luma.com": ("luma", _LUMA_PATH),
}


def parse_event_url(raw: str) -> PastedEvent | None:
    """Read an event out of a pasted link, or ``None`` if it is not exactly one."""
    text = (raw or "").strip()
    if not text or len(text) > MAX_URL_LENGTH or _VISIBLE_ASCII.fullmatch(text) is None:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme != "https" or host is None:
        return None
    # ``netloc`` is exactly the host when there is no userinfo and no port,
    # including the empty forms ("https://@host", "https://host:") that
    # ``username`` and ``port`` report as falsy.
    if parts.username or parts.password or port or parts.netloc.lower() != host:
        return None
    known = _PATTERNS.get(host)
    if known is None:
        return None
    source, pattern = known
    match = pattern.fullmatch(parts.path)
    if match is None:
        return None
    key = next((group for group in match.groups() if group is not None), None)
    return PastedEvent(source=source, key=key) if key is not None else None
