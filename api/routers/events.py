"""
api/routers/events.py
~~~~~~~~~~~~~~~~~~~~~
``/api/v1/events``: the Bengaluru tech-events tracker.

Thin: it validates the request and delegates to ``services/events/read.py`` (reads),
``services/events/user.py`` (saves, link import, manual events, people met) and
``services/events/met.py`` (the Outreach contract). No read here ever reaches an outside
source; the one write that does, ``POST /import``, requests only what a strictly matched
Eventbrite or GDG link names. Every route needs a signed-in user, every write is scoped to
that user, and another user's event, save or person is a 404 like an unknown id.

Route order matters. FastAPI matches in registration order, so fixed paths such as
``/people-met`` must be declared above the ``/{event_id}`` routes, or "people-met" is taken
for an event id.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.dependencies import get_session
from api.schemas.event_schemas import (
    EventContactIn,
    EventContactOut,
    EventContactPatch,
    EventDetail,
    EventMy,
    EventPage,
    ImportedEvent,
    ImportIn,
    ManualEventIn,
    MetAtEventOut,
    NeedsDetails,
    SaveIn,
)
from domain.event_relevance import FORMATS, TOPICS
from domain.event_time import Window
from services.events import met, read
from services.events import user as event_user

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(get_current_user)])

_MIN_YEAR = 2000
_MAX_YEAR = 2100


def _now() -> datetime:
    """The clock. A module-level function so a test can pin it."""
    return datetime.now(UTC)


async def _current_user_id(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> str:
    """The signed-in user's id; a token whose subject is not a user id is refused."""
    try:
        return str(uuid.UUID(str(user.get("sub"))))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: bad subject"
        ) from None


CurrentUserId = Annotated[str, Depends(_current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _check_date(name: str, value: date | None) -> None:
    if value is not None and not _MIN_YEAR <= value.year <= _MAX_YEAR:
        raise HTTPException(
            status_code=422, detail=f"{name} must be between {_MIN_YEAR} and {_MAX_YEAR}"
        )


def _check_vocabulary(topic: str | None, event_format: str | None) -> None:
    """422 for a topic or format outside the closed vocabulary, naming what is allowed."""
    for field, value, allowed in (("topic", topic, TOPICS), ("format", event_format, FORMATS)):
        if value and value not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown {field} {value!r}. Expected one of: {', '.join(allowed)}",
            )


@router.get("", response_model=EventPage)
async def list_events(
    user_id: CurrentUserId,
    session: Session,
    when: Annotated[
        Window | None,
        Query(description="today | week | weekend | month, in IST. Overrides from/to."),
    ] = None,
    start: Annotated[
        date | None, Query(alias="from", description="First day, an IST date. Default: today.")
    ] = None,
    end: Annotated[
        date | None,
        Query(
            alias="to",
            description="Last day, inclusive, an IST date. Default: a 30-day window from `from`.",
        ),
    ] = None,
    mode: Annotated[
        Literal["in_person", "online", "any"] | None,
        Query(description="Default: in_person, or any for saved=saved|going."),
    ] = None,
    topic: Annotated[str | None, Query(max_length=32)] = None,
    event_format: Annotated[str | None, Query(alias="format", max_length=16)] = None,
    hide_paid: Annotated[bool, Query()] = False,
    saved: Annotated[Literal["all", "saved", "going"], Query()] = "all",
    company_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100, description="Title or organiser")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=read.MAX_OFFSET)] = 0,
) -> dict[str, Any]:
    """Upcoming listed events, soonest first, with the ledger counts and the sources strip."""
    _check_date("from", start)
    _check_date("to", end)
    if start is not None and end is not None and end < start:
        raise HTTPException(status_code=422, detail="to must not be before from")
    _check_vocabulary(topic, event_format)
    return await read.list_events(
        session,
        user_id,
        read.EventQuery(
            when=when,
            start=start,
            end=end,
            mode=mode,
            topic=topic,
            format=event_format,
            hide_paid=hide_paid,
            saved=saved,
            company_id=str(company_id) if company_id else None,
            q=q,
            limit=limit,
            offset=offset,
        ),
        now=_now(),
    )


# Fixed paths (``/people-met``, ``/import``, ``/manual``, ``/people/{pid}``) are registered
# here, above the ``/{event_id}`` routes.


def _check_instant(name: str, value: datetime | None) -> None:
    """422 for a time no database or calendar should be asked about (and that cannot be a UTC)."""
    if value is not None and not _MIN_YEAR <= value.year <= _MAX_YEAR:
        raise HTTPException(
            status_code=422, detail=f"{name} must be between {_MIN_YEAR} and {_MAX_YEAR}"
        )


