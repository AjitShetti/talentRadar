"""
agents/interview/state.py
~~~~~~~~~~~~~~~~~~~~~~~~~
TypedDict state schema for the LangGraph interview agent.

Design notes
------------
* Uses ``total=False`` so every key is optional — nodes only return
  the fields they actually update, matching the LangGraph pattern in
  agents/graph.py (AgentState).
* ``conversation_history`` carries the full Q&A context sent to the
  LLM on every turn so the model remembers previous questions.
* ``followup_count`` is reset to 0 on each new question and capped at
  MAX_FOLLOWUPS_PER_QUESTION (1 for v1) to prevent infinite loops.
* ``next_action`` is the routing signal read by the graph's conditional
  edge after ``node_evaluate_answer``.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Maximum number of follow-up probes allowed per original question.
# The graph uses this constant to guard against infinite loops.
MAX_FOLLOWUPS_PER_QUESTION: int = 1

# Maximum questions before the session is force-ended regardless of timer.
MAX_QUESTIONS_PER_SESSION: int = 15


class InterviewAgentState(TypedDict, total=False):
    """
    State that flows through every node in the interview graph.

    Lifecycle
    ---------
    1. Initialised by ``POST /sessions/start`` with track / difficulty.
    2. Grows each turn as questions, answers, and scores accumulate.
    3. Returned to the API layer after each node; the frontend stores
       ``conversation_history`` in React state (stateless backend).
    """

    # ------------------------------------------------------------------ #
    # Session config — set once on session start                           #
    # ------------------------------------------------------------------ #
    track: str
    """Catalog track: python_dsa | python_backend | sql | system_design"""

    difficulty: str
    """Difficulty level chosen by the user: beginner | mid | senior"""

    user_id: str
    """UUID of the authenticated user (string form for JSON serialisation)."""

    voice_mode: bool
    """
    True when the session is conducted hands-free over voice.

    Voice mode changes *generation*, not routing: prompts switch to a spoken
    register (short, self-contained, no code blocks or bullet lists — none of
    which survive text-to-speech) and the evaluator additionally returns a
    ``verbal_ack`` the interviewer says before the next question.
    """

    # ------------------------------------------------------------------ #
    # Conversation history — grows each turn                              #
    # ------------------------------------------------------------------ #
    conversation_history: list[dict[str, Any]]
    """
    Full Q&A history in OpenAI-style message format:
        [{"role": "assistant", "content": "<question>"},
         {"role": "user",      "content": "<answer>"},
         ...]
    Sent to the LLM on every turn so it never repeats a question.
    """

    # ------------------------------------------------------------------ #
    # Turn-level state                                                     #
    # ------------------------------------------------------------------ #
    question_index: int
    """0-based position of the current original question (not follow-ups)."""

    followup_count: int
    """Number of follow-up probes for the CURRENT question (reset each Q)."""

    current_question: str
    """The question text returned to the frontend for display + TTS."""

    current_answer: str
    """The user's transcribed or typed answer submitted this turn."""

    is_followup: bool
    """True when the current turn is answering a follow-up probe."""

    # ------------------------------------------------------------------ #
    # Evaluation output                                                    #
    # ------------------------------------------------------------------ #
    last_score: dict[str, Any]
    """
    Per-turn score dict from node_evaluate_answer:
        {
            "correctness": float,   # 0-10
            "clarity":     float,   # 0-10
            "depth":       float,   # 0-10
            "needs_followup": bool,
            "feedback_note": str,   # brief LLM reasoning (not shown to user)
            "answer_summary": str,  # short summary of what was said
            "verbal_ack": str,      # spoken reaction, voice_mode only ("" otherwise)
        }
    """

    scores: list[dict[str, Any]]
    """Accumulated per-turn scores list (appended each turn)."""

    # ------------------------------------------------------------------ #
    # Routing signals                                                      #
    # ------------------------------------------------------------------ #
    next_action: str
    """
    Routing decision set by node_evaluate_answer / node_check_session:
        "followup"       — generate a follow-up probe
        "next_question"  — move to the next original question
        "end"            — session complete, compile final score
    """

    session_complete: bool
    """True when the session has been fully concluded."""

    # ------------------------------------------------------------------ #
    # Error                                                                #
    # ------------------------------------------------------------------ #
    error: str | None
    """Non-None when a node encountered an unrecoverable error."""
