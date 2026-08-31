"""
services/applications.py
~~~~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for the Application Tracker.

    update_application()  — advance status with event logging + timestamps
    tracker_analytics()   — funnel / conversion analytics for a user
    day_agenda()          — interviews scheduled today / in the days ahead
    recent_activity()     — what actually moved on the tracker lately
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import AsyncSessionLocal
from storage.models import ApplicationEvent, ApplicationStatus, Company, Job, JobApplication
from storage.repository import UnitOfWork
from services.base import parse_uuid

logger = logging.getLogger(__name__)

#: How far ahead the overview's agenda looks for scheduled interviews.
AGENDA_HORIZON_DAYS = 7

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


def _day_offset(moment: datetime) -> int:
    """Calendar days from today to ``moment``, in the server's UTC day."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment.astimezone(UTC).date() - datetime.now(UTC).date()).days


def _when_label(days: int) -> str:
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    return f"In {days} days"


async def day_agenda(
    *,
    user_id: str,
    within_days: int = AGENDA_HORIZON_DAYS,
) -> list[dict[str, Any]]:
    """
    Interviews the user has on the calendar, soonest first.

    Only forward-looking rows in a live stage are returned: a scheduled date
    that has passed, or one on an application that was since rejected, is
    history rather than something to prepare for.
    """
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return []

        result = await session.execute(
            select(JobApplication, Job.title, Company.name)
            .outerjoin(Job, Job.id == JobApplication.job_id)
            .outerjoin(Company, Company.id == Job.company_id)
            .where(JobApplication.user_id == user_uuid)
            .where(JobApplication.interview_scheduled_at.is_not(None))
            .where(JobApplication.status.notin_([
                ApplicationStatus.REJECTED,
                ApplicationStatus.WITHDRAWN,
            ]))
            .order_by(JobApplication.interview_scheduled_at.asc())
        )

        agenda: list[dict[str, Any]] = []
        for app, job_title, company_name in result.all():
            scheduled = app.interview_scheduled_at
            if scheduled is None:
                continue
            days = _day_offset(scheduled)
            if days < 0 or days > within_days:
                continue
            agenda.append({
                "application_id": str(app.id),
                "job_id": str(app.job_id) if app.job_id else None,
                "role": job_title or "Untitled role",
                "company": company_name or "Company not listed",
                "status": app.status.value,
                "scheduled_at": scheduled.isoformat(),
                "days_away": days,
                "when_label": _when_label(days),
            })
        return agenda
    except Exception as exc:  # the agenda is additive — never blank the page
        logger.warning("Day agenda unavailable for user %s: %s", user_id, exc)
        return []
    finally:
        await session.close()


async def recent_activity(*, user_id: str, limit: int = 6) -> list[dict[str, Any]]:
    """
    The last things that actually moved on the tracker.

    Status changes only — this answers "what happened with the roles I already
    applied to", so a note with no stage change is not an event worth surfacing.
    """
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return []

        result = await session.execute(
            select(ApplicationEvent, Job.title, Company.name)
            .join(JobApplication, JobApplication.id == ApplicationEvent.application_id)
            .outerjoin(Job, Job.id == JobApplication.job_id)
            .outerjoin(Company, Company.id == Job.company_id)
            .where(JobApplication.user_id == user_uuid)
            .order_by(ApplicationEvent.created_at.desc())
            .limit(limit)
        )

        return [
            {
                "application_id": str(event.application_id),
                "role": job_title or "Untitled role",
                "company": company_name or "Company not listed",
                "from_status": event.from_status,
                "to_status": event.to_status,
                "note": event.note,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "days_ago": _day_offset(event.created_at) * -1 if event.created_at else None,
            }
            for event, job_title, company_name in result.all()
        ]
    except Exception as exc:
        logger.warning("Recent activity unavailable for user %s: %s", user_id, exc)
        return []
    finally:
        await session.close()
