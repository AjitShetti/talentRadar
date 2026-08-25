"""
services/interviews.py
~~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for the Interview Lab.

    generate_questions()   — question generation per round type + difficulty
    evaluate_answer()      — LLM-as-judge scoring of a candidate answer
    adaptive_difficulty()  — next difficulty derived from rolling scores
    generate_prep_plan()   — personalised prep plan (job + company + resume)
    interview_insights()   — aggregate past sessions into dashboard feedback
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

# Sub-score dimensions, in the order the score card displays them.
DIMENSION_LABELS = {
    "correctness": "Correctness",
    "clarity": "Clarity",
    "depth": "Depth",
}

# Deterministic coaching line per weak dimension — no LLM call, so the
# dashboard stays fast and works offline.
DIMENSION_HINTS = {
    "correctness": (
        "Answers drift off the question. Restate what is being asked in one "
        "line, then answer that exact thing before adding context."
    ),
    "clarity": (
        "Answers are hard to follow. Lead with a one-sentence verdict, then "
        "give at most three supporting points."
    ),
    "depth": (
        "Answers stay on the surface. Add a trade-off, a complexity note, or "
        "a concrete example from your own work to each answer."
    ),
}

# Any answer scoring below this (0-100) is treated as a weak moment.
WEAK_MOMENT_THRESHOLD = 70.0


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


def _empty_insights() -> dict[str, Any]:
    """Zero-state payload — same shape as a populated one, so the UI has a
    single contract to render against."""
    return {
        "sessions_analyzed": 0,
        "questions_analyzed": 0,
        "average_score": 0.0,
        "delta": 0.0,
        "abandoned": 0,
        "trend": [],
        "dimensions": [],
        "weakest_dimension": None,
        "tracks": [],
        "weakest_track": None,
        "weak_moments": [],
        "focus": [],
    }


def _track_label(track: str) -> str:
    return TRACK_LABELS.get(track, track.replace("_", " ").title())


async def interview_insights(
    *,
    user_id: str,
    session_limit: int = 8,
    moment_limit: int = 3,
) -> dict[str, Any]:
    """
    Turn the user's recent mock interviews into dashboard-ready feedback.

    Purely deterministic (no LLM call) so the dashboard stays fast —
    everything is derived from the persisted sub-scores.

    Returns::

        {
          "sessions_analyzed": int, "questions_analyzed": int,
          "average_score": 0..100, "delta": float, "abandoned": int,
          "trend": [{"label", "score", "date", "completed"}],
          "dimensions": [{"key", "label", "score", "weakest"}],
          "weakest_dimension": {"key", "label", "score", "hint"} | None,
          "tracks": [{"track", "label", "score", "sessions"}],   # weakest first
          "weakest_track": {...} | None,
          "weak_moments": [{"question", "score", "track_label", "dimension", ...}],
          "focus": [str, ...],
        }
    """
    from services.base import parse_uuid
    from storage.database import AsyncSessionLocal
    from storage.interview_repository import InterviewRepository

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return _empty_insights()

    db = AsyncSessionLocal()
    try:
        repo = InterviewRepository()
        sessions = await repo.get_session_history(db, user_uuid, limit=session_limit)
        if not sessions:
            return _empty_insights()
        rows = await repo.get_answer_scores_for_sessions(db, [s.id for s in sessions])
    finally:
        await db.close()

    if not rows:
        return _empty_insights()

    session_by_id = {s.id: s for s in sessions}

    def _pct(correctness: float, clarity: float, depth: float) -> float:
        return ((correctness + clarity + depth) / 30.0) * 100.0

    # -- Per-answer percentages, grouped by session and by track --------- #
    per_session: dict[Any, list[float]] = {}
    per_track: dict[str, list[float]] = {}
    dim_totals: dict[str, float] = {key: 0.0 for key in DIMENSION_LABELS}
    scored_rows: list[tuple[float, Any]] = []

    for row in rows:
        parent = session_by_id.get(row.session_id)
        if parent is None:
            continue
        pct = _pct(row.score_correctness, row.score_clarity, row.score_depth)
        per_session.setdefault(row.session_id, []).append(pct)
        per_track.setdefault(parent.track.value, []).append(pct)
        dim_totals["correctness"] += row.score_correctness
        dim_totals["clarity"] += row.score_clarity
        dim_totals["depth"] += row.score_depth
        scored_rows.append((pct, row))

    if not scored_rows:
        return _empty_insights()

    answered = len(scored_rows)
    average_score = round(sum(pct for pct, _ in scored_rows) / answered, 1)

    # -- Sub-score dimensions (0-10 averages scaled to 0-100) ------------ #
    dim_scores = {
        key: round((total / answered) * 10.0, 1) for key, total in dim_totals.items()
    }
    weakest_key = min(dim_scores, key=lambda k: dim_scores[k])
    dimensions: list[dict[str, Any]] = [
        {
            "key": key,
            "label": DIMENSION_LABELS[key],
            "score": dim_scores[key],
            "weakest": key == weakest_key,
        }
        for key in DIMENSION_LABELS
    ]
    weakest_dimension: dict[str, Any] = {
        "key": weakest_key,
        "label": DIMENSION_LABELS[weakest_key],
        "score": dim_scores[weakest_key],
        "hint": DIMENSION_HINTS[weakest_key],
    }

    # -- Score trend, oldest -> newest ----------------------------------- #
    trend: list[dict[str, Any]] = [
        {
            "label": _track_label(s.track.value),
            "score": round(sum(per_session[s.id]) / len(per_session[s.id]), 1),
            "date": s.created_at.isoformat() if s.created_at else None,
            "completed": bool(s.completed),
        }
        for s in reversed(sessions)
        if per_session.get(s.id)
    ]

    delta = 0.0
    if len(trend) >= 2:
        latest = float(trend[-1]["score"])
        prior = [float(t["score"]) for t in trend[:-1]]
        delta = round(latest - (sum(prior) / len(prior)), 1)

    # -- Weakest round type ---------------------------------------------- #
    session_count_by_track: dict[str, int] = {}
    for s in sessions:
        if per_session.get(s.id):
            key = s.track.value
            session_count_by_track[key] = session_count_by_track.get(key, 0) + 1

    tracks: list[dict[str, Any]] = sorted(
        (
            {
                "track": track,
                "label": _track_label(track),
                "score": round(sum(pcts) / len(pcts), 1),
                "sessions": session_count_by_track.get(track, 0),
            }
            for track, pcts in per_track.items()
        ),
        key=lambda t: float(t["score"]),
    )
    weakest_track = tracks[0] if len(tracks) > 1 else None

    # -- What actually went wrong: the lowest-scoring answers ------------ #
    weak_moments: list[dict[str, Any]] = []
    for pct, row in sorted(scored_rows, key=lambda pair: pair[0])[:moment_limit]:
        if pct >= WEAK_MOMENT_THRESHOLD:
            break
        parent = session_by_id[row.session_id]
        sub = {
            "correctness": row.score_correctness,
            "clarity": row.score_clarity,
            "depth": row.score_depth,
        }
        low_key = min(sub, key=lambda k: sub[k])
        question = (row.question_text or "").strip()
        weak_moments.append(
            {
                "question": question[:160] + ("…" if len(question) > 160 else ""),
                "score": round(pct, 1),
                "track_label": _track_label(parent.track.value),
                "difficulty": parent.difficulty.value,
                "dimension": DIMENSION_LABELS[low_key],
                "dimension_score": round(sub[low_key], 1),
                "was_followup": bool(row.was_followup),
            }
        )

    # -- Deterministic focus lines --------------------------------------- #
    abandoned = sum(1 for s in sessions if not s.completed)
    focus: list[str] = [str(weakest_dimension["hint"])]
    if weakest_track and float(weakest_track["score"]) < average_score:
        focus.append(
            f"{weakest_track['label']} is your weakest round at "
            f"{weakest_track['score']}% — run your next session there."
        )
    if abandoned:
        plural = "s" if abandoned > 1 else ""
        focus.append(
            f"You left {abandoned} session{plural} unfinished. Finishing a round "
            "is what produces a full, comparable score."
        )

    return {
        "sessions_analyzed": len(trend),
        "questions_analyzed": answered,
        "average_score": average_score,
        "delta": delta,
        "abandoned": abandoned,
        "trend": trend,
        "dimensions": dimensions,
        "weakest_dimension": weakest_dimension,
        "tracks": tracks,
        "weakest_track": weakest_track,
        "weak_moments": weak_moments,
        "focus": focus[:3],
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
