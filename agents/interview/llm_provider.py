"""
agents/interview/llm_provider.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pluggable LLM client for the interview agent.

Design
------
* Abstracts over the underlying provider so swapping Groq → OpenAI
  → Anthropic requires only changing this file.
* Uses Groq; the model id comes from ``Settings.groq_interview_model``.
* Returns structured dicts parsed from the model's JSON output.
* Raises ``LLMProviderError`` on parse failures so nodes can catch
  it and emit a graceful fallback question rather than crashing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from groq import AsyncGroq

from config.settings import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


class LLMProviderError(Exception):
    """Raised when the LLM returns unparseable or empty output."""


class LLMProvider:
    """
    Thin async wrapper over the Groq chat completion API.

    All methods return plain Python dicts so the caller is not coupled
    to any provider-specific response object.
    """

    #: Fallback model id used when settings carry no override.
    #: Kept in sync with ``Settings.groq_interview_model`` — older Llama
    #: generations were decommissioned by Groq and returned 404s here.
    MODEL = "openai/gpt-oss-120b"

    #: A candidate is waiting on every call, so each attempt is time-boxed.
    #: Two retries stay: Groq's free-tier 429s usually ask for well under a
    #: second, and in end-to-end runs a single retry left answers unscored
    #: that a second one would have scored.
    TIMEOUT_SECONDS = 20.0
    MAX_RETRIES = 2

    def __init__(self, model: str | None = None) -> None:
        self._client = AsyncGroq(
            api_key=_settings.groq_api_key,
            timeout=self.TIMEOUT_SECONDS,
            max_retries=self.MAX_RETRIES,
        )
        self._model = model or _settings.groq_interview_model or self.MODEL

    @property
    def _is_reasoning_model(self) -> bool:
        """gpt-oss spends completion tokens on hidden reasoning before answering."""
        return "gpt-oss" in self._model

    # ------------------------------------------------------------------
    # Core completion helper
    # ------------------------------------------------------------------

    async def _chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        expect_json: bool = False,
    ) -> str:
        """
        Send a chat completion request and return the assistant's text.

        Args:
            system_prompt: The system message injected before ``messages``.
            messages:      OpenAI-style message list from state.conversation_history.
            temperature:   Sampling temperature (lower = more deterministic).
            max_tokens:    Upper limit on generated tokens.
            expect_json:   If True, pass ``response_format`` hint to coerce JSON.

        Raises:
            LLMProviderError: On empty or missing response content.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._is_reasoning_model:
            # Reasoning tokens count against max_tokens. With the old 200-256
            # budgets the model reasoned, then got cut off mid-question — the
            # candidate heard "...and how they" — or mid-JSON, which Groq
            # rejects as "Failed to generate JSON". Low effort plus headroom
            # fixes both without making the turn noticeably slower.
            kwargs["reasoning_effort"] = "low"
            kwargs["max_tokens"] = max_tokens + 1024
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}

        # Every provider-side failure (decommissioned model, rate limit,
        # network blip) is normalised to LLMProviderError so the nodes can
        # fall back to the static question bank instead of 500-ing the API.
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if expect_json and "json_validate_failed" in str(exc):
                # Groq's JSON mode rejects output it cannot validate. One
                # plain retry, parsed leniently, rescues most of these.
                logger.info("Groq JSON mode rejected output; retrying without it")
                kwargs.pop("response_format", None)
                try:
                    response = await self._client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    raise self._provider_error(retry_exc) from retry_exc
            else:
                raise self._provider_error(exc) from exc

        choice = response.choices[0]
        content = choice.message.content
        if not content:
            raise LLMProviderError("LLM returned empty content")
        if choice.finish_reason == "length":
            # A truncated question is worse than a fallback one: it is spoken
            # aloud and the candidate cannot tell it was cut off.
            raise LLMProviderError("LLM output was truncated at the token limit")
        return content.strip()

    def _provider_error(self, exc: Exception) -> LLMProviderError:
        logger.warning("Groq call failed (model=%s): %s", self._model, exc)
        return LLMProviderError(f"Groq request failed for model {self._model!r}: {exc}")

    # ------------------------------------------------------------------
    # Interview-specific methods
    # ------------------------------------------------------------------

    async def generate_question(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, Any]],
    ) -> str:
        """
        Generate the next interview question based on conversation history.

        Returns plain text (the question string to speak / display).
        """
        raw = await self._chat(
            system_prompt,
            conversation_history,
            temperature=0.8,
            max_tokens=256,
            expect_json=False,
        )
        return raw

    async def generate_followup(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, Any]],
        feedback_note: str,
    ) -> str:
        """
        Generate a targeted follow-up probe based on the evaluation note.

        The ``feedback_note`` (e.g. "Answer was too shallow on complexity")
        is appended as a hidden system hint so the follow-up is calibrated.
        """
        augmented_system = (
            f"{system_prompt}\n\n"
            f"[INTERNAL HINT — do not repeat this to the user]\n"
            f"The previous answer was evaluated as: {feedback_note}\n"
            f"Generate ONE focused follow-up question to probe deeper on "
            f"exactly that gap. Keep it under 2 sentences."
        )
        raw = await self._chat(
            augmented_system,
            conversation_history,
            temperature=0.7,
            max_tokens=200,
            expect_json=False,
        )
        return raw

    async def evaluate_answer(
        self,
        system_prompt: str,
        question: str,
        answer: str,
        track: str,
        difficulty: str,
        voice_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Score the user's answer and decide whether a follow-up is needed.

        Returns a dict with keys:
            correctness    : float 0-10
            clarity        : float 0-10
            depth          : float 0-10
            needs_followup : bool
            feedback_note  : str   (LLM reasoning, NOT shown to user)
            answer_summary : str   (brief summary of what the user said)
            verbal_ack     : str   (spoken reaction; "" outside voice_mode)

        Raises:
            LLMProviderError: If the JSON cannot be parsed or is missing keys.
        """
        eval_messages = [
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Candidate Answer: {answer}\n\n"
                    f"Track: {track} | Difficulty: {difficulty}\n\n"
                    "Evaluate the answer and return a JSON object with the "
                    "exact keys: correctness, clarity, depth, needs_followup, "
                    "feedback_note, answer_summary, tip"
                    + (", verbal_ack." if voice_mode else ".")
                ),
            }
        ]
        raw = await self._chat(
            system_prompt,
            eval_messages,
            temperature=0.3,   # lower temp → more consistent scoring
            max_tokens=512,
            expect_json=True,
        )
        return self._parse_eval_json(raw)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_eval_json(self, raw: str) -> dict[str, Any]:
        """Parse and validate the evaluation JSON returned by the LLM."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # The non-JSON-mode retry may wrap the object in prose or a fence.
            start, end = raw.find("{"), raw.rfind("}")
            try:
                data = json.loads(raw[start : end + 1]) if 0 <= start < end else None
            except json.JSONDecodeError:
                data = None
            if not isinstance(data, dict):
                raise LLMProviderError(f"LLM eval output is not valid JSON: {raw!r}") from None

        required = {
            "correctness", "clarity", "depth",
            "needs_followup", "feedback_note", "answer_summary",
        }
        missing = required - data.keys()
        if missing:
            raise LLMProviderError(f"LLM eval missing keys: {missing} — raw={raw!r}")

        # Coerce and clamp sub-scores
        for field in ("correctness", "clarity", "depth"):
            try:
                data[field] = max(0.0, min(10.0, float(data[field])))
            except (TypeError, ValueError):
                data[field] = 0.0

        data["needs_followup"] = bool(data["needs_followup"])
        data["feedback_note"] = str(data.get("feedback_note", ""))[:512]
        data["answer_summary"] = str(data.get("answer_summary", ""))[:1024]
        # Optional — only requested in voice mode, and the model may still omit
        # it, so it is normalised rather than treated as a required key.
        data["verbal_ack"] = str(data.get("verbal_ack") or "")[:256]
        # Optional coaching line shown on screen after the answer.
        data["tip"] = str(data.get("tip") or "")[:400]
        return data
