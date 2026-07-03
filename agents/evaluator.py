"""
agents/evaluator.py
~~~~~~~~~~~~~~~~~~~
LLM-as-a-judge agent to evaluate a candidate's resume against a job description.
"""

import json
import logging
import time
from typing import Any

from groq import Groq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.1-8b-instant"
_MAX_TOKENS = 1500
_TEMPERATURE = 0.0

_SYSTEM_PROMPT = """\
You are an expert technical recruiter and resume evaluator. Your task is to evaluate 
how well a candidate's resume matches a given job description.

You must return a single valid JSON object containing exactly the following schema:
{
  "match_score": number,          // 0 to 100 representing how well the candidate matches the job
  "missing_skills": [string, ...], // List of critical skills required by the JD that are NOT in the resume
  "reasoning": string             // A detailed, constructive explanation of the score and missing skills
}

### Rules:
- Return ONLY the JSON object, with NO additional text, preamble, or explanation.
- Be objective and critical. A 100 match means perfect alignment.
- Extract missing skills directly from the job description's requirements.
"""

class CandidateEvaluator:
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

    def evaluate(self, resume_text: str, jd_text: str) -> dict[str, Any]:
        """
        Evaluate resume against JD and return structured JSON.
        """
        user_prompt = f"### JOB DESCRIPTION:\n{jd_text}\n\n### CANDIDATE RESUME:\n{resume_text}"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        
        raw_response = self._call_llm(messages)
        return self._extract_json(raw_response)

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
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON: {exc}. Text: {text[:300]}")
