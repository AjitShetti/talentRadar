"""
api/routers/routine.py
~~~~~~~~~~~~~~~~~~~~~~
The user's daily routine and the first-run tour (auth required).

GET   /routine                        - the blocks, today's ticks and the tour flag
PUT   /routine                        - replace the blocks
PUT   /routine/blocks/{id}/done       - tick or clear one block for today
POST  /routine/tour-seen              - stamp the tour as seen

Thin by design: a handler checks its request and calls one ``services/routine`` function,
and every route answers what ``read_routine`` would. The rules are ``domain/routine.py``;
a block that breaks one is a 422 whose ``detail`` is the sentence to show the user, so a
block's fields are read loosely here and judged there. Another user's block id is a 404,
exactly as for an id that does not exist. Nothing here sends a reminder.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.routers.profile import CurrentUserId
from domain.routine import RoutineInvalid
from services import routine as routine_service
from services.routine import RoutineBlockNotFound

router = APIRouter(prefix="/routine", tags=["Routine"])


class RoutineBlockSchema(BaseModel):
    """One block as the page sends it. Every field is checked by ``validate_blocks``."""

    id: object = None
    at: object = None
    kind: object = None
    label: object = None


class SaveRoutineSchema(BaseModel):
    blocks: list[RoutineBlockSchema]


class TickSchema(BaseModel):
    done: bool


@router.get("")
async def get_routine(user_id: CurrentUserId) -> dict[str, Any]:
    """The user's routine, today's ticks, and whether the tour is still to be shown."""
    return await routine_service.read_routine(user_id=user_id)


@router.put("")
async def save_routine(user_id: CurrentUserId, body: SaveRoutineSchema) -> dict[str, Any]:
    """Replace the routine's blocks. A refused block is a 422 carrying a sentence."""
    try:
        return await routine_service.save_routine(
            user_id=user_id, blocks=[block.model_dump() for block in body.blocks]
        )
    except RoutineInvalid as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.put("/blocks/{block_id}/done")
async def tick_block(user_id: CurrentUserId, block_id: str, body: TickSchema) -> dict[str, Any]:
    """Tick (``done: true``) or clear one block for today."""
    try:
        return await routine_service.set_tick(user_id=user_id, block_id=block_id, done=body.done)
    except RoutineBlockNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="That block is not in your routine."
        ) from exc


@router.post("/tour-seen")
async def tour_seen(user_id: CurrentUserId) -> dict[str, Any]:
    """Stamp the first-run tour as seen. A second call keeps the first stamp."""
    return await routine_service.mark_tour_seen(user_id=user_id)
