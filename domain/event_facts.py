"""
domain/event_facts.py
~~~~~~~~~~~~~~~~~~~~~
What a source parser hands to the store: the facts one source states about one
event, and nothing the store has to infer from I/O.

Each parser (Luma iCal, GDG JSON-LD, Eventbrite API) returns :class:`EventFacts`
and nothing else; ``services/events/store.py`` is the only writer. A field a
source did not state is ``None`` (or empty), never a guess: in particular
``starts_at`` is always the source's own start, and an event without one is
not built at all.

:func:`flatten` is the one way free text from a feed reaches a column.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EventFacts:
    """One event as one source states it."""

    source: str
    external_id: str
    url: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    snippet: str | None = None
    venue_name: str | None = None
    address: str | None = None
    locality: str | None = None
    lat: float | None = None
    lng: float | None = None
    area: str | None = None
    address_withheld: bool = False
    is_online: bool = False
    is_free: bool | None = None
    organiser_name: str | None = None
    organiser_url: str | None = None
    hosts: tuple[str, ...] = ()
    is_cancelled: bool = False
    timezone: str | None = None
    taxonomy: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceScope:
    """What a configured source vouches for.

    ``india`` is true when everything the source lists is in India (a Bengaluru
    feed); ``trusted_tech`` when everything it lists is technology (a curated
    developer-community calendar).
    """

    key: str
    india: bool
    trusted_tech: bool


# A tag is "<" or "</" and a letter, or "<!" for a comment or doctype, up to the
# next ">" with no "<" or ">" between. Requiring the letter keeps "under <5 min"
# as text; refusing a nested "<" keeps a run of unclosed "<a" linear, not quadratic.
_TAG = re.compile(r"</?[A-Za-z!][^<>]*>")
# Block-level tags separate words ("<p>One</p><p>Two</p>"); inline ones do not
# ("<b>Bold</b>ly").
_BLOCK_TAG = re.compile(
    r"</?(?:br|p|div|li|ul|ol|h[1-6]|tr|td|th|table|section|article|blockquote|hr)\b[^<>]*>",
    re.IGNORECASE,
)
# Control characters other than whitespace; PostgreSQL refuses NUL outright.
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")
_WHITESPACE = re.compile(r"\s+")
_ELLIPSIS = "…"
_TRAILING_SEPARATORS = " ,;:-–—"


def flatten(text: str | None, limit: int) -> str | None:
    """Plain, single-line text of at most ``limit`` characters, or ``None``.

    Strips tags, unescapes entities, drops control characters, collapses
    whitespace, and bounds the result at a word with a trailing "…". It never
    returns an empty string: nothing left (or ``limit < 1``) is ``None``.
    """
    if text is None or limit < 1:
        return None
    flat = _BLOCK_TAG.sub(" ", text)
    flat = _TAG.sub("", flat)
    flat = html.unescape(flat)
    flat = _CONTROL.sub("", flat)
    flat = _WHITESPACE.sub(" ", flat).strip()
    if not flat:
        return None
    if len(flat) <= limit:
        return flat
    room = limit - len(_ELLIPSIS)
    cut = flat[:room]
    if flat[room] != " ":  # the cut falls inside a word: back up to the last space
        space = cut.rfind(" ")
        if space > 0:
            cut = cut[:space]
    cut = cut.rstrip(_TRAILING_SEPARATORS)
    return cut + _ELLIPSIS
