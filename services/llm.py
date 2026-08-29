"""
services/llm.py
~~~~~~~~~~~~~~~
Thin LLM helpers used by the deterministic service layer.

Centralises Groq client creation, retry policy, and a small set of
prompt-driven generators (market summary, cover letter, career advice,
ATS analysis, role readiness). Agents never call the LLM directly — they go through
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

# Model ids live in settings so a decommissioned model can be swapped from .env.
_settings = get_settings()
_DEFAULT_MODEL = _settings.groq_model
_FAST_MODEL = _settings.groq_fast_model


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


async def generate_role_readiness(
    *,
    target_roles: list[str],
    resume_text: str,
    profile_skills: list[str],
    candidate_skills: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compare a resume against the user's target roles.

    Returns ``{"missing_skills": [{skill, reason}], "resume_improvements":
    [{title, reason}]}``. ``candidate_skills`` is the deterministic shortlist
    of in-demand skills (with posting counts) that were not detected in the
    resume — the model narrows and explains it rather than inventing demand.

    When the resume already covers what the target roles ask for,
    ``missing_skills`` comes back empty and ``resume_improvements`` carries
    the "you're covered, here's how to present it better" advice instead.
    """
    system = """\
You are a senior technical recruiter reviewing one candidate's resume against
the roles they are targeting in India.

Return a single JSON object with EXACTLY:
{
  "missing_skills": [{"skill": string, "reason": string}],
  "resume_improvements": [{"title": string, "reason": string}]
}

Rules:
- "missing_skills": AT MOST 3, and only skills that are (a) genuinely absent
  from the resume and (b) materially expected for the target roles. Prefer the
  ones named in CANDIDATE GAPS, which are ranked by real posting demand. Drop
  any that the resume already demonstrates under a different name. Order most
  consequential first. Each "reason" is ONE sentence (max 20 words) saying why
  that role needs it — no filler, no praise.
- If the resume already covers the essentials for these roles, return
  "missing_skills": [] and instead give 2-3 "resume_improvements": concrete,
  specific changes to how the resume is written or evidenced (quantified
  impact, missing scale/ownership signals, keyword alignment, structure) that
  would make this candidate read as ready for the target role. Ground every
  one in something actually present in the resume.
- When you do return missing skills, leave "resume_improvements" empty.
- Never invent experience the resume does not show. Return ONLY JSON."""
    prompt = (
        f"### TARGET ROLES:\n{json.dumps(target_roles)}\n\n"
        f"### SKILLS THEY LISTED ON THEIR PROFILE:\n{json.dumps(profile_skills)}\n\n"
        "### CANDIDATE GAPS (in-demand for these roles, not detected in the resume):\n"
        f"{json.dumps(candidate_skills)}\n\n"
        f"### RESUME:\n{resume_text[:6000]}"
    )
    raw = await _chat(system, prompt, temperature=0.2, max_tokens=700, json_mode=True)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Role readiness analysis returned a non-object payload")
    return data


_RESUME_STRUCTURE_SYSTEM_PROMPT = """\
You turn free-text resume content into a structured JSON document. Extract
ONLY what is actually present in the text — never invent names, dates,
companies, schools, or numbers.

Return a single JSON object with EXACTLY these top-level keys: "schema_version"
(always 1), "personal" (object), "sections" (array). Do not wrap it in any
other key such as "resume" or "document".

"personal" has EXACTLY these keys: "full_name", "headline", "email", "phone",
"location" (all strings), and "links" (array of {"label": string, "url": string}).

Each entry in "sections" has EXACTLY these keys: "id" (short slug), "type"
(one of "summary", "education", "experience", "projects", "skills",
"certifications", "custom"), "title" (string), "visible" (always true),
"order" (integer starting at 0), and "items" (array whose shape depends on
"type" — use these EXACT key names, never substitute synonyms like
"position", "date_range", or "details"):

- type "summary": items is [{"text": string}]
- type "education": items is [{"school": string, "location": string, "degree": string, "dates": string, "bullets": [string]}]
- type "experience": items is [{"title": string, "company": string, "location": string, "dates": string, "bullets": [string]}]
- type "projects": items is [{"name": string, "tech": string, "dates": string, "link": string, "bullets": [string]}]
- type "skills": items is [{"category": string, "items": [string]}] — group related skills under one row per category (e.g. one row for "Languages", one for "Tools"), never one row per individual skill
- type "certifications": items is [{"name": string, "issuer": string, "date": string}]
- type "custom": items is [{"heading": string, "bullets": [string]}]

Only include sections that have at least one real item, and only the fields
shown above — no extra keys.

### EXAMPLE
Input resume text:
Priya Nair
priya@example.com | Bengaluru

Summary
Frontend engineer focused on performance.

Experience
Frontend Engineer, Acme Corp (2021-2024)
- Cut bundle size by 30%

Skills
React, TypeScript

Output:
{"schema_version":1,"personal":{"full_name":"Priya Nair","headline":"","email":"priya@example.com","phone":"","location":"Bengaluru","links":[]},"sections":[{"id":"sum","type":"summary","title":"Summary","visible":true,"order":0,"items":[{"text":"Frontend engineer focused on performance."}]},{"id":"exp","type":"experience","title":"Experience","visible":true,"order":1,"items":[{"title":"Frontend Engineer","company":"Acme Corp","location":"","dates":"2021-2024","bullets":["Cut bundle size by 30%"]}]},{"id":"skl","type":"skills","title":"Skills","visible":true,"order":2,"items":[{"category":"Languages","items":["React","TypeScript"]}]}]}

Return ONLY JSON, matching this exact structure."""

