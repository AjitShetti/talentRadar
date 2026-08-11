"""
api/routers/agent.py
~~~~~~~~~~~~~~~~~~~~~~~
Personal AI Agent endpoints (auth required).

GET  /agent/next-action   - next-best-action recommendation from live state
GET  /agent/memories      - list agent memories (optional type filter)
POST /agent/memories      - persist a memory entry
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import get_current_user
from services.agent_memory import get_memories, recommend_next_action, remember

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Personal AI Agent"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


class RememberRequest(BaseModel):
    memory_type: str = Field(..., description="e.g. preference | goal | note | application")
    content: str
    metadata: dict | None = None


@router.get("/next-action")
async def next_action(user_id: CurrentUserId):
    """Proactive next-best-action recommendation."""
    result = await recommend_next_action(user_id=user_id)
    return result


@router.get("/memories")
async def list_memories(
    user_id: CurrentUserId,
    memory_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List the current user's agent memories."""
    return {
        "memories": await get_memories(user_id=user_id, memory_type=memory_type, limit=limit),
        "count": len(await get_memories(user_id=user_id, memory_type=memory_type, limit=limit)),
    }


@router.post("/memories")
async def create_memory(user_id: CurrentUserId, body: RememberRequest):
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
