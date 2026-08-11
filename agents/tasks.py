"""
agents/tasks.py
~~~~~~~~~~~~~~~
Celery tasks for AI Core agents.

These thin tasks delegate to the deterministic service layer — no LLM or
DB logic lives in the agent task itself.
"""

from __future__ import annotations

from typing import Any

from ingestion.celery_app import celery_app


@celery_app.task
def evaluate_candidate_task(resume_text: str, jd_text: str) -> dict[str, Any]:
    """
    Celery task to evaluate a candidate's resume against a job description.
    """
    import asyncio

    from services.resumes import analyze_resume

    async def _run() -> dict[str, Any]:
        return await analyze_resume(resume_text=resume_text, job_description=jd_text)

    return asyncio.run(_run())
