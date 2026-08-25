"""
services/career.py
~~~~~~~~~~~~~~~~~~
Deterministic tools for the Career Coach.

    identify_weaknesses()    — derive skill weaknesses from resume vs target jobs
    recommend_learning()     — turn weaknesses into LearningTask records
    target_role_readiness()  — the user's saved resume vs their target roles:
                               the 2-3 skills the roles ask for that the resume
                               doesn't show, or — when it already covers them —
                               how the resume itself could read better
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from services.base import as_list, parse_uuid
from storage.database import AsyncSessionLocal
from storage.models import LearningTask, Skill, UserSkill

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


# ─────────────────────────────────────────────────────────────────────────────
# Target-role readiness (resume vs target roles) — powers the dashboard's
# "Skills to strengthen" panel.
# ─────────────────────────────────────────────────────────────────────────────

#: The analysis costs a market query per role plus one LLM call, and the
#: dashboard asks for it on every visit. Cache on the inputs that can change
#: the answer (roles, profile skills, which resume + when it was last saved),
#: so re-uploading a resume or editing goals invalidates it immediately.
_READINESS_TTL_SECONDS = 30 * 60
_READINESS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_READINESS_CACHE_MAX_ENTRIES = 512

#: How many postings per target role we sample to learn what the role asks for.
_MARKET_SAMPLE_PER_ROLE = 40
_MAX_ROLES_SAMPLED = 3
#: Deterministic shortlist handed to the LLM to rank and explain.
_MAX_CANDIDATE_SKILLS = 12

_NO_ROLES_HEADLINE = (
    "Add a target role in Profile & Goals to see how your resume measures up."
)
_NO_RESUME_HEADLINE = (
    "Upload your resume in Profile & Goals — we compare it against your target "
    "roles to find what's missing."
)


async def target_role_readiness(*, user_id: str, limit: int = 3) -> dict[str, Any]:
    """
    Compare the user's saved resume against the roles they said they want.

    Returns the payload behind the dashboard's "Skills to strengthen" panel::

        {
          "status": "ok" | "no_target_roles" | "no_resume",
          "kind": "missing_skills" | "resume_improvements" | None,
          "headline": str,
          "items": [{"title": str, "detail": str}],   # at most ``limit``
          "target_roles": [str],
          "resume_filename": str | None,
          "resume_updated_at": str | None,
          "analysis": "llm" | "market" | "generic" | None,
        }

    ``kind`` is the whole point of the panel: when the resume is genuinely
    short of what the target roles ask for we name the two-to-three skills
    that matter most; when it already covers them we switch to concrete ways
    the resume itself could read better for that role.
    """
    from services.profiles import get_profile
    from services.resumes import get_active_resume

    profile = await get_profile(user_id=user_id) or {}
    target_roles = [role.strip() for role in as_list(profile.get("target_roles")) if role.strip()]
    profile_skills = _profile_skill_names(profile)
    resume = await get_active_resume(user_id=user_id)

    cache_key = _readiness_cache_key(user_id, target_roles, profile_skills, resume)
    cached = _readiness_cache_get(cache_key)
    if cached is not None:
        return cached

    if not target_roles:
        result = _readiness_shell("no_target_roles", _NO_ROLES_HEADLINE, [], resume)
    elif resume is None:
        result = _readiness_shell("no_resume", _NO_RESUME_HEADLINE, target_roles, None)
    else:
        result = await _analyse_readiness(
            target_roles=target_roles,
            profile_skills=profile_skills,
            resume=resume,
            limit=limit,
        )

    _readiness_cache_put(cache_key, result)
    return result


async def _analyse_readiness(
    *,
    target_roles: list[str],
    profile_skills: list[str],
    resume: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    """Resume vs target roles, with the LLM narrowing a deterministic shortlist."""
    from services.resumes import _extract_skills

    resume_text = str(resume.get("extracted_text") or "")
    resume_lower = resume_text.lower()

    try:
        resume_skills = _extract_skills(resume_text)
    except Exception:  # taxonomy extraction must never blank the panel
        logger.warning("Resume skill extraction failed", exc_info=True)
        resume_skills = set()

    known = resume_skills | {s.lower().strip() for s in profile_skills if s.strip()}
    demand, spelling, postings_sampled = await _role_skill_demand(target_roles)

    # A skill spelled out anywhere in the resume counts as covered even when the
    # taxonomy misses it — better to under-report a gap than to invent one.
    candidates = [
        {"skill": spelling.get(skill, skill), "postings": count}
        for skill, count in demand.most_common()
        if skill not in known and skill not in resume_lower
    ][:_MAX_CANDIDATE_SKILLS]

    roles_label = " / ".join(target_roles[:2])

    try:
        from services.llm import generate_role_readiness

        analysis = await generate_role_readiness(
            target_roles=target_roles,
            resume_text=resume_text,
            profile_skills=profile_skills,
            candidate_skills=candidates,
        )
        missing = _clean_items(analysis.get("missing_skills"), "skill", "reason", limit)
        if missing:
            return _readiness_shell(
                "ok",
                f"Not on your resume — {roles_label} postings keep asking for these.",
                target_roles,
                resume,
                kind="missing_skills",
                items=missing,
                analysis="llm",
            )

        improvements = _clean_items(analysis.get("resume_improvements"), "title", "reason", limit)
        if improvements:
            return _readiness_shell(
                "ok",
                f"Your resume covers the core skills for {roles_label}. Sharpen how it reads.",
                target_roles,
                resume,
                kind="resume_improvements",
                items=improvements,
                analysis="llm",
            )
    except Exception:  # fall back to the deterministic view
        logger.warning("Role readiness analysis failed; using market fallback", exc_info=True)

    return _market_fallback(
        candidates=candidates,
        postings_sampled=postings_sampled,
        target_roles=target_roles,
        roles_label=roles_label,
        resume=resume,
        limit=limit,
    )


async def _role_skill_demand(
    target_roles: list[str],
) -> tuple[Counter[str], dict[str, str], int]:
    """
    Count how often each skill is asked for across postings for these roles.

    Returns ``(demand, spelling, postings_sampled)``. Matching happens on the
    lowercased name; ``spelling`` keeps the posting's own casing so the panel
    can show "CI/CD" and "FastAPI" rather than a title-cased mangling.
    """
    from services.jobs import search_jobs

    demand: Counter[str] = Counter()
    spelling: dict[str, str] = {}
    postings = 0
    for role in target_roles[:_MAX_ROLES_SAMPLED]:
        try:
            market = await search_jobs(query=role, limit=_MARKET_SAMPLE_PER_ROLE)
        except Exception:  # one bad role must not sink the rest
            logger.warning("Market sample failed for target role %r", role, exc_info=True)
            continue
        jobs = market.get("jobs", [])
        postings += len(jobs)
        for job in jobs:
            # Count each skill once per posting, not once per mention.
            seen: set[str] = set()
            for raw in as_list(job.get("skills")):
                name = raw.strip()
                key = name.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                demand[key] += 1
                spelling.setdefault(key, name)
    return demand, spelling, postings


def _market_fallback(
    *,
    candidates: list[dict[str, Any]],
    postings_sampled: int,
    target_roles: list[str],
    roles_label: str,
    resume: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    """Deterministic answer for when the LLM is unavailable or returns nothing."""
    if candidates:
        items = [
            {
                "title": str(candidate["skill"]),
                "detail": (
                    f"Asked for in {candidate['postings']} of the "
                    f"{postings_sampled} {roles_label} postings we sampled."
                ),
            }
            for candidate in candidates[:limit]
        ]
        return _readiness_shell(
            "ok",
            f"Not on your resume — {roles_label} postings keep asking for these.",
            target_roles,
            resume,
            kind="missing_skills",
            items=items,
            analysis="market",
        )

    return _readiness_shell(
        "ok",
        f"Your resume covers the core skills for {roles_label}. Sharpen how it reads.",
        target_roles,
        resume,
        kind="resume_improvements",
        items=_generic_improvements(roles_label)[:limit],
        analysis="generic",
    )


def _generic_improvements(roles_label: str) -> list[dict[str, str]]:
    """Presentation advice that holds without an LLM read of the resume."""
    return [
        {
            "title": "Quantify your top bullets",
            "detail": (
                f"Put numbers — users, scale, latency, revenue — on your three most "
                f"recent bullets so a {roles_label} reviewer can size the work."
            ),
        },
        {
            "title": f"Lead with {roles_label} language",
            "detail": (
                "Mirror the target title in your headline and summary; ATS keyword "
                "matching runs on the exact phrasing the posting uses."
            ),
        },
        {
            "title": "Show ownership, not tasks",
            "detail": (
                "Name what you decided and shipped end to end, not what the team did — "
                "that's the line between a strong and an average resume at this level."
            ),
        },
    ]


def _clean_items(
    raw: object, title_key: str, detail_key: str, limit: int
) -> list[dict[str, str]]:
    """Coerce the LLM's list into ``[{title, detail}]``, dropping anything empty."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get(title_key) or "").strip()
        detail = str(entry.get(detail_key) or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        items.append({"title": title, "detail": detail})
        if len(items) >= limit:
            break
    return items


def _profile_skill_names(profile: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for skill in profile.get("skills") or []:
        name = skill.get("name") if isinstance(skill, dict) else skill
        if name and str(name).strip():
            names.append(str(name).strip())
    return names


def _readiness_shell(
    status: str,
    headline: str,
    target_roles: list[str],
    resume: dict[str, Any] | None,
    *,
    kind: str | None = None,
    items: list[dict[str, str]] | None = None,
    analysis: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "kind": kind,
        "headline": headline,
        "items": items or [],
        "target_roles": target_roles,
        "resume_filename": (resume or {}).get("filename"),
        "resume_updated_at": (resume or {}).get("updated_at"),
        "analysis": analysis,
    }


def _readiness_cache_key(
    user_id: str,
    target_roles: list[str],
    profile_skills: list[str],
    resume: dict[str, Any] | None,
) -> str:
    signature = json.dumps(
        {
            "user": user_id,
            "roles": sorted(r.lower() for r in target_roles),
            "skills": sorted(s.lower() for s in profile_skills),
            "resume": (resume or {}).get("id"),
            "saved": (resume or {}).get("updated_at"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


def _readiness_cache_get(key: str) -> dict[str, Any] | None:
    entry = _READINESS_CACHE.get(key)
    if entry is None:
        return None
    stored_at, payload = entry
    if time.time() - stored_at > _READINESS_TTL_SECONDS:
        _READINESS_CACHE.pop(key, None)
        return None
    return payload


def _readiness_cache_put(key: str, payload: dict[str, Any]) -> None:
    if len(_READINESS_CACHE) >= _READINESS_CACHE_MAX_ENTRIES:
        oldest = min(_READINESS_CACHE, key=lambda k: _READINESS_CACHE[k][0])
        _READINESS_CACHE.pop(oldest, None)
    _READINESS_CACHE[key] = (time.time(), payload)
