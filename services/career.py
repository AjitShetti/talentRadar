"""
services/career.py
~~~~~~~~~~~~~~~~~~
Deterministic tools for the Career Coach.

    identify_weaknesses()  — derive skill weaknesses from resume vs target jobs
    recommend_learning()   — turn weaknesses into LearningTask records
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from storage.database import AsyncSessionLocal
from storage.models import LearningTask, Skill, UserSkill
from services.base import as_list, parse_uuid

logger = logging.getLogger(__name__)


async def identify_weaknesses(
    *,
    user_id: str,
    resume_text: str | None = None,
    target_skills: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Identify skill weaknesses for a user.

    Sources of "known skills":
      1. user_skills rows (proficiency)
      2. resume text (ML extraction) when provided

    Gaps are skills that appear in ``target_skills`` but are missing from
    the user's known set. When ``target_skills`` is empty it falls back to
    the top demanded skills in the market (via market service).
    """
    from services.jobs import search_jobs

    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        known: dict[str, float] = {}

        if user_uuid:
            result = await session.execute(
                select(UserSkill).where(UserSkill.user_id == user_uuid)
            )
            for row in result.scalars().all():
                known[row.skill_name.lower()] = row.proficiency or 3.0

        if resume_text:
            from services.resumes import _extract_skills
            for skill in _extract_skills(resume_text):
                known.setdefault(skill.lower(), 3.0)

        target: set[str] = set()
        if target_skills:
            target = {s.lower().strip() for s in target_skills if s}
        else:
            market = await search_jobs(query="software engineer", limit=50)
            for job in market.get("jobs", []):
                target.update(s.lower() for s in (job.get("skills") or []))

        missing = sorted(
            s for s in target
            if s not in known or (known[s] or 0) < 2.5
        )[:limit]

        weaknesses = [
            {
                "skill_name": s,
                "proficiency": known.get(s),
                "severity": "high" if known.get(s) is None else "medium",
            }
            for s in missing
        ]

        return {
            "user_id": user_id,
            "known_skills_count": len(known),
            "target_skills_count": len(target),
            "weaknesses": weaknesses,
        }
    finally:
        await session.close()


async def recommend_learning(
    *,
    user_id: str,
    weaknesses: list[dict[str, Any]] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Generate and persist LearningTask records from identified weaknesses.

    Uses the LLM career coach to turn each gap into a concrete task with
    resources and priority. When persist=True, records are upserted into
    ``learning_tasks``.
    """
    from services.llm import generate_career_advice

    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if weaknesses is None:
            result = await identify_weaknesses(user_id=user_id)
            weaknesses = result.get("weaknesses", [])

        gaps = [w.get("skill_name") for w in weaknesses if w.get("skill_name")]
        if not gaps:
            return {"tasks": [], "total": 0}

        advice = await generate_career_advice(
            profile={"user_id": user_id}, gaps=gaps
        )

        tasks: list[dict[str, Any]] = []
        for item in advice:
            skill = str(item.get("skill_name") or "").strip()
            if not skill:
                continue
            tasks.append(
                {
                    "skill_name": skill,
                    "title": str(item.get("title") or f"Learn {skill}"),
                    "description": item.get("description"),
                    "resources": as_list(item.get("resources")),
                    "priority": item.get("priority"),
                }
            )

        # Persist into learning_tasks (one per skill, replace prior pending)
        if persist and user_uuid:
            for t in tasks:
                existing = (
                    await session.execute(
                        select(LearningTask).where(
                            LearningTask.user_id == user_uuid,
                            LearningTask.skill_name == t["skill_name"].lower(),
                            LearningTask.status == "pending",
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.title = t["title"]
                    existing.description = t.get("description")
                    existing.resources = t.get("resources")
                    existing.priority = t.get("priority")
                    existing.updated_at = datetime.now(tz=timezone.utc)
                else:
                    session.add(
                        LearningTask(
                            user_id=user_uuid,
                            skill_name=t["skill_name"].lower(),
                            title=t["title"],
                            description=t.get("description"),
                            resources=t.get("resources"),
                            priority=t.get("priority"),
                        )
                    )
            await session.commit()

        return {"tasks": tasks, "total": len(tasks)}
    finally:
        await session.close()
