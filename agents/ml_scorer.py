"""
agents/ml_scorer.py
~~~~~~~~~~~~~~~~~~~
ML-powered scoring for job-candidate matching.

Uses cosine similarity on embeddings and optional rule-based
scoring on structured fields (skills overlap, seniority match, etc.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.state import CandidateProfile, RetrievalResult
from ingestion.embeddings.embedder import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class MatchScore:
    """Detailed match score between a candidate and a job."""
    job_id: str
    overall_score: float  # 0.0 - 1.0
    skill_match: float  # 0.0 - 1.0
    seniority_match: float  # 0.0 - 1.0
    location_match: float  # 0.0 - 1.0
    embedding_similarity: float  # 0.0 - 1.0
    matched_skills: list[str]
    missing_skills: list[str]
    reasoning: str


class MLScorer:
    """
    Scores job-candidate matches using multiple signals.

    Scoring weights (configurable):
    - Skill overlap: 40%
    - Embedding similarity: 30%
    - Seniority alignment: 15%
    - Location match: 15%
    """

    def __init__(
        self,
        skill_weight: float = 0.40,
        embedding_weight: float = 0.30,
        seniority_weight: float = 0.15,
        location_weight: float = 0.15,
    ):
        self.skill_weight = skill_weight
        self.embedding_weight = embedding_weight
        self.seniority_weight = seniority_weight
        self.location_weight = location_weight

    def score_match(
        self,
        candidate: CandidateProfile,
        job: RetrievalResult,
        candidate_embedding: list[float] | None = None,
        job_embedding: list[float] | None = None,
    ) -> MatchScore:
        """
        Compute a detailed match score between a candidate and a job.

        Parameters
        ----------
        candidate : CandidateProfile
            The candidate's profile.
        job : RetrievalResult
            The job posting to match against.
        candidate_embedding : list[float] | None
            Dense embedding of the candidate profile (optional).
        job_embedding : list[float] | None
            Dense embedding of the job posting (optional).

        Returns
        -------
        MatchScore
            Detailed breakdown of the match quality.
        """
        # Skill overlap score
        skill_match, matched, missing = self._compute_skill_match(
            set(s.lower() for s in candidate.skills),
            set(s.lower() for s in job.skills),
        )

        # Embedding similarity (semantic match)
        embedding_sim = 0.0
        if candidate_embedding and job_embedding:
            embedding_sim = max(
                0.0, cosine_similarity(candidate_embedding, job_embedding)
            )

        # Seniority alignment
        seniority_match = self._compute_seniority_match(
            candidate.seniority, job.seniority
        )

        # Location compatibility
        location_match = self._compute_location_match(
            candidate.location,
            candidate.is_remote,
            job.location,
            job.is_remote,
        )

        # Weighted overall score
        overall = (
            self.skill_weight * skill_match
            + self.embedding_weight * embedding_sim
            + self.seniority_weight * seniority_match
            + self.location_weight * location_match
        )

        # Build reasoning string
        reasoning = self._build_reasoning(
            skill_match, embedding_sim, seniority_match, location_match, matched, missing
        )

        return MatchScore(
            job_id=job.job_id,
            overall_score=round(overall, 3),
            skill_match=round(skill_match, 3),
            seniority_match=round(seniority_match, 3),
            location_match=round(location_match, 3),
            embedding_similarity=round(embedding_sim, 3),
            matched_skills=[m for m in matched],
            missing_skills=[m for m in missing],
            reasoning=reasoning,
        )

    def score_batch(
        self,
        candidate: CandidateProfile,
        jobs: list[RetrievalResult],
        candidate_embedding: list[float] | None = None,
    ) -> list[MatchScore]:
        """Score a candidate against multiple jobs and return sorted by overall_score."""
        scores = []
        for job in jobs:
            job_embedding = None  # Would fetch from ChromaDB in production
            score = self.score_match(candidate, job, candidate_embedding, job_embedding)
            scores.append(score)
        # Sort by overall score descending
        scores.sort(key=lambda s: s.overall_score, reverse=True)
        return scores

    @staticmethod
    def _compute_skill_match(
        candidate_skills: set[str], job_skills: set[str]
    ) -> tuple[float, set[str], set[str]]:
        """Compute Jaccard-style skill match score."""
        if not job_skills:
            return 0.0, set(), set()

        matched = candidate_skills & job_skills
        missing = job_skills - candidate_skills
        score = len(matched) / len(job_skills)
        return score, matched, missing

    @staticmethod
    def _compute_seniority_match(
        candidate_seniority: str | None, job_seniority: str | None
    ) -> float:
        """Score seniority alignment."""
        if not candidate_seniority or not job_seniority:
            return 0.5  # Neutral if unknown

        seniority_order = [
            "intern", "junior", "mid", "senior", "lead",
            "principal", "staff", "director", "vp", "c_level",
        ]

        try:
            c_idx = seniority_order.index(candidate_seniority.lower())
            j_idx = seniority_order.index(job_seniority.lower())
            diff = abs(c_idx - j_idx)
            # Score decreases with distance
            return max(0.0, 1.0 - (diff * 0.25))
        except ValueError:
            return 0.5

    @staticmethod
    def _compute_location_match(
        candidate_location: str | None,
        candidate_remote: bool,
        job_location: str | None,
        job_remote: bool,
    ) -> float:
        """Score location compatibility."""
        # Both remote = perfect match
        if candidate_remote and job_remote:
            return 1.0

        # Candidate wants remote but job isn't = poor match
        if candidate_remote and not job_remote:
            return 0.2

        # Job is remote but candidate wants location = okay match
        if not candidate_remote and job_remote:
            return 0.6

        # Both location-based: check if they match
        if candidate_location and job_location:
            c_loc = candidate_location.lower()
            j_loc = job_location.lower()
            if c_loc == j_loc:
                return 1.0
            # Check if one contains the other (e.g., "San Francisco" in "San Francisco, CA")
            if c_loc in j_loc or j_loc in c_loc:
                return 0.7
            # Same country at least
            c_parts = c_loc.split(",")
            j_parts = j_loc.split(",")
            if len(c_parts) > 1 and len(j_parts) > 1:
                c_country = c_parts[-1].strip()
                j_country = j_parts[-1].strip()
                if c_country == j_country:
                    return 0.5
            return 0.3

        return 0.5  # Unknown

    @staticmethod
    def _build_reasoning(
        skill_score: float,
        embedding_score: float,
        seniority_score: float,
        location_score: float,
        matched_skills: set[str],
        missing_skills: set[str],
    ) -> str:
        """Build a human-readable reasoning string."""
        parts = []

        if skill_score >= 0.8:
            parts.append(f"Excellent skill match ({int(skill_score * 100)}%)")
        elif skill_score >= 0.5:
            parts.append(f"Good skill match ({int(skill_score * 100)}%)")
        else:
            parts.append(f"Limited skill match ({int(skill_score * 100)}%)")

        if matched_skills:
            parts.append(f"matched: {', '.join(list(matched_skills)[:5])}")

        if missing_skills:
            parts.append(f"missing: {', '.join(list(missing_skills)[:3])}")

        if seniority_score >= 0.75:
            parts.append("seniority aligned")
        elif seniority_score < 0.5:
            parts.append("seniority mismatch")

        if location_score >= 0.7:
            parts.append("location compatible")
        elif location_score < 0.4:
            parts.append("location mismatch")

        return "; ".join(parts)
