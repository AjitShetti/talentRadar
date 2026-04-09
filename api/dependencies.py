"""
api/dependencies.py
~~~~~~~~~~~~~~~~~~~
FastAPI dependency providers for database sessions,
repository instances, and shared services.
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status

from storage.database import AsyncSessionLocal, get_db
from storage.repository import UnitOfWork, CompanyRepository, JobRepository, IngestionRunRepository


async def get_unit_of_work() -> AsyncGenerator[UnitOfWork, None]:
    """
    Provide a UnitOfWork instance for dependency injection.

    Usage:
        @router.get("/jobs")
        async def list_jobs(uow: UnitOfWork = Depends(get_unit_of_work)):
            ...
    """
    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)
        try:
            yield uow
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_job_repository() -> AsyncGenerator[JobRepository, None]:
    """Provide a JobRepository instance."""
    async with AsyncSessionLocal() as session:
        repo = JobRepository(session)
        try:
            yield repo
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_company_repository() -> AsyncGenerator[CompanyRepository, None]:
    """Provide a CompanyRepository instance."""
    async with AsyncSessionLocal() as session:
        repo = CompanyRepository(session)
        try:
            yield repo
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_ingestion_run_repository() -> AsyncGenerator[IngestionRunRepository, None]:
    """Provide an IngestionRunRepository instance."""
    async with AsyncSessionLocal() as session:
        repo = IngestionRunRepository(session)
        try:
            yield repo
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def pagination_params(
    limit: int = 20,
    offset: int = 0,
) -> dict[str, int]:
    """Standard pagination parameters."""
    return {"limit": min(limit, 100), "offset": max(offset, 0)}