_EXPERIENCE_ALIASES = {
    "title": ["title", "position", "role", "job_title", "jobTitle"],
    "company": ["company", "employer", "organization", "org"],
    "location": ["location", "city"],
    "dates": ["dates", "date_range", "duration", "period"],
    "bullets": ["bullets", "details", "highlights", "achievements", "responsibilities"],
}
_EDUCATION_ALIASES = {
    "school": ["school", "institution", "university", "college"],
    "location": ["location", "city"],
    "degree": ["degree", "degree_name", "qualification"],
    "dates": ["dates", "date_range", "duration", "period"],
    "bullets": ["bullets", "details", "highlights"],
}
_PROJECT_ALIASES = {
    "name": ["name", "title", "project_name"],
    "tech": ["tech", "technologies", "stack", "tools"],
    "dates": ["dates", "date_range", "duration"],
    "link": ["link", "url", "href"],
    "bullets": ["bullets", "details", "highlights"],
}
_CERT_ALIASES = {
    "name": ["name", "title"],
    "issuer": ["issuer", "organization", "org", "authority"],
    "date": ["date", "date_range", "year"],
}
_CUSTOM_ALIASES = {
    "heading": ["heading", "title", "name"],
    "bullets": ["bullets", "details", "highlights"],
}
_VALID_SECTION_TYPES = {"summary", "education", "experience", "projects", "skills", "certifications", "custom"}


def _pick_str(item: dict[str, Any], aliases: list[str]) -> str:
    for key in aliases:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _pick_list(item: dict[str, Any], aliases: list[str]) -> list[str]:
    for key in aliases:
        val = item.get(key)
        if isinstance(val, list):
            return [str(v).strip() for v in val if v and str(v).strip()]
    return []


def _normalize_item(item: Any, section_type: str) -> dict[str, Any]:  # noqa: ANN401
    """Coerce one LLM-produced item to the editor's exact per-type key names.

    Models given a JSON-object contract still drift toward synonyms
    ("position" for "title", "details" for "bullets") despite the prompt
    spelling out exact keys — this maps the common ones back rather than
    silently rendering blank fields in the editor.
    """
    if not isinstance(item, dict):
        item = {}
    if section_type == "summary":
        return {"text": _pick_str(item, ["text", "summary", "content"])}
    if section_type == "experience":
        return {
            "title": _pick_str(item, _EXPERIENCE_ALIASES["title"]),
            "company": _pick_str(item, _EXPERIENCE_ALIASES["company"]),
            "location": _pick_str(item, _EXPERIENCE_ALIASES["location"]),
            "dates": _pick_str(item, _EXPERIENCE_ALIASES["dates"]),
            "bullets": _pick_list(item, _EXPERIENCE_ALIASES["bullets"]),
        }
    if section_type == "education":
        return {
            "school": _pick_str(item, _EDUCATION_ALIASES["school"]),
            "location": _pick_str(item, _EDUCATION_ALIASES["location"]),
            "degree": _pick_str(item, _EDUCATION_ALIASES["degree"]),
            "dates": _pick_str(item, _EDUCATION_ALIASES["dates"]),
            "bullets": _pick_list(item, _EDUCATION_ALIASES["bullets"]),
        }
    if section_type == "projects":
        return {
            "name": _pick_str(item, _PROJECT_ALIASES["name"]),
            "tech": _pick_str(item, _PROJECT_ALIASES["tech"]),
            "dates": _pick_str(item, _PROJECT_ALIASES["dates"]),
            "link": _pick_str(item, _PROJECT_ALIASES["link"]),
            "bullets": _pick_list(item, _PROJECT_ALIASES["bullets"]),
        }
    if section_type == "certifications":
        return {
            "name": _pick_str(item, _CERT_ALIASES["name"]),
            "issuer": _pick_str(item, _CERT_ALIASES["issuer"]),
            "date": _pick_str(item, _CERT_ALIASES["date"]),
        }
    # custom
    return {
        "heading": _pick_str(item, _CUSTOM_ALIASES["heading"]),
        "bullets": _pick_list(item, _CUSTOM_ALIASES["bullets"]),
    }


