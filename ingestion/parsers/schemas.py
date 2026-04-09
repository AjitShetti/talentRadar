"""
ingestion/parsers/schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic models for the ingestion pipeline.

Two model groups:
  1. RawJobResult      — validated envelope for a single Tavily search hit
  2. ParsedJobDescription — structured output from LLM extraction over raw JD text
"""

from __future__ import annotations

from typing import Annotated
import re

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


# ─────────────────────────────────────────────────────────────────────────────
# Raw Tavily result
# ─────────────────────────────────────────────────────────────────────────────

class RawJobResult(BaseModel):
    """
    A single result returned by the Tavily search API.
    Mirrors the Tavily /search response schema exactly.
    """

    title: str = Field(description="Page / document title from the search result")
    url: str = Field(description="Canonical URL of the job posting page")
    content: str = Field(description="Extracted page content (plain text snippet)")
    score: float = Field(default=0.0, description="Relevance score assigned by Tavily (0-1)")
    published_date: str | None = Field(
        default=None, description="ISO-8601 publish date if present in result metadata"
    )
    raw_content: str | None = Field(
        default=None,
        description="Full raw text of the page when Tavily include_raw_content=True",
    )

    @field_validator("url")
    @classmethod
    def url_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("url must not be empty")
        return v.strip()

    @property
    def best_content(self) -> str:
        """Return the richest available text (raw_content preferred over snippet)."""
        return (self.raw_content or self.content).strip()

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────────────────────────────────────
# LLM-parsed job description
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN_CURRENCIES = {"USD", "EUR", "GBP", "INR", "CAD", "AUD", "SGD", "AED"}

_SENIORITY_CHOICES = {
    "intern", "junior", "mid", "senior", "lead",
    "principal", "staff", "director", "vp", "c_level",
}

_EMPLOYMENT_TYPE_CHOICES = {
    "full_time", "part_time", "contract", "internship", "freelance",
}


class ParsedJobDescription(BaseModel):
    """
    Structured representation of a job description extracted by the LLM.

    All fields except ``title``, ``company``, and ``raw_text`` are optional
    because real-world JDs vary enormously in what they disclose.

    Usage
    -----
    ::

        parsed = ParsedJobDescription(**llm_json_output)
        # write to DB via JobRepository.upsert_by_external_id(...)
    """

    # ── Core identity ────────────────────────────────────────────────────── #
    title: str = Field(
        description="Exact job title as written in the posting."
    )
    company: str = Field(
        description="Hiring company name (normalised, PascalCase if possible)."
    )

    # ── Skills ───────────────────────────────────────────────────────────── #
    skills: list[str] = Field(
        default_factory=list,
        description="Technology / skill tokens mentioned in requirements (e.g. Python, SQL, AWS).",
    )

    # ── Experience ───────────────────────────────────────────────────────── #
    experience: str | None = Field(
        default=None,
        description="Required years of experience as a human-readable string, e.g. '3-5 years'.",
    )

    # ── Location ─────────────────────────────────────────────────────────── #
    location: str | None = Field(
        default=None,
        description="Job location string as written in the posting, e.g. 'San Francisco, CA'.",
    )
    is_remote: bool = Field(
        default=False,
        description="True when the posting explicitly mentions remote / work-from-home.",
    )

    # ── Compensation ─────────────────────────────────────────────────────── #
    salary: str | None = Field(
        default=None,
        description="Raw compensation string exactly as written, e.g. '$120k - $150k / year'.",
    )
    salary_min: float | None = Field(
        default=None,
        ge=0,
        description="Numeric lower bound of salary range (annualised, no currency symbol).",
    )
    salary_max: float | None = Field(
        default=None,
        ge=0,
        description="Numeric upper bound of salary range (annualised, no currency symbol).",
    )
    salary_currency: str | None = Field(
        default=None,
        description="ISO-4217 currency code: USD, EUR, GBP, INR, …",
    )

    # ── Classification ───────────────────────────────────────────────────── #
    employment_type: str | None = Field(
        default=None,
        description=(
            "One of: full_time | part_time | contract | internship | freelance"
        ),
    )
    seniority: str | None = Field(
        default=None,
        description=(
            "One of: intern | junior | mid | senior | lead | "
            "principal | staff | director | vp | c_level"
        ),
    )

    # ── Source traceability ──────────────────────────────────────────────── #
    source_url: str | None = Field(
        default=None,
        description="URL of the original job posting page.",
    )
    raw_text: str = Field(
        description="The verbatim input text fed to the LLM for extraction.",
    )

    # ─────────────────────────────────────────────────────────────
    # Validators
    # ─────────────────────────────────────────────────────────────

    @field_validator("skills", mode="before")
    @classmethod
    def deduplicate_skills(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        seen: set[str] = set()
        result: list[str] = []
        for item in v:
            normalised = str(item).strip()
            key = normalised.lower()
            if normalised and key not in seen:
                seen.add(key)
                result.append(normalised)
        return result

    @field_validator("employment_type", mode="before")
    @classmethod
    def normalise_employment_type(cls, v: object) -> str | None:
        if v is None:
            return None
        normalised = str(v).lower().replace("-", "_").replace(" ", "_")
        return normalised if normalised in _EMPLOYMENT_TYPE_CHOICES else None

    @field_validator("seniority", mode="before")
    @classmethod
    def normalise_seniority(cls, v: object) -> str | None:
        if v is None:
            return None
        normalised = str(v).lower().replace("-", "_").replace(" ", "_")
        return normalised if normalised in _SENIORITY_CHOICES else None

    @field_validator("salary_currency", mode="before")
    @classmethod
    def normalise_currency(cls, v: object) -> str | None:
        if v is None:
            return None
        upper = str(v).upper().strip()
        return upper if upper in _KNOWN_CURRENCIES else None

    @model_validator(mode="after")
    def salary_range_consistency(self) -> "ParsedJobDescription":
        """Swap min/max if they are accidentally inverted."""
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            self.salary_min, self.salary_max = self.salary_max, self.salary_min
        return self

    # ─────────────────────────────────────────────────────────────
    # Convenience helpers
    # ─────────────────────────────────────────────────────────────

    def to_job_kwargs(self) -> dict:
        """
        Map this parsed JD to the keyword-argument dict expected by
        ``JobRepository.upsert_by_external_id(external_id, source, defaults)``.

        Column name mapping (parsed field → ORM column):
          salary       → salary_raw
          location     → location_raw
        """
        return {
            "title": self.title,
            "description_raw": self.raw_text,
            "description_clean": self.raw_text,
            "location_raw": self.location,
            "is_remote": self.is_remote,
            "salary_raw": self.salary,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_currency": self.salary_currency,
            "skills": self.skills or None,
            "source_url": self.source_url,
            # employment_type / seniority are Postgres enum columns —
            # the caller must cast these to EmploymentType / SeniorityLevel
            # before passing to the ORM. Stored as raw strings here.
            "employment_type": self.employment_type,
            "seniority": self.seniority,
        }

    class Config:
        populate_by_name = True
        str_strip_whitespace = True