@router.get("/people-met", response_model=list[MetAtEventOut])
async def get_people_met(
    user_id: CurrentUserId,
    session: Session,
    since: Annotated[
        datetime | None,
        Query(description="ISO-8601. Only people added at or after this instant."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=met.MAX_LIMIT)] = 100,
) -> list[dict[str, Any]]:
    """The caller's people met, oldest first: the contract Outreach reads (spec section 8)."""
    _check_instant("since", since)
    people = await met.people_met(session, user_id, since=since, limit=limit)
    return [{**asdict(person), "context_line": met.context_line(person)} for person in people]


@router.post("/import", response_model=ImportedEvent | NeedsDetails)
async def import_event(body: ImportIn, user_id: CurrentUserId, session: Session) -> dict[str, Any]:
    """Add an event by link: ``{event}``, or ``{needs_details, reason}`` for the manual form.

    Ten a day per user. The link is matched against strict patterns and a request is built
    only from the id it names; any other link is never fetched.
    """
    return await event_user.import_link(session, user_id, body.url, now=_now())


@router.post("/manual", response_model=EventDetail, status_code=status.HTTP_201_CREATED)
async def add_manual_event(
    body: ManualEventIn, user_id: CurrentUserId, session: Session
) -> dict[str, Any]:
    """An event the caller adds by hand: private to them, and saved as going."""
    try:
        created = await event_user.create_manual(session, user_id, body)
    except event_user.LimitReachedError as limit:
        raise HTTPException(status_code=422, detail=str(limit)) from None
    detail = await read.event_detail(session, user_id, created.id)
    if detail is None:  # unreachable: the caller owns it
        raise HTTPException(status_code=500, detail="Event could not be read back")
    return detail


@router.patch("/people/{person_id}", response_model=EventContactOut)
async def update_person(
    person_id: uuid.UUID, body: EventContactPatch, user_id: CurrentUserId, session: Session
) -> dict[str, Any]:
    """Change a person the caller met. 404 unless the person is theirs."""
    contact = await event_user.update_contact(session, user_id, person_id, body, now=_now())
    if contact is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return read.contact_out(contact)


@router.delete("/people/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(person_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Response:
    """Remove a person the caller met. 404 unless the person is theirs."""
    if not await event_user.delete_contact(session, user_id, person_id):
        raise HTTPException(status_code=404, detail="Person not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{event_id}", response_model=EventDetail)
async def get_event(
    event_id: uuid.UUID, user_id: CurrentUserId, session: Session
) -> dict[str, Any]:
    """One event. 404 for an unknown id and for another user's private event."""
    detail = await read.event_detail(session, user_id, event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return detail


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Response:
    """Remove an event the caller added by hand, with their save and people at it.

    404 for every other event (a source's, another user's) and for an unknown id, with the same
    body as ``GET /{event_id}``.
    """
    if not await event_user.delete_manual(session, user_id, event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{event_id}/ics")
async def get_event_ics(event_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Response:
    """The event as a one-event calendar file. Nothing is added to a calendar for the user."""
    found = await read.event_ics(session, user_id, event_id, now=_now())
    if found is None:
        raise HTTPException(status_code=404, detail="Event not found")
    filename, body = found
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{event_id}/save", response_model=EventMy)
async def save_event(
    event_id: uuid.UUID, body: SaveIn, user_id: CurrentUserId, session: Session
) -> dict[str, Any]:
    """Save the event or mark it going (an upsert). 404 for an event the caller cannot see."""
    save = await event_user.save_event(session, user_id, event_id, body.status, body.note)
    if save is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": save.status, "note": save.note}


@router.delete("/{event_id}/save", status_code=status.HTTP_204_NO_CONTENT)
async def unsave_event(event_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Response:
    """Remove the caller's save. 404 when they have none."""
    if not await event_user.unsave_event(session, user_id, event_id):
        raise HTTPException(status_code=404, detail="Save not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{event_id}/people", response_model=list[EventContactOut])
async def list_people(
    event_id: uuid.UUID, user_id: CurrentUserId, session: Session
) -> list[dict[str, Any]]:
    """The caller's own people at this event; nobody else's."""
    contacts = await event_user.list_contacts(session, user_id, event_id)
    if contacts is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return [read.contact_out(contact) for contact in contacts]


@router.post(
    "/{event_id}/people", response_model=EventContactOut, status_code=status.HTTP_201_CREATED
)
async def add_person(
    event_id: uuid.UUID, body: EventContactIn, user_id: CurrentUserId, session: Session
) -> dict[str, Any]:
    """Note someone met at an event. 409 until it has started; marks the caller as going."""
    try:
        contact = await event_user.add_contact(session, user_id, event_id, body, now=_now())
    except event_user.NotStartedError:
        raise HTTPException(
            status_code=409, detail="People can be added once the event has started"
        ) from None
    except event_user.LimitReachedError as limit:
        raise HTTPException(status_code=422, detail=str(limit)) from None
    if contact is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return read.contact_out(contact)
