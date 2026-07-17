"""
agents/interview/prompts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
All system prompts for the interview agent, keyed by track + difficulty.

Design notes
------------
* Prompts are pure strings — no f-string interpolation at import time,
  so they're cheap to load.  Dynamic values (track, difficulty, history)
  are injected by the node functions at call time.
* Each prompt section has a clear job: generate question, evaluate answer,
  or probe with a follow-up.  Mixing responsibilities in one prompt
  degrades reliability.
* The EVALUATOR prompt requests strict JSON so ``LLMProvider._parse_eval_json``
  can parse it reliably with ``response_format: json_object``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Base interviewer persona — injected once per session
# ---------------------------------------------------------------------------

_INTERVIEWER_BASE = """\
You are an expert technical interviewer conducting a structured mock interview.
Your tone is professional, encouraging, and concise.
Ask ONE question at a time. Never repeat a question already asked in the conversation.
Do not provide answers, hints, or explanations — only ask the question.
"""


# ---------------------------------------------------------------------------
# Track-specific question pools / personas
# ---------------------------------------------------------------------------

_TRACK_CONTEXT: dict[str, str] = {
    "python_dsa": (
        "Focus on Python data structures and algorithms: lists, dicts, sets, "
        "time/space complexity, sorting, searching, recursion, dynamic programming, "
        "graph traversal (BFS/DFS), heaps, and sliding window patterns."
    ),
    "python_backend": (
        "Focus on Python backend development: FastAPI/Django patterns, async I/O, "
        "SQLAlchemy ORM, REST API design, authentication (JWT/OAuth), Celery task "
        "queues, caching (Redis), error handling, and 12-factor app principles."
    ),
    "sql": (
        "Focus on SQL and databases: SELECT / JOIN / GROUP BY / HAVING, window "
        "functions (ROW_NUMBER, RANK, LAG/LEAD), CTEs, indexing strategy, EXPLAIN "
        "ANALYZE, normalisation vs denormalisation, transactions, and PostgreSQL-"
        "specific features (JSONB, ARRAY, materialized views)."
    ),
    "system_design": (
        "Focus on system design: requirements gathering, capacity estimation, "
        "choosing between SQL vs NoSQL, caching layers, message queues (Kafka/RabbitMQ), "
        "load balancing, horizontal vs vertical scaling, CAP theorem, distributed "
        "consistency, microservices vs monolith trade-offs, and API gateway patterns."
    ),
}

_DIFFICULTY_GUIDANCE: dict[str, str] = {
    "beginner": (
        "Questions should be foundational — definitions, basic usage, and simple "
        "scenarios.  Avoid edge cases or deep internals at this level."
    ),
    "mid": (
        "Questions should require solid understanding and practical experience.  "
        "Include trade-off comparisons and moderate complexity scenarios."
    ),
    "senior": (
        "Questions should probe expert-level understanding: edge cases, performance "
        "implications, architectural trade-offs, and real-world failure modes.  "
        "Expect nuanced, multi-part answers."
    ),
}


# ---------------------------------------------------------------------------
# Public prompt builders
# ---------------------------------------------------------------------------

def build_question_prompt(track: str, difficulty: str) -> str:
    """
    Build the system prompt for question generation.

    Called once per turn by ``node_generate_question`` and
    ``node_generate_followup``.
    """
    track_ctx  = _TRACK_CONTEXT.get(track, "general technical topics")
    diff_ctx   = _DIFFICULTY_GUIDANCE.get(difficulty, "intermediate level")

    return (
        f"{_INTERVIEWER_BASE}\n\n"
        f"TOPIC AREA:\n{track_ctx}\n\n"
        f"DIFFICULTY LEVEL ({difficulty.upper()}):\n{diff_ctx}\n\n"
        "Based on the conversation history, ask the NEXT logical technical "
        "question. Keep the question clear and self-contained (under 3 sentences)."
    )


def build_evaluator_prompt(track: str, difficulty: str) -> str:
    """
    Build the system prompt for answer evaluation.

    Called once per turn by ``node_evaluate_answer``.
    """
    track_ctx = _TRACK_CONTEXT.get(track, "general technical topics")
    diff_ctx  = _DIFFICULTY_GUIDANCE.get(difficulty, "intermediate level")

    return f"""\
You are an expert technical evaluator for a mock interview.

TOPIC AREA:
{track_ctx}

DIFFICULTY ({difficulty.upper()}):
{diff_ctx}

Your task: evaluate the candidate's answer to the given question.

Return ONLY a JSON object with EXACTLY these keys:
{{
  "correctness":    <float 0-10>,
  "clarity":        <float 0-10>,
  "depth":          <float 0-10>,
  "needs_followup": <true | false>,
  "feedback_note":  "<internal reasoning, max 2 sentences, NOT shown to the user>",
  "answer_summary": "<brief neutral summary of what the candidate said, max 3 sentences>"
}}

Scoring guide:
  correctness  — factual / technical accuracy
  clarity      — how clearly and concisely the answer was communicated
  depth        — technical depth, nuance, and awareness of trade-offs

Set needs_followup=true when the answer is partially correct, superficial,
or misses an important aspect that a short follow-up could uncover.
Set needs_followup=false when the answer is complete, clearly wrong
(follow-up won't help), or already received a follow-up on this question.
"""


def build_followup_prompt(track: str, difficulty: str) -> str:
    """
    Build the system prompt for follow-up probe generation.

    Functionally identical to ``build_question_prompt`` but with an
    extra instruction to be targeted rather than picking a new topic.
    Caller injects the feedback_note hint directly into the system prompt
    via ``LLMProvider.generate_followup``.
    """
    track_ctx = _TRACK_CONTEXT.get(track, "general technical topics")
    diff_ctx  = _DIFFICULTY_GUIDANCE.get(difficulty, "intermediate level")

    return (
        f"{_INTERVIEWER_BASE}\n\n"
        f"TOPIC AREA:\n{track_ctx}\n\n"
        f"DIFFICULTY LEVEL ({difficulty.upper()}):\n{diff_ctx}\n\n"
        "The candidate's previous answer was incomplete or superficial in a "
        "specific way (see the INTERNAL HINT below). Ask ONE targeted follow-up "
        "probe to help them demonstrate deeper understanding of exactly that gap. "
        "Do not introduce an entirely new topic."
    )
