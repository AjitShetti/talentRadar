"""
tests/test_agents.py
~~~~~~~~~~~~~~~~~~~~
Tests for the AI agent layer.

Covers:
- ML scorer (skill matching, seniority matching, location matching)
- State objects
- Orchestrator intent classification
"""

import pytest


from agents.state import CandidateProfile, QueryContext, RetrievalResult





class TestQueryContext:
    """Test QueryContext state object."""

    def test_default_values(self):
        """QueryContext should have sensible defaults."""
        ctx = QueryContext(raw_query="test")
        assert ctx.intent.value == "general"
        assert ctx.keywords == []
        assert ctx.skills == []
        assert ctx.limit == 10
        assert ctx.offset == 0

    def test_with_filters(self):
        """QueryContext should accept filters."""
        ctx = QueryContext(
            raw_query="remote python jobs",
            skills=["Python"],
            is_remote=True,
            seniority="senior",
            limit=20,
        )
        assert ctx.skills == ["Python"]
        assert ctx.is_remote is True
        assert ctx.seniority == "senior"
        assert ctx.limit == 20


class TestRetrievalResult:
    """Test RetrievalResult state object."""

    def test_basic_result(self):
        """RetrievalResult should store job info."""
        result = RetrievalResult(
            job_id="job1",
            title="Engineer",
            company="TechCorp",
            skills=["Python", "SQL"],
            score=0.85,
        )
        assert result.job_id == "job1"
        assert result.title == "Engineer"
        assert result.company == "TechCorp"
        assert result.score == 0.85
        assert len(result.skills) == 2
