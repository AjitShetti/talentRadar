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
    #: What entry costs, as the source prints it ("₹500", "₹500.00–₹1,500.00"). ``None``
    #: when the source states no price, which is also the case for a free event.
    price: str | None = None
    #: The page a person registers on when it is not the event page itself (GDG's
    #: ``offers.url``, e.g. KonfHub). A string read from the source, never fetched.
    register_url: str | None = None
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


PRICE_MAX = 40
_CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$"}
_AMOUNT = re.compile(r"\d{1,9}(?:\.\d{1,2})?")
_CURRENCY = re.compile(r"[A-Z]{3}")


def price_label(amount: object, currency: object) -> str | None:
    """``"₹499"`` for 499 INR; ``None`` for zero, a negative or an amount that is not a number.

    The amount is shown as the source states it ("12.50" stays "12.50"). Only INR and USD
    get a symbol; any other ISO code is written before the amount ("EUR 12.50").
    """
    if isinstance(amount, bool) or not isinstance(amount, (int, float, str)):
        return None
    text = str(amount).strip()
    if _AMOUNT.fullmatch(text) is None or float(text) <= 0:
        return None
    code = currency.strip().upper() if isinstance(currency, str) else ""
    if _CURRENCY.fullmatch(code) is None:
        return None
    symbol = _CURRENCY_SYMBOLS.get(code)
    return f"{symbol}{text}" if symbol else f"{code} {text}"


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
