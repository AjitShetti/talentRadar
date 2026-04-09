"""
ingestion/parsers/jd_parser.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LLM-powered job description parser using Groq + few-shot JSON extraction.

Design
------
* Uses ``llama-3.1-8b-instant`` via Groq for fast, cost-effective extraction.
* A SYSTEM prompt defines the strict JSON output schema with clear field
  descriptions and constraints.
* Two few-shot examples (user/assistant pairs) are embedded before the actual
  input so the model learns the exact JSON structure expected.
* JSON is extracted from the response with a greedy regex before Pydantic
  validation — this makes the parser robust against the model adding prose
  before/after the JSON block.
* ``batch_parse()`` processes a list of results and accumulates errors
  per-item rather than aborting the whole batch.

Usage
-----
::

    parser = JDParser()
    parsed = parser.parse_jd(raw_text, source_url="https://example.com/jobs/123")

    results = parser.batch_parse(raw_results)   # list[RawJobResult]
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from groq import Groq
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings
from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Model configuration
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_MODEL = "llama-3.1-8b-instant"   # fast & cheap; swap to 70b for quality
_MAX_TOKENS = 1024
_TEMPERATURE = 0.0   # zero temperature for deterministic, schema-faithful output

# ─────────────────────────────────────────────────────────────────────────────
# System prompt — defines the contract the model must honour
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a precise job-description parser. Your ONLY task is to extract
structured information from raw job-posting text and return it as a single
valid JSON object.

### Output schema (strict):
{
  "title":            string,           // exact job title
  "company":          string,           // hiring company name
  "skills":           [string, ...],    // tech skills / tools mentioned (e.g. Python, SQL, AWS)
  "experience":       string | null,    // required experience (e.g. "3-5 years"), null if not stated
  "location":         string | null,    // job location string, null if not stated
  "is_remote":        boolean,          // true if remote / WFH is explicitly mentioned
  "salary":           string | null,    // raw compensation string, null if not stated
  "salary_min":       number | null,    // numeric lower bound (annualised, no symbols), null if unknown
  "salary_max":       number | null,    // numeric upper bound (annualised, no symbols), null if unknown
  "salary_currency":  string | null,    // ISO-4217 code: USD | EUR | GBP | INR | …, null if unknown
  "employment_type":  string | null,    // full_time | part_time | contract | internship | freelance
  "seniority":        string | null     // intern | junior | mid | senior | lead | principal | staff | director | vp | c_level
}

### Rules:
- Return ONLY the JSON object, with NO additional text, preamble, or explanation.
- If a field cannot be determined from the text, use null (not an empty string).
- Never invent data. Only extract what is explicitly stated.
- Normalise salary to annual figures when the posting states monthly or hourly rates.
- Extract ALL skills mentioned in the requirements or responsibilities sections.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Few-shot examples — two representative (input, expected output) pairs
# ─────────────────────────────────────────────────────────────────────────────

_FEW_SHOT_EXAMPLES: list[dict[str, str]] = [
    # ── Example 1: SWE posting with salary range ──────────────────────────
    {
        "user": """\
Senior Software Engineer – Platform Infrastructure
Stripe | San Francisco, CA (Hybrid)

About the role
We're looking for a Senior Software Engineer to join our Platform team.
You'll own distributed services that process millions of transactions per second.

What you'll do:
- Design and build high-throughput microservices in Go and Python
- Partner with ML teams to integrate real-time fraud models
- On-call rotation (PagerDuty)

Requirements:
- 5+ years of software engineering experience
- Strong proficiency in Go or Python
- Experience with Kubernetes, Docker, gRPC, and PostgreSQL
- Familiarity with AWS (EC2, S3, RDS)

Compensation: $180,000 – $240,000 / year + equity + benefits""",
        "assistant": """\
{
  "title": "Senior Software Engineer",
  "company": "Stripe",
  "skills": ["Go", "Python", "Kubernetes", "Docker", "gRPC", "PostgreSQL", "AWS", "EC2", "S3", "RDS", "PagerDuty"],
  "experience": "5+ years",
  "location": "San Francisco, CA",
  "is_remote": false,
  "salary": "$180,000 – $240,000 / year",
  "salary_min": 180000,
  "salary_max": 240000,
  "salary_currency": "USD",
  "employment_type": "full_time",
  "seniority": "senior"
}""",
    },
    # ── Example 2: Data Science posting — partial fields ──────────────────
    {
        "user": """\
Data Scientist III – Growth Analytics
Acme Corp | Remote (India preferred)

We need a data scientist to drive growth experiments.

Key Skills:
Python, SQL, A/B Testing, Machine Learning, Pandas, scikit-learn, Spark

Responsibilities:
- Run end-to-end experimentation pipelines
- Build predictive models for user churn and LTV
- Collaborate with Product on hypothesis design

You'll bring:
3-5 years of hands-on data science experience
Strong SQL and Python skills
Experience with big-data tools (Spark, Hadoop)

