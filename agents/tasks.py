"""
agents/tasks.py
~~~~~~~~~~~~~~~
Celery tasks for AI Core agents.
"""

from typing import Any
from ingestion.celery_app import celery_app
from agents.evaluator import CandidateEvaluator

@celery_app.task
def evaluate_candidate_task(resume_text: str, jd_text: str) -> dict[str, Any]:
    """
    Celery task to evaluate a candidate's resume against a job description.
    """
    evaluator = CandidateEvaluator()
    return evaluator.evaluate(resume_text=resume_text, jd_text=jd_text)
