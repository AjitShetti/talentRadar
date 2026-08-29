"""
api/routers/resumes.py
~~~~~~~~~~~~~~~~~~~~~~~~
Resume Studio endpoints (auth required).

POST /resumes/extract-text     - extract text from an uploaded PDF/DOCX resume and save it
GET  /resumes/me               - the user's saved resume, if any (auto-load on visit)
GET  /resumes/target-jobs      - jobs the user is already tracking, for the job picker
POST /resumes/analyze          - ATS score + skill-gap analysis
POST /resumes/tailor           - tailor a resume for a target job (LaTeX + PDF)
POST /resumes/cover-letter     - generate a tailored cover letter
GET  /resumes/gaps             - pure skill-gap computation
GET  /resumes/document         - the structured document behind the LaTeX editor
PUT  /resumes/document         - save edits to that document (autosave)
POST /resumes/document/compile - render the document to LaTeX + PDF
"""

from __future__ import annotations

import logging
import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from api.auth import get_current_user
from api.utils.file_parser import extract_text_from_bytes
from config.settings import get_settings
from services.llm import generate_cover_letter
from services.resumes import (
    analyze_resume,
    compile_resume_document,
    get_active_resume,
    get_resume_document,
    list_target_jobs,
    save_resume,
    save_resume_document,
    skill_gap,
    tailor_resume,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resumes", tags=["Resume Studio"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


class AnalyzeRequest(BaseModel):
    resume_text: str
    job_description: str
    job_title: str | None = None


class TailorRequest(BaseModel):
    resume_text: str
    job_description: str
    job_title: str | None = None


class CoverLetterRequest(BaseModel):
    resume_text: str
    job_description: str
    job_title: str
    company: str
    tone: str = "professional"


class ResumeDocumentRequest(BaseModel):
    document: dict


class CompileDocumentRequest(BaseModel):
    document: dict


class SkillGapRequest(BaseModel):
    resume_skills: list[str] | None = None
    resume_text: str | None = None
    job_skills: list[str] | None = None
    job_description: str | None = None


class SavedResumeResponse(BaseModel):
    id: str
    extracted_text: str
    filename: str | None = None
    updated_at: str | None = None


class TargetJobResponse(BaseModel):
    id: str
    title: str
    company_name: str | None = None
    description: str | None = None
    location_raw: str | None = None


# Resume formats we can actually extract text from.
ALLOWED_RESUME_EXTENSIONS = frozenset({"pdf", "docx", "doc", "txt", "md", "rtf"})

_UPLOAD_CHUNK = 64 * 1024


def _safe_filename(raw: str | None) -> str:
    """Reduce a client-supplied filename to a harmless display label.

    The client controls this string entirely. Keep only the basename (so
    ``../../etc/passwd`` cannot survive as a path), drop control characters
    and anything outside a conservative allowlist, and cap the length.
    """
    candidate = os.path.basename((raw or "").replace("\\", "/").strip()) or "resume.txt"
    candidate = re.sub(r"[^A-Za-z0-9._ -]", "_", candidate)
    candidate = candidate.lstrip(".") or "resume.txt"
    return candidate[:120]


async def _read_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks, aborting as soon as it exceeds ``max_bytes``.

    Reading with a bare ``await upload.read()`` pulled the whole body into
    memory before anything could reject it, so a single request could push an
    arbitrarily large blob straight through to the database.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Resume exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/extract-text", response_model=SavedResumeResponse)
async def extract_resume_text(
    user_id: CurrentUserId,
    resume_file: UploadFile = File(...),
):
    """Extract text from an uploaded resume file (PDF/DOCX) and save it as the
    user's working resume, so Resume Studio can auto-load it next visit."""
    filename = _safe_filename(resume_file.filename)
    file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else None
    if file_type not in ALLOWED_RESUME_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported resume format. Upload one of: "
                + ", ".join(sorted(ALLOWED_RESUME_EXTENSIONS))
            ),
        )

    file_bytes = await _read_capped(resume_file, get_settings().max_resume_upload_bytes)
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        resume_text = extract_text_from_bytes(file_bytes, filename)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Resume extraction failed for %s: %s", filename, exc)
        raise HTTPException(
            status_code=400,
            detail="Could not read that file. Please upload a valid PDF, DOCX, or TXT resume.",
        ) from exc

    # Postgres text columns reject NUL bytes, which arrive whenever a binary
    # file slips past extraction. Strip them here rather than letting the
    # INSERT blow up with a 500.
    resume_text = (resume_text or "").replace(chr(0), "")
    if not resume_text.strip():
        raise HTTPException(
            status_code=400,
            detail="No readable text found in that file. Is it a scanned image?",
        )

    saved = await save_resume(
        user_id=user_id, extracted_text=resume_text, filename=filename, file_type=file_type
    )
    return SavedResumeResponse(**saved)


