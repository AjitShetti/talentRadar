"""
agents/learning_path.py
~~~~~~~~~~~~~~~~~~~~~~~
Generates a structured learning path based on missing skills using Groq.
"""

import logging
from typing import Any

from groq import Groq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.1-8b-instant"
_MAX_TOKENS = 2000
_TEMPERATURE = 0.5

_SYSTEM_PROMPT = """\
You are an expert technical mentor. The user will provide a list of technical skills they are missing for a target job.
Your task is to generate a comprehensive, structured Markdown learning path to help them acquire those skills.

### Structure:
- Provide a brief introductory paragraph.
- For each missing skill, create a section (## Skill Name) that includes:
  - Why it's important.
  - A 3-step learning plan (e.g., Fundamentals, Intermediate, Advanced).
  - Recommended free resources (documentation, tutorials, etc.).
- Conclude with a brief summary or words of encouragement.

### Rules:
- Output ONLY valid Markdown format.
- Do NOT output JSON.
- Be concise but highly actionable.
"""

class LearningPathGenerator:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        settings = get_settings()
        _key = api_key or settings.groq_api_key
        if not _key:
            raise ValueError("GROQ_API_KEY is not set.")
        self._client = Groq(api_key=_key)
        self._model = model

    def generate(self, missing_skills: list[str]) -> str:
        """
        Generate a markdown learning path for the given missing skills.
        """
        if not missing_skills:
            return "No missing skills identified! You are perfectly aligned with the job requirements."

        user_prompt = f"Please generate a learning path for the following missing skills: {', '.join(missing_skills)}"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        
        return self._call_llm(messages)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
        )
        return response.choices[0].message.content or ""
