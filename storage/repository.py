"""
storage/repository.py
~~~~~~~~~~~~~~~~~~~~~
Async CRUD repositories for every ORM model in TalentRadar.

Design
------
* Each repository accepts an ``AsyncSession`` injected from the outside —
  it never creates its own session, keeping transaction control at the
  call-site (FastAPI route / ingestion worker / test).
* Generic ``BaseRepository`` holds reusable get / list / create / update /
  delete logic so concrete repos only add domain-specific queries.
* All list methods accept ``limit`` / ``offset`` for cursor-friendly
  pagination and return a ``(items, total_count)`` tuple.
* Upsert helpers are provided for the hot paths:
    - ``CompanyRepository.upsert_by_domain``
    - ``JobRepository.upsert_by_external_id``
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Generic, Sequence, Type, TypeVar

from sqlalchemy import desc, func, select, update, and_, or_, cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import String

from storage.models import (
    Company,
    EmploymentType,
    IngestionRun,
    IngestionStatus,
    Job,
    JobStatus,
    SeniorityLevel,
)

# ---------------------------------------------------------------------------
# Generic type variable bound to any SQLAlchemy ORM model
# ---------------------------------------------------------------------------
ModelT = TypeVar("ModelT")


# ===========================================================================
# Base repository
# ===========================================================================

class BaseRepository(Generic[ModelT]):
    """
    Generic async CRUD operations.

    Sub-classes must set ``model`` to their ORM class, e.g.::

        class CompanyRepository(BaseRepository[Company]):
            model = Company
    """

    model: Type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # CREATE                                                               #
    # ------------------------------------------------------------------ #

    async def create(self, **kwargs: Any) -> ModelT:
        """Instantiate, persist, and return a single record."""
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()   # populate server-generated defaults
        await self.session.refresh(instance)
        return instance

    async def bulk_create(self, records: list[dict[str, Any]]) -> list[ModelT]:
        """Insert a batch of records and return the hydrated instances."""
        instances = [self.model(**data) for data in records]
        self.session.add_all(instances)
        await self.session.flush()
        for inst in instances:
            await self.session.refresh(inst)
        return instances

    # ------------------------------------------------------------------ #
    # READ                                                                 #
    # ------------------------------------------------------------------ #

    async def get(self, id: uuid.UUID) -> ModelT | None:
        """Return record by primary key, or ``None``."""
        return await self.session.get(self.model, id)

    async def get_or_raise(self, id: uuid.UUID) -> ModelT:
        """Return record by primary key; raise ``ValueError`` if missing."""
        instance = await self.get(id)
        if instance is None:
            raise ValueError(
                f"{self.model.__name__} with id={id} not found."
            )
        return instance

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at",
        desc_order: bool = True,
    ) -> tuple[Sequence[ModelT], int]:
        """
        Return a page of records and the total count.

        Returns
        -------
        (items, total_count)
        """
        col = getattr(self.model, order_by, None)
        if col is None:
            raise ValueError(f"Unknown column '{order_by}' on {self.model.__name__}")

        order = desc(col) if desc_order else col

        count_stmt = select(func.count()).select_from(self.model)
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = select(self.model).order_by(order).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    # ------------------------------------------------------------------ #
    # UPDATE                                                               #
    # ------------------------------------------------------------------ #

    async def update(self, id: uuid.UUID, **kwargs: Any) -> ModelT | None:
        """Partial update — only supplied fields are changed."""
        instance = await self.get(id)
        if instance is None:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def bulk_update(
        self, ids: list[uuid.UUID], **kwargs: Any
    ) -> int:
        """
        Update multiple records by PK list.
        Returns the number of rows affected.
        """
        pk_col = getattr(self.model, "id")
        stmt = (
            update(self.model)
            .where(pk_col.in_(ids))
            .values(**kwargs)
            .execution_options(synchronize_session="fetch")
        )
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # DELETE                                                               #
    # ------------------------------------------------------------------ #

    async def delete(self, id: uuid.UUID) -> bool:
        """Hard-delete by primary key. Returns True if a row was removed."""
        instance = await self.get(id)
        if instance is None:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True


# ===========================================================================
# CompanyRepository
# ===========================================================================

class CompanyRepository(BaseRepository[Company]):
    """CRUD + domain queries for the ``companies`` table."""

    model = Company

    # ------------------------------------------------------------------ #
    # Specialised reads                                                    #
    # ------------------------------------------------------------------ #

    async def get_by_domain(self, domain: str) -> Company | None:
        """Lookup by unique domain (e.g. 'stripe.com')."""
        stmt = select(Company).where(Company.domain == domain)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_jobs(self, id: uuid.UUID) -> Company | None:
        """Return a company with its job postings eagerly loaded."""
        stmt = (
            select(Company)
            .where(Company.id == id)
            .options(selectinload(Company.jobs))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self,
        *,
        name: str | None = None,
        industry: str | None = None,
        hq_country: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Company], int]:
        """
        Filter companies by optional criteria.
        ``name`` is a case-insensitive substring match.
        """
        filters = []
        if name:
            filters.append(Company.name.ilike(f"%{name}%"))
        if industry:
            filters.append(Company.industry == industry)
        if hq_country:
            filters.append(Company.hq_country == hq_country)

        where = and_(*filters) if filters else True  # type: ignore[arg-type]

        count_stmt = (
            select(func.count()).select_from(Company).where(where)
        )
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Company)
            .where(where)
            .order_by(Company.name)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    # ------------------------------------------------------------------ #
    # Upsert                                                               #
    # ------------------------------------------------------------------ #

    async def upsert_by_domain(
        self,
        domain: str,
        defaults: dict[str, Any],
    ) -> tuple[Company, bool]:
        """
        Fetch-or-create by domain.

        Returns
        -------
        (company, created)
            ``created`` is True when a new row was inserted.
        """
        existing = await self.get_by_domain(domain)
        if existing:
            return existing, False

        company = await self.create(domain=domain, **defaults)
        return company, True

    # ------------------------------------------------------------------ #
    # Delete helpers                                                       #
    # ------------------------------------------------------------------ #

    async def delete_with_jobs(self, id: uuid.UUID) -> bool:
        """
        Hard-delete a company and cascade-delete its jobs.
        (The FK cascade handles the SQL side; this method is a convenience
        wrapper that also confirms the company existed.)
        """
        return await self.delete(id)


# ===========================================================================
# IngestionRunRepository
# ===========================================================================

class IngestionRunRepository(BaseRepository[IngestionRun]):
    """CRUD + lifecycle helpers for the ``ingestion_runs`` table."""

    model = IngestionRun

    # ------------------------------------------------------------------ #
    # Lifecycle transitions                                                #
    # ------------------------------------------------------------------ #

    async def start(self, id: uuid.UUID) -> IngestionRun | None:
        """Mark a PENDING run as RUNNING and record ``started_at``."""
        return await self.update(
            id,
            status=IngestionStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
        )

    async def finish(
        self,
        id: uuid.UUID,
        *,
        status: IngestionStatus = IngestionStatus.SUCCESS,
        jobs_discovered: int = 0,
        jobs_inserted: int = 0,
        jobs_updated: int = 0,
        jobs_skipped: int = 0,
        error_message: str | None = None,
        error_trace: dict | None = None,
    ) -> IngestionRun | None:
        """Complete a run (success, partial, or failed) and update counters."""
        return await self.update(
            id,
            status=status,
            finished_at=datetime.now(tz=timezone.utc),
            jobs_discovered=jobs_discovered,
            jobs_inserted=jobs_inserted,
            jobs_updated=jobs_updated,
            jobs_skipped=jobs_skipped,
            error_message=error_message,
            error_trace=error_trace,
        )

    async def fail(
        self,
        id: uuid.UUID,
        error_message: str,
        error_trace: dict | None = None,
    ) -> IngestionRun | None:
        """Convenience wrapper — mark run as FAILED with an error payload."""
        return await self.finish(
            id,
            status=IngestionStatus.FAILED,
            error_message=error_message,
            error_trace=error_trace,
        )

    # ------------------------------------------------------------------ #
    # Specialised reads                                                    #
    # ------------------------------------------------------------------ #

    async def get_latest_for_source(self, source: str) -> IngestionRun | None:
        """Return the most recently created run for a given source."""
        stmt = (
            select(IngestionRun)
            .where(IngestionRun.source == source)
            .order_by(desc(IngestionRun.created_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_status(
        self,
        status: IngestionStatus,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[IngestionRun], int]:
        """Page through runs filtered by status."""
        count_stmt = (
            select(func.count())
            .select_from(IngestionRun)
            .where(IngestionRun.status == status)
        )
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(IngestionRun)
            .where(IngestionRun.status == status)
            .order_by(desc(IngestionRun.created_at))
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def list_by_company(
        self,
        company_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[IngestionRun], int]:
        """Return all runs scoped to a specific company."""
        count_stmt = (
            select(func.count())
            .select_from(IngestionRun)
            .where(IngestionRun.company_id == company_id)
        )
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(IngestionRun)
            .where(IngestionRun.company_id == company_id)
            .order_by(desc(IngestionRun.started_at))
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def list_running(self) -> Sequence[IngestionRun]:
        """Return all currently RUNNING ingestion runs (for health checks)."""
        stmt = (
            select(IngestionRun)
            .where(IngestionRun.status == IngestionStatus.RUNNING)
            .order_by(IngestionRun.started_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_with_jobs(self, id: uuid.UUID) -> IngestionRun | None:
        """Eagerly load the jobs discovered in this run."""
        stmt = (
            select(IngestionRun)
            .where(IngestionRun.id == id)
            .options(selectinload(IngestionRun.jobs))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


# ===========================================================================
# JobRepository
# ===========================================================================

class JobRepository(BaseRepository[Job]):
    """CRUD + rich search/filter logic for the ``jobs`` table."""

    model = Job

    # ------------------------------------------------------------------ #
    # Specialised reads                                                    #
    # ------------------------------------------------------------------ #

    async def get_by_external_id(
        self, external_id: str, source: str
    ) -> Job | None:
        """Lookup by the (external_id, source) deduplication key."""
        stmt = select(Job).where(
            and_(Job.external_id == external_id, Job.source == source)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_company(self, id: uuid.UUID) -> Job | None:
        """Return a job posting with its company eagerly loaded."""
        stmt = (
            select(Job)
            .where(Job.id == id)
            .options(selectinload(Job.company))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # Rich filter / search                                                  #
    # ------------------------------------------------------------------ #

    async def search(
        self,
        *,
        # Text search
        title: str | None = None,
        # Classification filters
        status: JobStatus | None = JobStatus.ACTIVE,
        employment_type: EmploymentType | None = None,
        seniority: SeniorityLevel | None = None,
        # Location filters
        country: str | None = None,
        city: str | None = None,
        is_remote: bool | None = None,
        # Salary range
        salary_min_gte: float | None = None,
        salary_max_lte: float | None = None,
        # Skill / tag containment (Postgres ARRAY @> operator)
        skills: list[str] | None = None,
        tags: list[str] | None = None,
        # Scoping
        company_id: uuid.UUID | None = None,
        ingestion_run_id: uuid.UUID | None = None,
        # Date window
        posted_after: datetime | None = None,
        posted_before: datetime | None = None,
        # Pagination & ordering
        limit: int = 50,
        offset: int = 0,
        order_by: str = "posted_at",
        desc_order: bool = True,
    ) -> tuple[Sequence[Job], int]:
        """
        Full-featured job search with optional filters.

        Returns
        -------
        (jobs, total_count)
        """
        filters = []

        if title:
            filters.append(Job.title.ilike(f"%{title}%"))
        if status:
            filters.append(Job.status == status)
        if employment_type:
            filters.append(Job.employment_type == employment_type)
        if seniority:
            filters.append(Job.seniority == seniority)
        if country:
            filters.append(Job.country.ilike(f"%{country}%"))
        if city:
            filters.append(Job.city.ilike(f"%{city}%"))
        if is_remote is not None:
            filters.append(Job.is_remote == is_remote)
        if salary_min_gte is not None:
            filters.append(Job.salary_min >= salary_min_gte)
        if salary_max_lte is not None:
            filters.append(Job.salary_max <= salary_max_lte)
        if company_id:
            filters.append(Job.company_id == company_id)
        if ingestion_run_id:
            filters.append(Job.ingestion_run_id == ingestion_run_id)
        if posted_after:
            filters.append(Job.posted_at >= posted_after)
        if posted_before:
            filters.append(Job.posted_at <= posted_before)

        # ARRAY containment: jobs.skills @> ARRAY['Python', 'SQL']
        if skills:
            filters.append(
                Job.skills.contains(cast(skills, ARRAY(String)))  # type: ignore[arg-type]
            )
        if tags:
            filters.append(
                Job.tags.contains(cast(tags, ARRAY(String)))  # type: ignore[arg-type]
            )

        where = and_(*filters) if filters else True  # type: ignore[arg-type]

        count_stmt = select(func.count()).select_from(Job).where(where)
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        col = getattr(Job, order_by, Job.posted_at)
        order = desc(col) if desc_order else col

        stmt = (
            select(Job)
            .where(where)
            .order_by(order)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def list_by_company(
        self,
        company_id: uuid.UUID,
        *,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Job], int]:
        """List jobs for a specific company, optionally filtered by status."""
        filters = [Job.company_id == company_id]
        if status:
            filters.append(Job.status == status)
        where = and_(*filters)

        count_stmt = select(func.count()).select_from(Job).where(where)
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Job)
            .where(where)
            .order_by(desc(Job.posted_at))
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def list_by_skills(
        self,
        skills: list[str],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Job], int]:
        """Find active jobs that require **all** listed skills (AND logic)."""
        where = and_(
            Job.status == JobStatus.ACTIVE,
            Job.skills.contains(cast(skills, ARRAY(String))),  # type: ignore[arg-type]
        )
        count_stmt = select(func.count()).select_from(Job).where(where)
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Job)
            .where(where)
            .order_by(desc(Job.posted_at))
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def list_remote(
        self,
        *,
        seniority: SeniorityLevel | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Job], int]:
        """Active remote jobs, optionally filtered by seniority level."""
        filters = [Job.status == JobStatus.ACTIVE, Job.is_remote.is_(True)]
        if seniority:
            filters.append(Job.seniority == seniority)
        where = and_(*filters)

        count_stmt = select(func.count()).select_from(Job).where(where)
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Job)
            .where(where)
            .order_by(desc(Job.posted_at))
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    # ------------------------------------------------------------------ #
    # Status transitions                                                   #
    # ------------------------------------------------------------------ #

    async def expire_jobs(self, before: datetime) -> int:
        """
        Bulk-mark ACTIVE jobs whose ``expires_at`` is in the past as EXPIRED.
        Returns the count of updated rows.
        """
        stmt = (
            update(Job)
            .where(
                and_(
                    Job.status == JobStatus.ACTIVE,
                    Job.expires_at <= before,
                )
            )
            .values(status=JobStatus.EXPIRED)
            .execution_options(synchronize_session="fetch")
        )
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    async def archive(self, id: uuid.UUID) -> Job | None:
        """Move a single job to ARCHIVED status."""
        return await self.update(id, status=JobStatus.ARCHIVED)

    async def mark_duplicate(self, id: uuid.UUID) -> Job | None:
        """Flag a job as DUPLICATE."""
        return await self.update(id, status=JobStatus.DUPLICATE)

    # ------------------------------------------------------------------ #
    # Analytics                                                            #
    # ------------------------------------------------------------------ #

    async def increment_view(self, id: uuid.UUID) -> None:
        """Atomically increment the view counter for a job."""
        stmt = (
            update(Job)
            .where(Job.id == id)
            .values(view_count=Job.view_count + 1)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)

    async def increment_apply(self, id: uuid.UUID) -> None:
        """Atomically increment the apply counter for a job."""
        stmt = (
            update(Job)
            .where(Job.id == id)
            .values(apply_count=Job.apply_count + 1)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)

    async def set_embedding_id(self, id: uuid.UUID, embedding_id: str) -> None:
        """Attach a ChromaDB embedding ID after vectorising the description."""
        stmt = (
            update(Job)
            .where(Job.id == id)
            .values(embedding_id=embedding_id)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)

    # ------------------------------------------------------------------ #
    # Upsert                                                               #
    # ------------------------------------------------------------------ #

    async def upsert_by_external_id(
        self,
        external_id: str,
        source: str,
        defaults: dict[str, Any],
    ) -> tuple[Job, bool]:
        """
        Insert a new job or update an existing one matched by
        ``(external_id, source)``.

        Returns
        -------
        (job, created)
            ``created`` is True when a new row was inserted.
        """
        existing = await self.get_by_external_id(external_id, source)
        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            await self.session.flush()
            await self.session.refresh(existing)
            return existing, False

        job = await self.create(
            external_id=external_id,
            source=source,
            **defaults,
        )
        return job, True

    # ------------------------------------------------------------------ #
    # Aggregations                                                          #
    # ------------------------------------------------------------------ #

    async def count_by_status(self) -> dict[str, int]:
        """Return a mapping of ``{status: count}`` across all jobs."""
        stmt = select(Job.status, func.count(Job.id)).group_by(Job.status)
        rows = (await self.session.execute(stmt)).all()
        return {row[0].value: row[1] for row in rows}

    async def count_by_source(self) -> dict[str, int]:
        """Return a mapping of ``{source: count}`` for active jobs."""
        stmt = (
            select(Job.source, func.count(Job.id))
            .where(Job.status == JobStatus.ACTIVE)
            .group_by(Job.source)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def salary_stats(
        self,
        *,
        currency: str = "USD",
        seniority: SeniorityLevel | None = None,
    ) -> dict[str, float | None]:
        """
        Compute min / max / avg salary_min for active jobs.
        Optionally scoped to a seniority level.
        """
        filters = [
            Job.status == JobStatus.ACTIVE,
            Job.salary_currency == currency,
            Job.salary_min.is_not(None),
        ]
        if seniority:
            filters.append(Job.seniority == seniority)

        stmt = select(
            func.min(Job.salary_min),
            func.max(Job.salary_min),
            func.avg(Job.salary_min),
        ).where(and_(*filters))

        row = (await self.session.execute(stmt)).one()
        return {
            "salary_min": row[0],
            "salary_max": row[1],
            "salary_avg": float(row[2]) if row[2] else None,
        }


# ===========================================================================
# Unit-of-Work facade  (optional convenience wrapper)
# ===========================================================================

class UnitOfWork:
    """
    Groups all repositories behind a single session so callers can do:

        async with UnitOfWork(session) as uow:
            company = await uow.companies.upsert_by_domain(...)
            job = await uow.jobs.upsert_by_external_id(...)
            # session.commit() is called automatically on __aexit__
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.companies = CompanyRepository(session)
        self.ingestion_runs = IngestionRunRepository(session)
        self.jobs = JobRepository(session)

    async def __aenter__(self) -> "UnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        if exc_type:
            await self._session.rollback()
        else:
            await self._session.commit()
