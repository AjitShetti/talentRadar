"""
api/schemas/event_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Request and response models for the events API (``api/routers/events.py``).

``EventCard`` is one row of the list, ``EventDetail`` the drawer, ``EventPage`` the
list response. The frontend types in ``frontend/lib/api.ts`` copy these key for key, so
a field added here is added there. Times are aware UTC; the client formats them in IST.

The request models (``SaveIn``, ``ImportIn``, ``ManualEventIn``, ``EventContactIn``,
``EventContactPatch``) are where what a person typed is made safe to store: control
characters and NUL are removed, a single-line field is one line, a link is plain ASCII
``https`` on the host it is meant for, and a time carries its UTC offset. Nothing a request
model accepts is ever fetched.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

SaveStatus = Literal["saved", "going"]

_MIN_YEAR = 2000
_MAX_YEAR = 2100
_LINK_MAX = 512
_EMAIL_MAX = 254
_CR, _LF = chr(13), chr(10)
_CRLF = _CR + _LF

# Everything below space and DEL except tab, newline, carriage return and form/vertical feed,
# which ``str.split`` treats as whitespace. NUL is the one a database refuses outright.
_CONTROL = re.compile("[" + "".join(map(chr, (*range(9), *range(14, 32), 127))) + "]")
_VISIBLE_ASCII = re.compile(r"[!-~]+")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

#: What a host is made of. The host is already lower-cased by ``urlsplit``. A denylist of
#: separators would miss the next one: ``evil.io\.linkedin.com`` parses here as a
#: ``linkedin.com`` subdomain and is opened by a browser as ``evil.io``, so only letters,
#: digits, dots and hyphens are let through, in labels: no empty one (``a..b``) and no dot at
#: either end (``.linkedin.com``, ``example.test.``).
_HOST_PATTERN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")

#: The host each profile field must be on: that host or one of its subdomains.
_PROFILE_HOSTS: dict[str, tuple[str, ...]] = {
    "linkedin_url": ("linkedin.com",),
    "x_url": ("x.com", "twitter.com"),
    "github_url": ("github.com",),
}


def _line(value: str) -> str:
    """One line of text: control characters gone, any run of whitespace one space."""
    return " ".join(_CONTROL.sub("", value).split())


def _paragraphs(value: str) -> str:
    """Free text that may span lines: control characters gone, every line end a plain LF."""
    text = _CONTROL.sub("", value)
    return text.replace(_CRLF, _LF).replace(_CR, _LF).strip()


def _https_link(value: str, hosts: tuple[str, ...] | None = None) -> str:
    """``value`` if it is a plain ``https`` link, and on one of ``hosts`` when they are given.

    Visible ASCII only, no userinfo and no port, a host of letters, digits, dots and hyphens
    only, and a host matched exactly (or as a subdomain of a listed one), so
    ``linkedin.com.evil.io``, ``evil.io/linkedin.com`` and a backslash after ``evil.io`` are all
    refused. ``ValueError`` says what is wrong without echoing the link.
    """
    text = value.strip()
    if not text:
        raise ValueError("a link is empty")
    if len(text) > _LINK_MAX * 2 or _VISIBLE_ASCII.fullmatch(text) is None:
        raise ValueError("a link must be plain visible ASCII")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError("not a valid link") from None
    host = parts.hostname
    if parts.scheme != "https" or host is None:
        raise ValueError("a link must start with https://")
    if parts.username or parts.password or port or parts.netloc.lower() != host:
        raise ValueError("a link must not carry a login or a port")
    if _HOST_PATTERN.fullmatch(host) is None:
        raise ValueError("a link's host must be a plain domain name")
    if hosts is not None and not any(host == h or host.endswith(f".{h}") for h in hosts):
        raise ValueError(f"must be a link on {' or '.join(hosts)}")
    return text


def _aware(value: datetime) -> datetime:
    """A time that states its UTC offset, in 2000-2100; a bare local time is a 422, not a guess."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("needs a UTC offset, e.g. 2026-10-20T18:30:00+05:30")
    if not _MIN_YEAR <= value.year <= _MAX_YEAR:
        raise ValueError(f"must be between {_MIN_YEAR} and {_MAX_YEAR}")
    return value


class EventCompanyOut(BaseModel):
    """A company on the map that the event is linked to."""

    id: str
    name: str


class EventDetailCompany(EventCompanyOut):
    """The same, with how the event names it."""

    role: Literal["organiser", "host"]


class _EventBase(BaseModel):
    """The fields a card and a detail share."""

    id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = Field(None, description="Null when unstated; readers assume +2 h")
    is_online: bool
    venue_name: str | None = None
    area: str | None = None
    lat: float | None = None
    lng: float | None = None
    geo_precision: str | None = Field(None, description="exact | area | none")
    is_free: bool | None = Field(None, description="Null when the source did not say")
    organiser_name: str | None = None
    format: str | None = None
    topics: list[str] = Field(default_factory=list)
    source: str = Field(description="luma | gdg | eventbrite | user")
    url: str | None = Field(None, description="Null for a manual event with no link")
    my_status: SaveStatus | None = None
    is_cancelled: bool = False


class EventCard(_EventBase):
    """One event in the list."""

    companies: list[EventCompanyOut] = Field(default_factory=list)


class EventMy(BaseModel):
    """The caller's own side of an event."""

    status: SaveStatus
    note: str | None = None


class EventAlsoOn(BaseModel):
    """The same event on another source."""

    source: str
    url: str


