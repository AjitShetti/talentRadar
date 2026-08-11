"""
api/routers/career.py
~~~~~~~~~~~~~~~~~~~~~~~
Career Coach endpoints (auth required).

GET  /career/weaknesses   - identify skill gaps from profile/resume
POST /career/recommend    - generate + persist learning tasks
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import get_current_user
from services.career import identify_weaknesses, recommend_learning

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/career", tags=["Career Coach"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


class RecommendRequest(BaseModel):
    persist: bool = True


@router.get("/weaknesses")
async def weaknesses(user_id: CurrentUserId):
    """Identify skill weaknesses for the current user."""
    try:
        return await identify_weaknesses(user_id=user_id)
    except Exception as exc:
        logger.error("Weakness identification failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed: {exc}") from exc


@router.post("/recommend")
async def recommend(user_id: CurrentUserId, body: RecommendRequest):
    """Generate (and optionally persist) learning recommendations from gaps."""
    try:
        return await recommend_learning(user_id=user_id, persist=body.persist)
    except Exception as exc:
        logger.error("Learning recommendation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed: {exc}") from exc
