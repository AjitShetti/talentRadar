"""
domain/event_identity.py
~~~~~~~~~~~~~~~~~~~~~~~~
Two questions about an event that need no database to answer.

**Is this the same event as that one?** The same meetup is listed on Luma and on
Eventbrite under slightly different titles, a few minutes apart. :func:`same_event`
says when two rows are one event, from three independent facts that must all
agree: the start times, the words of the titles, and where they are.

**Which companies does it name?** :func:`company_candidates` reads the organiser,
the "Hosted by" names and the title ("BitGo x Giottus", "Talk @ Razorpay") for
names a company might go by. They are only *candidates*: the caller resolves each
against the companies we already know and links nothing it cannot resolve
exactly. An event never creates a company, and a person's name simply matches
none.

A title with no distinguishing words matches nothing: when the words cannot say,
two rows stay two rows rather than one event being hidden behind another.

Pure functions, no I/O.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from domain.company_identity import normalize_name
from domain.event_time import as_utc

#: Two events start "at the same time" when no more than this apart.
START_WINDOW = timedelta(minutes=45)
MIN_TITLE_OVERLAP = 0.5
MAX_SAME_VENUE_KM = 1.5

_EARTH_KM = 6371.0

#: Words that say nothing about *which* event a title is. A year or the city is
#: on nearly every title; "x" and "at" are how a title joins its parts.
_STOP_TOKENS = frozenset(
    (
        # joining words
        "the",
        "a",
        "an",
        "and",
        "x",
        "with",
        "at",
        "in",
        "of",
        "for",
        # on nearly every Bengaluru event title
        "bangalore",
        "bengaluru",
        "blr",
        "meetup",
        "edition",
        "2025",
        "2026",
        "2027",
    )
)
_NON_WORD = re.compile(r"[\W_]+")


class EventLike(Protocol):
    """What :func:`same_event` reads: a parsed event or a stored row alike."""

    @property
    def title(self) -> str: ...
    @property
    def starts_at(self) -> datetime: ...
    @property
    def lat(self) -> float | None: ...
    @property
    def lng(self) -> float | None: ...
    @property
    def area(self) -> str | None: ...
    @property
    def is_online(self) -> bool: ...


def _fold(text: str) -> str:
    """``text`` without accents ("Café" -> "Cafe")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def title_tokens(title: str) -> frozenset[str]:
    """The distinguishing words of a title: lowercase, unaccented, no stop words.

    Punctuation and symbols (``×``, ``:``, ``-``) only separate words. A title
    made of nothing but stop words has no tokens.
    """
    words = _NON_WORD.split(_fold(title).lower())
    return frozenset(word for word in words if word and word not in _STOP_TOKENS)


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def _point(event: EventLike) -> tuple[float, float] | None:
    return None if event.lat is None or event.lng is None else (event.lat, event.lng)


def _same_place(a: EventLike, b: EventLike) -> bool:
    """Could these two be at one place? Only a *known* difference says no."""
    if a.is_online and b.is_online:
        return True
    comparable = False
    point_a, point_b = _point(a), _point(b)
    if point_a is not None and point_b is not None:
        comparable = True
        if _km(*point_a, *point_b) <= MAX_SAME_VENUE_KM:
            return True
    if a.area and b.area:
        comparable = True
        if a.area.strip().casefold() == b.area.strip().casefold():
            return True
    return not comparable


def same_event(a: EventLike, b: EventLike) -> bool:
    """Whether two listings are one event.

    All three must hold:

    * the starts are within 45 minutes (inclusive);
    * the title tokens overlap by a Jaccard index of at least one half. A title
      with no distinguishing words ("Meetup Bangalore") is evidence of nothing,
      so it is never the same as anything;
    * the places are compatible: both online, within 1.5 km, in the same area,
      or either unknown.
    """
    if abs(as_utc(a.starts_at) - as_utc(b.starts_at)) > START_WINDOW:
        return False
    first, second = title_tokens(a.title), title_tokens(b.title)
    if not first or not second:
        return False
    if len(first & second) / len(first | second) < MIN_TITLE_OVERLAP:
        return False
    return _same_place(a, b)


