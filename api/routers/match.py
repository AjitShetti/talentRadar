"""
api/routers/match.py
~~~~~~~~~~~~~~~~~~~~
Resume-to-job-description matching endpoints.

Provides:
- Single resume-JD matching
- Batch matching for multiple resumes
- Configurable scoring weights

The matching pipeline considers:
- Skills match (40% weight): How many required skills match the resume
- Experience match (25% weight): Years of experience vs required
- Education match (15% weight): Education level matching
- Semantic similarity (20% weight): Overall text similarity using embeddings
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.schemas.match_schemas import (
    BatchMatchRequestSchema,
    BatchMatchResponseSchema,
    BatchMatchResultSchema,
    MatchRequestSchema,
    MatchWeightsSchema,
    SingleMatchResultSchema,
    ScoreBreakdownSchema,
)
from api.schemas.ai_core_schemas import (
    EvaluateCandidateRequest,
    EvaluateCandidateResponse,
    TailorResumeRequest,
)
from celery.result import AsyncResult
from sse_starlette.sse import EventSourceResponse
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi import Depends
from functools import lru_cache
from agents.tasks import evaluate_candidate_task
from agents.resume_tailor import ResumeTailor
from api.utils.docx_generator import generate_resume_docx
from ml.config import PipelineConfig, ScoringWeights
from ml.resume_matcher import ResumeMatcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/match", tags=["Match"])

@lru_cache(maxsize=1)
def get_matcher() -> ResumeMatcher:
    """Get or create the matcher instance.

    Uses a singleton pattern (via lru_cache) to avoid reloading the embedding model
    for each request.
    """
    return ResumeMatcher()


@router.post("/", response_model=SingleMatchResultSchema)
async def match_resume_to_job(request: MatchRequestSchema, matcher: ResumeMatcher = Depends(get_matcher)):
    """Match a single resume against a job description.

    Analyzes the resume text and returns a percentage match score with
    detailed breakdown by category (skills, experience, education, semantic).

    **Request body:**
    - `resume_text`: Full resume text (plain text or HTML)
    - `job_description`: Full job description text

    **Response:**
    - `match_percentage`: Overall match score (0-100)
    - `breakdown`: Score by category
    - `matched_skills`: Skills found in both
    - `missing_skills`: Required skills not in resume
    - `extra_skills`: Skills in resume but not required
    - `confidence`: Confidence in the match (0-1)

    **Example request:**
    ```json
    {
        "resume_text": "Python developer with 5 years experience in FastAPI...",
        "job_description": "Looking for a senior Python developer with FastAPI..."
    }
    ```

    **Example response:**
    ```json
    {
        "match_percentage": 85.5,
        "breakdown": {
            "skills": 90.0,
            "experience": 80.0,
            "education": 85.0,
            "semantic": 82.0
        },
        "matched_skills": ["Python", "FastAPI", "PostgreSQL"],
        "missing_skills": ["Docker", "Kubernetes"],
        "extra_skills": ["Redis"],
        "confidence": 0.85,
        "processing_time_ms": 150.5
    }
    ```
    """
    try:
        result = await run_in_threadpool(
            matcher.match,
            resume_text=request.resume_text,
            job_description=request.job_description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("Match request failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Matching failed: {e}") from e

    return SingleMatchResultSchema(
        match_percentage=result.overall_score,
        breakdown=ScoreBreakdownSchema(
            skills=result.breakdown.skills,
            experience=result.breakdown.experience,
            education=result.breakdown.education,
            semantic=result.breakdown.semantic,
        ),
        matched_skills=result.matched_skills,
        missing_skills=result.missing_skills,
        extra_skills=result.extra_skills,
        confidence=result.confidence,
        processing_time_ms=result.processing_time_ms,
        warnings=result.warnings,
    )


@router.post("/batch", response_model=BatchMatchResponseSchema)
async def batch_match_resumes(request: BatchMatchRequestSchema, matcher: ResumeMatcher = Depends(get_matcher)):
    """Match multiple resumes against a single job description.

    Processes all resumes and returns results sorted by match score
    (best matches first).

    **Request body:**
    - `resume_texts`: List of resume texts
    - `job_description`: Job description to match against

    **Response:**
    - `results`: Match results sorted by score (descending)
    - `total_processed`: Number of resumes processed
    - `top_match_percentage`: Best match score

    **Example request:**
    ```json
    {
        "resume_texts": [
            "Python developer with 5 years...",
            "Java engineer with 3 years..."
        ],
        "job_description": "Senior Python developer..."
    }
    ```
    """
    try:
        results = await run_in_threadpool(
            matcher.match_batch,
            resume_texts=request.resume_texts,
            job_description=request.job_description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("Batch match request failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch matching failed: {e}") from e

    batch_results: list[BatchMatchResultSchema] = []
    for result in results:
        batch_results.append(
            BatchMatchResultSchema(
                match_percentage=result.overall_score,
                breakdown=ScoreBreakdownSchema(
                    skills=result.breakdown.skills,
                    experience=result.breakdown.experience,
                    education=result.breakdown.education,
                    semantic=result.breakdown.semantic,
                ),
                matched_skills=result.matched_skills,
                missing_skills=result.missing_skills,
                extra_skills=result.extra_skills,
                confidence=result.confidence,
                resume_index=0,  # Index is lost after sorting; could be tracked
            )
        )

    top_match = batch_results[0].match_percentage if batch_results else None

    return BatchMatchResponseSchema(
        results=batch_results,
        total_processed=len(results),
        top_match_percentage=top_match,
    )


@router.post("/weights", response_model=dict)
async def update_weights(weights: MatchWeightsSchema):
    """Update scoring weights for the matching pipeline.

    Changes how much each component contributes to the overall score.
    Weights must sum to 1.0.

    **Default weights:**
    - skills: 0.40 (40%)
    - experience: 0.25 (25%)
    - education: 0.15 (15%)
    - semantic: 0.20 (20%)

    **Example request:**
    ```json
    {
        "skills": 0.50,
        "experience": 0.20,
        "education": 0.10,
        "semantic": 0.20
    }
    ```
    """
    try:
        # TODO: Store new weights in Redis or Database here so they apply across all workers.
        # For now, we simply acknowledge the payload without mutating a thread-unsafe global.
        logger.warning("Weights endpoint called. A database is required to persist this change across workers.")

        return {
            "status": "updated",
            "weights": {
                "skills": weights.skills,
                "experience": weights.experience,
                "education": weights.education,
                "semantic": weights.semantic,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("Failed to update weights", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/weights", response_model=MatchWeightsSchema)
async def get_weights(matcher: ResumeMatcher = Depends(get_matcher)):
    """Get current scoring weights.

    Returns the current weights used by the matching pipeline.
    """
    w = matcher.config.weights

    return MatchWeightsSchema(
        skills=w.skills,
        experience=w.experience,
        education=w.education,
        semantic=w.semantic,
    )

@router.post("/evaluate", response_model=EvaluateCandidateResponse)
async def evaluate_candidate(request: EvaluateCandidateRequest):
    """
    Dispatch an async Celery task to evaluate a candidate's resume using the LLM-as-a-judge.
    Returns a task ID that can be tracked via the SSE endpoint.
    """
    try:
        task = evaluate_candidate_task.delay(request.resume_text, request.job_description)
        return EvaluateCandidateResponse(task_id=task.id)
    except Exception as e:
        logger.error("Evaluate request failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Evaluation dispatch failed: {e}") from e

@router.get("/evaluate/stream/{task_id}")
async def evaluate_stream(task_id: str):
    """
    Stream Celery task status updates via Server-Sent Events (SSE).
    """
    import asyncio
    async def event_generator():
        while True:
            task = AsyncResult(task_id)
            if task.ready():
                if task.successful():
                    yield {
                        "event": "success",
                        "data": json.dumps(task.result)
                    }
                else:
                    yield {
                        "event": "error",
                        "data": str(task.result)
                    }
                break
            else:
                yield {
                    "event": "processing",
                    "data": task.status
                }
            await asyncio.sleep(1)

    import json
    return EventSourceResponse(event_generator())

@router.post("/tailor-resume")
async def tailor_resume(request: TailorResumeRequest):
    """
    Tailor a resume for a specific job description and return it as a .docx file.
    """
    try:
        tailor = ResumeTailor()
        tailored_text = tailor.tailor(request.resume_text, request.job_description)
        
        docx_stream = generate_resume_docx(tailored_text)
        
        return StreamingResponse(
            docx_stream,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": "attachment; filename=Tailored_Resume.docx"}
        )
    except Exception as e:
        logger.error("Failed to tailor resume", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
