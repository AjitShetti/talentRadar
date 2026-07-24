"""
api/routers/applications.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Job application tracking endpoints (auth required).

GET    /applications           - list user's applications
POST   /applications           - create/save an application
PATCH  /applications/{id}      - update status or notes
DELETE /applications/{id}      - remove an application
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.application_schemas import (
    ApplicationCreateSchema,
    ApplicationUpdateSchema,
    ApplicationResponseSchema,
    ApplicationListResponseSchema,
    ApplicationJobSchema,
)
from storage.database import get_db_dep
from storage.models import JobApplication, ApplicationStatus, Job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/applications", tags=["Applications"])
from api.auth import get_current_user

async def get_current_user_id(
    user: Annotated[dict, Depends(get_current_user)],
) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]
DBSession = Annotated[AsyncSession, Depends(get_db_dep)]


async def _get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


def _job_to_schema(job: Job | None) -> ApplicationJobSchema | None:
    if job is None:
        return None
    return ApplicationJobSchema(
        id=str(job.id),
        title=job.title,
        location_raw=getattr(job, 'location_raw', None),
        is_remote=getattr(job, 'is_remote', False) or False,
        source_url=getattr(job, 'source_url', None),
        salary_raw=getattr(job, 'salary_raw', None),
        skills=list(job.skills or []),
    )


def _app_to_response(app: JobApplication, job: Job | None) -> ApplicationResponseSchema:
    return ApplicationResponseSchema(
        id=str(app.id),
        job_id=str(app.job_id) if app.job_id else None,
        status=app.status.value,
        notes=app.notes,
        applied_at=app.applied_at,
        created_at=app.created_at,
        updated_at=app.updated_at,
        job=_job_to_schema(job),
    )


@router.get("/", response_model=ApplicationListResponseSchema)
async def list_applications(
    user_id: CurrentUserId,
    db: DBSession,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApplicationListResponseSchema:
    q = (
        select(JobApplication)
        .where(JobApplication.user_id == uuid.UUID(user_id))
        .order_by(JobApplication.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status_filter:
        try:
            q = q.where(JobApplication.status == ApplicationStatus(status_filter))
        except ValueError:
            pass

    result = await db.execute(q)
    apps = result.scalars().all()

    responses = []
    for app in apps:
        job = await _get_job(db, app.job_id) if app.job_id else None
        responses.append(_app_to_response(app, job))

    count_q = select(func.count()).select_from(JobApplication).where(JobApplication.user_id == uuid.UUID(user_id))
    total = (await db.execute(count_q)).scalar() or 0

    return ApplicationListResponseSchema(applications=responses, total=total)


@router.post("/", response_model=ApplicationResponseSchema, status_code=status.HTTP_201_CREATED)
async def create_application(
    user_id: CurrentUserId,
    db: DBSession,
    body: ApplicationCreateSchema,
) -> ApplicationResponseSchema:
    try:
        job_uuid = uuid.UUID(body.job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id")

    existing = await db.execute(
        select(JobApplication).where(
            JobApplication.user_id == uuid.UUID(user_id),
            JobApplication.job_id == job_uuid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Application already exists")

    try:
        app_status = ApplicationStatus(body.status)
    except ValueError:
        app_status = ApplicationStatus.SAVED

    app = JobApplication(
        user_id=uuid.UUID(user_id),
        job_id=job_uuid,
        status=app_status,
        notes=body.notes,
    )
    db.add(app)
    await db.flush()

    job = await _get_job(db, job_uuid)
    logger.info("Application created: id=%s user=%s job=%s", app.id, user_id, job_uuid)
    return _app_to_response(app, job)


@router.patch("/{application_id}", response_model=ApplicationResponseSchema)
async def update_application(
    application_id: str,
    user_id: CurrentUserId,
    db: DBSession,
    body: ApplicationUpdateSchema,
) -> ApplicationResponseSchema:
    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid application_id")

    result = await db.execute(
        select(JobApplication).where(
            JobApplication.id == app_uuid,
            JobApplication.user_id == uuid.UUID(user_id),
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    if body.status is not None:
        try:
            app.status = ApplicationStatus(body.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    if body.notes is not None:
        app.notes = body.notes
    if body.applied_at is not None:
        app.applied_at = body.applied_at

    db.add(app)
    await db.flush()

    job = await _get_job(db, app.job_id) if app.job_id else None
    return _app_to_response(app, job)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    application_id: str,
    user_id: CurrentUserId,
    db: DBSession,
) -> None:
    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid application_id")

    result = await db.execute(
        select(JobApplication).where(
            JobApplication.id == app_uuid,
            JobApplication.user_id == uuid.UUID(user_id),
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    await db.delete(app)
    await db.flush()