def _normalize_skills_items(items_raw: list[Any]) -> list[dict[str, Any]]:
    """Group skills into {category, items} rows, tolerating a flat
    [{"name": "SQL"}, ...] list some models produce instead of grouping."""
    grouped: list[dict[str, Any]] = []
    flat_names: list[str] = []
    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        values = _pick_list(raw, ["items", "skills", "values"])
        if values:
            category = _pick_str(raw, ["category", "group", "type"]) or "Skills"
            grouped.append({"category": category, "items": values})
            continue
        name = _pick_str(raw, ["name", "skill", "title"])
        if name:
            flat_names.append(name)
    if flat_names:
        grouped.append({"category": "Skills", "items": flat_names})
    return grouped


def _unwrap_document(data: Any) -> dict[str, Any]:  # noqa: ANN401
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    if not isinstance(data, dict):
        return {}
    if "personal" not in data and "sections" not in data:
        for key in ("resume", "document", "result", "data"):
            inner = data.get(key)
            if isinstance(inner, dict):
                return inner
    return data


def _normalize_resume_document(raw_data: Any) -> dict[str, Any]:  # noqa: ANN401
    data = _unwrap_document(raw_data)
    personal_raw = data.get("personal") if isinstance(data.get("personal"), dict) else {}
    personal = {
        "full_name": _pick_str(personal_raw, ["full_name", "name"]),
        "headline": _pick_str(personal_raw, ["headline", "title"]),
        "email": _pick_str(personal_raw, ["email"]),
        "phone": _pick_str(personal_raw, ["phone"]),
        "location": _pick_str(personal_raw, ["location"]),
        "links": [
            {"label": _pick_str(link, ["label"]), "url": _pick_str(link, ["url"])}
            for link in (personal_raw.get("links") or [])
            if isinstance(link, dict) and _pick_str(link, ["url"])
        ],
    }

    sections_out: list[dict[str, Any]] = []
    for idx, section in enumerate(data.get("sections") or []):
        if not isinstance(section, dict):
            continue
        section_type = str(section.get("type") or "").strip().lower()
        if section_type not in _VALID_SECTION_TYPES:
            section_type = "custom"
        items_raw = section.get("items")
        items_raw = items_raw if isinstance(items_raw, list) else []

        if section_type == "skills":
            items = _normalize_skills_items(items_raw)
        else:
            items = [_normalize_item(item, section_type) for item in items_raw]
            items = [i for i in items if any(v for v in i.values())]

        if not items:
            continue

        sections_out.append({
            "id": str(section.get("id") or f"s{idx}"),
            "type": section_type,
            "title": str(section.get("title") or section_type.replace("_", " ").title()),
            "visible": True,
            "order": idx,
            "items": items,
        })

    return {"schema_version": 1, "personal": personal, "sections": sections_out}


async def extract_resume_structure(resume_text: str) -> dict[str, Any]:
    """
    Parse free-text resume content into the Resume Studio editor's
    structured document shape (personal block + ordered sections), so a
    user with an already-uploaded resume gets a populated editor instead of
    a blank one on first visit.

    Never fabricates content — every field must trace back to the input
    text; a heading with nothing to put in it should be omitted. The raw
    LLM output is run through _normalize_resume_document() because models
    reliably drift toward synonym keys ("position" for "title") even when
    the prompt spells out exact ones — silently rendering blank fields in
    the editor is worse than a defensive remap.
    """
    prompt = f"### RESUME TEXT:\n{resume_text[:8000]}"
    raw = await _chat(_RESUME_STRUCTURE_SYSTEM_PROMPT, prompt, temperature=0.1, max_tokens=2000, json_mode=True)
    data = json.loads(raw)
    document = _normalize_resume_document(data)
    if not document["personal"]["full_name"] and not document["sections"]:
        raise ValueError("Resume structure extraction produced an empty document")
    return document


async def generate_copilot_reply(*, question: str, context: dict[str, Any]) -> str:
    """
    Career Copilot: narrate a structured agent result as a short chat reply.

    The context dict is the *only* permitted source of facts — the copilot sits
    on top of the user's real tracker, so an invented number is worse than a
    vague answer.
    """
    system = """\
You are TalentRadar's career copilot, talking to one job seeker about their own
job search in India. You are given their real data as JSON.

Rules:
- Use ONLY numbers, companies, roles and skills present in the JSON. Never invent one.
- If the JSON does not answer the question, say what you do know and name the
  page that would help (Search, Applications, Resume Studio, Interview Lab,
  Company Intel).
- 2-4 sentences. Plain text, no markdown, no bullet lists, no preamble.
- Speak directly to the user as "you". Be specific and warm, never salesy."""
    prompt = (
        f"### THEIR QUESTION:\n{question}\n\n"
        f"### THEIR DATA:\n{json.dumps(context, default=str)[:6000]}"
    )
    try:
        reply = await _chat(system, prompt, model=_FAST_MODEL, temperature=0.4, max_tokens=350)
        return reply.strip()
    except Exception as exc:
        logger.warning("Copilot reply generation failed: %s", exc)
        raise
