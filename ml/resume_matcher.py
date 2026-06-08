"""
ml/resume_matcher.py
~~~~~~~~~~~~~~~~~~~~
Main pipeline class for resume-to-job-description matching.

Orchestrates the full pipeline:
1. Text preprocessing
2. Feature extraction
3. Multi-strategy scoring
4. Result aggregation and formatting

Supports single and batch matching.

Example usage:
    >>> from ml.resume_matcher import ResumeMatcher
    >>> matcher = ResumeMatcher()
    >>> result = matcher.match(resume_text, job_description_text)
    >>> print(result.overall_score)
    85.5
    >>> print(result.breakdown)
    {'skills': 90.0, 'experience': 80.0, 'education': 85.0, 'semantic': 82.0}

    # Batch matching
    >>> results = matcher.match_batch(resume_texts, job_description_text)
    >>> for r in results:
    ...     print(f"Resume: {r.overall_score}%")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ml.config import PipelineConfig, ScoringWeights
from ml.feature_extractor import ExtractedFeatures, extract_features
from ml.models.skill_matcher import SkillMatcher
from ml.preprocessing import CleanedText, preprocess_text
from ml.scorers import CompositeScorer
from ml.utils import get_logger, truncate_text

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MatchBreakdown:
    """Detailed breakdown of match scores by category.

    Attributes:
        skills: Skill match score (0-100)
        experience: Experience match score (0-100)
        education: Education match score (0-100)
        semantic: Semantic similarity score (0-100)
    """

    skills: float
    experience: float
    education: float
    semantic: float

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            "skills": self.skills,
            "experience": self.experience,
            "education": self.education,
            "semantic": self.semantic,
        }


@dataclass
class MatchResult:
    """Complete result from a single match computation.

    Attributes:
        overall_score: Final weighted match percentage (0-100)
        breakdown: Score breakdown by category
        matched_skills: Skills found in both resume and JD
        missing_skills: Skills required by JD but not in resume
        extra_skills: Skills in resume but not required by JD
        confidence: Overall confidence in the match (0-1)
        processing_time_ms: Time taken to compute match in milliseconds
        warnings: Any warnings during processing
    """

    overall_score: float
    breakdown: MatchBreakdown
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    extra_skills: list[str] = field(default_factory=list)
    confidence: float = 1.0
    processing_time_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "overall_score": self.overall_score,
            "breakdown": self.breakdown.to_dict(),
            "matched_skills": self.matched_skills,
            "missing_skills": self.missing_skills,
            "extra_skills": self.extra_skills,
            "confidence": round(self.confidence, 2),
            "processing_time_ms": round(self.processing_time_ms, 1),
            "warnings": self.warnings,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline class
# ─────────────────────────────────────────────────────────────────────────────


class ResumeMatcher:
    """Main pipeline for resume-to-job-description matching.

    Orchestrates text preprocessing, feature extraction, scoring,
    and result formatting.

    Attributes:
        config: PipelineConfig with weights and settings

    Example:
        >>> matcher = ResumeMatcher()
        >>> result = matcher.match(
        ...     resume_text="Python developer with 5 years experience...",
        ...     job_description="Looking for Python developer with FastAPI..."
        ... )
        >>> result.overall_score
        85.5
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        """Initialize the matcher with configuration.

        Args:
            config: Optional custom configuration. Uses defaults if not provided.
        """
        self.config = config or PipelineConfig()
        self._composite_scorer = CompositeScorer(self.config)
        self._skill_matcher = SkillMatcher()

        logger.info(
            "ResumeMatcher initialized",
            weights=self.config.weights.to_dict(),
            embedding_model=self.config.embedding_model,
        )

    def match(
        self,
        resume_text: str,
        job_description: str,
    ) -> MatchResult:
        """Compute match score between a resume and job description.

        Full pipeline:
        1. Validate inputs
        2. Preprocess both texts
        3. Extract features
        4. Run composite scoring
        5. Format results

        Args:
            resume_text: Raw resume text
            job_description: Raw job description text

        Returns:
            MatchResult with score, breakdown, and details

        Raises:
            ValueError: If inputs are empty or too long

        Example:
            >>> matcher = ResumeMatcher()
            >>> result = matcher.match(resume_text, jd_text)
            >>> print(f"Match: {result.overall_score}%")
        """
        start_time = time.monotonic()
        warnings: list[str] = []

        # ── Step 1: Validate inputs ──────────────────────────────────────
        self._validate_input(resume_text, job_description, warnings)

        # ── Step 2: Preprocess texts ─────────────────────────────────────
        try:
            resume_cleaned = preprocess_text(
                resume_text,
                max_length=self.config.max_resume_length,
            )
            jd_cleaned = preprocess_text(
                job_description,
                max_length=self.config.max_jd_length,
            )
        except ValueError as e:
            raise ValueError(f"Preprocessing failed: {e}") from e

        # ── Step 3: Extract features ─────────────────────────────────────
        resume_features = extract_features(
            resume_cleaned,
            text_type="resume",
            known_skills=self.config.known_skills,
        )
        jd_features = extract_features(
            jd_cleaned,
            text_type="job_description",
            known_skills=self.config.known_skills,
        )

        # ── Step 4: Run composite scoring ────────────────────────────────
        component_scores = self._composite_scorer.score(
            resume_features=resume_features,
            jd_features=jd_features,
            resume_text=resume_text,
            jd_text=job_description,
        )

        # ── Step 5: Extract skill details ────────────────────────────────
        skill_match = self._skill_matcher.match(
            resume_features.skills.matched_skills,
            jd_features.skills.matched_skills,
        )

        # ── Step 6: Build result ─────────────────────────────────────────
        processing_time_ms = (time.monotonic() - start_time) * 1000

        breakdown = MatchBreakdown(
            skills=component_scores.get("skills", 0.0),
            experience=component_scores.get("experience", 0.0),
            education=component_scores.get("education", 0.0),
            semantic=component_scores.get("semantic", 0.0),
        )

        result = MatchResult(
            overall_score=component_scores.get("overall_score", 0.0),
            breakdown=breakdown,
            matched_skills=skill_match.matched_skills,
            missing_skills=skill_match.missing_skills,
            extra_skills=skill_match.extra_skills,
            confidence=self._compute_confidence(component_scores),
            processing_time_ms=processing_time_ms,
            warnings=warnings,
        )

        logger.info(
            "Match complete",
            overall_score=result.overall_score,
            processing_time_ms=round(processing_time_ms, 1),
            matched_skills=len(result.matched_skills),
            missing_skills=len(result.missing_skills),
        )

        return result

    def match_batch(
        self,
        resume_texts: list[str],
        job_description: str,
    ) -> list[MatchResult]:
        """Match multiple resumes against a single job description.

        Processes resumes sequentially. For high-throughput scenarios,
        consider parallel processing with concurrent.futures.

        Args:
            resume_texts: List of resume texts
            job_description: Single job description text

        Returns:
            List of MatchResult, one per resume, in same order as input

        Example:
            >>> matcher = ResumeMatcher()
            >>> results = matcher.match_batch([resume1, resume2], jd_text)
            >>> results.sort(key=lambda r: r.overall_score, reverse=True)
            >>> print(f"Best match: {results[0].overall_score}%")
        """
        if not resume_texts:
            return []

        results: list[MatchResult] = []
        logger.info("Batch matching started", num_resumes=len(resume_texts))

        for i, resume_text in enumerate(resume_texts):
            try:
                result = self.match(resume_text, job_description)
                results.append(result)
            except Exception as e:
                logger.error(
                    "Batch match failed for resume",
                    index=i,
                    error=str(e),
                )
                results.append(
                    MatchResult(
                        overall_score=0.0,
                        breakdown=MatchBreakdown(
                            skills=0.0, experience=0.0, education=0.0, semantic=0.0
                        ),
                        warnings=[f"Match failed: {e}"],
                    )
                )

        # Sort by score descending (best matches first)
        results.sort(key=lambda r: r.overall_score, reverse=True)

        return results

    def update_weights(self, weights: ScoringWeights) -> None:
        """Update scoring weights.

        Args:
            weights: New scoring weights (must sum to 1.0)
        """
        self.config = PipelineConfig(
            weights=weights,
            embedding_model=self.config.embedding_model,
            known_skills=self.config.known_skills,
        )
        self._composite_scorer = CompositeScorer(self.config)
        logger.info("Scoring weights updated", weights=weights.to_dict())

    # ── Internal helpers ─────────────────────────────────────────────────

    def _validate_input(
        self,
        resume_text: str,
        job_description: str,
        warnings: list[str],
    ) -> None:
        """Validate input texts.

        Args:
            resume_text: Resume text
            job_description: Job description text
            warnings: List to append warnings to

        Raises:
            ValueError: If inputs are invalid
        """
        if not resume_text or not resume_text.strip():
            raise ValueError("Resume text is empty")

        if not job_description or not job_description.strip():
            raise ValueError("Job description text is empty")

        if len(resume_text) > self.config.max_resume_length:
            warnings.append(
                f"Resume text truncated from {len(resume_text)} to "
                f"{self.config.max_resume_length} characters"
            )

        if len(job_description) > self.config.max_jd_length:
            warnings.append(
                f"Job description truncated from {len(job_description)} to "
                f"{self.config.max_jd_length} characters"
            )

    @staticmethod
    def _compute_confidence(component_scores: dict[str, float]) -> float:
        """Compute overall confidence based on component scores.

        Higher confidence when all scorers agree and have high scores.

        Args:
            component_scores: Scores from each component

        Returns:
            Confidence value (0-1)
        """
        if not component_scores:
            return 0.0

        scores = [
            v for k, v in component_scores.items()
            if k not in ("overall_score",)
        ]

        if not scores:
            return 0.0

        # Confidence based on score consistency (low variance = high confidence)
        mean_score = sum(scores) / len(scores)
        if len(scores) > 1:
            variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
            std_dev = variance ** 0.5
            # Lower std_dev -> higher confidence
            confidence = max(0.3, 1.0 - (std_dev / 50.0))
        else:
            confidence = 0.5  # Single component

        return round(min(confidence, 1.0), 2)
