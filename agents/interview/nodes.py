"""
agents/interview/nodes.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
LangGraph node functions for the mock interview agent.

Graph topology (see graph.py for the wiring):

    START
      ↓
    node_generate_question
      ↓                       ← returned to API layer for TTS
    [API layer submits answer]
      ↓
    node_evaluate_answer
      ↓
    router_after_eval
      ├── "followup"       → node_generate_followup → [API layer for TTS]
      ├── "next_question"  → node_generate_question  (loop)
      └── "end"            → node_end_session
                                 ↓
                               END

Each node:
  - Takes the full InterviewAgentState.
  - Returns only the keys it modifies (partial update pattern from graph.py).
  - Never commits to the database — that is handled by the API layer using
    InterviewRepository so the graph stays stateless and easily testable.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from agents.interview.fallback_questions import get_fallback_question
from agents.interview.llm_provider import LLMProvider, LLMProviderError
from agents.interview.prompts import (
    build_evaluator_prompt,
    build_followup_prompt,
    build_question_prompt,
)
from agents.interview.state import (
    MAX_FOLLOWUPS_PER_QUESTION,
    MAX_QUESTIONS_PER_SESSION,
    InterviewAgentState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# node_generate_question
# ---------------------------------------------------------------------------

async def node_generate_question(state: InterviewAgentState) -> InterviewAgentState:
    """
    Generate the next original interview question.

    Flow:
        1. Try Groq LLM with full conversation history.
        2. On failure fall back to the static question bank.
        3. Append the question as an assistant turn in conversation_history.
        4. Reset followup_count to 0 and mark is_followup=False.
    """
    track      = state["track"]
    difficulty = state["difficulty"]
    history    = list(state.get("conversation_history", []))
    q_index    = state.get("question_index", 0)
    voice_mode = state.get("voice_mode", False)

    system_prompt = build_question_prompt(track, difficulty, voice_mode)
    llm = LLMProvider()
    question: str | None = None

    try:
        question = await llm.generate_question(system_prompt, history)
        logger.info("LLM question generated (q=%d)", q_index)
    except LLMProviderError as exc:
        logger.warning("LLM question generation failed (q=%d): %s", q_index, exc)

    # Fallback: pick from static bank if LLM failed
    if not question:
        used = {msg["content"] for msg in history if msg["role"] == "assistant"}
        question = get_fallback_question(track, difficulty, used)
        if question:
            logger.info("Using fallback question (q=%d)", q_index)
        else:
            # Truly exhausted — end the session gracefully
            logger.warning("All fallback questions exhausted — ending session")
            return {
                "next_action": "end",
                "session_complete": True,
                "error": None,
            }

    updated_history = [*history, {"role": "assistant", "content": question}]

    return {
        "current_question": question,
        "conversation_history": updated_history,
        "followup_count": 0,
        "is_followup": False,
        "next_action": "awaiting_answer",  # not a routing key, just informational
    }


# ---------------------------------------------------------------------------
# node_evaluate_answer
# ---------------------------------------------------------------------------

async def node_evaluate_answer(state: InterviewAgentState) -> InterviewAgentState:
    """
    Score the user's answer and decide the next routing action.

    Flow:
        1. Append the user's answer to conversation_history.
        2. Call LLM evaluator to get sub-scores + follow-up decision.
        3. On LLM failure, assign neutral scores and skip follow-up.
        4. Accumulate score in ``scores`` list.
        5. Set ``next_action`` based on:
              - needs_followup=True AND followup_count < MAX → "followup"
              - question_index+1 >= MAX_QUESTIONS → "end"
              - else → "next_question"
    """
    track         = state["track"]
    difficulty    = state["difficulty"]
    history       = list(state.get("conversation_history", []))
    answer        = state.get("current_answer", "")
    question      = state.get("current_question", "")
    followup_count = state.get("followup_count", 0)
    question_index = state.get("question_index", 0)
    is_followup   = state.get("is_followup", False)
    scores        = list(state.get("scores", []))
    voice_mode    = state.get("voice_mode", False)

    # Append the user's answer to history
    updated_history = [*history, {"role": "user", "content": answer}]

    # Evaluate
    system_prompt = build_evaluator_prompt(track, difficulty, voice_mode)
    llm = LLMProvider()
    score_data: dict[str, Any]

    try:
        score_data = await llm.evaluate_answer(
            system_prompt, question, answer, track, difficulty, voice_mode
        )
        logger.info(
            "Answer evaluated: q=%d followup=%s correct=%.1f clarity=%.1f depth=%.1f",
            question_index, is_followup,
            score_data["correctness"], score_data["clarity"], score_data["depth"],
        )
    except LLMProviderError as exc:
        logger.warning("LLM evaluation failed (q=%d): %s — using neutral scores", question_index, exc)
        score_data = {
            "correctness": 5.0,
            "clarity": 5.0,
            "depth": 5.0,
            "needs_followup": False,
            "feedback_note": "Evaluation unavailable (LLM error).",
            "answer_summary": answer[:256],
            "verbal_ack": "Got it, thank you." if voice_mode else "",
        }

    # Attach positional metadata for the repository layer
    score_record = {
        **score_data,
        "question_index": len(scores),        # absolute index including follow-ups
        "question_text": question,
        "was_followup": is_followup,
    }
    scores.append(score_record)

    # Determine next action
    wants_followup = (
        score_data["needs_followup"]
        and followup_count < MAX_FOLLOWUPS_PER_QUESTION
    )

    # After a follow-up, always move to the next original question
    if is_followup:
        wants_followup = False

    next_original_q_index = question_index if is_followup else question_index + 1

    if wants_followup:
        next_action = "followup"
    elif next_original_q_index >= MAX_QUESTIONS_PER_SESSION:
        next_action = "end"
    else:
        next_action = "next_question"

    return {
        "conversation_history": updated_history,
        "last_score": score_data,
        "scores": scores,
        "next_action": next_action,
        # Advance original question index only when this was NOT a follow-up
        "question_index": next_original_q_index,
    }


# ---------------------------------------------------------------------------
# node_generate_followup
# ---------------------------------------------------------------------------

async def node_generate_followup(state: InterviewAgentState) -> InterviewAgentState:
    """
    Generate a targeted follow-up probe based on the evaluation feedback note.

    The feedback_note from the last evaluation is injected as a hidden
    system hint so the follow-up directly addresses the specific gap.
    """
    track         = state["track"]
    difficulty    = state["difficulty"]
    history       = list(state.get("conversation_history", []))
    followup_count = state.get("followup_count", 0)
    feedback_note  = state.get("last_score", {}).get("feedback_note", "")
    voice_mode     = state.get("voice_mode", False)

    system_prompt = build_followup_prompt(track, difficulty, voice_mode)
    llm = LLMProvider()
    followup: str | None = None

    try:
        followup = await llm.generate_followup(system_prompt, history, feedback_note)
        logger.info("Follow-up generated (count=%d)", followup_count + 1)
    except LLMProviderError as exc:
        logger.warning("LLM follow-up generation failed: %s", exc)

    # Fallback: generic probe if LLM fails
    if not followup:
        followup = "Could you elaborate a bit more on your last point?"

    updated_history = [*history, {"role": "assistant", "content": followup}]

    return {
        "current_question": followup,
        "conversation_history": updated_history,
        "followup_count": followup_count + 1,
        "is_followup": True,
        "next_action": "awaiting_answer",
    }


# ---------------------------------------------------------------------------
# node_end_session
# ---------------------------------------------------------------------------

async def node_end_session(state: InterviewAgentState) -> InterviewAgentState:
    """
    Compile the final session state.

    This node does NOT write to the database — the API router handles
    persistence via InterviewRepository after the graph returns.

    Returns:
        - session_complete=True
        - A closing assistant message appended to conversation_history
    """
    history = list(state.get("conversation_history", []))
    scores  = state.get("scores", [])
    track   = state.get("track", "")
    voice_mode = state.get("voice_mode", False)

    # Compute aggregate total (0–100) from accumulated scores
    if scores:
        avg_c  = sum(s["correctness"] for s in scores) / len(scores)
        avg_cl = sum(s["clarity"]     for s in scores) / len(scores)
        avg_d  = sum(s["depth"]       for s in scores) / len(scores)
        total_score = round(((avg_c + avg_cl + avg_d) / 30.0) * 100.0, 1)
    else:
        total_score = 0.0

    track_label = track.replace("_", " ").title()
    if voice_mode:
        # Spoken aloud, so no "question(s)" and no "/100" — both read badly.
        answered = "one question" if len(scores) == 1 else f"{len(scores)} questions"
        closing = (
            f"That's everything I had for the {track_label} round. "
            f"You worked through {answered}, and you scored "
            f"{total_score:.0f} out of 100 overall. "
            "Thanks for your time — your detailed feedback is on screen now."
        )
    else:
        closing = (
            f"That wraps up our {track_label} interview session! "
            f"You answered {len(scores)} question(s). "
            f"Your overall score is {total_score:.0f}/100. "
            "Great effort — check your detailed feedback on the results page."
        )
    updated_history = [*history, {"role": "assistant", "content": closing}]

    logger.info(
        "Interview session ended: questions=%d total_score=%.1f",
        len(scores), total_score,
    )

    return {
        "conversation_history": updated_history,
        "session_complete": True,
        "next_action": "end",
        "error": None,
    }


# ---------------------------------------------------------------------------
# Routing functions (conditional edges)
# ---------------------------------------------------------------------------

def router_after_eval(
    state: InterviewAgentState,
) -> Literal["node_generate_followup", "node_generate_question", "node_end_session"]:
    """
    Route after node_evaluate_answer based on the next_action signal.

    Returns the node name to execute next (LangGraph conditional edge).
    """
    action = state.get("next_action", "next_question")
    if action == "followup":
        return "node_generate_followup"
    elif action == "end":
        return "node_end_session"
    else:
        return "node_generate_question"
