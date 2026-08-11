"""
agents/studio_agents.py
~~~~~~~~~~~~~~~~~~~~~~~
Thin agents for the TalentRadar job-seeker journey.

Each agent is a stateless orchestrator that calls ONLY deterministic
service tools (``services/``) — no DB or LLM logic lives here. They exist
to give the LangGraph orchestrator and the API BFF a uniform
``{success, data, error}`` contract.

    ResumeStudioAgent     — Resume Studio (ATS analysis, tailoring)
    CareerCoachAgent      — Career Coach (weaknesses, learning tasks)
    CompanyAgent          — Company Intelligence
    ApplicationAgent      — Application Tracker (update, funnel)
    PersonalAgent         — Personal AI Agent (memory, next-best-action)
"""

from __future__ import annotations

import logging
from typing import Any

from services.agent_memory import get_memories, recommend_next_action, remember
from services.applications import tracker_analytics, update_application
from services.career import identify_weaknesses, recommend_learning
from services.companies import company_intel, get_company
from services.resumes import analyze_resume, skill_gap, tailor_resume

logger = logging.getLogger(__name__)


class ResumeStudioAgent:
    """Resume Studio tools wrapped in the uniform agent contract."""

    async def analyze(self, *, resume_text: str, jd_text: str | None = None) -> dict[str, Any]:
        return _ok(await analyze_resume(resume_text=resume_text, job_description=jd_text or ""))

    async def tailor(self, *, resume_text: str, jd_text: str) -> dict[str, Any]:
        return _ok(await tailor_resume(resume_text=resume_text, job_description=jd_text))

    async def gaps(self, *, resume_text: str, target_skills: list[str]) -> dict[str, Any]:
        return _ok(await skill_gap(resume_text=resume_text, job_skills=target_skills))


class CareerCoachAgent:
    """Career Coach tools wrapped in the uniform agent contract."""

    async def weaknesses(self, *, user_id: str, resume_text: str | None = None) -> dict[str, Any]:
        return _ok(await identify_weaknesses(user_id=user_id, resume_text=resume_text))

    async def recommend(self, *, user_id: str, persist: bool = True) -> dict[str, Any]:
        return _ok(await recommend_learning(user_id=user_id, persist=persist))


class CompanyAgent:
    """Company Intelligence tools wrapped in the uniform agent contract."""

    async def profile(self, *, company_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        if company_id:
            return _ok(await company_intel(company_id=company_id))
        if name:
            return _ok(await get_company(name=name))
        return _fail("Provide either company_id or company name.")

    async def intel(self, *, company_id: str) -> dict[str, Any]:
        return _ok(await company_intel(company_id=company_id))


class ApplicationAgent:
    """Application Tracker tools wrapped in the uniform agent contract."""

    async def update(
        self,
        *,
        application_id: str,
        status: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        return _ok(await update_application(application_id=application_id, status=status, notes=notes))

    async def funnel(self, *, user_id: str) -> dict[str, Any]:
        return _ok(await tracker_analytics(user_id=user_id))


class PersonalAgent:
    """Personal AI Agent tools wrapped in the uniform agent contract."""

    async def next_action(self, *, user_id: str) -> dict[str, Any]:
        return _ok(await recommend_next_action(user_id=user_id))

    async def remember(
        self,
        *,
        user_id: str,
        memory_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _ok(await remember(user_id=user_id, memory_type=memory_type, content=content, metadata=metadata))

    async def memories(self, *, user_id: str, memory_type: str | None = None) -> dict[str, Any]:
        return _ok(await get_memories(user_id=user_id, memory_type=memory_type))


def _ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _fail(error: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": error}
