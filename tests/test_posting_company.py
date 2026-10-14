"""
tests/test_posting_company.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Which ``companies`` row a scraped posting attaches to (review #2).

Job ingestion used to key every employer on its own placeholder domain, so a
posting for an employer the company map already lists made a second row, and
the map showed that employer with no open roles. Runs against Postgres.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from services.job_persistence import posting_company
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company
from storage.repository import UnitOfWork

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def uow():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        yield UnitOfWork(session)
        await session.rollback()
    await engine.dispose()


async def _rows(uow: UnitOfWork, name: str) -> int:
    query = (
        select(func.count()).select_from(Company).where(func.lower(Company.name) == name.lower())
    )
    return int((await uow.companies.session.execute(query)).scalar_one())


async def test_a_posting_attaches_to_the_listed_employer(uow):
    listed = Company(name="Ledgerline", domain="ledgerline.in")
    uow.companies.session.add(listed)
    await uow.companies.session.flush()

    company = await posting_company(uow, "Ledgerline")

    assert company is listed
    assert await _rows(uow, "Ledgerline") == 1


async def test_an_unknown_employer_still_gets_its_placeholder_row(uow):
    company = await posting_company(uow, "Brand New Co")

    assert company.domain == "brand-new-co.talentradar.internal"
    again = await posting_company(uow, "Brand New Co")
    assert again is company


async def test_an_existing_placeholder_row_is_reused(uow):
    jobs_row = Company(name="Acme", domain="acme.talentradar.internal")
    uow.companies.session.add(jobs_row)
    await uow.companies.session.flush()

    assert await posting_company(uow, "Acme") is jobs_row
