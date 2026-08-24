"""
domain/experience.py
~~~~~~~~~~~~~~~~~~~~
Experience-level vocabulary shared by the search filters and the JD parser.

Jobs carry no numeric experience column, so a user's "3-5 years" filter is
expressed as a set of :class:`~domain.enums.SeniorityLevel` values. The same
table is used in reverse by the parser: when an LLM extraction yields
``experience="4-7 years"`` but no seniority, the years are mapped back onto a
level so the posting is still reachable through the filter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.enums import SeniorityLevel


@dataclass(frozen=True)
class ExperienceBucket:
    """One selectable experience band in the job-search filter."""

    key: str
    label: str
    min_years: float
    max_years: float | None
    levels: tuple[SeniorityLevel, ...]


EXPERIENCE_BUCKETS: tuple[ExperienceBucket, ...] = (
    ExperienceBucket(
        key="fresher",
        label="Fresher (0-1 yrs)",
        min_years=0,
        max_years=1,
        levels=(SeniorityLevel.INTERN, SeniorityLevel.JUNIOR),
    ),
    ExperienceBucket(
        key="junior",
        label="1-3 yrs",
        min_years=1,
        max_years=3,
        levels=(SeniorityLevel.JUNIOR, SeniorityLevel.MID),
    ),
    ExperienceBucket(
        key="mid",
        label="3-5 yrs",
        min_years=3,
        max_years=5,
        levels=(SeniorityLevel.MID, SeniorityLevel.SENIOR),
    ),
    ExperienceBucket(
        key="senior",
        label="5-8 yrs",
        min_years=5,
        max_years=8,
        levels=(SeniorityLevel.SENIOR, SeniorityLevel.LEAD),
    ),
    ExperienceBucket(
        key="lead",
        label="8+ yrs",
        min_years=8,
        max_years=None,
        levels=(
            SeniorityLevel.LEAD,
            SeniorityLevel.PRINCIPAL,
            SeniorityLevel.STAFF,
            SeniorityLevel.DIRECTOR,
            SeniorityLevel.VP,
            SeniorityLevel.C_LEVEL,
        ),
    ),
)

_BUCKETS_BY_KEY: dict[str, ExperienceBucket] = {b.key: b for b in EXPERIENCE_BUCKETS}

#: Seniority a given number of years maps to (used by the JD parser fallback).
_YEARS_TO_LEVEL: tuple[tuple[float, SeniorityLevel], ...] = (
    (1, SeniorityLevel.JUNIOR),
    (3, SeniorityLevel.MID),
    (5, SeniorityLevel.MID),
    (8, SeniorityLevel.SENIOR),
    (12, SeniorityLevel.LEAD),
)

# JDs write ranges with a hyphen, an en dash, or the word "to".
_YEARS_RE = re.compile(r"(\d{1,2})(?:\s*(?:\+|plus))?\s*(?:-|to|–)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)")  # noqa: RUF001
_FRESHER_RE = re.compile(r"\b(fresher|fresh graduate|entry[- ]level|no experience|0\s*years?)\b")
_INTERN_RE = re.compile(r"\b(intern|internship|trainee|apprentice)\b")


def get_bucket(key: str | None) -> ExperienceBucket | None:
    """Look up an experience bucket by its filter key (``"mid"``, ``"lead"``…)."""
    if not key:
        return None
    return _BUCKETS_BY_KEY.get(key.strip().lower())


def seniority_levels_for(key: str | None) -> list[SeniorityLevel]:
    """Seniority levels a bucket key expands to; empty when the key is unknown."""
    bucket = get_bucket(key)
    return list(bucket.levels) if bucket else []


def bucket_for_years(years: float | None) -> str | None:
    """Map a candidate's years of experience onto the bucket key that fits."""
    if years is None:
        return None
    for bucket in EXPERIENCE_BUCKETS:
        if bucket.max_years is None or years < bucket.max_years:
            return bucket.key
    return EXPERIENCE_BUCKETS[-1].key


def parse_years(text: str | None) -> float | None:
    """
    Pull the minimum years of experience out of a free-text requirement.

    ``"3-5 years"`` -> 3.0, ``"5+ years"`` -> 5.0, ``"Fresher"`` -> 0.0.
    Returns ``None`` when the text states no requirement.
    """
    if not text:
        return None
    lowered = text.lower()
    if _INTERN_RE.search(lowered) or _FRESHER_RE.search(lowered):
        return 0.0
    match = _YEARS_RE.search(lowered)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def infer_seniority(text: str | None) -> str | None:
    """
    Derive a seniority value from an experience string.

    Used as a parser fallback so postings whose seniority the LLM left blank
    are still matched by the experience filter.
    """
    if not text:
        return None
    lowered = text.lower()
    if _INTERN_RE.search(lowered):
        return SeniorityLevel.INTERN.value
    years = parse_years(text)
    if years is None:
        return None
    for threshold, level in _YEARS_TO_LEVEL:
        if years < threshold:
            return level.value
    return SeniorityLevel.PRINCIPAL.value
