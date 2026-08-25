"""
tests/test_interview_voice.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for voice mode in the LangGraph interview agent.

Voice mode is a *generation* concern, not a routing one: the graph topology is
identical either way, so these cover the three things that actually differ —
the spoken-delivery constraints in the prompts, the ``verbal_ack`` that rides
along on the evaluation call, and the spoken closing message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agents.interview.llm_provider import LLMProvider, LLMProviderError
from agents.interview.nodes import (
    node_end_session,
    node_evaluate_answer,
    node_generate_question,
)
from agents.interview.prompts import (
    build_evaluator_prompt,
    build_followup_prompt,
    build_question_prompt,
)
from api.schemas.interview_schemas import StartSessionRequest

if TYPE_CHECKING:
    from collections.abc import Callable


def _state(**overrides: object) -> dict[str, Any]:
    """Minimal interview state; overrides win."""
    base: dict[str, Any] = {
        "track": "python_backend",
        "difficulty": "mid",
        "user_id": "test-user",
        "voice_mode": True,
        "conversation_history": [],
        "question_index": 0,
        "followup_count": 0,
        "scores": [],
        "current_question": "How does async I/O work in FastAPI?",
        "current_answer": "It runs coroutines on an event loop.",
        "is_followup": False,
        "session_complete": False,
        "error": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

class TestVoicePrompts:
    """Voice mode must reach the model as an explicit delivery constraint."""

    @pytest.mark.parametrize(
        "builder", [build_question_prompt, build_followup_prompt]
    )
    def test_spoken_constraints_only_in_voice_mode(
        self, builder: Callable[[str, str, bool], str]
    ) -> None:
        spoken = builder("python_dsa", "senior", True)
        typed = builder("python_dsa", "senior", False)

        assert "SPOKEN INTERVIEW" in spoken
        assert "code blocks" in spoken
        assert "SPOKEN INTERVIEW" not in typed

    def test_voice_mode_defaults_off(self) -> None:
        assert "SPOKEN INTERVIEW" not in build_question_prompt("sql", "mid")

    def test_track_and_difficulty_survive_voice_mode(self) -> None:
        prompt = build_question_prompt("sql", "senior", True)
        assert "window" in prompt          # SQL track context
        assert "SENIOR" in prompt

    def test_evaluator_requests_verbal_ack_in_voice_mode(self) -> None:
        assert "verbal_ack" in build_evaluator_prompt("sql", "mid", True)
        assert "verbal_ack" not in build_evaluator_prompt("sql", "mid", False)

    def test_evaluator_ack_must_not_leak_the_score(self) -> None:
        prompt = build_evaluator_prompt("sql", "mid", True)
        assert "never state the score" in prompt


# ---------------------------------------------------------------------------
# Evaluation parsing
# ---------------------------------------------------------------------------

class TestVerbalAckParsing:
    """``verbal_ack`` is optional — the model may simply omit it."""

    _BASE = (
        '{"correctness": 8, "clarity": 7, "depth": 6, "needs_followup": false, '
        '"feedback_note": "solid", "answer_summary": "explained the event loop"'
    )

    def test_missing_ack_normalises_to_empty(self) -> None:
        parsed = LLMProvider._parse_eval_json(LLMProvider, self._BASE + "}")  # type: ignore[arg-type]
        assert parsed["verbal_ack"] == ""

    def test_ack_is_kept_when_present(self) -> None:
        raw = self._BASE + ', "verbal_ack": "Right, that tracks."}'
        parsed = LLMProvider._parse_eval_json(LLMProvider, raw)  # type: ignore[arg-type]
        assert parsed["verbal_ack"] == "Right, that tracks."

    def test_ack_is_length_capped(self) -> None:
        raw = self._BASE + f', "verbal_ack": "{"x" * 400}"}}'
        parsed = LLMProvider._parse_eval_json(LLMProvider, raw)  # type: ignore[arg-type]
        assert len(parsed["verbal_ack"]) == 256

    def test_ack_does_not_become_a_required_key(self) -> None:
        """A missing ack must not fail parsing the way a missing score does."""
        with pytest.raises(LLMProviderError):
            LLMProvider._parse_eval_json(  # type: ignore[arg-type]
                LLMProvider, '{"correctness": 8}'
            )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

class TestVoiceModeNodes:
    """The nodes must carry voice_mode through to every LLM call."""

    async def test_question_node_passes_voice_mode_to_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, str] = {}

        async def fake_generate(
            self: object, system_prompt: str, history: list[dict[str, str]]
        ) -> str:
            seen["prompt"] = system_prompt
            return "Walk me through how you would cache that."

        monkeypatch.setattr(LLMProvider, "generate_question", fake_generate)
        result = await node_generate_question(_state())  # type: ignore[arg-type]

        assert "SPOKEN INTERVIEW" in seen["prompt"]
        assert result["current_question"].startswith("Walk me through")

    async def test_evaluate_node_forwards_voice_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, bool] = {}

        async def fake_evaluate(
            self: object, system_prompt: str, question: str, answer: str,
            track: str, difficulty: str, voice_mode: bool = False,
        ) -> dict[str, Any]:
            seen["voice_mode"] = voice_mode
            return {
                "correctness": 7.0, "clarity": 8.0, "depth": 6.0,
                "needs_followup": False, "feedback_note": "ok",
                "answer_summary": "event loop", "verbal_ack": "Got it.",
            }

        monkeypatch.setattr(LLMProvider, "evaluate_answer", fake_evaluate)
        result = await node_evaluate_answer(_state())  # type: ignore[arg-type]

        assert seen["voice_mode"] is True
        assert result["last_score"]["verbal_ack"] == "Got it."

    async def test_evaluation_failure_still_yields_a_spoken_ack(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dead air is worse than a generic line, so the fallback speaks too."""
        async def boom(*args: object, **kwargs: object) -> dict[str, Any]:
            raise LLMProviderError("groq down")

        monkeypatch.setattr(LLMProvider, "evaluate_answer", boom)
        voice = await node_evaluate_answer(_state())  # type: ignore[arg-type]
        typed = await node_evaluate_answer(_state(voice_mode=False))  # type: ignore[arg-type]

        assert voice["last_score"]["verbal_ack"] == "Got it, thank you."
        assert typed["last_score"]["verbal_ack"] == ""

    async def test_closing_message_is_speakable_in_voice_mode(self) -> None:
        scores = [{"correctness": 8.0, "clarity": 8.0, "depth": 8.0}]
        result = await node_end_session(_state(scores=scores))  # type: ignore[arg-type]
        closing = result["conversation_history"][-1]["content"]

        # "question(s)" and "80/100" are unintelligible read aloud.
        assert "question(s)" not in closing
        assert "/100" not in closing
        assert "out of 100" in closing
        assert result["session_complete"] is True

    async def test_closing_message_singular_reads_naturally(self) -> None:
        result = await node_end_session(  # type: ignore[arg-type]
            _state(scores=[{"correctness": 5.0, "clarity": 5.0, "depth": 5.0}])
        )
        assert "one question" in result["conversation_history"][-1]["content"]

    async def test_typed_closing_message_is_unchanged(self) -> None:
        scores = [{"correctness": 8.0, "clarity": 8.0, "depth": 8.0}] * 2
        result = await node_end_session(  # type: ignore[arg-type]
            _state(voice_mode=False, scores=scores)
        )
        assert "question(s)" in result["conversation_history"][-1]["content"]


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

class TestStartSessionContract:
    def test_voice_mode_is_opt_in(self) -> None:
        assert StartSessionRequest(track="sql", difficulty="mid").voice_mode is False

    def test_voice_mode_accepted(self) -> None:
        body = StartSessionRequest(track="sql", difficulty="mid", voice_mode=True)
        assert body.voice_mode is True
