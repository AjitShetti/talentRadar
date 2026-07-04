"""
tests/test_resume_matcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the resume-to-job-description matching pipeline.

Tests cover:
- Configuration and weights validation
- Text preprocessing
- Feature extraction (skills, experience, education, keywords)
- Skill matching (exact and alias-based)
- Experience matching
- Education matching
- Semantic scoring
- Full pipeline (ResumeMatcher)
- API endpoints
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
pytestmark = pytest.mark.asyncio

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: Sample data
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_RESUME = """
John Doe - Senior Python Developer
Email: john@example.com

SUMMARY
Experienced Python developer with 6 years of experience building
scalable web applications.

EXPERIENCE
Senior Python Developer at TechCorp (2021-Present)
- Built REST APIs using FastAPI and PostgreSQL
- Implemented microservices architecture with Docker and Kubernetes
- Used Redis for caching and Celery for async task processing

Python Developer at StartupInc (2020-2021)
- Developed web applications with Django and Flask
- Worked with AWS services (EC2, S3, RDS)

SKILLS
Python, FastAPI, Django, Flask, PostgreSQL, Redis, Docker,
Kubernetes, AWS, Git, REST APIs, Celery, Linux

EDUCATION
Master of Science in Computer Science, Stanford University, 2020
"""

SAMPLE_JD = """
Senior Python Engineer - CloudTech

We're looking for a senior Python engineer to join our cloud platform team.

REQUIREMENTS
- 5+ years of Python experience
- Strong experience with FastAPI or Django
- Experience with PostgreSQL and Redis
- Proficiency with Docker and Kubernetes
- AWS cloud experience required
- Bachelor's degree in Computer Science or related field

NICE TO HAVE
- Experience with Celery and async processing
- Linux system administration
- CI/CD pipeline experience

RESPONSIBILITIES
- Design and build scalable microservices
- Optimize API performance
- Mentor junior engineers
"""

SAMPLE_RESUME_NO_SKILLS = """
Jane Smith
Recent graduate looking for entry-level developer position.

EDUCATION
Bachelor of Science in Computer Science, MIT, 2025

Some class projects in Python and Java.
"""

SAMPLE_JD_SENIOR = """
Principal Engineer

