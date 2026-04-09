"""
agents/state.py
~~~~~~~~~~~~~~~
Typed state objects for the agent orchestration graph.

Defines the data structures that flow through the LangGraph-style
agent pipeline: user intent, query context, retrieval results,
and final responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntentType(str, Enum):
    """Classified user intent for routing."""
    SEARCH_JOBS = "search_jobs"
    FIND_CANDIDATES = "find_candidates"
    MARKET_TRENDS = "market_trends"
    COMPANY_INFO = "company_info"
    GENERAL = "general"


@dataclass
class QueryContext:
    """Parsed user query with extracted intent and filters."""
    raw_query: str
    intent: IntentType = IntentType.GENERAL
    keywords: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    location: str | None = None
    is_remote: bool | None = None
    seniority: str | None = None
    employment_type: str | None = None
    company: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    limit: int = 10
    offset: int = 0


@dataclass
class RetrievalResult:
    """A single result from vector/DB retrieval."""
    job_id: str
    title: str
    company: str
    location: str | None = None
    is_remote: bool = False
    seniority: str | None = None
    skills: list[str] = field(default_factory=list)
    description: str | None = None
    source_url: str | None = None
    posted_at: str | None = None
    score: float = 0.0  # Relevance/match score
    match_reason: str | None = None  # Why this was retrieved


@dataclass
class AgentResponse:
    """Final response from an agent."""
    success: bool
    intent: IntentType
    results: list[RetrievalResult] = field(default_factory=list)
    summary: str | None = None  # LLM-generated summary
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class CandidateProfile:
    """Represents a candidate's profile for matching."""
    name: str | None = None
    skills: list[str] = field(default_factory=list)
    experience_years: int | None = None
    current_title: str | None = None
    desired_title: str | None = None
    location: str | None = None
    is_remote: bool = False
    seniority: str | None = None
    resume_text: str | None = None
    embedding: list[float] | None = None
