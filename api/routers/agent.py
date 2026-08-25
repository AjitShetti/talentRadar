"""
api/routers/agent.py
~~~~~~~~~~~~~~~~~~~~~~~
Career Copilot endpoints (auth required).

GET    /agent/next-action    - next-best-action recommendation from live state
GET    /agent/briefing       - today's briefing: the copilot page's default view
POST   /agent/chat           - one conversational turn through the agent graph
POST   /agent/cards/dismiss  - hide or snooze a briefing card
GET    /agent/memories       - list agent memories (optional type filter)
POST   /agent/memories       - persist a memory entry
DELETE /agent/memories/{id}  - forget a memory
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import get_current_user
from api.schemas.agent_schemas import (
    BriefingResponseSchema,
    ChatRequestSchema,
    ChatResponseSchema,
    DismissRequestSchema,
    RememberRequestSchema,
)
from services.agent_memory import (
    dismiss_card,
    forget,
    get_memories,
    recommend_next_action,
    remember,
)
from services.copilot import STARTERS, build_briefing, chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Career Copilot"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


@router.get("/next-action")
async def next_action(user_id: CurrentUserId) -> dict[str, Any]:
    """Proactive next-best-action recommendation."""
    result = await recommend_next_action(user_id=user_id)
    return result


@router.get("/briefing", response_model=BriefingResponseSchema)
async def briefing(user_id: CurrentUserId) -> dict[str, Any]:
    """
    Today's briefing — the cards the copilot page opens with.

    Deterministic: computed from the user's applications, interview sessions
    and skill gaps, with dismissed/snoozed cards filtered out.
    """
    try:
        return await build_briefing(user_id=user_id)
    except Exception as exc:
        logger.error("Briefing failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not build your briefing: {exc}") from exc


@router.post("/chat", response_model=ChatResponseSchema)
async def copilot_chat(user_id: CurrentUserId, body: ChatRequestSchema) -> dict[str, Any]:
    """Ask the copilot a question; routed by intent through the agent graph."""
    result = await chat(
        user_id=user_id,
        message=body.message,
        history=[turn.model_dump() for turn in body.history],
    )
    return result


@router.get("/starters")
async def starters() -> dict[str, list[str]]:
    """Suggested opening questions, one per supported intent."""
    return {"starters": STARTERS}


@router.post("/cards/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(user_id: CurrentUserId, body: DismissRequestSchema) -> None:
    """Hide a briefing card, permanently or for ``snooze_days``."""
    result = await dismiss_card(
        user_id=user_id,
        card_id=body.card_id,
        snooze_days=body.snooze_days,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="Could not dismiss that card")


@router.get("/memories")
async def list_memories(
    user_id: CurrentUserId,
    memory_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List the current user's agent memories."""
    memories = await get_memories(user_id=user_id, memory_type=memory_type, limit=limit)
    return {"memories": memories, "count": len(memories)}


@router.post("/memories")
async def create_memory(user_id: CurrentUserId, body: RememberRequestSchema) -> dict[str, Any]:
    """Persist a memory entry for the personal agent."""
    result = await remember(
        user_id=user_id,
        memory_type=body.memory_type,
        content=body.content,
        metadata=body.metadata,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="Could not store memory")
    return result


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(user_id: CurrentUserId, memory_id: str) -> None:
    """Forget a memory. The copilot stops using it immediately."""
    if not await forget(user_id=user_id, memory_id=memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
