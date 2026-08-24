"""
services/profiles.py
~~~~~~~~~~~~~~~~~~~~
Deterministic tools for onboarding / profile management.

    get_or_create_profile()  — ensure a user has a Profile row
    get_profile()            — serialised profile (None when missing)
    upsert_profile()         — write onboarding fields + user skills
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from storage.database import AsyncSessionLocal
from storage.models import Profile, Skill, UserSkill
from services.base import as_list, parse_uuid

logger = logging.getLogger(__name__)

#: ``user_skills.proficiency`` is an INTEGER column on a 1-5 scale.
_DEFAULT_PROFICIENCY = 3
_MIN_PROFICIENCY = 1
_MAX_PROFICIENCY = 5


def _clamp_proficiency(value: Any) -> int:
    """Coerce any incoming proficiency to the 1-5 integer the column accepts."""
    try:
        as_int = round(float(value))
    except (TypeError, ValueError):
        return _DEFAULT_PROFICIENCY
    return max(_MIN_PROFICIENCY, min(_MAX_PROFICIENCY, as_int))


_SKILL_CATEGORY_HINTS: dict[str, str] = {
    "python": "language", "javascript": "language", "typescript": "language",
    "java": "language", "go": "language", "sql": "language",
    "react": "frontend", "angular": "frontend", "vue": "frontend", "next.js": "frontend",
    "node.js": "backend", "django": "backend", "flask": "backend", "fastapi": "backend",
    "postgresql": "database", "mysql": "database", "mongodb": "database", "redis": "database",
    "aws": "cloud", "gcp": "cloud", "azure": "cloud", "docker": "devops", "kubernetes": "devops",
    "machine learning": "ml", "nlp": "ml", "deep learning": "ml",
}


async def get_profile(*, user_id: str) -> dict[str, Any] | None:
    """Return a serialised profile, or ``None`` if the user has none yet."""
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return None
        profile = (
            await session.execute(
                select(Profile).where(Profile.user_id == user_uuid)
            )
        ).scalar_one_or_none()
        if profile is None:
            return None
        skills = (
            await session.execute(
                select(UserSkill).where(UserSkill.user_id == user_uuid)
            )
        ).scalars().all()
        return {
            "user_id": str(profile.user_id),
            "full_name": profile.full_name,
            "headline": profile.headline,
            "summary": profile.summary,
            "target_roles": profile.target_roles or [],
            "target_locations": profile.target_locations or [],
            "is_remote_preferred": profile.is_remote_preferred,
            "target_salary_min": profile.target_salary_min,
            "target_salary_max": profile.target_salary_max,
            "salary_currency": profile.salary_currency,
            "years_experience": profile.years_experience,
            "current_role": profile.current_role,
            "career_goals": profile.career_goals,
            "onboarding_completed": profile.onboarding_completed,
            "active_resume_id": str(profile.active_resume_id) if profile.active_resume_id else None,
            "skills": [
                {"name": s.skill_name, "proficiency": s.proficiency}
                for s in skills
            ],
        }
    finally:
        await session.close()


async def get_or_create_profile(*, user_id: str) -> dict[str, Any]:
    """Ensure a Profile row exists for a user, creating an empty one if not."""
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            raise ValueError(f"Invalid user_id: {user_id!r}")
        profile = (
            await session.execute(
                select(Profile).where(Profile.user_id == user_uuid)
            )
        ).scalar_one_or_none()
        created = False
        if profile is None:
            profile = Profile(user_id=user_uuid)
            session.add(profile)
            created = True
        await session.commit()
        await session.refresh(profile)
        return {"created": created, "profile": await _serialise_short(profile)}
    finally:
        await session.close()


async def upsert_profile(*, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Upsert onboarding fields and skill rows for a user. Returns the
    serialised profile with ``onboarding_completed`` computed from the
    presence of core fields (full_name, target_roles, years_experience).
    """
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            raise ValueError(f"Invalid user_id: {user_id!r}")

        profile = (
            await session.execute(
                select(Profile).where(Profile.user_id == user_uuid)
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = Profile(user_id=user_uuid)
            session.add(profile)

        for field, value in data.items():
            if field in {
                "full_name", "headline", "summary", "target_roles",
                "target_locations", "is_remote_preferred", "target_salary_min",
                "target_salary_max", "salary_currency", "years_experience",
                "current_role", "career_goals",
            }:
                setattr(profile, field, value)
            elif field == "active_resume_id" and parse_uuid(value):
                profile.active_resume_id = parse_uuid(value)

        profile.onboarding_completed = bool(
            profile.full_name and profile.target_roles and profile.years_experience is not None
        )
        profile.updated_at = datetime.now(tz=timezone.utc)

        # Upsert skills
        skills = as_list(data.get("skills"))
        for item in skills:
            if isinstance(item, str):
                name, prof = item, _DEFAULT_PROFICIENCY
            elif isinstance(item, dict):
                name = item.get("name") or item.get("skill_name")
                prof = item.get("proficiency") or _DEFAULT_PROFICIENCY
            else:
                continue
            if not name:
                continue
            name_l = str(name).strip().lower()
            if not name_l:
                continue
            existing = (
                await session.execute(
                    select(UserSkill).where(
                        UserSkill.user_id == user_uuid,
                        UserSkill.skill_name == name_l,
                    )
                )
            ).scalar_one_or_none()
            # ensure the canonical Skill row exists so UserSkill can point at it
            canonical = (
                await session.execute(
                    select(Skill).where(Skill.name == name_l)
                )
            ).scalar_one_or_none()
            if canonical is None:
                canonical = Skill(name=name_l, category=_SKILL_CATEGORY_HINTS.get(name_l))
                session.add(canonical)
                await session.flush()

            if existing:
                existing.proficiency = _clamp_proficiency(prof)
                existing.skill_id = canonical.id
            else:
                session.add(
                    UserSkill(
                        user_id=user_uuid,
                        skill_name=name_l,
                        skill_id=canonical.id,
                        proficiency=_clamp_proficiency(prof),
                    )
                )

        await session.commit()
        result = await get_profile(user_id=user_id)
        return result or {}
    finally:
        await session.close()


async def _serialise_short(profile: Profile) -> dict[str, Any]:
    return {
        "user_id": str(profile.user_id),
        "onboarding_completed": profile.onboarding_completed,
        "full_name": profile.full_name,
    }
