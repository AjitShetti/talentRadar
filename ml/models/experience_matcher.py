"""
ml/models/experience_matcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Experience level matching between resume and job description.

Compares years of experience from resume against JD requirements
and produces a normalized match score.
"""

from __future__ import annotations

from dataclasses import dataclass

from ml.utils import get_logger

logger = get_logger(__name__)


@dataclass
class ExperienceMatchResult:
    """Result of experience matching.

    Attributes:
        resume_years: Years of experience from resume
        required_years: Years required by JD
        meets_requirement: Whether resume meets or exceeds requirement
        match_score: Normalized score (0-100)
        ratio: Ratio of resume_years / required_years
    """

    resume_years: float = 0.0
    required_years: float = 0.0
    meets_requirement: bool = False
    match_score: float = 0.0
    ratio: float = 0.0


class ExperienceMatcher:
    """Matches experience between resume and job description.

    Computes a score based on how well the candidate's experience
    aligns with the job requirements.

    Scoring logic:
    - If resume_years >= required_years: 100%
    - If resume_years >= 80% of required: linear scale from 80-100
    - If resume_years >= 50% of required: linear scale from 50-80
    - If resume_years < 50% of required: linear scale from 0-50

    If JD has no explicit experience requirement, returns 100% (neutral).
    If resume has no experience info, returns 50% (neutral midpoint).

    Example:
        >>> matcher = ExperienceMatcher()
        >>> result = matcher.match(resume_years=5, required_years=5)
        >>> result.meets_requirement
        True
        >>> result.match_score
        100.0
        >>> result = matcher.match(resume_years=3, required_years=5)
        >>> result.match_score
        80.0
    """

    def match(
        self,
        resume_years: float,
        required_years: float,
    ) -> ExperienceMatchResult:
        """Match experience against requirements.

        Args:
            resume_years: Years of experience from resume (0 if unknown)
            required_years: Years required by JD (0 if not specified)

        Returns:
            ExperienceMatchResult with score and details
        """
        # Handle edge cases
        if required_years <= 0:
            # JD doesn't specify experience requirement
            return ExperienceMatchResult(
                resume_years=resume_years,
                required_years=0,
                meets_requirement=True,
                match_score=100.0,
                ratio=1.0,
            )

        if resume_years <= 0:
            # Resume doesn't specify experience - neutral midpoint
            return ExperienceMatchResult(
                resume_years=0,
                required_years=required_years,
                meets_requirement=False,
                match_score=50.0,
                ratio=0.0,
            )

        ratio = resume_years / required_years
        meets_requirement = resume_years >= required_years

        # Compute score based on ratio
        if meets_requirement:
            # Exceeds or meets requirement - full score
            score = 100.0
        elif ratio >= 0.8:
            # 80-100% of requirement: score 80-100
            score = 80 + (ratio - 0.8) / 0.2 * 20
        elif ratio >= 0.5:
            # 50-80% of requirement: score 50-80
            score = 50 + (ratio - 0.5) / 0.3 * 30
        else:
            # < 50% of requirement: score 0-50
            score = ratio / 0.5 * 50

        score = round(min(score, 100.0), 1)

        logger.debug(
            "Experience matching complete",
            resume_years=resume_years,
            required_years=required_years,
            ratio=round(ratio, 2),
            score=score,
        )

        return ExperienceMatchResult(
            resume_years=resume_years,
            required_years=required_years,
            meets_requirement=meets_requirement,
            match_score=score,
            ratio=round(ratio, 2),
        )
