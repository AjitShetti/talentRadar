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
# Voice-mode addendum — injected when the session is conducted hands-free
# ---------------------------------------------------------------------------

# Everything here exists because the question is going through text-to-speech
# and the candidate hears it once, with no transcript to scan back over.
_VOICE_ADDENDUM = """DELIVERY — THIS IS A SPOKEN INTERVIEW:
The candidate HEARS your question read aloud; they cannot re-read it.
* Write exactly what a human interviewer would say out loud — plain sentences.
* Never use code blocks, bullet points, numbered lists, markdown, or symbols
  like -> or {} — they are unintelligible when spoken.
* Keep it to two sentences at most, and put the actual question last so it is
  the part they remember.
* Spell out anything that reads badly aloud: say "big O of n log n", not "O(n log n)".
* Ask for a spoken explanation of an approach, never for code to be dictated.
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

def build_question_prompt(
    track: str, difficulty: str, voice_mode: bool = False
) -> str:
    """
    Build the system prompt for question generation.

    Called once per turn by ``node_generate_question`` and
    ``node_generate_followup``.  When ``voice_mode`` is set the question is
    additionally constrained to a spoken register (see ``_VOICE_ADDENDUM``).
    """
    track_ctx  = _TRACK_CONTEXT.get(track, "general technical topics")
    diff_ctx   = _DIFFICULTY_GUIDANCE.get(difficulty, "intermediate level")
    voice_ctx  = f"\n\n{_VOICE_ADDENDUM}" if voice_mode else ""

    return (
        f"{_INTERVIEWER_BASE}{voice_ctx}\n\n"
        f"TOPIC AREA:\n{track_ctx}\n\n"
        f"DIFFICULTY LEVEL ({difficulty.upper()}):\n{diff_ctx}\n\n"
        "Based on the conversation history, ask the NEXT logical technical "
        "question. Keep the question clear and self-contained (under 3 sentences)."
    )


def build_evaluator_prompt(
    track: str, difficulty: str, voice_mode: bool = False
) -> str:
    """
    Build the system prompt for answer evaluation.

    Called once per turn by ``node_evaluate_answer``.

    In ``voice_mode`` the evaluator is also asked for a ``verbal_ack`` — the
    one-line reaction the interviewer speaks before moving on.  It rides along
    on this existing call rather than costing a second LLM round-trip, which
    matters because the candidate is sitting in silence waiting for it.
    """
    track_ctx = _TRACK_CONTEXT.get(track, "general technical topics")
    diff_ctx  = _DIFFICULTY_GUIDANCE.get(difficulty, "intermediate level")
    ack_key   = (
        ',\n  "verbal_ack":     "<one short spoken reaction, max 12 words>"'
        if voice_mode else ""
    )
    ack_guide = (
        "\n\nverbal_ack — what the interviewer SAYS OUT LOUD before the next "
        "question, e.g. \"Right, that covers the indexing side.\" or \"Okay, "
        "let's move on.\" Acknowledge neutrally; never state the score, never "
        "reveal whether the answer was right, never teach the correct answer."
        if voice_mode else ""
    )

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
  "answer_summary": "<brief neutral summary of what the candidate said, max 3 sentences>"{ack_key}
}}

Scoring guide:
  correctness  — factual / technical accuracy
  clarity      — how clearly and concisely the answer was communicated
  depth        — technical depth, nuance, and awareness of trade-offs

Set needs_followup=true when the answer is partially correct, superficial,
or misses an important aspect that a short follow-up could uncover.
Set needs_followup=false when the answer is complete, clearly wrong
(follow-up won't help), or already received a follow-up on this question.{ack_guide}
"""


def build_followup_prompt(
    track: str, difficulty: str, voice_mode: bool = False
) -> str:
    """
    Build the system prompt for follow-up probe generation.

    Functionally identical to ``build_question_prompt`` but with an
    extra instruction to be targeted rather than picking a new topic.
    Caller injects the feedback_note hint directly into the system prompt
    via ``LLMProvider.generate_followup``.
    """
    track_ctx = _TRACK_CONTEXT.get(track, "general technical topics")
    diff_ctx  = _DIFFICULTY_GUIDANCE.get(difficulty, "intermediate level")
    voice_ctx = f"\n\n{_VOICE_ADDENDUM}" if voice_mode else ""

    return (
        f"{_INTERVIEWER_BASE}{voice_ctx}\n\n"
        f"TOPIC AREA:\n{track_ctx}\n\n"
        f"DIFFICULTY LEVEL ({difficulty.upper()}):\n{diff_ctx}\n\n"
        "The candidate's previous answer was incomplete or superficial in a "
        "specific way (see the INTERNAL HINT below). Ask ONE targeted follow-up "
        "probe to help them demonstrate deeper understanding of exactly that gap. "
        "Do not introduce an entirely new topic."
    )
