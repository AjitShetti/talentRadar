"""
api/routers/dashboard.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Dashboard aggregator endpoints (auth required).

GET /dashboard/overview - one-shot summary of the job-seeker journey:
                          profile status, funnel metrics, interview feedback
                          and resume-vs-target-role skill focus.

The overview page pairs this with GET /agent/briefing, which owns the
next-best action and everything else that is a *decision* rather than a
report. The two are fetched in parallel and rendered as one page; keeping
them as separate endpoints means the briefing cards paint immediately
instead of waiting on this payload's LLM-backed skill focus.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import get_current_user
from services.applications import tracker_analytics
from services.career import target_role_readiness
from services.interviews import interview_insights
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
async def overview(user_id: CurrentUserId) -> dict[str, Any]:
    """Aggregate the full job-seeker journey into one dashboard payload."""
    profile = await get_profile(user_id=user_id)
    analytics = await tracker_analytics(user_id=user_id)

    # Resume vs target roles. Samples the market and calls the LLM, so it is
    # additive like interview insights -- a failure must not blank the page.
    try:
        skills_focus = await target_role_readiness(user_id=user_id, limit=3)
    except Exception:
        logger.warning("Role readiness unavailable for user %s", user_id, exc_info=True)
        skills_focus = None

    # Interview feedback is additive — a failure here must not blank the
    # rest of the dashboard.
    try:
        interviews = await interview_insights(user_id=user_id)
    except Exception:
        logger.warning("Interview insights unavailable for user %s", user_id, exc_info=True)
        interviews = None

    return {
        "profile": {
            "exists": bool(profile),
            "onboarding_completed": bool(profile and profile.get("onboarding_completed")),
            "full_name": profile.get("full_name") if profile else None,
            "target_roles": profile.get("target_roles") if profile else None,
        },
        "analytics": {
            **analytics.get("metrics", {}),
            "total_applications": analytics.get("total_applications", 0),
        },
        "funnel": analytics.get("funnel", []),
        "skills_focus": skills_focus,
        "interviews": interviews,
    }
