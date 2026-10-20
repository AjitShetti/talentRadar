"""
services/events/sources.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The curated list of event sources, and what each one vouches for.

``data/events/sources.json`` is checked in and edited by hand (each entry carries a
``checked`` date): the Luma Discover city feed and organiser calendars, the Google
Developer Group chapters, and the Eventbrite organiser and venue ids. It is data, so
:func:`load_sources` reads it defensively: a missing or unreadable file is an empty list,
and an entry that is not usable (an id that would not build a feed address, a chapter slug
that is not a chapter page, a missing name) is skipped with a warning. The refresh steps
therefore never raise because somebody mistyped an id, and never request an address
nobody vetted: every id is checked with the same builders the fetchers use.

:func:`scope_for` turns an entry into the :class:`~domain.event_facts.SourceScope` the
relevance rules read: whether everything the source lists is in India and whether it is all
technology. Only what the list *says* is trusted: a calendar is trusted as tech when its
entry says so, a GDG chapter always, and everything else (Eventbrite) not at all.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from domain.event_facts import SourceScope, flatten
from services.events import gdg, luma

logger = logging.getLogger(__name__)

#: Anchored on this file, as ``services/company_map/hiring_list.py`` anchors its list: a
#: path relative to the working directory would load nothing under another one.
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "events" / "sources.json"

#: The checked-in file is a few kilobytes; anything near this is not it.
MAX_FILE_BYTES = 1_000_000
NAME_MAX = 100

_ID_PREFIX = {"discover": "discplace-", "calendar": "cal-"}
# ASCII digits only: str.isdigit() also accepts "٣" and "²".
_EVENTBRITE_ID = re.compile(r"[0-9]{1,32}")


@dataclass(frozen=True)
class LumaFeed:
    """One Luma iCal subscription: a Discover city page or an organiser's calendar."""

    kind: Literal["discover", "calendar"]
    id: str
    name: str  # the calendar's display name: the "<name> calendar" reason uses it verbatim
    trusted_tech: bool
    india: bool


@dataclass(frozen=True)
class GdgChapter:
    """One Google Developer Group chapter page (``gdg.community.dev/<slug>/``)."""

    slug: str
    name: str


@dataclass(frozen=True)
class SourceList:
    """Everything the refresh steps read, in the order the list gives it."""

    luma: tuple[LumaFeed, ...]
    gdg: tuple[GdgChapter, ...]
    eventbrite_organizers: tuple[str, ...]
    eventbrite_venues: tuple[str, ...]


_EMPTY = SourceList(luma=(), gdg=(), eventbrite_organizers=(), eventbrite_venues=())


# ── reading the file ─────────────────────────────────────────────────────────


def _entries(section: object, key: str) -> list[Any]:
    """``section[key]`` as a list; anything else is no entries."""
    if not isinstance(section, dict):
        return []
    entries = section.get(key)
    return entries if isinstance(entries, list) else []


def _name(entry: dict[str, Any]) -> str | None:
    value = entry.get("name")
    return flatten(value, NAME_MAX) if isinstance(value, str) else None


def _luma_feeds(section: object) -> tuple[LumaFeed, ...]:
    feeds: list[LumaFeed] = []
    seen: set[tuple[str, str]] = set()
    for kind, key in (("discover", "discover"), ("calendar", "calendars")):
        for entry in _entries(section, key):
            ident = entry.get("id") if isinstance(entry, dict) else None
            name = _name(entry) if isinstance(entry, dict) else None
            try:
                if not isinstance(ident, str) or not ident.startswith(_ID_PREFIX[kind]):
                    raise ValueError("an id of the other kind")
                luma.feed_url(kind, ident)  # type: ignore[arg-type]
            except ValueError:
                logger.warning(
                    "Events source list: skipping Luma %s entry %r", kind, str(ident)[:60]
                )
                continue
            if name is None or (kind, ident) in seen:
                if name is None:
                    logger.warning("Events source list: Luma entry %s has no name", ident)
                continue
            seen.add((kind, ident))
            feeds.append(
                LumaFeed(
                    kind=kind,  # type: ignore[arg-type]
                    id=ident,
                    name=name,
                    # Only a real JSON true counts: "true" or 1 vouches for nothing.
                    trusted_tech=entry.get("trusted_tech") is True,
                    india=entry.get("india") is True,
                )
            )
    return tuple(feeds)


def _gdg_chapters(section: object) -> tuple[GdgChapter, ...]:
    chapters: list[GdgChapter] = []
    seen: set[str] = set()
    for entry in _entries(section, "chapters"):
        slug = entry.get("slug") if isinstance(entry, dict) else None
        name = _name(entry) if isinstance(entry, dict) else None
        try:
            if not isinstance(slug, str):
                raise ValueError("not a slug")
            gdg.chapter_url(slug)
        except ValueError:
            logger.warning("Events source list: skipping GDG entry %r", str(slug)[:60])
            continue
        if name is None or slug in seen:
            if name is None:
                logger.warning("Events source list: GDG chapter %s has no name", slug)
            continue
        seen.add(slug)
        chapters.append(GdgChapter(slug=slug, name=name))
    return tuple(chapters)


def _eventbrite_ids(section: object, key: str) -> tuple[str, ...]:
    """Digit ids, as bare strings or as ``{"id": …}`` objects, once each, in order."""
    ids: list[str] = []
    for entry in _entries(section, key):
        value = entry.get("id") if isinstance(entry, dict) else entry
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value) if 0 <= value < 10**32 else None
        if isinstance(value, str) and _EVENTBRITE_ID.fullmatch(value) and value not in ids:
            ids.append(value)
        elif value is not None:
            logger.warning(
                "Events source list: skipping Eventbrite %s entry %r", key, str(value)[:40]
            )
    return tuple(ids)


def load_sources(path: Path = DEFAULT_PATH) -> SourceList:
    """The usable entries of the source list at ``path``; empty when it cannot be read."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning("Events source list %s unreadable: %s", path.name, type(exc).__name__)
        return _EMPTY
    if not isinstance(data, dict):
        return _EMPTY
    return SourceList(
        luma=_luma_feeds(data.get("luma")),
        gdg=_gdg_chapters(data.get("gdg")),
        eventbrite_organizers=_eventbrite_ids(data.get("eventbrite"), "organizers"),
        eventbrite_venues=_eventbrite_ids(data.get("eventbrite"), "venues"),
    )


# ── what a source vouches for ────────────────────────────────────────────────


def scope_for(feed: LumaFeed | GdgChapter | str) -> SourceScope:
    """The relevance scope of a list entry, or of a source named by its string.

    * A Luma feed keeps what its entry says, and its display name as ``key`` (the
      "<name> calendar" reason uses it verbatim).
    * A GDG chapter is always India and always technology: ``key`` is the chapter's name.
    * Anything else (``"eventbrite"``) vouches for nothing: India is read per event from its
      timezone, technology from its category.
    """
    if isinstance(feed, LumaFeed):
        return SourceScope(key=feed.name, india=feed.india, trusted_tech=feed.trusted_tech)
    if isinstance(feed, GdgChapter):
        return SourceScope(key=feed.name, india=True, trusted_tech=True)
    if feed == "gdg":
        return SourceScope(key="gdg", india=True, trusted_tech=True)
    return SourceScope(key=feed, india=False, trusted_tech=False)