@router.get("/me", response_model=SavedResumeResponse | None)
async def get_my_resume(user_id: CurrentUserId):
    """Return the current user's saved resume, if any."""
    resume = await get_active_resume(user_id=user_id)
    return SavedResumeResponse(**resume) if resume else None


@router.get("/target-jobs", response_model=list[TargetJobResponse])
async def get_target_jobs(user_id: CurrentUserId):
    """Jobs the user is already tracking, for the Resume Studio job picker."""
    jobs = await list_target_jobs(user_id=user_id)
    return [TargetJobResponse(**job) for job in jobs]


@router.post("/analyze")
async def analyze_resume_endpoint(user_id: CurrentUserId, body: AnalyzeRequest):
    """Run Resume Studio ATS analysis for a resume against a job description."""
    try:
        return await analyze_resume(
            resume_text=body.resume_text,
            job_description=body.job_description,
            job_title=body.job_title,
        )
    except Exception as exc:
        logger.error("Resume analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Resume analysis failed: {exc}") from exc


@router.post("/tailor")
async def tailor_resume_endpoint(user_id: CurrentUserId, body: TailorRequest):
    """Tailor a resume for a specific job (returns LaTeX + optional PDF)."""
    try:
        return await tailor_resume(
            resume_text=body.resume_text,
            job_description=body.job_description,
            job_title=body.job_title,
        )
    except Exception as exc:
        logger.error("Resume tailoring failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Resume tailoring failed: {exc}") from exc


@router.post("/cover-letter")
async def cover_letter_endpoint(user_id: CurrentUserId, body: CoverLetterRequest):
    """Generate a tailored cover letter for a job."""
    try:
        content = await generate_cover_letter(
            resume_text=body.resume_text,
            jd_text=body.job_description,
            job_title=body.job_title,
            company=body.company,
            tone=body.tone,
        )
        return {"content": content, "tone": body.tone}
    except Exception as exc:
        logger.error("Cover letter generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Cover letter generation failed: {exc}") from exc


@router.get("/document")
async def get_resume_document_endpoint(user_id: CurrentUserId):
    """The structured document behind the Resume Studio LaTeX editor."""
    try:
        return await get_resume_document(user_id=user_id)
    except Exception as exc:
        logger.error("Fetching resume document failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not load your resume document: {exc}") from exc


@router.put("/document")
async def save_resume_document_endpoint(user_id: CurrentUserId, body: ResumeDocumentRequest):
    """Save edits to the structured resume document (autosave)."""
    try:
        return await save_resume_document(user_id=user_id, document=body.document)
    except Exception as exc:
        logger.error("Saving resume document failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not save your resume document: {exc}") from exc


@router.post("/document/compile")
async def compile_resume_document_endpoint(user_id: CurrentUserId, body: CompileDocumentRequest):
    """Render the structured resume document to LaTeX and compile it to PDF."""
    try:
        return await compile_resume_document(body.document)
    except Exception as exc:
        logger.error("Compiling resume document failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not compile your resume: {exc}") from exc


@router.post("/gaps")
async def skill_gap_endpoint(user_id: CurrentUserId, body: SkillGapRequest):
    """Pure deterministic skill-gap computation."""
    try:
        return await skill_gap(
            resume_text=body.resume_text,
            resume_skills=body.resume_skills,
            jd_text=body.job_description,
            job_skills=body.job_skills,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
