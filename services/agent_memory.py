"""
services/agent_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for the Personal AI Agent.

    remember()               — persist a memory entry for a user
    get_memories()           — list memories (optionally by type)
    recommend_next_action()  — compute the next-best-action from the user's
                               live state (profile, applications, interviews,
                               weaknesses) — LLM used only for phrasing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from storage.database import AsyncSessionLocal
from storage.models import AgentMemory
from services.base import parse_uuid

logger = logging.getLogger(__name__)


async def remember(
    *,
    user_id: str,
    memory_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    ttl_days: int | None = None,
) -> dict[str, Any] | None:
    """Persist a memory entry for the personal agent."""
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return None
        memory = AgentMemory(
            user_id=user_uuid,
            memory_type=memory_type,
            content=content,
            extra_metadata=metadata,
            expires_at=(
                datetime.now(tz=timezone.utc).replace(
                    microsecond=0
                ) + _td(days=ttl_days)
                if ttl_days
                else None
            ),
        )
        session.add(memory)
        await session.flush()
        return {
            "id": str(memory.id),
            "memory_type": memory.memory_type,
            "content": memory.content,
            "metadata": memory.extra_metadata,
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
        }
    finally:
        await session.close()


async def get_memories(
    *,
    user_id: str,
    memory_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List a user's memories, optionally filtered by type."""
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return []
        stmt = (
            select(AgentMemory)
            .where(AgentMemory.user_id == user_uuid)
            .order_by(AgentMemory.created_at.desc())
            .limit(limit)
        )
        if memory_type:
            stmt = stmt.where(AgentMemory.memory_type == memory_type)
        result = await session.execute(stmt)
        return [
            {
                "id": str(m.id),
                "memory_type": m.memory_type,
                "content": m.content,
                "metadata": m.extra_metadata,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in result.scalars().all()
        ]
    finally:
        await session.close()


async def recommend_next_action(
    *,
    user_id: str,
) -> dict[str, Any]:
    """
    Compute the next-best-action recommendation for a user based on their
    live state — this is the deterministic brain of the Personal Agent.

    Decision logic (deterministic, LLM only for the summary text):
      1. No profile / onboarding incomplete   → "Complete onboarding"
      2. No applications                      → "Discover jobs to apply"
      3. Has saved but not applied            → "Apply to saved jobs"
      4. Applied but no interview             → "Practice interviews / prepare"
      5. Has interviews scheduled             → "Complete interview prep plan"
      6. Has weaknesses                       → "Follow learning plan"
      7. Otherwise                            → "Track market trends"
    """
    from services.applications import tracker_analytics
    from services.career import identify_weaknesses
    from services.companies import get_company  # noqa: F401  (imports are cheap)
    from services.profiles import get_profile

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return {"recommendation": "Please log in to continue.", "reason": "auth"}

    profile = await get_profile(user_id=user_id)
    analytics = await tracker_analytics(user_id=user_id)
    total = analytics.get("total_applications", 0)
    funnel = {f["stage"]: f["count"] for f in analytics.get("funnel", [])}
    saved = funnel.get("saved", 0)
    applied = funnel.get("applied", 0)
    interviews = funnel.get("interview", 0)

    # Determine the deterministic next action
    if not profile:
        action = "complete_onboarding"
        title = "Complete your profile"
        detail = "Add your resume and target roles so TalentRadar can rank jobs for you."
        href = "/settings"
    elif total == 0:
        action = "discover_jobs"
        title = "Discover matching jobs"
        detail = "You have no applications yet. Start by finding jobs that match your profile."
        href = "/search"
    elif saved > 0:
        action = "apply_saved"
        title = "Apply to your saved jobs"
        detail = f"You have {saved} saved job(s). Tailor a resume and apply to move them forward."
        href = "/applications"
    elif applied > 0 and interviews == 0:
        action = "prepare_interview"
        title = "Prepare for interviews"
        detail = "You've applied but have no interviews yet. Use the Interview Lab to stay sharp."
        href = "/interview"
    elif interviews > 0:
        action = "interview_prep"
        title = "Complete your interview prep plan"
        detail = "You have interviews scheduled. Review the personalised prep plan."
        href = "/interview/prep"
    else:
        weaknesses = await identify_weaknesses(user_id=user_id, limit=5)
        if weaknesses.get("weaknesses"):
            action = "improve_skills"
            title = "Close your skill gaps"
            detail = "Follow your learning plan to address your weakest skills."
            href = "/career"
        else:
            action = "track_trends"
            title = "Explore market trends"
            detail = "Stay ahead by watching hiring demand and salary trends."
            href = "/trends"

    return {
        "recommendation": title,
        "detail": detail,
        "action": action,
        "href": href,
        "context": {
            "total_applications": total,
            "saved": saved,
            "applied": applied,
            "interviews": interviews,
            "onboarding_completed": bool(profile and profile.get("onboarding_completed")),
        },
    }


def _td(days: int):
    from datetime import timedelta
    return timedelta(days=days)
