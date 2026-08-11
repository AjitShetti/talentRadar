"""
services/interviews.py
~~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for the Interview Lab.

    generate_questions()   — question generation per round type + difficulty
    evaluate_answer()      — LLM-as-judge scoring of a candidate answer
    adaptive_difficulty()  — next difficulty derived from rolling scores
    generate_prep_plan()   — personalised prep plan (job + company + resume)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

VALID_TRACKS = {"coding", "technical", "behavioral", "system_design"}
VALID_DIFFICULTIES = {"beginner", "mid", "senior"}

TRACK_LABELS = {
    "coding": "Coding / DSA",
    "technical": "Technical / Domain",
    "behavioral": "Behavioral / STAR",
    "system_design": "System Design",
    "python_dsa": "Python DSA",
    "python_backend": "Python Backend",
    "sql": "SQL",
}


async def generate_questions(
    *,
    track: str,
    difficulty: str,
    count: int = 1,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generate ``count`` interview questions for a round type.

    Uses the existing interview LLM provider (Groq) with the static
    question bank as a fallback. ``context`` may carry job/company info
    to personalise questions.
    """
    from agents.interview.fallback_questions import get_fallback_question
    from agents.interview.llm_provider import LLMProvider, LLMProviderError
    from agents.interview.prompts import build_question_prompt

    track = (track or "technical").lower()
    difficulty = (difficulty or "mid").lower()
    if track not in VALID_TRACKS and track not in TRACK_LABELS:
        track = "technical"

    questions: list[str] = []
    fallback_count = 0
    llm = LLMProvider()
    system_prompt = build_question_prompt(track, difficulty)

    for _ in range(count):
        question: str | None = None
        try:
            question = await llm.generate_question(
                system_prompt, context.get("history") if context else None
            )
        except LLMProviderError as exc:
            logger.warning("LLM question generation failed: %s", exc)

        if not question:
            used = set(questions)
            question = get_fallback_question(track, difficulty, used)
            if question:
                fallback_count += 1
        if question:
            questions.append(question)

    return {
        "track": track,
        "difficulty": difficulty,
        "questions": questions,
        "count": len(questions),
        "used_fallback": fallback_count > 0,
    }


async def evaluate_answer(
    *,
    track: str,
    difficulty: str,
    question: str,
    answer: str,
) -> dict[str, Any]:
    """
    Score a candidate's answer.

    Returns::

        {
          "correctness": 0..10, "clarity": 0..10, "depth": 0..10,
          "needs_followup": bool, "feedback_note": str, "answer_summary": str,
          "score_percentage": 0..100
        }
    """
    from agents.interview.llm_provider import LLMProvider, LLMProviderError
    from agents.interview.prompts import build_evaluator_prompt

    llm = LLMProvider()
    system_prompt = build_evaluator_prompt(track, difficulty)
    try:
        score_data = await llm.evaluate_answer(
            system_prompt, question, answer, track, difficulty
        )
    except LLMProviderError as exc:
        logger.warning("LLM evaluation failed, using neutral scores: %s", exc)
        score_data = {
            "correctness": 5.0,
            "clarity": 5.0,
            "depth": 5.0,
            "needs_followup": False,
            "feedback_note": "Evaluation unavailable (LLM error).",
            "answer_summary": answer[:256],
        }

    total = round(((score_data["correctness"] + score_data["clarity"] + score_data["depth"]) / 30.0) * 100.0, 1)
    return {**score_data, "score_percentage": total}


async def adaptive_difficulty(
    *,
    scores: list[float],
    current_difficulty: str = "mid",
    min_improve: float = 70.0,
) -> dict[str, Any]:
    """
    Derive the next difficulty from the rolling average of answer scores.

    Simple deterministic policy:
        avg >= min_improve and current != senior      → step up
        avg <  max(40, min_improve - 30) and current != beginner → step down
        otherwise                                     → stay
    """
    order = ["beginner", "mid", "senior"]
    if current_difficulty not in order:
        current_difficulty = "mid"
    idx = order.index(current_difficulty)

    if not scores:
        avg = 0.0
    else:
        avg = sum(scores) / len(scores)

    if avg >= min_improve and idx < len(order) - 1:
        nxt = order[idx + 1]
    elif avg < max(40.0, min_improve - 30.0) and idx > 0:
        nxt = order[idx - 1]
    else:
        nxt = current_difficulty

    return {
        "current_difficulty": current_difficulty,
        "average_score": round(avg, 1),
        "next_difficulty": nxt,
        "changed": nxt != current_difficulty,
    }


async def generate_prep_plan(
    *,
    job: dict[str, Any] | None = None,
    company: dict[str, Any] | None = None,
    resume_text: str | None = None,
) -> dict[str, Any]:
    """
    Build a personalised interview preparation plan from the job, the
    company's interview patterns, and the candidate's resume.
    """
    from services.llm import _chat

    rounds: list[str] = []
    focus: list[str] = []

    if company and company.get("interview_patterns"):
        patterns = company["interview_patterns"]
        if isinstance(patterns, dict):
            rounds = list(patterns.get("rounds") or [])
            focus = list(patterns.get("focus_areas") or [])

    job_skills = (job or {}).get("skills") or []
    resume_skills: list[str] = []
    if resume_text:
        try:
            resume_skills = list(
                (await _extract_skill_set(resume_text))
            )
        except Exception:  # noqa: BLE001
            resume_skills = []

    gap = sorted(set(s.lower() for s in job_skills) - set(s.lower() for s in resume_skills))

    if not rounds:
        rounds = ["coding", "technical", "behavioral", "system_design"]
    if not focus:
        focus = [
            "Review company products and tech stack",
            f"Strengthen missing skills: {', '.join(gap[:5])}" if gap else "Solidify strengths",
            "Prepare 3 STAR behavioral stories",
            "Rehearse salary expectations and questions to ask",
        ]

    summary = None
    try:
        system = "You are an interview coach. Produce a concise, actionable prep plan in Markdown."
        prompt = (
            f"Job: {json_safe(job)}\nCompany: {json_safe(company)}\n"
            f"Rounds: {rounds}\nFocus: {focus}"
        )
        summary = await _chat(system, prompt, max_tokens=600)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Prep plan summary failed: %s", exc)

    return {
        "interview_rounds": rounds,
        "focus_areas": focus,
        "skill_gap": gap,
        "summary": summary,
    }


async def _extract_skill_set(text: str) -> set[str]:
    from services.resumes import _extract_skills
    return _extract_skills(text)


def json_safe(value: dict[str, Any] | None) -> str:
    import json
    try:
        return json.dumps(value or {}, default=str)
    except Exception:  # noqa: BLE001
        return "{}"
