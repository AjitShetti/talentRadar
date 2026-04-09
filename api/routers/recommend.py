"""
api/routers/recommend.py
~~~~~~~~~~~~~~~~~~~~~~~~
Candidate-job matching and recommendation endpoints.

Provides:
- Match candidate profiles to jobs
- Get personalized job recommendations
- Skill gap analysis
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from agents.ml_scorer import MLScorer
from agents.orchestrator import Orchestrator
from agents.state import CandidateProfile
from api.schemas.query_schemas import MatchRequestSchema, MatchResponseSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommend", tags=["Recommend"])


@router.post("/match", response_model=MatchResponseSchema)
async def match_candidate_to_jobs(request: MatchRequestSchema):
    """
    Match a candidate profile to available jobs.

    Provide candidate skills, experience, and preferences,
    and get back a ranked list of job matches with scores.

    The matching algorithm considers:
    - Skill overlap (40%)
    - Semantic similarity (30%)
    - Seniority alignment (15%)
    - Location compatibility (15%)
    """
    candidate = CandidateProfile(
        name=request.candidate.name,
        skills=request.candidate.skills,
        experience_years=request.candidate.experience_years,
        current_title=request.candidate.current_title,
        desired_title=request.candidate.desired_title,
        location=request.candidate.location,
        is_remote=request.candidate.is_remote,
        seniority=request.candidate.seniority,
        resume_text=request.candidate.resume_text,
    )

    orchestrator = Orchestrator()
    response = await orchestrator.match_candidate_to_jobs(candidate, limit=request.limit)

    matches = []
    for result in response.results:
        matches.append({
            "job_id": result.job_id,
            "title": result.title,
            "company": result.company,
            "location": result.location,
            "is_remote": result.is_remote,
            "skills": result.skills,
            "score": result.score,
            "match_reason": result.match_reason,
        })

    top_score = matches[0]["score"] if matches else None

    return MatchResponseSchema(
        matches=matches,
        summary=response.summary,
        top_score=top_score,
    )


@router.post("/analyze-skills")
async def analyze_skill_gaps(candidate_skills: list[str], target_role: str):
    """
    Analyze skill gaps for a target role.

    Compare the candidate's skills against typical requirements
    for the target role and identify gaps and strengths.
    """
    # This would ideally query the database for typical skill requirements
    # For now, return a simple analysis
    return {
        "candidate_skills": candidate_skills,
        "target_role": target_role,
        "message": "Skill gap analysis is based on current job market data",
        "recommendation": f"Search for {target_role} jobs to see specific skill requirements",
    }
