"""
api/routers/practice.py
~~~~~~~~~~~~~~~~~~~~~~~
Practice endpoints (auth required). Thin: validation and delegation to ``services/practice``.

GET    /practice/today          - today's question, computed (writes nothing)
POST   /practice/today/done     - mark it done, optionally with a rating
PATCH  /practice/today/feel     - rate the question marked done
DELETE /practice/today/done     - undo a misclicked done (today only)
POST   /practice/today/swap     - a different question; one a day, then 409
GET    /practice/history        - the calendar and its counts (weeks 1-26)

The services raise ``PracticeConflict``, ``PracticeInvalid`` and ``PracticeNotFound``; each
becomes the status below, carrying a message written for the person using the page.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import get_current_user
from api.schemas.practice_schemas import (
    DoneRequestSchema,
    FeelRequestSchema,
    PracticeHistorySchema,
    PracticeTodaySchema,
)
from services.practice import daily
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound

router = APIRouter(prefix="/practice", tags=["Practice"])


async def get_current_user_id(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim"
        )
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


async def _answer(call: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Await a Practice service call, turning its errors into HTTP statuses."""
    try:
        return await call
    except PracticeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PracticeInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/today", response_model=PracticeTodaySchema)
async def get_today(user_id: CurrentUserId) -> dict[str, Any]:
    """Today's question for the signed-in user."""
    return await _answer(daily.today(user_id))


@router.post("/today/done", response_model=PracticeTodaySchema)
async def mark_done(
    user_id: CurrentUserId, body: DoneRequestSchema | None = None
) -> dict[str, Any]:
    """Mark today's question done. Repeating it changes nothing."""
    return await _answer(daily.mark_done(user_id, body.feel if body else None))


@router.patch("/today/feel", response_model=PracticeTodaySchema)
async def set_feel(user_id: CurrentUserId, body: FeelRequestSchema) -> dict[str, Any]:
    """The one-tap rating after "Done"."""
    return await _answer(daily.set_feel(user_id, body.feel))


@router.delete("/today/done", response_model=PracticeTodaySchema)
async def undo_done(user_id: CurrentUserId) -> dict[str, Any]:
    """Undo, for today only."""
    return await _answer(daily.undo_done(user_id))


@router.post("/today/swap", response_model=PracticeTodaySchema)
async def swap(user_id: CurrentUserId) -> dict[str, Any]:
    """A different question for today. 409 once it has been used."""
    return await _answer(daily.swap(user_id))


@router.get("/history", response_model=PracticeHistorySchema)
async def get_history(
    user_id: CurrentUserId, weeks: Annotated[int, Query(ge=1, le=26)] = 12
) -> dict[str, Any]:
    """The calendar of practised days and three neutral counts."""
    return await _answer(daily.history(user_id, weeks=weeks))
