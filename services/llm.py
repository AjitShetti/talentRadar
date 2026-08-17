"""
services/llm.py
~~~~~~~~~~~~~~~
Thin LLM helpers used by the deterministic service layer.

Centralises Groq client creation, retry policy, and a small set of
prompt-driven generators (market summary, cover letter, career advice,
ATS analysis). Agents never call the LLM directly — they go through
these helpers or the higher-level services.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from groq import AsyncGroq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.3-70b-versatile"
_FAST_MODEL = "llama-3.1-8b-instant"


def get_llm() -> AsyncGroq:
    settings = get_settings()
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is not set.")
    return AsyncGroq(api_key=settings.groq_api_key)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=True,
)
async def _chat(
    system: str,
    user: str,
    *,
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 800,
    json_mode: bool = False,
) -> str:
    client = get_llm()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


async def generate_ats_analysis(resume_text: str, jd_text: str) -> dict[str, Any]:
    """LLM ATS-gap analysis: match score, missing skills, reasoning (JSON)."""
    system = """\
You are an expert ATS resume analyst. Evaluate how well a resume matches a job
description. Return a single JSON object with EXACTLY:
{
  "ats_score": 0..100,
  "missing_skills": [string],
  "matched_skills": [string],
  "suggestions": [string],
  "reasoning": string
}
Return ONLY JSON, no preamble."""
    prompt = f"### JOB DESCRIPTION:\n{jd_text}\n\n### RESUME:\n{resume_text}"
    try:
        raw = await _chat(system, prompt, temperature=0.2, max_tokens=700, json_mode=True)
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ATS analysis failed: %s", exc)
        return {
            "ats_score": 0.0,
            "missing_skills": [],
            "matched_skills": [],
            "suggestions": [],
            "reasoning": f"ATS analysis unavailable: {exc}",
        }


async def generate_cover_letter(
    *,
    resume_text: str,
    jd_text: str,
    job_title: str,
    company: str,
    tone: str = "professional",
) -> str:
    """Generate a tailored cover letter for a job."""
    system = f"""\
You are an expert career coach. Write a compelling cover letter in a
{tone} tone. Use the candidate's real experience only — do not invent
facts. Keep it under 350 words. Output plain text, no markdown headers."""
    prompt = f"""\
### JOB TITLE: {job_title}
### COMPANY: {company}
### JOB DESCRIPTION:
{jd_text[:4000]}

### RESUME:
{resume_text[:4000]}
"""
    try:
        return await _chat(system, prompt, temperature=0.5, max_tokens=700)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cover letter generation failed: %s", exc)
        raise ValueError(f"Cover letter generation failed: {exc}") from exc


async def generate_career_advice(*, profile: dict[str, Any], gaps: list[str]) -> list[dict[str, str]]:
    """Career coach: turn skill gaps into actionable learning tasks (JSON list)."""
    system = """\
You are a senior technical career coach. Given a candidate profile and a list
of skill gaps, return a JSON array of learning recommendations. Each item:
{
  "skill_name": string,
  "title": string,
  "description": string,
  "resources": [string],
  "priority": 1..5
}
Return ONLY a JSON array."""
    prompt = (
        f"### PROFILE:\n{json.dumps(profile, default=str)}\n\n"
        f"### SKILL GAPS:\n{json.dumps(gaps)}"
    )
    try:
        raw = await _chat(system, prompt, temperature=0.4, max_tokens=900, json_mode=True)
        data = json.loads(raw)
        if isinstance(data, dict):  # tolerate {"recommendations": [...]}
            data = data.get("recommendations", [])
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Career advice generation failed: %s", exc)
        return []
