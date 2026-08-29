"""
services/resumes.py
~~~~~~~~~~~~~~~~~~~
Deterministic tools for Resume Studio.

    analyze_resume()     — ATS score + skill-gap analysis vs a job description
    tailor_resume()      — produce a job-specific tailored resume (LaTeX/PDF)
    skill_gap()          — pure skill set difference computation
    generate_cover_letter() — tailored cover letter for a job
    save_resume()        — persist the user's working resume so it can be
                            auto-loaded next visit instead of re-uploaded
    get_active_resume()  — fetch the user's saved resume, if any
    list_target_jobs()   — jobs the user is already tracking, for the
                            Resume Studio job picker
    get_resume_document()     — the structured document behind the LaTeX editor
    save_resume_document()    — persist edits to that document
    compile_resume_document() — render it to LaTeX/PDF
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from services.base import as_list, parse_uuid

if TYPE_CHECKING:
    from storage.models import Resume

logger = logging.getLogger(__name__)


async def analyze_resume(
    resume_text: str,
    job_description: str,
    *,
    job_title: str | None = None,
) -> dict[str, Any]:
    """
    Run Resume Studio's analysis for a resume against a target JD:

        - ATS score (0-100) via LLM judge
        - missing / matched skills
        - concrete suggestions

    Returns a serialisable dict.
    """
    from services.llm import generate_ats_analysis

    combined_jd = f"Job Title: {job_title}\n\n{job_description}" if job_title else job_description
    analysis = await generate_ats_analysis(resume_text, combined_jd)

    # Fallback deterministic skill-gap when LLM returns empty lists
    if not analysis.get("missing_skills"):
        analysis["missing_skills"] = (
            await skill_gap(resume_text=resume_text, jd_text=job_description)
        )["missing_skills"]

    return {
        "ats_score": round(float(analysis.get("ats_score", 0.0)), 1),
        "missing_skills": as_list(analysis.get("missing_skills")),
        "matched_skills": as_list(analysis.get("matched_skills")),
        "suggestions": as_list(analysis.get("suggestions")),
        "reasoning": analysis.get("reasoning", ""),
    }


async def tailor_resume(
    resume_text: str,
    job_description: str,
    *,
    job_title: str | None = None,
) -> dict[str, Any]:
    """
    Produce a tailored resume for a specific job.

    Returns::

        {
          "candidate_name": str,
          "latex_content": str,   # compilable LaTeX (Jake's Resume template)
          "pdf_base64": str | None,
          "filename": str | None,
        }
    """
    from agents.resume_tailor import ResumeTailor

    combined_jd = f"Job Title: {job_title}\n\n{job_description}" if job_title else job_description
    tailor = ResumeTailor()
    tailored = tailor.tailor(resume_text, combined_jd)

    candidate_name = str(tailored.get("candidate_name") or "Applicant").strip().replace(" ", "_")
    latex = str(tailored.get("latex_content") or "")
    pdf_base64: str | None = None
    filename: str | None = None

    if latex:
        try:
            import base64

            from api.utils.latex_compiler import compile_latex_to_pdf
            pdf_bytes = compile_latex_to_pdf(latex)
            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            filename = f"{candidate_name}_resume.pdf"
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF compilation failed; returning LaTeX only: %s", exc)

    return {
        "candidate_name": candidate_name,
        "latex_content": latex,
        "pdf_base64": pdf_base64,
        "filename": filename,
    }


async def skill_gap(
    *,
    resume_text: str | None = None,
    resume_skills: list[str] | None = None,
    jd_text: str | None = None,
    job_skills: list[str] | None = None,
) -> dict[str, Any]:
    """
    Pure deterministic skill-gap computation.

    At least one of (resume_text | resume_skills) and one of
    (jd_text | job_skills) must be supplied. When text is provided the
    ML feature extractor is used to derive skill tokens.
    """
    resume_set: set[str]
    job_set: set[str]

    if resume_skills is not None:
        resume_set = {s.lower().strip() for s in resume_skills if s}
    elif resume_text:
        resume_set = _extract_skills(resume_text)
    else:
        raise ValueError("Provide resume_skills or resume_text")

    if job_skills is not None:
        job_set = {s.lower().strip() for s in job_skills if s}
    elif jd_text:
        job_set = _extract_skills(jd_text)
    else:
        raise ValueError("Provide job_skills or jd_text")

    matched = sorted(resume_set & job_set)
    missing = sorted(job_set - resume_set)
    extra = sorted(resume_set - job_set)
    coverage = round(len(matched) / len(job_set) * 100, 1) if job_set else 0.0

    return {
        "matched_skills": matched,
        "missing_skills": missing,
        "extra_skills": extra,
        "coverage_percentage": coverage,
    }


def _extract_skills(text: str) -> set[str]:
    """Use the ML feature extractor's skill detection on raw text."""
    from ml.feature_extractor import extract_features
    from ml.preprocessing import preprocess_text

    cleaned = preprocess_text(text)
    features = extract_features(cleaned)
    # ``features.skills`` is a SkillFeatures dataclass, not a list -- iterating it
    # raised TypeError and took every resume_text-based gap computation with it.
    return {s.lower().strip() for s in features.skills.matched_skills if s and s.strip()}


