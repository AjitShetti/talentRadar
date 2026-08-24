"""
api/routers/profile.py
~~~~~~~~~~~~~~~~~~~~~~~~
Profile / onboarding endpoints (auth required).

GET   /profile            - current user's profile
POST  /profile            - upsert onboarding fields + skills
POST  /profile/complete   - mark onboarding complete (alias for upsert)
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth import get_current_user
from services.profiles import get_or_create_profile, get_profile, upsert_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["Profile"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


class ProfileSkillSchema(BaseModel):
    name: str
    proficiency: float = Field(default=3.0, ge=0, le=5)


class ProfileUpsertSchema(BaseModel):
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    target_roles: list[str] | None = None
    target_locations: list[str] | None = None
    is_remote_preferred: bool | None = None
    target_salary_min: float | None = None
    target_salary_max: float | None = None
    salary_currency: str | None = None
    years_experience: float | None = None
    current_role: str | None = None
    career_goals: str | None = None
    active_resume_id: str | None = None
    skills: list[ProfileSkillSchema | str] | None = None


@router.get("/")
async def get_profile_endpoint(user_id: CurrentUserId):
    """Get the current user's career profile."""
    profile = await get_profile(user_id=user_id)
    if profile is None:
        return {"onboarding_completed": False, "profile": None}
    return {"onboarding_completed": profile["onboarding_completed"], "profile": profile}


@router.post("/")
async def upsert_profile_endpoint(user_id: CurrentUserId, body: ProfileUpsertSchema):
    """Create or update the current user's profile (onboarding)."""
    data: dict[str, Any] = body.model_dump(exclude_none=True)
    try:
        result = await upsert_profile(user_id=user_id, data=data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # surface a readable message instead of a bare 500
        logger.exception("Profile save failed for user %s", user_id)
        raise HTTPException(status_code=500, detail=f"Could not save profile: {exc}") from exc
    if not result:
        raise HTTPException(status_code=400, detail="Could not save profile")
    return {"onboarding_completed": result.get("onboarding_completed", False), "profile": result}


@router.post("/ensure")
async def ensure_profile(user_id: CurrentUserId):
    """Ensure a profile row exists (returns created flag)."""
    try:
        result = await get_or_create_profile(user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
