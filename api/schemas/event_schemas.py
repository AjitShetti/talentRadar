"""
api/schemas/event_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Response models for the events read API (``api/routers/events.py``).

``EventCard`` is one row of the list, ``EventDetail`` the drawer, ``EventPage`` the
list response. The frontend types in ``frontend/lib/api.ts`` copy these key for key, so
a field added here is added there. Times are aware UTC; the client formats them in IST.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SaveStatus = Literal["saved", "going"]


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