async def generate_cover_letter(
    *,
    resume_text: str,
    job_description: str,
    job_title: str,
    company: str,
    tone: str = "professional",
) -> str:
    """Generate a tailored cover letter via the shared LLM helper."""
    from services.llm import generate_cover_letter as _llm

    return await _llm(
        resume_text=resume_text,
        jd_text=job_description,
        job_title=job_title,
        company=company,
        tone=tone,
    )


async def save_resume(
    *,
    user_id: str,
    extracted_text: str,
    filename: str | None = None,
    file_type: str | None = None,
) -> dict[str, Any]:
    """
    Persist (or overwrite) the user's single working resume so Resume
    Studio can auto-load it on the next visit instead of asking for a
    re-upload every time.
    """
    from sqlalchemy import select

    from storage.database import AsyncSessionLocal
    from storage.models import Profile, Resume

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        raise ValueError(f"Invalid user_id: {user_id!r}")

    session = AsyncSessionLocal()
    try:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_uuid))
        ).scalar_one_or_none()
        if profile is None:
            profile = Profile(user_id=user_uuid)
            session.add(profile)
            await session.flush()

        resume = None
        if profile.active_resume_id:
            resume = (
                await session.execute(select(Resume).where(Resume.id == profile.active_resume_id))
            ).scalar_one_or_none()
        if resume is None:
            resume = Resume(profile_id=profile.id, version_name="Original")
            session.add(resume)

        resume.extracted_text = extracted_text
        if filename:
            resume.file_path = filename
        if file_type:
            resume.file_type = file_type
        # A fresh upload supersedes whatever structured document was derived
        # from the old text — clear it so the next get_resume_document() call
        # regenerates sections from this new text instead of silently keeping
        # the stale ones.
        resume.structured_content = None

        await session.flush()
        profile.active_resume_id = resume.id
        await session.commit()

        # ``updated_at`` is generated by the database (onupdate=now()), so the
        # in-memory attribute is stale/unloaded after COMMIT. Reading it in
        # _resume_summary() would trigger an implicit lazy refresh from a
        # synchronous context and blow up with "greenlet_spawn has not been
        # called" -- after the row was already written, so the caller saw a
        # 500 for a write that actually succeeded. Refresh explicitly instead.
        await session.refresh(resume)

        return _resume_summary(resume)
    finally:
        await session.close()


