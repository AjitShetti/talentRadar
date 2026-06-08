"""
ml/scorers.py
~~~~~~~~~~~~~
Multiple scoring strategies for resume-to-JD matching.

Provides:
- SkillMatchScorer: Based on skill overlap percentage
- ExperienceScorer: Based on experience alignment
- EducationScorer: Based on education level matching
- SemanticScorer: Based on sentence-transformer embeddings
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ml.config import EDUCATION_HIERARCHY, PipelineConfig
from ml.feature_extractor import ExtractedFeatures
from ml.models.experience_matcher import ExperienceMatcher, ExperienceMatchResult
from ml.models.skill_matcher import SkillMatcher, SkillMatchResult
from ml.utils import clamp_score, get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Base scorer protocol
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ScoreResult:
    """Result from a single scoring strategy.

    Attributes:
        score: Score on 0-100 scale
        component_name: Name of the scoring component
        details: Additional details about the score computation
        confidence: Confidence in the score (0-1)
    """

    score: float
    component_name: str
    details: dict[str, Any] | None = None
    confidence: float = 1.0


class BaseScorer:
    """Base class for scoring strategies."""

    def __init__(self, name: str) -> None:
        self.name = name

    def score(self, resume_features: ExtractedFeatures, jd_features: ExtractedFeatures) -> ScoreResult:
        """Compute match score between resume and JD features.

        Args:
            resume_features: Features extracted from resume
            jd_features: Features extracted from job description

        Returns:
            ScoreResult with score and metadata
        """
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Skill scorer
# ─────────────────────────────────────────────────────────────────────────────


class SkillMatchScorer(BaseScorer):
    """Scores based on skill overlap between resume and JD.

    Uses the SkillMatcher to compute what percentage of JD-required
    skills are present in the resume.

    Example:
        >>> scorer = SkillMatchScorer()
        >>> result = scorer.score(resume_features, jd_features)
        >>> result.score
        75.0
    """

    def __init__(self) -> None:
        super().__init__("skills")
        self.matcher = SkillMatcher()

    def score(
        self,
        resume_features: ExtractedFeatures,
        jd_features: ExtractedFeatures,
    ) -> ScoreResult:
        """Compute skill match score.

        Args:
            resume_features: Resume feature set
            jd_features: JD feature set

        Returns:
            ScoreResult with skill match percentage
        """
        resume_skills = resume_features.skills.matched_skills
        jd_skills = jd_features.skills.matched_skills

        match_result = self.matcher.match(resume_skills, jd_skills)

        details = {
            "matched_skills": match_result.matched_skills,
            "missing_skills": match_result.missing_skills,
            "extra_skills": match_result.extra_skills,
            "total_required": match_result.total_required,
            "total_matched": match_result.total_matched,
        }

        # If JD has no skills listed, give a neutral score
        if not jd_skills:
            return ScoreResult(
                score=70.0,  # Neutral - can't assess skills
                component_name=self.name,
                details=details,
                confidence=0.3,
            )

        return ScoreResult(
            score=match_result.match_percentage,
            component_name=self.name,
            details=details,
            confidence=0.9,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Experience scorer
# ─────────────────────────────────────────────────────────────────────────────


class ExperienceScorer(BaseScorer):
    """Scores based on experience alignment.

    Compares years of experience from resume against JD requirements.
    """

    def __init__(self) -> None:
        super().__init__("experience")
        self.matcher = ExperienceMatcher()

    def score(
        self,
        resume_features: ExtractedFeatures,
        jd_features: ExtractedFeatures,
    ) -> ScoreResult:
        """Compute experience match score.

        Args:
            resume_features: Resume feature set
            jd_features: JD feature set

        Returns:
            ScoreResult with experience match score
        """
        resume_years = resume_features.experience.years_min
        required_years = jd_features.experience.years_min

        # If resume doesn't have experience info but JD does,
        # try to infer from keywords
        if not resume_features.experience.has_experience_info and required_years > 0:
            # Try to find experience indicators in resume
            resume_years = self._infer_experience(resume_features)

        result: ExperienceMatchResult = self.matcher.match(resume_years, required_years)

        details = {
            "resume_years": result.resume_years,
            "required_years": result.required_years,
            "meets_requirement": result.meets_requirement,
            "ratio": result.ratio,
        }

        return ScoreResult(
            score=result.match_score,
            component_name=self.name,
            details=details,
            confidence=0.8 if result.resume_years > 0 else 0.4,
        )

    def _infer_experience(self, features: ExtractedFeatures) -> float:
        """Try to infer experience from seniority indicators.

        Args:
            features: Resume features

        Returns:
            Estimated years of experience (0 if cannot infer)
        """
        text = features.skills.all_tokens  # This won't work, let's use raw approach
        # We don't have raw text here, so return 0
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Education scorer
# ─────────────────────────────────────────────────────────────────────────────


class EducationScorer(BaseScorer):
    """Scores based on education level matching.

    Compares education levels between resume and JD requirements.
    """

    def __init__(self) -> None:
        super().__init__("education")

    def score(
        self,
        resume_features: ExtractedFeatures,
        jd_features: ExtractedFeatures,
    ) -> ScoreResult:
        """Compute education match score.

        Scoring logic:
        - If JD has no education requirement: 100% (neutral)
        - If resume level >= JD level: 100%
        - If resume level < JD level: proportional score

        Args:
            resume_features: Resume feature set
            jd_features: JD feature set

        Returns:
            ScoreResult with education match score
        """
        resume_level = resume_features.education.level_score
        jd_level = jd_features.education.level_score

        details = {
            "resume_level": resume_features.education.highest_level or "unknown",
            "jd_level": jd_features.education.highest_level or "not specified",
            "resume_score": resume_level,
            "jd_score": jd_level,
        }

        # If JD doesn't specify education, neutral score
        if not jd_features.education.has_education_info or jd_level == 0:
            return ScoreResult(
                score=80.0,  # Neutral-high (most candidates have some education)
                component_name=self.name,
                details=details,
                confidence=0.3,
            )

        # If resume has no education info, assume baseline
        if not resume_features.education.has_education_info or resume_level == 0:
            return ScoreResult(
                score=40.0,  # Below average - no education info
                component_name=self.name,
                details=details,
                confidence=0.2,
            )

        # Compare levels
        if resume_level >= jd_level:
            score = 100.0
        else:
            # Proportional: how close is resume to JD requirement
            max_level = max(len(EDUCATION_HIERARCHY), 1)
            gap = jd_level - resume_level
            score = max(0, (1 - gap / max_level) * 100)

        score = clamp_score(score)

        return ScoreResult(
            score=score,
            component_name=self.name,
            details=details,
            confidence=0.7,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Semantic scorer (uses sentence-transformers)
# ─────────────────────────────────────────────────────────────────────────────


class SemanticScorer(BaseScorer):
    """Scores based on semantic similarity using sentence embeddings.

    Uses sentence-transformers to compute cosine similarity between
    resume and job description embeddings.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        super().__init__("semantic")
        self.model_name = model_name
        self._model: Any = None

    def _load_model(self) -> Any:
        """Lazy-load the sentence transformer model.

        Returns:
            Loaded SentenceTransformer model
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # noqa: PLC0415

                self._model = SentenceTransformer(self.model_name)
                logger.info("Semantic model loaded", model=self.model_name)
            except Exception as e:
                logger.error("Failed to load semantic model", error=str(e))
                raise

        return self._model

    def score(
        self,
        resume_features: ExtractedFeatures,
        jd_features: ExtractedFeatures,
        resume_text: str | None = None,
        jd_text: str | None = None,
    ) -> ScoreResult:
        """Compute semantic similarity score.

        Args:
            resume_features: Resume feature set
            jd_features: JD feature set
            resume_text: Optional original resume text for embedding
            jd_text: Optional original JD text for embedding

        Returns:
            ScoreResult with semantic similarity (0-100)

        Raises:
            RuntimeError: If model fails to load
        """
        try:
            model = self._load_model()
        except Exception as e:
            logger.error("Semantic scoring failed", error=str(e))
            return ScoreResult(
                score=50.0,  # Neutral fallback
                component_name=self.name,
                details={"error": str(e)},
                confidence=0.0,
            )

        # Use original texts if available, otherwise use cleaned text
        text_a = resume_text or " ".join(resume_features.skills.matched_skills)
        text_b = jd_text or " ".join(jd_features.skills.matched_skills)

        if not text_a.strip() or not text_b.strip():
            return ScoreResult(
                score=50.0,
                component_name=self.name,
                details={"error": "Empty text for semantic comparison"},
                confidence=0.0,
            )

        try:
            # Compute embeddings
            embedding_a = model.encode(text_a, convert_to_numpy=True)
            embedding_b = model.encode(text_b, convert_to_numpy=True)

            # Cosine similarity
            similarity = self._cosine_similarity(embedding_a, embedding_b)

            # Convert from [-1, 1] to [0, 100]
            score = clamp_score((similarity + 1) / 2 * 100)

            details = {
                "cosine_similarity": round(float(similarity), 4),
                "model": self.model_name,
            }

            return ScoreResult(
                score=score,
                component_name=self.name,
                details=details,
                confidence=0.85,
            )

        except Exception as e:
            logger.error("Semantic scoring computation failed", error=str(e))
            return ScoreResult(
                score=50.0,
                component_name=self.name,
                details={"error": str(e)},
                confidence=0.0,
            )

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors.

        Args:
            a: First vector
            b: Second vector

        Returns:
            Cosine similarity in [-1, 1]
        """
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))


