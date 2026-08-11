"""
services/applications.py
~~~~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for the Application Tracker.

    update_application()  — advance status with event logging + timestamps
    tracker_analytics()   — funnel / conversion analytics for a user
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import AsyncSessionLocal
from storage.models import ApplicationEvent, ApplicationStatus, JobApplication
from storage.repository import UnitOfWork
from services.base import parse_uuid

logger = logging.getLogger(__name__)

# Status → timestamp column to keep in sync
_TIMESTAMP_COLUMNS: dict[str, str] = {
    ApplicationStatus.APPLIED.value: "applied_at_explicit",
    ApplicationStatus.ONLINE_ASSESSMENT.value: "oa_completed_at",
    ApplicationStatus.INTERVIEW.value: "interview_scheduled_at",
}


async def update_application(
    *,
    application_id: str,
    status: str | None = None,
    notes: str | None = None,
    resume_version_id: str | None = None,
    cover_letter_id: str | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """
    Update an application's status / notes and record a status-change
    event in ``application_events`` for the timeline and analytics.

    Returns the serialised application or ``None`` if not found.
    """
    own = session is None
    session = session or AsyncSessionLocal()

    try:
        app_uuid = parse_uuid(application_id)
        if not app_uuid:
            return None

        app = await _get_application(session, app_uuid)
        if app is None:
            return None

        old_status = app.status.value if app.status else None

        if status is not None:
            try:
                new_status = ApplicationStatus(status)
            except ValueError:
                raise ValueError(f"Invalid status: {status}")
            if new_status != app.status:
                event = ApplicationEvent(
                    application_id=app.id,
                    from_status=old_status,
                    to_status=new_status.value,
                    note=notes,
                )
                session.add(event)
                app.status = new_status
                # Track funnel timestamps
                col = _TIMESTAMP_COLUMNS.get(new_status.value)
                if col:
                    setattr(app, col, datetime.now(tz=timezone.utc))
                if new_status in (ApplicationStatus.OFFER, ApplicationStatus.REJECTED):
                    app.outcome_at = datetime.now(tz=timezone.utc)

        if notes is not None:
            app.notes = notes
        if resume_version_id is not None and parse_uuid(resume_version_id):
            app.resume_version_id = parse_uuid(resume_version_id)
        if cover_letter_id is not None and parse_uuid(cover_letter_id):
            app.cover_letter_id = parse_uuid(cover_letter_id)

        await session.flush()
        return await _serialise_application(session, app)
    finally:
        if own:
            await session.close()


async def _get_application(session: AsyncSession, app_uuid: uuid.UUID) -> JobApplication | None:
    stmt = select(JobApplication).where(JobApplication.id == app_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _serialise_application(session: AsyncSession, app: JobApplication) -> dict[str, Any]:
    from storage.models import Job

    job = None
    if app.job_id:
        stmt = select(Job).where(Job.id == app.job_id)
        job = (await session.execute(stmt)).scalar_one_or_none()

    events_result = await session.execute(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == app.id)
        .order_by(ApplicationEvent.created_at)
    )
    events = events_result.scalars().all()

    return {
        "id": str(app.id),
        "job_id": str(app.job_id) if app.job_id else None,
        "job_title": job.title if job else None,
        "company": job.company.name if job and job.company else None,
        "status": app.status.value,
        "notes": app.notes,
        "applied_at": app.applied_at.isoformat() if app.applied_at else None,
        "resume_version_id": str(app.resume_version_id) if app.resume_version_id else None,
        "cover_letter_id": str(app.cover_letter_id) if app.cover_letter_id else None,
        "applied_at_explicit": app.applied_at_explicit.isoformat() if app.applied_at_explicit else None,
        "oa_completed_at": app.oa_completed_at.isoformat() if app.oa_completed_at else None,
        "interview_scheduled_at": app.interview_scheduled_at.isoformat() if app.interview_scheduled_at else None,
        "outcome_at": app.outcome_at.isoformat() if app.outcome_at else None,
        "events": [
            {
                "from_status": e.from_status,
                "to_status": e.to_status,
                "note": e.note,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


async def tracker_analytics(
    *,
    user_id: str,
) -> dict[str, Any]:
    """
    Funnel analytics for a user's applications:

        - per-stage counts (saved → applied → oa → screening → interview → offer)
        - conversion rates between stages
        - total applied, interview rate, offer rate
    """
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return {"error": "invalid user_id"}

        result = await session.execute(
            select(JobApplication.status, JobApplication.id)
            .where(JobApplication.user_id == user_uuid)
        )
        rows = result.all()

        counts: dict[str, int] = {}
        for status, _ in rows:
            key = status.value if isinstance(status, ApplicationStatus) else str(status)
            counts[key] = counts.get(key, 0) + 1

        total = len(rows)
        applied = counts.get(ApplicationStatus.APPLIED.value, 0)
        oa = counts.get(ApplicationStatus.ONLINE_ASSESSMENT.value, 0)
        interview = counts.get(ApplicationStatus.INTERVIEW.value, 0)
        offers = counts.get(ApplicationStatus.OFFER.value, 0)
        rejected = counts.get(ApplicationStatus.REJECTED.value, 0)

        def _rate(num: int) -> float:
            return round(num / total * 100, 1) if total else 0.0

        funnel = [
            {"stage": "saved", "count": counts.get("saved", 0), "conversion_rate": 100.0},
            {"stage": "applied", "count": applied, "conversion_rate": _rate(applied)},
            {"stage": "online_assessment", "count": oa, "conversion_rate": _rate(oa)},
            {"stage": "interview", "count": interview, "conversion_rate": _rate(interview)},
            {"stage": "offer", "count": offers, "conversion_rate": _rate(offers)},
        ]

        return {
            "total_applications": total,
            "funnel": funnel,
            "metrics": {
                "applied": applied,
                "online_assessment": oa,
                "interview": interview,
                "offers": offers,
                "rejected": rejected,
                "interview_rate": _rate(interview),
                "offer_rate": _rate(offers),
                "rejection_rate": _rate(rejected),
            },
        }
    finally:
        await session.close()
