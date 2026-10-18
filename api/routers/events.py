"""
api/routers/events.py
~~~~~~~~~~~~~~~~~~~~~
``/api/v1/events``: the Bengaluru tech-events tracker's read side.

Thin: it validates the query and delegates to ``services/events/read.py``. No read here
ever reaches an outside source. Every route needs a signed-in user.

Route order matters. FastAPI matches in registration order, so fixed paths such as
``/people-met`` must be declared above the ``/{event_id}`` routes at the foot of this
file, or "people-met" is taken for an event id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.dependencies import get_session
from api.schemas.event_schemas import EventDetail, EventPage
from domain.event_relevance import FORMATS, TOPICS
from domain.event_time import Window
from services.events import read

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
        Query(alias="to", description="Last day, inclusive, an IST date. Default: 30 days on."),
    ] = None,
    mode: Annotated[Literal["in_person", "online", "any"], Query()] = "in_person",
    topic: Annotated[str | None, Query(max_length=32)] = None,
    event_format: Annotated[str | None, Query(alias="format", max_length=16)] = None,
    hide_paid: Annotated[bool, Query()] = False,
    saved: Annotated[Literal["all", "saved", "going"], Query()] = "all",
    company_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100, description="Title or organiser")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
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


@router.get("/{event_id}", response_model=EventDetail)
async def get_event(
    event_id: uuid.UUID, user_id: CurrentUserId, session: Session
) -> dict[str, Any]:
    """One event. 404 for an unknown id and for another user's private event."""
    detail = await read.event_detail(session, user_id, event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return detail


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