async def get_active_resume(*, user_id: str) -> dict[str, Any] | None:
    """Return the user's saved resume (most recently uploaded/edited), or None."""
    from sqlalchemy import select

    from storage.database import AsyncSessionLocal
    from storage.models import Profile, Resume

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return None

    session = AsyncSessionLocal()
    try:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_uuid))
        ).scalar_one_or_none()
        if profile is None:
            return None

        resume = None
        if profile.active_resume_id:
            resume = (
                await session.execute(select(Resume).where(Resume.id == profile.active_resume_id))
            ).scalar_one_or_none()
        if resume is None:
            resume = (
                await session.execute(
                    select(Resume)
                    .where(Resume.profile_id == profile.id)
                    .order_by(Resume.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if resume is None or not resume.extracted_text:
            return None

        return _resume_summary(resume)
    finally:
        await session.close()


def _resume_summary(resume: Resume) -> dict[str, Any]:
    return {
        "id": str(resume.id),
        "extracted_text": resume.extracted_text or "",
        "filename": resume.file_path,
        "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
    }


async def list_target_jobs(*, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """
    Jobs the user is already tracking (saved/applied), newest first — feeds
    the Resume Studio job picker so the user selects a target instead of
    pasting a job description by hand.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from storage.database import AsyncSessionLocal
    from storage.models import Job, JobApplication

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return []

    session = AsyncSessionLocal()
    try:
        apps = (
            await session.execute(
                select(JobApplication)
                .where(JobApplication.user_id == user_uuid, JobApplication.job_id.is_not(None))
                .order_by(JobApplication.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

        job_ids = list(dict.fromkeys(app.job_id for app in apps if app.job_id is not None))
        if not job_ids:
            return []

        jobs_by_id = {
            job.id: job
            for job in (
                await session.execute(
                    select(Job).options(selectinload(Job.company)).where(Job.id.in_(job_ids))
                )
            ).scalars().all()
        }

        results = []
        for job_id in job_ids:
            job = jobs_by_id.get(job_id)
            if job is None:
                continue
            results.append({
                "id": str(job.id),
                "title": job.title,
                "company_name": getattr(job.company, "name", None) if job.company else None,
                "description": job.description_clean,
                "location_raw": job.location_raw,
            })
        return results
    finally:
        await session.close()


_DEFAULT_DOCUMENT: dict[str, Any] = {
    "schema_version": 1,
    "personal": {
        "full_name": "", "headline": "", "email": "", "phone": "",
        "location": "", "links": [],
    },
    "sections": [
        {"id": "summary", "type": "summary", "title": "Professional Summary", "visible": True, "order": 0, "items": []},
        {"id": "education", "type": "education", "title": "Education", "visible": True, "order": 1, "items": []},
        {"id": "experience", "type": "experience", "title": "Experience", "visible": True, "order": 2, "items": []},
        {"id": "projects", "type": "projects", "title": "Projects", "visible": True, "order": 3, "items": []},
        {"id": "skills", "type": "skills", "title": "Technical Skills", "visible": True, "order": 4, "items": []},
    ],
}


async def get_resume_document(*, user_id: str) -> dict[str, Any]:
    """
    The structured document behind the Resume Studio LaTeX editor.

    Bootstraps from the user's plain-text resume via one-time LLM extraction
    the first time this is called for a resume that doesn't have a
    structured document yet. Returns a blank default document if the user
    has no resume at all, so the editor always has something to render.
    """
    import copy

    from sqlalchemy import select

    from storage.database import AsyncSessionLocal
    from storage.models import Profile, Resume

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        return copy.deepcopy(_DEFAULT_DOCUMENT)

    session = AsyncSessionLocal()
    try:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_uuid))
        ).scalar_one_or_none()
        resume = None
        if profile and profile.active_resume_id:
            resume = (
                await session.execute(select(Resume).where(Resume.id == profile.active_resume_id))
            ).scalar_one_or_none()

        if resume is None:
            return copy.deepcopy(_DEFAULT_DOCUMENT)

        if resume.structured_content:
            return dict(resume.structured_content)

        if not resume.extracted_text:
            return copy.deepcopy(_DEFAULT_DOCUMENT)

        from services.llm import extract_resume_structure

        try:
            document = await extract_resume_structure(resume.extracted_text)
        except Exception as exc:
            logger.warning("Resume structure extraction failed; returning blank document: %s", exc)
            return copy.deepcopy(_DEFAULT_DOCUMENT)

        resume.structured_content = document
        await session.commit()
        return document
    finally:
        await session.close()


async def save_resume_document(*, user_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """Persist edits to the user's structured resume document (autosave)."""
    from sqlalchemy import select

    from storage.database import AsyncSessionLocal
    from storage.models import Profile, Resume

    user_uuid = parse_uuid(user_id)
    if not user_uuid:
        raise ValueError(f"Invalid user_id: {user_id!r}")

    session = AsyncSessionLocal()
    try:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_uuid))
        ).scalar_one_or_none()
        if profile is None:
            profile = Profile(user_id=user_uuid)
            session.add(profile)
            await session.flush()

        resume = None
        if profile.active_resume_id:
            resume = (
                await session.execute(select(Resume).where(Resume.id == profile.active_resume_id))
            ).scalar_one_or_none()
        if resume is None:
            resume = Resume(profile_id=profile.id, version_name="Original")
            session.add(resume)
            await session.flush()
            profile.active_resume_id = resume.id

        resume.structured_content = document
        await session.commit()
        return document
    finally:
        await session.close()


async def compile_resume_document(document: dict[str, Any]) -> dict[str, Any]:
    """
    Render a structured resume document to LaTeX and compile it to PDF.

    Purely deterministic (no LLM call) — safe to run on every debounced edit
    in the editor. Degrades to LaTeX-only with a ``compile_error`` if
    ``pdflatex`` fails, same pattern as ``tailor_resume()``.
    """
    import base64

    from agents.latex_templates import render_resume_latex

    latex = render_resume_latex(document)
    pdf_base64: str | None = None
    filename: str | None = None
    compile_error: str | None = None

    if latex:
        try:
            from api.utils.latex_compiler import compile_latex_to_pdf
            pdf_bytes = compile_latex_to_pdf(latex)
            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            candidate_name = str((document.get("personal") or {}).get("full_name") or "resume")
            filename = (candidate_name.strip().replace(" ", "_") or "resume") + ".pdf"
        except Exception as exc:
            logger.warning("PDF compilation failed; returning LaTeX only: %s", exc)
            compile_error = "PDF rendering was unavailable — the LaTeX source is still up to date."

    return {
        "latex_content": latex,
        "pdf_base64": pdf_base64,
        "filename": filename,
        "compile_error": compile_error,
    }