# --- company candidates -----------------------------------------------------

Role = Literal["organiser", "host"]

_TITLE_MAX = 300
_NAME_MAX = 80
_NAME_WORDS_MAX = 6
_WHITESPACE = re.compile(r"\s+")

# "A x B", "A + B", "A × B". A bare "x" or "+" needs spaces around it ("C++" and
# "Xenon" are not pairs); the multiplication sign does not.
_PAIR = re.compile(r"\s+[x+]\s+|\s*×\s*", re.IGNORECASE)
# Where a name stops and the rest of a title ("Giottus: Crypto Custody") starts,
# and, for the first side of a pair, where the preamble ("Workshop: BitGo") ends.
_PREFIX_BREAK = re.compile(r"\s*[:|–—]\s*|\s+-\s+")
_TAIL_BREAK = re.compile(r"\s*[:|(\[,–—]\s*|\s+-\s+|\s+@\s*|\s+at\s+", re.IGNORECASE)
# The last "@ Name" or " at Name" of a title.
_AT_NAME = re.compile(r".*(?:(?<=\s)at\s+|@\s*)(?P<name>[^@\s].*)", re.IGNORECASE)
_EDGE_PUNCTUATION = " \t.,;:!?\"'“”‘’-–—|"


@dataclass(frozen=True)
class CompanyCandidate:
    """A name an event mentions, and how it came to be one.

    ``matched_on`` is ``organiser_name``, ``host_name``, ``title_pair`` or
    ``title_at``.
    """

    name: str
    role: Role
    matched_on: str


def _given_name(value: str | None) -> str | None:
    """A name a source stated (organiser, host), or ``None`` if unusable."""
    name = _WHITESPACE.sub(" ", value or "").strip()
    return name if name and len(name) <= _NAME_MAX else None


def _title_name(fragment: str) -> str | None:
    """A company-sized name out of a piece of title, or ``None`` if it is not one."""
    name = _WHITESPACE.sub(" ", fragment).strip(_EDGE_PUNCTUATION)
    words = name.split(" ")
    if not name or len(name) > _NAME_MAX or len(words) > _NAME_WORDS_MAX:
        return None
    if words[0].isdigit():  # "at 6 PM" is a time, not a company
        return None
    return name


def _pair_names(title: str) -> list[str]:
    sides = _PAIR.split(title)
    if len(sides) < 2:
        return []
    names: list[str] = []
    for index, side in enumerate(sides):
        if index == 0:
            side = _PREFIX_BREAK.split(side)[-1]
        name = _title_name(_TAIL_BREAK.split(side)[0])
        if name is not None:
            names.append(name)
    return names


def _at_name(title: str) -> str | None:
    match = _AT_NAME.fullmatch(title)
    if match is None:
        return None
    return _title_name(_TAIL_BREAK.split(match.group("name"))[0])


def company_candidates(
    organiser: str | None, hosts: Sequence[str], title: str
) -> list[CompanyCandidate]:
    """The company names an event mentions, strongest evidence first.

    In order: the organiser, each "Hosted by" name, the sides of a title pair,
    and the trailing "@ Name" / "at Name" of the title. Names that normalise
    alike (``Okta`` and ``Okta Inc.``) are one candidate, and the first wins, so
    an organiser is never demoted to a host by the title repeating it.
    """
    title = _WHITESPACE.sub(" ", title or "").strip()[:_TITLE_MAX]
    found: list[tuple[str | None, Role, str]] = [
        (_given_name(organiser), "organiser", "organiser_name")
    ]
    found += [(_given_name(host), "host", "host_name") for host in hosts]
    found += [(name, "host", "title_pair") for name in _pair_names(title)]
    found.append((_at_name(title), "host", "title_at"))

    seen: set[str] = set()
    candidates: list[CompanyCandidate] = []
    for name, role, matched_on in found:
        if name is None:
            continue
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(CompanyCandidate(name=name, role=role, matched_on=matched_on))
    return candidates