class EventContactOut(BaseModel):
    """A person the caller met at an event. Private to the caller."""

    id: str
    event_id: str
    name: str
    title: str | None = None
    company_name: str | None = None
    company_id: str | None = None
    linkedin_url: str | None = None
    x_url: str | None = None
    github_url: str | None = None
    email: str | None = None
    note: str | None = None
    created_at: datetime | None = None
    linkedin_search_url: str | None = Field(
        None, description="A LinkedIn people search, built here, when no profile URL was typed"
    )


class EventDetail(_EventBase):
    """The card, plus what the drawer shows."""

    companies: list[EventDetailCompany] = Field(default_factory=list)
    snippet: str | None = Field(None, description="At most 200 characters")
    reasons: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    also_on: list[EventAlsoOn] = Field(default_factory=list)
    my: EventMy | None = None
    people: list[EventContactOut] = Field(default_factory=list)


class EventCounts(BaseModel):
    """The ledger line. Independent of the list's filters."""

    upcoming: int
    this_week: int
    going: int


class EventSourceStatus(BaseModel):
    """When a source was last read, and how it is doing."""

    source: Literal["luma", "gdg", "eventbrite"]
    last_read_at: str | None = Field(None, description="ISO-8601 UTC, or null")
    status: str = Field(description="unknown | healthy | degraded | failing | off")


class EventPage(BaseModel):
    """``GET /events``."""

    total: int
    events: list[EventCard]
    counts: EventCounts
    sources: list[EventSourceStatus]


# ═════════════════════════════════════════════════════════════════════════════
# what a person sends
# ═════════════════════════════════════════════════════════════════════════════


class SaveIn(BaseModel):
    """``PUT /events/{id}/save``. An omitted ``note`` leaves the note as it was; an empty one clears it."""

    status: SaveStatus
    note: str | None = Field(None, max_length=2000)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return None if value is None else _paragraphs(value)


class ImportIn(BaseModel):
    """``POST /events/import``. Any string: one that matches no known event page is not an error."""

    url: str = Field(min_length=1, max_length=8192)


class ManualEventIn(BaseModel):
    """``POST /events/manual``: an event the user adds by hand. Private to them."""

    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime | None = None
    venue_name: str | None = Field(None, max_length=200)
    is_online: bool = False
    url: str | None = Field(None, max_length=1024)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        cleaned = _line(value)
        if not cleaned:
            raise ValueError("a title is required")
        return cleaned

    @field_validator("venue_name")
    @classmethod
    def _venue(cls, value: str | None) -> str | None:
        return (_line(value) or None) if value is not None else None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _times(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @field_validator("url")
    @classmethod
    def _link(cls, value: str | None) -> str | None:
        return _https_link(value) if value is not None and value.strip() else None

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> ManualEventIn:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class _ContactFields(BaseModel):
    """The optional fields of a person met, shared by the create and the patch bodies."""

    title: str | None = Field(None, max_length=200)
    company_name: str | None = Field(None, max_length=200)
    linkedin_url: str | None = Field(None, max_length=_LINK_MAX)
    x_url: str | None = Field(None, max_length=_LINK_MAX)
    github_url: str | None = Field(None, max_length=_LINK_MAX)
    email: str | None = Field(None, max_length=_EMAIL_MAX)
    note: str | None = Field(None, max_length=2000)

    @field_validator("title", "company_name")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        return (_line(value) or None) if value is not None else None

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return (_paragraphs(value) or None) if value is not None else None

    @field_validator("linkedin_url", "x_url", "github_url")
    @classmethod
    def _profile_link(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None or not value.strip():
            return None
        return _https_link(value, _PROFILE_HOSTS[str(info.field_name)])

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        text = value.strip()
        if _EMAIL.fullmatch(text) is None or _CONTROL.search(text):
            raise ValueError("not an email address")
        return text


class EventContactIn(_ContactFields):
    """``POST /events/{id}/people``. Everything is what the user typed; nothing is looked up."""

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = _line(value)
        if not cleaned:
            raise ValueError("a name is required")
        return cleaned


class EventContactPatch(_ContactFields):
    """``PATCH /events/people/{id}``. Only the fields sent change; a ``null`` clears one."""

    name: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _line(value)
        if not cleaned:
            raise ValueError("a name is required")
        return cleaned

    @model_validator(mode="after")
    def _name_is_kept(self) -> EventContactPatch:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be cleared")
        return self


# ═════════════════════════════════════════════════════════════════════════════
# what comes back
# ═════════════════════════════════════════════════════════════════════════════


class ImportedEvent(BaseModel):
    """``POST /events/import`` when the link resolved to an event."""

    event: EventCard


class NeedsDetails(BaseModel):
    """``POST /events/import`` when it did not: the client opens the manual form."""

    needs_details: Literal[True] = True
    reason: Literal[
        "unrecognised",
        "luma_not_indexed",
        "eventbrite_off",
        "not_readable",
        "daily_cap",
        "not_in_bengaluru",
    ]


class MetAtEventOut(BaseModel):
    """One person the caller met: the Outreach contract (``services/events/met.py``)."""

    source_ref: str = Field(description="event_contact:<uuid>, Outreach's idempotency key")
    name: str
    title: str | None = None
    company_name: str | None = None
    company_id: str | None = Field(None, description="A company on the map, when the name matched")
    linkedin_url: str | None = None
    x_url: str | None = None
    github_url: str | None = None
    email: str | None = Field(None, description="Only what the person gave the user")
    note: str | None = None
    event_id: str
    event_title: str
    event_url: str = Field(description="Empty for a manual event with no link")
    met_on: date = Field(description="The IST date of the event's start")
    created_at: datetime
    context_line: str = Field(
        description="The note for the Outreach touch: 'Met at <event title> on 16 Oct'"
    )
