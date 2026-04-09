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

from agents.ml_scorer import MLScorer, MatchScore
from agents.state import CandidateProfile, QueryContext, RetrievalResult


class TestMLScorer:
    """Test the ML-powered job-candidate matching."""

    @pytest.fixture
    def scorer(self):
        return MLScorer()

    @pytest.fixture
    def candidate(self):
        return CandidateProfile(
            name="Test User",
            skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
            experience_years=5,
            desired_title="Senior Engineer",
            is_remote=True,
            seniority="senior",
        )

    @pytest.fixture
    def job_match(self):
        return RetrievalResult(
            job_id="job1",
            title="Senior Python Engineer",
            company="TechCorp",
            location="Remote",
            is_remote=True,
            seniority="senior",
            skills=["Python", "FastAPI", "SQL", "AWS"],
            score=0.0,
        )

    def test_skill_match_perfect(self, scorer, candidate, job_match):
        """Candidate has all required skills."""
        score = scorer.score_match(candidate, job_match)
        assert score.skill_match == 1.0  # All job skills present
        assert "python" in score.matched_skills

    def test_skill_match_partial(self, scorer):
        """Candidate has some but not all skills."""
        candidate = CandidateProfile(skills=["Python"])
        job = RetrievalResult(
            job_id="job1",
            title="Engineer",
            company="Co",
            skills=["Python", "Java", "Go", "Rust"],
        )
        score = scorer.score_match(candidate, job)
        assert score.skill_match == 0.25  # 1 out of 4
        assert len(score.missing_skills) == 3

    def test_skill_match_empty(self, scorer):
        """No overlap in skills."""
        candidate = CandidateProfile(skills=["Python"])
        job = RetrievalResult(
            job_id="job1",
            title="Designer",
            company="Co",
            skills=["Figma", "Sketch"],
        )
        score = scorer.score_match(candidate, job)
        assert score.skill_match == 0.0

    def test_seniority_match_exact(self, scorer, candidate, job_match):
        """Exact seniority match."""
        score = scorer.score_match(candidate, job_match)
        assert score.seniority_match == 1.0

    def test_seniority_match_adjacent(self, scorer):
        """Adjacent seniority levels."""
        candidate = CandidateProfile(seniority="senior")
        job = RetrievalResult(job_id="job1", title="Role", company="Co", seniority="lead")
        score = scorer.score_match(candidate, job)
        assert score.seniority_match == 0.75  # One level difference

    def test_seniority_match_unknown(self, scorer, candidate, job_match):
        """Unknown seniority returns neutral score."""
        job = RetrievalResult(job_id="job1", title="Role", company="Co", seniority=None)
        score = scorer.score_match(candidate, job)
        assert score.seniority_match == 0.5

    def test_location_match_both_remote(self, scorer, candidate, job_match):
        """Both candidate and job are remote."""
        score = scorer.score_match(candidate, job_match)
        assert score.location_match == 1.0

    def test_location_match_candidate_wants_remote(self, scorer, candidate):
        """Candidate wants remote but job isn't."""
        job = RetrievalResult(
            job_id="job1",
            title="Role",
            company="Co",
            is_remote=False,
            location="San Francisco",
        )
        score = scorer.score_match(candidate, job)
        assert score.location_match == 0.2

    def test_location_match_exact_match(self, scorer):
        """Exact location match."""
        candidate = CandidateProfile(location="San Francisco", is_remote=False)
        job = RetrievalResult(
            job_id="job1",
            title="Role",
            company="Co",
            is_remote=False,
            location="San Francisco",
        )
        score = scorer.score_match(candidate, job)
        assert score.location_match == 1.0

    def test_overall_score_range(self, scorer, candidate, job_match):
        """Overall score should be between 0 and 1."""
        score = scorer.score_match(candidate, job_match)
        assert 0.0 <= score.overall_score <= 1.0

    def test_batch_scoring_sorted(self, scorer, candidate):
        """Batch scoring should return results sorted by score."""
        jobs = [
            RetrievalResult(job_id="job1", title="Python Dev", company="Co1", skills=["Python"]),
            RetrievalResult(job_id="job2", title="Java Dev", company="Co2", skills=["Java"]),
            RetrievalResult(job_id="job3", title="Python Senior", company="Co3", skills=["Python", "FastAPI"], seniority="senior"),
        ]
        scores = scorer.score_batch(candidate, jobs)
        assert scores[0].overall_score >= scores[1].overall_score >= scores[2].overall_score


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
