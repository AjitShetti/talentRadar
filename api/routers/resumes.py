"""
api/routers/resumes.py
~~~~~~~~~~~~~~~~~~~~~~~~
Resume Studio endpoints (auth required).

POST /resumes/analyze     - ATS score + skill-gap analysis (text or uploaded file)
POST /resumes/tailor      - tailor a resume for a target job (LaTeX + PDF)
POST /resumes/cover-letter - generate a tailored cover letter
GET  /resumes/gaps        - pure skill-gap computation
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from api.auth import get_current_user
from api.utils.file_parser import extract_text_from_bytes
from services.llm import generate_cover_letter
from services.resumes import analyze_resume, skill_gap, tailor_resume

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


class SkillGapRequest(BaseModel):
    resume_skills: list[str] | None = None
    resume_text: str | None = None
    job_skills: list[str] | None = None
    job_description: str | None = None


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


@router.post("/analyze/file")
async def analyze_resume_file(
    user_id: CurrentUserId,
    resume_file: UploadFile = File(...),
    job_description: str = ...,
    job_title: str | None = None,
):
    """Analyze an uploaded resume file (PDF/DOCX) against a job description."""
    try:
        file_bytes = await resume_file.read()
        resume_text = extract_text_from_bytes(file_bytes, resume_file.filename or "resume.txt")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {exc}") from exc
    return await analyze_resume(
        resume_text=resume_text,
        job_description=job_description,
        job_title=job_title,
    )


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
