"""
api/routers/match.py
~~~~~~~~~~~~~~~~~~~~
Resume-to-job-description matching endpoints.

Provides:
- Single resume-JD matching (upload + tailored PDF)
- Batch matching for multiple resumes
- Read-only view of the scoring weights
- LLM-as-a-judge candidate evaluation with SSE progress

The matching pipeline considers:
- Skills match (40% weight): How many required skills match the resume
- Experience match (25% weight): Years of experience vs required
- Education match (15% weight): Education level matching
- Semantic similarity (20% weight): Overall text similarity using embeddings

Auth
----
Every endpoint here spends money or CPU on the caller's behalf — an LLM
completion, an embedding pass, or a pdflatex run. They are therefore all
behind ``get_current_user_id``. Leaving them open let anyone drain the
project's Groq quota and pin the container, which on a free tier takes the
whole deployment down for everybody.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from functools import lru_cache
from io import BytesIO
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from agents.resume_tailor import ResumeTailor
from api.auth import get_current_user
from api.schemas.ai_core_schemas import (
    EvaluateCandidateRequest,
    EvaluateCandidateResponse,
    TailorResumeRequest,
)
from api.schemas.match_schemas import (
    BatchMatchRequestSchema,
    BatchMatchResponseSchema,
    BatchMatchResultSchema,
    MatchAndTailorResponseSchema,
    MatchWeightsSchema,
    ScoreBreakdownSchema,
)
from api.utils.file_parser import extract_text_from_bytes
from api.utils.latex_compiler import LatexCompileError, compile_latex_to_pdf_async
from api.utils.uploads import content_disposition, read_capped, safe_filename
from config.settings import get_settings
from ml.resume_matcher import ResumeMatcher
from services.resumes import analyze_resume

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/match", tags=["Match"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing sub claim",
        )
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]

# ---------------------------------------------------------------------------
# Async evaluation tasks
# ---------------------------------------------------------------------------
# In-memory, therefore per-process: a task started on one worker is invisible
# to the SSE stream if that request lands on another. Run the API with a
# single worker (see infra/Dockerfile) or move this to Redis before scaling
# out. Entries carry a creation time so an abandoned task cannot leak.

_EVALUATION_TASKS: dict[str, dict[str, Any]] = {}
#: Strong references to in-flight tasks. asyncio only holds a weak reference to
#: a bare create_task() result, so without this the GC can cancel a running
#: evaluation mid-flight.
_EVALUATION_TASK_REFS: set[asyncio.Task[None]] = set()
#: Drop finished-but-never-collected results after this long.
_EVALUATION_TTL_SECONDS = 300.0
#: Refuse new evaluations past this many in flight, so the dict is bounded.
_MAX_EVALUATION_TASKS = 200


def _prune_evaluation_tasks() -> None:
    now = asyncio.get_event_loop().time()
    stale = [
        task_id
        for task_id, info in _EVALUATION_TASKS.items()
        if now - info.get("created_at", now) > _EVALUATION_TTL_SECONDS
    ]
    for task_id in stale:
        _EVALUATION_TASKS.pop(task_id, None)


@lru_cache(maxsize=1)
def get_matcher() -> ResumeMatcher:
    """Get or create the matcher instance.

    Singleton (via lru_cache) so the embedding model is loaded once per
    process rather than per request.
    """
    return ResumeMatcher()


# ---------------------------------------------------------------------------
# POST /match/  — upload a resume, score it, get a tailored PDF back
# ---------------------------------------------------------------------------

@router.post("/", response_model=MatchAndTailorResponseSchema)
async def match_resume_to_job(
    user_id: CurrentUserId,
    resume_file: UploadFile = File(..., description="The resume file in PDF or DOCX format"),
    job_description: str = Form(..., description="Full job description text"),
    job_title: str = Form(..., description="Target job title"),
    matcher: ResumeMatcher = Depends(get_matcher),
) -> MatchAndTailorResponseSchema:
    """Match a resume file against a job description and return a tailored resume.

    Parses the uploaded resume (PDF or DOCX), scores it against the supplied
    job description, and returns a tailored resume rendered to PDF and
    base64-encoded.
    """
    settings = get_settings()
    try:
        file_bytes = await read_capped(
            resume_file, settings.max_resume_upload_bytes, label="Resume"
        )
        filename = safe_filename(resume_file.filename, fallback="resume.txt")
        resume_text = extract_text_from_bytes(file_bytes, filename)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("File extraction failed", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="That file could not be read. Upload a PDF, DOCX or plain-text resume.",
        ) from exc

    combined_jd = f"Job Title: {job_title}\n\nJob Description:\n{job_description}"

    try:
        result = await run_in_threadpool(
            matcher.match,
            resume_text=resume_text,
            job_description=combined_jd,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Resume match failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not score this resume.") from exc

    # Tailoring and PDF rendering are best-effort: a score with no PDF is far
    # more useful than a 500 that throws the score away too.
    file_base64: str | None = None
    pdf_filename: str | None = None
    warnings = list(result.warnings or [])
    try:
        tailor = ResumeTailor()
        tailored_result = await run_in_threadpool(tailor.tailor, resume_text, combined_jd)
        candidate_name = str(tailored_result.get("candidate_name") or "Applicant")
        latex_content = str(tailored_result.get("latex_content") or "")
        if latex_content:
            pdf_bytes = await compile_latex_to_pdf_async(latex_content)
            file_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            pdf_filename = safe_filename(
                f"{candidate_name.strip().replace(' ', '_')}_resume.pdf",
                fallback="resume.pdf",
            )
    except Exception as exc:
        logger.warning("Tailoring/PDF step failed; returning score only: %s", exc)
        warnings.append("The tailored PDF could not be generated, so only the score is shown.")

    return MatchAndTailorResponseSchema(
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
        warnings=warnings,
        file_base64=file_base64,
        filename=pdf_filename,
    )


# ---------------------------------------------------------------------------
# POST /match/batch
# ---------------------------------------------------------------------------

@router.post("/batch", response_model=BatchMatchResponseSchema)
async def batch_match_resumes(
    user_id: CurrentUserId,
    request: BatchMatchRequestSchema,
    matcher: ResumeMatcher = Depends(get_matcher),
) -> BatchMatchResponseSchema:
    """Match multiple resumes against a single job description.

    Results come back sorted best-first. Each result carries the
    ``resume_index`` it came from, so a sorted list can still be mapped back
    onto the request's ``resume_texts`` order.
    """
    try:
        results = await run_in_threadpool(
            matcher.match_batch,
            resume_texts=request.resume_texts,
            job_description=request.job_description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Batch match request failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Batch matching failed.") from exc

    # ``match_batch`` returns results in input order; sorting happens here so
    # the original index survives. Previously every result reported index 0,
    # which made the response impossible to map back to a resume.
    indexed = list(enumerate(results))
    indexed.sort(key=lambda pair: pair[1].overall_score, reverse=True)

    batch_results = [
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
            resume_index=original_index,
        )
        for original_index, result in indexed
    ]

    return BatchMatchResponseSchema(
        results=batch_results,
        total_processed=len(results),
        top_match_percentage=batch_results[0].match_percentage if batch_results else None,
    )


# ---------------------------------------------------------------------------
# GET /match/weights
# ---------------------------------------------------------------------------

@router.get("/weights", response_model=MatchWeightsSchema)
async def get_weights(matcher: ResumeMatcher = Depends(get_matcher)) -> MatchWeightsSchema:
    """Return the weights the matching pipeline currently uses.

    There is deliberately no POST counterpart. Weights are process-local, so a
    write endpoint could only ever mutate one worker's copy — the old one
    logged a warning, changed nothing, and still answered ``"status":
    "updated"``, which is worse than not having it. Change the weights in
    ``ml/config.py`` and redeploy.
    """
    w = matcher.config.weights
    return MatchWeightsSchema(
        skills=w.skills,
        experience=w.experience,
        education=w.education,
        semantic=w.semantic,
    )


# ---------------------------------------------------------------------------
# POST /match/evaluate  (+ SSE stream)
# ---------------------------------------------------------------------------

@router.post("/evaluate", response_model=EvaluateCandidateResponse)
async def evaluate_candidate(
    user_id: CurrentUserId,
    request: EvaluateCandidateRequest,
) -> EvaluateCandidateResponse:
    """
    Dispatch an async evaluation task for a candidate's resume using
    LLM-as-a-judge. Returns a task id trackable via the SSE stream endpoint.
    """
    _prune_evaluation_tasks()
    if len(_EVALUATION_TASKS) >= _MAX_EVALUATION_TASKS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Too many evaluations in flight. Try again shortly.",
        )

    task_id = str(uuid.uuid4())
    created_at = asyncio.get_event_loop().time()
    _EVALUATION_TASKS[task_id] = {
        "status": "processing",
        "result": None,
        "error": None,
        "created_at": created_at,
        "owner": user_id,
    }

    async def _run_eval() -> None:
        try:
            res = await analyze_resume(request.resume_text, request.job_description)
            outcome = {"status": "success", "result": res, "error": None}
        except Exception as err:
            logger.error("Evaluation failed: %s", err, exc_info=True)
            outcome = {"status": "error", "result": None, "error": "Evaluation failed."}
        existing = _EVALUATION_TASKS.get(task_id)
        if existing is not None:
            existing.update(outcome)

    task = asyncio.create_task(_run_eval())
    _EVALUATION_TASK_REFS.add(task)
    task.add_done_callback(_EVALUATION_TASK_REFS.discard)

    return EvaluateCandidateResponse(task_id=task_id)


@router.get("/evaluate/stream/{task_id}")
async def evaluate_stream(task_id: str, user_id: CurrentUserId) -> EventSourceResponse:
    """Stream evaluation status updates via Server-Sent Events."""

    async def event_generator():
        # 0.5s per tick — 120 ticks is a 60s ceiling, matching the documented
        # wait (the old loop ran 60 ticks and gave up after 30s).
        for _ in range(120):
            task_info = _EVALUATION_TASKS.get(task_id)
            if not task_info or task_info.get("owner") != user_id:
                yield {"event": "error", "data": "Task not found"}
                return

            state = task_info.get("status")
            if state == "success":
                yield {"event": "success", "data": json.dumps(task_info["result"])}
                _EVALUATION_TASKS.pop(task_id, None)
                return
            if state == "error":
                yield {"event": "error", "data": str(task_info.get("error") or "Unknown error")}
                _EVALUATION_TASKS.pop(task_id, None)
                return

            yield {"event": "processing", "data": "processing"}
            await asyncio.sleep(0.5)

        yield {"event": "error", "data": "Evaluation timed out"}
        _EVALUATION_TASKS.pop(task_id, None)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# POST /match/tailor-resume
# ---------------------------------------------------------------------------

@router.post("/tailor-resume")
async def tailor_resume(
    user_id: CurrentUserId,
    request: TailorResumeRequest,
) -> StreamingResponse:
    """Tailor a resume for a specific job description and stream it back as a PDF."""
    try:
        tailor = ResumeTailor()
        # Both the completion and the pdflatex run are blocking; neither may
        # execute on the event loop or every other request stalls behind them.
        tailored_result = await run_in_threadpool(
            tailor.tailor, request.resume_text, request.job_description
        )
        candidate_name = str(tailored_result.get("candidate_name") or "Applicant")
        latex_content = str(tailored_result.get("latex_content") or "")
        pdf_bytes = await compile_latex_to_pdf_async(latex_content)
    except LatexCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to tailor resume", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not tailor this resume.") from exc

    filename = f"{candidate_name.strip().replace(' ', '_') or 'Applicant'}_resume.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename)},
    )