Requirements:
- 10+ years of software engineering experience
- PhD in Computer Science preferred
- Expert-level Python, C++, and system design
"""


# ─────────────────────────────────────────────────────────────────────────────
# Config tests
# ─────────────────────────────────────────────────────────────────────────────


class TestScoringWeights:
    """Tests for ScoringWeights validation."""

    def test_default_weights_sum_to_one(self):
        from ml.config import ScoringWeights

        weights = ScoringWeights()
        total = weights.skills + weights.experience + weights.education + weights.semantic
        assert abs(total - 1.0) < 1e-6

    def test_custom_valid_weights(self):
        from ml.config import ScoringWeights

        weights = ScoringWeights(skills=0.5, experience=0.2, education=0.1, semantic=0.2)
        assert weights.skills == 0.5

    def test_invalid_weights_raise_error(self):
        from ml.config import ScoringWeights

        with pytest.raises(ValueError, match="must sum to 1.0"):
            ScoringWeights(skills=0.5, experience=0.5, education=0.5, semantic=0.5)

    def test_to_dict(self):
        from ml.config import ScoringWeights

        weights = ScoringWeights()
        result = weights.to_dict()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"skills", "experience", "education", "semantic"}


class TestPipelineConfig:
    """Tests for PipelineConfig."""

    def test_default_config(self):
        from ml.config import PipelineConfig

        config = PipelineConfig()
        assert config.weights.skills == 0.40
        assert config.min_match_threshold == 30.0
        assert len(config.known_skills) > 0

    def test_custom_weights(self):
        from ml.config import PipelineConfig, ScoringWeights

        weights = ScoringWeights(skills=0.5, experience=0.2, education=0.1, semantic=0.2)
        config = PipelineConfig(weights=weights)
        assert config.weights.skills == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Utils tests
# ─────────────────────────────────────────────────────────────────────────────


class TestUtils:
    """Tests for ml/utils.py."""

    def test_clamp_score_within_range(self):
        from ml.utils import clamp_score

        assert clamp_score(50.0) == 50.0
        assert clamp_score(0.0) == 0.0
        assert clamp_score(100.0) == 100.0

    def test_clamp_score_above_max(self):
        from ml.utils import clamp_score

        assert clamp_score(150.0) == 100.0

    def test_clamp_score_below_min(self):
        from ml.utils import clamp_score

        assert clamp_score(-10.0) == 0.0

    def test_clamp_score_rounding(self):
        from ml.utils import clamp_score

        # Python uses banker's rounding
        assert clamp_score(85.55) == 85.5
        assert clamp_score(85.65) == 85.7  # 6 is even, rounds up

    def test_compute_weighted_score(self):
        from ml.utils import compute_weighted_score

        scores = {"skills": 90, "experience": 80, "education": 85, "semantic": 82}
        weights = {"skills": 0.4, "experience": 0.25, "education": 0.15, "semantic": 0.2}
        # 0.4*90 + 0.25*80 + 0.15*85 + 0.2*82 = 36 + 20 + 12.75 + 16.4 = 85.15 -> 85.2
        result = compute_weighted_score(scores, weights)
        assert result == 85.2

    def test_compute_weighted_score_mismatched_keys(self):
        from ml.utils import compute_weighted_score

        with pytest.raises(ValueError, match="must match"):
            compute_weighted_score({"a": 1}, {"b": 1})

    def test_extract_numbers(self):
        from ml.utils import extract_numbers

        assert extract_numbers("5+ years, 3.5 years") == [5.0, 3.5]

    def test_extract_year_range_open_ended(self):
        from ml.utils import extract_year_range

        result = extract_year_range("5+ years of experience")
        assert result == (5.0, float("inf"))

    def test_extract_year_range_range(self):
        from ml.utils import extract_year_range

        result = extract_year_range("3-5 years")
        assert result == (3.0, 5.0)

    def test_extract_year_range_none(self):
        from ml.utils import extract_year_range

        result = extract_year_range("no years mentioned")
        assert result is None

    def test_truncate_text_short(self):
        from ml.utils import truncate_text

        assert truncate_text("Hi", 10) == "Hi"

    def test_truncate_text_long(self):
        from ml.utils import truncate_text

        result = truncate_text("Hello World", 8)
        assert result == "Hello..."
        assert len(result) <= 8


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPreprocessing:
    """Tests for ml/preprocessing.py."""

    def test_remove_html(self):
        from ml.preprocessing import remove_html

        assert remove_html("<p>Hello <b>World</b></p>").strip() == "Hello  World"

    def test_normalize_whitespace(self):
        from ml.preprocessing import normalize_whitespace

        assert normalize_whitespace("Hello   world\n\n  test") == "Hello world test"

    def test_clean_text_basic(self):
        from ml.preprocessing import clean_text

        result = clean_text("<p>Python Developer with 5+ years exp.</p>")
        assert "Python Developer" in result
        assert "<p>" not in result

    def test_clean_text_empty(self):
        from ml.preprocessing import clean_text

        assert clean_text("") == ""
        assert clean_text("   ") == ""

    def test_clean_text_lowercase(self):
        from ml.preprocessing import clean_text

        result = clean_text("Hello WORLD", lowercase=True)
        assert result == "hello world"

    def test_preprocess_text(self):
        from ml.preprocessing import preprocess_text

        result = preprocess_text("<h1>John Doe</h1><p>Python developer</p>")
        assert result.cleaned
        assert "<h1>" not in result.cleaned
        assert result.word_count > 0

    def test_preprocess_text_empty_raises(self):
        from ml.preprocessing import preprocess_text

        with pytest.raises(ValueError, match="empty"):
            preprocess_text("")

    def test_preprocess_text_with_sections(self):
        from ml.preprocessing import preprocess_text

        text = "SKILLS\nPython, Java\nEXPERIENCE\n5 years at Google"
        result = preprocess_text(text)
        assert "skills" in result.sections or "experience" in result.sections

    def test_detect_sections(self):
        from ml.preprocessing import detect_sections

        text = "EXPERIENCE\nWorked at Google\nEDUCATION\nBS in CS"
        sections = detect_sections(text)
        assert len(sections) == 2
        section_names = [s[0] for s in sections]
        assert "experience" in section_names
        assert "education" in section_names

    def test_extract_section_text(self):
        from ml.preprocessing import extract_section_text

        text = "SKILLS\nPython, Java\nOther content here"
        sections = extract_section_text(text)
        assert "skills" in sections
        assert "Python" in sections["skills"]


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFeatureExtractor:
    """Tests for ml/feature_extractor.py."""

    def test_extract_skills_basic(self):
        from ml.feature_extractor import extract_skills

        text = "Experienced Python developer with FastAPI and PostgreSQL skills"
        result = extract_skills(text)
        assert "Python" in result.matched_skills
        assert "FastAPI" in result.matched_skills
        assert "PostgreSQL" in result.matched_skills

    def test_extract_skills_empty(self):
        from ml.feature_extractor import extract_skills

        result = extract_skills("no skills mentioned here")
        assert len(result.matched_skills) == 0

    def test_extract_experience_open_ended(self):
        from ml.feature_extractor import extract_experience

        text = "Requires 5+ years of Python experience"
        result = extract_experience(text)
        assert result.has_experience_info
        assert result.years_min == 5.0

    def test_extract_experience_range(self):
        from ml.feature_extractor import extract_experience

        text = "3-5 years of software development experience"
        result = extract_experience(text)
        assert result.has_experience_info
        assert result.years_min == 3.0
        assert result.years_max == 5.0

    def test_extract_experience_none(self):
        from ml.feature_extractor import extract_experience

        result = extract_experience("We need a good developer")
        assert not result.has_experience_info

    def test_extract_education_master(self):
        from ml.feature_extractor import extract_education

        text = "MS in Computer Science from Stanford University"
        result = extract_education(text)
        assert result.has_education_info
        assert result.highest_level == "master"
        assert result.level_score == 4

    def test_extract_education_bachelor(self):
        from ml.feature_extractor import extract_education

        text = "Bachelor of Science in Computer Science"
        result = extract_education(text)
        assert result.has_education_info
        assert result.highest_level == "bachelor"

    def test_extract_education_phd(self):
        from ml.feature_extractor import extract_education

        text = "PhD in Machine Learning from MIT"
        result = extract_education(text)
        assert result.highest_level == "phd"
        assert result.level_score == 6

    def test_extract_keywords(self):
        from ml.feature_extractor import extract_keywords

        text = "Python developer building REST APIs with FastAPI and PostgreSQL"
        result = extract_keywords(text, top_n=5)
        assert len(result.top_keywords) <= 5

    def test_extract_features_full(self):
        from ml.feature_extractor import extract_features
        from ml.preprocessing import preprocess_text

        cleaned = preprocess_text(SAMPLE_RESUME)
        features = extract_features(cleaned, text_type="resume")
        assert len(features.skills.matched_skills) > 0
        assert features.word_count > 0


# ─────────────────────────────────────────────────────────────────────────────
# Skill matcher tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSkillMatcher:
    """Tests for ml/models/skill_matcher.py."""

    def setup_method(self) -> None:
        from ml.models.skill_matcher import SkillMatcher

        self.matcher = SkillMatcher()

    def test_exact_match(self):
        result = self.matcher.match(["Python", "FastAPI"], ["Python", "FastAPI"])
        assert result.match_percentage == 100.0
        assert len(result.matched_skills) == 2

    def test_partial_match(self):
        result = self.matcher.match(["Python"], ["Python", "FastAPI", "Redis"])
        assert result.match_percentage == pytest.approx(33.3, abs=0.5)
        assert "Python" in result.matched_skills
        assert len(result.missing_skills) == 2

    def test_no_match(self):
        result = self.matcher.match(["Java"], ["Python", "FastAPI"])
        assert result.match_percentage == 0.0
        assert len(result.missing_skills) == 2

    def test_alias_match(self):
        # "py" should match "Python" via alias
        result = self.matcher.match(["python3"], ["Python"])
        assert result.match_percentage == 100.0

    def test_empty_resume_skills(self):
        result = self.matcher.match([], ["Python", "FastAPI"])
        assert result.match_percentage == 0.0
        assert len(result.missing_skills) == 2

    def test_empty_jd_skills(self):
        result = self.matcher.match(["Python", "FastAPI"], [])
        assert result.match_percentage == 100.0

    def test_extra_skills(self):
        result = self.matcher.match(
            ["Python", "FastAPI", "Redis", "Docker"],
            ["Python", "FastAPI"],
        )
        assert len(result.extra_skills) == 2

    def test_k8s_alias(self):
        result = self.matcher.match(["k8s"], ["Kubernetes"])
        assert result.match_percentage == 100.0

    def test_js_alias(self):
        result = self.matcher.match(["js"], ["JavaScript"])
        assert result.match_percentage == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Experience matcher tests
# ─────────────────────────────────────────────────────────────────────────────


class TestExperienceMatcher:
    """Tests for ml/models/experience_matcher.py."""

    def setup_method(self) -> None:
        from ml.models.experience_matcher import ExperienceMatcher

        self.matcher = ExperienceMatcher()

    def test_exact_match(self):
        result = self.matcher.match(5, 5)
        assert result.meets_requirement
        assert result.match_score == 100.0

    def test_exceeds_requirement(self):
        result = self.matcher.match(10, 5)
        assert result.meets_requirement
        assert result.match_score == 100.0

    def test_80_percent(self):
        result = self.matcher.match(4, 5)
        assert result.match_score == 80.0

    def test_no_jd_requirement(self):
        result = self.matcher.match(5, 0)
        assert result.match_score == 100.0

    def test_no_resume_experience(self):
        result = self.matcher.match(0, 5)
        assert result.match_score == 50.0

    def test_ratio(self):
        result = self.matcher.match(3, 6)
        assert result.ratio == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Scorers tests
# ─────────────────────────────────────────────────────────────────────────────


class TestScorers:
    """Tests for ml/scorers.py."""

    def _make_features(self, skills=None, experience_years=0, education_level=""):
        """Helper to create mock ExtractedFeatures."""
        from ml.feature_extractor import (
            EducationFeatures,
            ExperienceFeatures,
            ExtractedFeatures,
            KeywordFeatures,
            SkillFeatures,
        )

        return ExtractedFeatures(
            skills=SkillFeatures(matched_skills=skills or []),
            experience=ExperienceFeatures(
                years_min=experience_years,
                has_experience_info=experience_years > 0,
            ),
            education=EducationFeatures(
                highest_level=education_level,
                level_score={"bachelor": 3, "master": 4, "phd": 6}.get(education_level, 0),
                has_education_info=bool(education_level),
            ),
            keywords=KeywordFeatures(),
        )

    def test_skill_scorer_perfect_match(self):
        from ml.scorers import SkillMatchScorer

        scorer = SkillMatchScorer()
        features = self._make_features(skills=["Python", "FastAPI"])
        result = scorer.score(features, features)
        assert result.score == 100.0

    def test_skill_scorer_partial_match(self):
        from ml.scorers import SkillMatchScorer

        scorer = SkillMatchScorer()
        resume = self._make_features(skills=["Python"])
        jd = self._make_features(skills=["Python", "FastAPI", "Redis"])
        result = scorer.score(resume, jd)
        assert result.score == pytest.approx(33.3, abs=1.0)

    def test_education_scorer_meets_requirement(self):
        from ml.scorers import EducationScorer

        scorer = EducationScorer()
        resume = self._make_features(education_level="master")
        jd = self._make_features(education_level="bachelor")
        result = scorer.score(resume, jd)
        assert result.score == 100.0

    def test_education_scorer_below_requirement(self):
        from ml.scorers import EducationScorer

        scorer = EducationScorer()
        resume = self._make_features(education_level="bachelor")
        jd = self._make_features(education_level="phd")
        result = scorer.score(resume, jd)
        assert result.score < 100.0

    def test_semantic_scorer_cosine_similarity(self):
        from ml.scorers import SemanticScorer

        # Test cosine similarity directly
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])
        assert SemanticScorer._cosine_similarity(a, b) == 1.0

        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert SemanticScorer._cosine_similarity(a, b) == 0.0

    @patch("ml.scorers.SemanticScorer._load_model")
    def test_semantic_scorer_with_mock(self, mock_load):
        from ml.scorers import SemanticScorer

        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda text, **kwargs: np.array([0.5, 0.5, 0.7])
        mock_load.return_value = mock_model

        scorer = SemanticScorer()
        resume = self._make_features(skills=["Python"])
        jd = self._make_features(skills=["Python"])
        result = scorer.score(resume, jd, "Python developer", "Python developer")
        # Same embeddings -> cosine sim = 1.0 -> score = 100
        assert result.score == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Composite scorer tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCompositeScorer:
    """Tests for CompositeScorer."""

    def _make_features(self, skills=None, experience_years=0, education_level=""):
        from ml.feature_extractor import (
            EducationFeatures,
            ExperienceFeatures,
            ExtractedFeatures,
            KeywordFeatures,
            SkillFeatures,
        )

        return ExtractedFeatures(
            skills=SkillFeatures(matched_skills=skills or []),
            experience=ExperienceFeatures(
                years_min=experience_years,
                has_experience_info=experience_years > 0,
            ),
            education=EducationFeatures(
                highest_level=education_level,
                level_score={"bachelor": 3, "master": 4, "phd": 6}.get(education_level, 0),
                has_education_info=bool(education_level),
            ),
            keywords=KeywordFeatures(),
        )

    @patch("ml.scorers.SemanticScorer._load_model")
    def test_composite_score(self, mock_load):
        from ml.config import PipelineConfig
        from ml.scorers import CompositeScorer

        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda text, **kwargs: np.array([0.5, 0.5, 0.7])
        mock_load.return_value = mock_model

        config = PipelineConfig()
        scorer = CompositeScorer(config)

        resume = self._make_features(
            skills=["Python", "FastAPI", "PostgreSQL"],
            experience_years=5,
            education_level="master",
        )
        jd = self._make_features(
            skills=["Python", "FastAPI", "PostgreSQL"],
            experience_years=5,
            education_level="bachelor",
        )

        result = scorer.score(resume, jd, "Python text", "Python text")
        assert "overall_score" in result
        assert "skills" in result
        assert result["skills"] == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# ResumeMatcher (full pipeline) tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResumeMatcher:
    """Tests for the full ResumeMatcher pipeline."""

    @patch("ml.scorers.SemanticScorer._load_model")
    def setup_method(self, method, mock_load):
        """Set up matcher with mocked semantic model."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda text, **kwargs: np.array([0.5, 0.5, 0.7])
        mock_load.return_value = mock_model

        from ml.resume_matcher import ResumeMatcher

        self.matcher = ResumeMatcher()

    def test_match_basic(self):
        result = self.matcher.match(SAMPLE_RESUME, SAMPLE_JD)
        assert 0 <= result.overall_score <= 100
        assert isinstance(result.breakdown.skills, float)
        assert isinstance(result.breakdown.experience, float)
        assert isinstance(result.breakdown.education, float)
        assert isinstance(result.breakdown.semantic, float)

    def test_match_has_matched_skills(self):
        result = self.matcher.match(SAMPLE_RESUME, SAMPLE_JD)
        assert len(result.matched_skills) > 0

    def test_match_identifies_missing_skills(self):
        result = self.matcher.match(SAMPLE_RESUME, SAMPLE_JD)
        # Missing skills list should exist (may or may not have items)
        assert isinstance(result.missing_skills, list)

    def test_match_returns_processing_time(self):
        result = self.matcher.match(SAMPLE_RESUME, SAMPLE_JD)
        assert result.processing_time_ms >= 0

    def test_match_empty_resume_raises(self):
        with pytest.raises(ValueError, match="empty"):
            self.matcher.match("", SAMPLE_JD)

    def test_match_empty_jd_raises(self):
        with pytest.raises(ValueError, match="empty"):
            self.matcher.match(SAMPLE_RESUME, "")

    def test_match_perfect_match(self):
        # Same text should score reasonably high
        text = "Python developer with FastAPI, PostgreSQL, Docker experience"
        result = self.matcher.match(text, text)
        assert result.overall_score >= 70.0

    def test_match_low_match(self):
        resume = "I am a high school student looking for a summer job"
        jd = "PhD in Machine Learning with 10+ years experience in deep learning"
        result = self.matcher.match(resume, jd)
        # Should be a low but non-zero score
        assert result.overall_score < 60.0

    @patch("ml.scorers.SemanticScorer._load_model")
    def test_match_batch(self, mock_load):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda text, **kwargs: np.array([0.5, 0.5, 0.7])
        mock_load.return_value = mock_model

        from ml.resume_matcher import ResumeMatcher

        matcher = ResumeMatcher()
        resumes = [SAMPLE_RESUME, SAMPLE_RESUME_NO_SKILLS]
        results = matcher.match_batch(resumes, SAMPLE_JD)

        assert len(results) == 2
        # Results should be sorted by score descending
        assert results[0].overall_score >= results[1].overall_score

    def test_match_batch_empty(self):
        results = self.matcher.match_batch([], SAMPLE_JD)
        assert results == []

    def test_update_weights(self):
        from ml.config import ScoringWeights

        new_weights = ScoringWeights(
            skills=0.5, experience=0.2, education=0.1, semantic=0.2
        )
        self.matcher.update_weights(new_weights)
        assert self.matcher.config.weights.skills == 0.5

    def test_to_dict(self):
        result = self.matcher.match(SAMPLE_RESUME, SAMPLE_JD)
        d = result.to_dict()
        assert "overall_score" in d
        assert "breakdown" in d
        assert "matched_skills" in d
        assert "missing_skills" in d
        assert "confidence" in d