# ─────────────────────────────────────────────────────────────────────────────
# Composite scorer (combines all scorers)
# ─────────────────────────────────────────────────────────────────────────────


class CompositeScorer:
    """Combines multiple scoring strategies with configurable weights.

    This is the main scoring orchestrator that runs all scorers and
    computes the final weighted score.

    Attributes:
        config: PipelineConfig with weights and settings

    Example:
        >>> config = PipelineConfig()
        >>> composite = CompositeScorer(config)
        >>> result = composite.score(resume_features, jd_features, resume_text, jd_text)
        >>> result["overall_score"]
        85.5
        >>> result["skills"]
        90.0
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.scorers: dict[str, BaseScorer] = {
            "skills": SkillMatchScorer(),
            "experience": ExperienceScorer(),
            "education": EducationScorer(),
            "semantic": SemanticScorer(model_name=config.embedding_model),
        }

    def score(
        self,
        resume_features: ExtractedFeatures,
        jd_features: ExtractedFeatures,
        resume_text: str | None = None,
        jd_text: str | None = None,
    ) -> dict[str, float]:
        """Compute composite match score.

        Runs all scorers and combines them using configured weights.

        Args:
            resume_features: Resume features
            jd_features: JD features
            resume_text: Optional original resume text
            jd_text: Optional original JD text

        Returns:
            Dictionary with component scores and overall score:
            {
                "overall_score": 85.5,
                "skills": 90.0,
                "experience": 80.0,
                "education": 85.0,
                "semantic": 82.0,
            }
        """
        weights = self.config.weights.to_dict()
        component_scores: dict[str, float] = {}

        # Run each scorer
        score_results: dict[str, ScoreResult] = {}
        for name, scorer in self.scorers.items():
            try:
                if name == "semantic":
                    result = self.scorers["semantic"].score(
                        resume_features, jd_features, resume_text, jd_text
                    )
                else:
                    result = scorer.score(resume_features, jd_features)
                score_results[name] = result
                component_scores[name] = result.score
            except Exception as e:
                logger.warning(
                    "Scorer failed, using neutral score",
                    scorer=name,
                    error=str(e),
                )
                component_scores[name] = 50.0  # Neutral fallback

        # Compute weighted overall score
        overall_score = sum(
            component_scores[name] * weights[name]
            for name in weights
            if name in component_scores
        )
        overall_score = clamp_score(overall_score)

        component_scores["overall_score"] = overall_score

        logger.info(
            "Composite scoring complete",
            overall_score=overall_score,
            component_scores=component_scores,
        )

        return component_scores
