"""
api/routers/dashboard.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Dashboard aggregator endpoints (auth required).

GET /dashboard/overview - one-shot summary of the job-seeker journey:
                          today's agenda (scheduled interviews), what moved on
                          the tracker lately, profile status, funnel metrics,
                          interview feedback and resume-vs-target-role skill
                          focus.

The overview page is a plan for the day, not a report on the account, so the
dated and actionable parts of this payload (``agenda``, ``recent_activity``,
``job_matches``) lead the page and the counts sit underneath them.

The overview page pairs this with GET /agent/briefing, which owns the
next-best action and everything else that is a *decision* rather than a
report. The two are fetched in parallel and rendered as one page; keeping
them as separate endpoints means the briefing cards paint immediately
instead of waiting on this payload's LLM-backed skill focus.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import get_current_user
from services.applications import day_agenda, recent_activity, tracker_analytics
from services.career import target_role_readiness
from services.interviews import interview_insights
from services.job_matching import compute_daily_matches_for_user, get_daily_matches_for_user
from services.profiles import get_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


#: user_id -> the date an on-demand daily-match computation was last attempted.
#: Process-local and deliberately cheap: the only cost of losing it on restart
#: is one extra recompute.
_MATCH_ATTEMPTS: dict[str, date] = {}


def _matches_attempted_today(user_id: str) -> bool:
    return _MATCH_ATTEMPTS.get(user_id) == date.today()


def _mark_matches_attempted(user_id: str) -> None:
    if len(_MATCH_ATTEMPTS) > 5000:      # bound the map on a busy day
        _MATCH_ATTEMPTS.clear()
    _MATCH_ATTEMPTS[user_id] = date.today()


@router.get("/overview")
async def overview(user_id: CurrentUserId) -> dict[str, Any]:
    """Aggregate the full job-seeker journey into one dashboard payload."""
    # These four are independent reads. Awaiting them in sequence made the
    # page's latency their sum instead of their max.
    profile, analytics, agenda, activity = await asyncio.gather(
        get_profile(user_id=user_id),
        tracker_analytics(user_id=user_id),
        day_agenda(user_id=user_id),
        recent_activity(user_id=user_id),
    )

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

    # Today's target-role job matches. The daily scheduler (see
    # services/job_matching.py) normally precomputes and caches these; a
    # cache miss (e.g. a user who just set a target role today) is filled in
    # here on demand so the card isn't empty until tomorrow's run.
    try:
        job_matches = await get_daily_matches_for_user(user_id=user_id)
        if job_matches is None and not _matches_attempted_today(user_id):
            # Only once per user per day. ``compute_daily_matches_for_user``
            # writes no rows when it finds nothing, so "no rows for today" is
            # indistinguishable from "never ran" — which meant a user whose
            # target roles matched nothing re-ran three live job searches on
            # every single dashboard load.
            _mark_matches_attempted(user_id)
            job_matches = await compute_daily_matches_for_user(user_id=user_id)
    except Exception:
        logger.warning("Daily job matches unavailable for user %s", user_id, exc_info=True)
        job_matches = None

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
        "agenda": agenda,
        "recent_activity": activity,
        "skills_focus": skills_focus,
        "interviews": interviews,
        "job_matches": job_matches,
    }
