"""
api/routers/dashboard.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Dashboard aggregator endpoints (auth required).

GET /dashboard/overview - one-shot summary of the job-seeker journey:
                          profile status, next action, funnel metrics,
                          recent interviews, learning tasks, company intel
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import get_current_user
from services.agent_memory import recommend_next_action
from services.applications import tracker_analytics
from services.career import identify_weaknesses
from services.profiles import get_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


@router.get("/overview")
async def overview(user_id: CurrentUserId):
    """Aggregate the full job-seeker journey into one dashboard payload."""
    profile = await get_profile(user_id=user_id)
    analytics = await tracker_analytics(user_id=user_id)
    recommendation = await recommend_next_action(user_id=user_id)
    weaknesses = await identify_weaknesses(user_id=user_id, limit=5)

    return {
        "profile": {
            "exists": bool(profile),
            "onboarding_completed": bool(profile and profile.get("onboarding_completed")),
            "full_name": profile.get("full_name") if profile else None,
            "target_roles": profile.get("target_roles") if profile else None,
        },
        "recommendation": recommendation,
        "analytics": analytics.get("metrics", {}),
        "funnel": analytics.get("funnel", []),
        "weaknesses": weaknesses.get("weaknesses", [])[:5],
    }