Contract position, 6-month engagement.
Rate: $60-80/hour""",
        "assistant": """\
{
  "title": "Data Scientist III",
  "company": "Acme Corp",
  "skills": ["Python", "SQL", "A/B Testing", "Machine Learning", "Pandas", "scikit-learn", "Spark", "Hadoop"],
  "experience": "3-5 years",
  "location": "India",
  "is_remote": true,
  "salary": "$60-80/hour",
  "salary_min": 124800,
  "salary_max": 166400,
  "salary_currency": "USD",
  "employment_type": "contract",
  "seniority": "mid"
}""",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Parser class
# ─────────────────────────────────────────────────────────────────────────────

class JDParser:
    """
    Extracts structured job description fields from raw text using Groq LLM.

    Parameters
    ----------
    api_key:
        Groq API key. Defaults to ``settings.groq_api_key``.
    model:
        Groq model ID. Defaults to ``llama-3.1-8b-instant``.
    inter_request_delay:
        Seconds to sleep between consecutive API calls (rate-limit safety).

    Example
    -------
    ::

        parser = JDParser()
        jd = parser.parse_jd(raw_text, source_url="https://stripe.com/jobs/123")
        print(jd.title, jd.skills)
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        inter_request_delay: float = 0.5,
    ) -> None:
        settings = get_settings()
        _key = api_key or settings.groq_api_key
        if not _key:
            raise ValueError(
                "GROQ_API_KEY is not set. Add it to your .env file or pass it explicitly."
            )
        self._client = Groq(api_key=_key)
        self._model = model
        self._delay = inter_request_delay

    # ─────────────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────────────

    def parse_jd(
        self,
        raw_text: str,
        *,
        source_url: str | None = None,
    ) -> ParsedJobDescription:
        """
        Extract structured fields from a single raw job description.

        Parameters
        ----------
        raw_text:
            The verbatim job posting text (full page content preferred).
        source_url:
            URL of the original posting (attached to the output for traceability).

        Returns
        -------
        ParsedJobDescription
            Pydantic-validated structured output.

        Raises
        ------
        ValueError
            If the LLM response cannot be parsed as valid JSON or fails
            Pydantic validation after two attempts.
        """
        messages = self._build_messages(raw_text)
        raw_response = self._call_llm(messages)
        extracted = self._extract_json(raw_response)

        # Attach source traceability fields before validation
        extracted["source_url"] = source_url
        extracted["raw_text"] = raw_text

        try:
            return ParsedJobDescription(**extracted)
        except Exception as exc:
            logger.error(
                "Pydantic validation failed for url=%r: %s\nRaw LLM output:\n%s",
                source_url,
                exc,
                raw_response[:500],
            )
            raise ValueError(f"JD validation failed: {exc}") from exc

    def batch_parse(
        self,
        raw_results: list[RawJobResult],
    ) -> list[ParsedJobDescription]:
        """
        Parse a list of ``RawJobResult`` objects.

        Per-item errors are logged and skipped; the batch never aborts.

        Parameters
        ----------
        raw_results:
            Validated Tavily results from ``TavilyJobScraper.search_jobs()``.

        Returns
        -------
        list[ParsedJobDescription]
            Successfully parsed JDs (may be shorter than ``raw_results``).
        """
        parsed: list[ParsedJobDescription] = []
        total = len(raw_results)

        for i, result in enumerate(raw_results, start=1):
            logger.info("Parsing JD %d/%d — url=%r", i, total, result.url)
            try:
                jd = self.parse_jd(result.best_content, source_url=result.url)
                parsed.append(jd)
            except Exception as exc:
                logger.warning(
                    "Skipping url=%r after parse failure: %s", result.url, exc
                )

            # Rate-limit safety between consecutive API calls
            if i < total:
                time.sleep(self._delay)

        logger.info(
            "Batch parse complete: %d/%d succeeded", len(parsed), total
        )
        return parsed

    # ─────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────

    def _build_messages(self, raw_text: str) -> list[dict[str, str]]:
        """
        Assemble the full few-shot message list:
        [system] → [user, assistant] × N_examples → [user: actual input]
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
        ]

        for example in _FEW_SHOT_EXAMPLES:
            messages.append({"role": "user", "content": example["user"]})
            messages.append({"role": "assistant", "content": example["assistant"]})

        # Trim input to avoid exceeding context window (~8k tokens for 8b-instant)
        truncated = raw_text[:6000]
        messages.append({"role": "user", "content": truncated})

        return messages

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call Groq chat completion with automatic retry on transient errors."""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
        )
        content = response.choices[0].message.content or ""
        return content.strip()

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """
        Extract the first JSON object from a potentially decorated response.

        Strategy:
        1. Try to parse the entire response as JSON (happy path).
        2. Extract the first ``{...}`` block using a greedy regex.
        3. Raise ``ValueError`` if both attempts fail.
        """
        # Strategy 1: whole response is valid JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 2: extract first {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Strategy 3: strip markdown code fences and retry
        clean = re.sub(r"```(?:json)?", "", text).strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            pass

        raise ValueError(
            f"Could not extract valid JSON from LLM response. "
            f"First 300 chars: {text[:300]!r}"
        )