# ─────────────────────────────────────────────────────────────────────────────
# API endpoint tests
# ─────────────────────────────────────────────────────────────────────────────


class TestMatchAPI:
    """Tests for the match API endpoints."""

    @patch("ml.scorers.SemanticScorer._load_model")
    def setup_method(self, method, mock_load):
        """Set up API client with mocked semantic model."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda text, **kwargs: np.array([0.5, 0.5, 0.7])
        mock_load.return_value = mock_model

        # Reset the module-level matcher
        import api.routers.match as match_module
        match_module._matcher = None

    async def test_match_endpoint_success(self, api_client):
        """Test the POST /api/v1/match/ endpoint."""
        response = await api_client.post(
            "/api/v1/match/",
            json={
                "resume_text": SAMPLE_RESUME,
                "job_description": SAMPLE_JD,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "match_percentage" in data
        assert "breakdown" in data
        assert "matched_skills" in data
        assert "missing_skills" in data
        assert 0 <= data["match_percentage"] <= 100

    async def test_match_endpoint_empty_resume(self, api_client):
        """Test validation error for empty resume."""
        response = await api_client.post(
            "/api/v1/match/",
            json={
                "resume_text": "",
                "job_description": SAMPLE_JD,
            },
        )
        assert response.status_code == 422  # Validation error

    async def test_match_endpoint_empty_jd(self, api_client):
        """Test validation error for empty JD."""
        response = await api_client.post(
            "/api/v1/match/",
            json={
                "resume_text": SAMPLE_RESUME,
                "job_description": "",
            },
        )
        assert response.status_code == 422

    async def test_match_endpoint_short_resume(self, api_client):
        """Test validation error for too-short resume (< 10 chars)."""
        response = await api_client.post(
            "/api/v1/match/",
            json={
                "resume_text": "Hi",
                "job_description": SAMPLE_JD,
            },
        )
        assert response.status_code == 422

    async def test_batch_match_endpoint(self, api_client):
        """Test the POST /api/v1/match/batch endpoint."""
        response = await api_client.post(
            "/api/v1/match/batch",
            json={
                "resume_texts": [SAMPLE_RESUME, SAMPLE_RESUME_NO_SKILLS],
                "job_description": SAMPLE_JD,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "total_processed" in data
        assert data["total_processed"] == 2

    async def test_get_weights_endpoint(self, api_client):
        """Test the GET /api/v1/match/weights endpoint."""
        response = await api_client.get("/api/v1/match/weights")
        assert response.status_code == 200
        data = response.json()
        assert "skills" in data
        assert "experience" in data
        assert "education" in data
        assert "semantic" in data

    async def test_update_weights_endpoint(self, api_client):
        """Test the POST /api/v1/match/weights endpoint."""
        response = await api_client.post(
            "/api/v1/match/weights",
            json={
                "skills": 0.50,
                "experience": 0.20,
                "education": 0.10,
                "semantic": 0.20,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"

    async def test_update_weights_invalid(self, api_client):
        """Test weight validation."""
        response = await api_client.post(
            "/api/v1/match/weights",
            json={
                "skills": 0.50,
                "experience": 0.50,
                "education": 0.50,
                "semantic": 0.50,
            },
        )
        # Should fail because weights don't sum to 1.0
        assert response.status_code in (400, 422)
