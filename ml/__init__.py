"""
ml/__init__.py
~~~~~~~~~~~~~~
Resume-to-job-description matching pipeline for TalentRadar.

Provides:
- ResumeMatcher: Main pipeline class for matching resumes to job descriptions
- PipelineConfig: Configuration with scoring weights
- Support for skills, experience, education, and semantic matching

Example usage:
    >>> from ml import ResumeMatcher, PipelineConfig
    >>> matcher = ResumeMatcher()
    >>> result = matcher.match(resume_text, job_description)
    >>> print(f"Match: {result.overall_score}%")
"""

from __future__ import annotations

from ml.config import PipelineConfig, ScoringWeights
from ml.resume_matcher import MatchResult, ResumeMatcher

__all__ = [
    "ResumeMatcher",
    "MatchResult",
    "PipelineConfig",
    "ScoringWeights",
]
