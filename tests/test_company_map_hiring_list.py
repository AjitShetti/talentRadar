"""
tests/test_company_map_hiring_list.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The ``hiring_list`` source: Bengaluru employers from a shared recruiter list,
checked into ``data/company_sources``. Name and website only — the list's
people and emails never reach the data file or the database.
"""

from __future__ import annotations

import json
import re

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from domain.company_identity import SOURCE_PRECEDENCE
from services.company_map import hiring_list, refresh
from services.company_map.refresh import run_step
from services.company_map.store import upsert_company_ex
from services.company_map.types import ImportedCompany
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company, CompanyContact

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# ---------------------------------------------------------------------------
# the data file and its parser
# ---------------------------------------------------------------------------


def test_the_checked_in_list_parses_into_companies_with_their_own_domains() -> None:
    companies = hiring_list.load()
    assert len(companies) >= 80
    domains = [c.domain for c in companies]
    assert len(domains) == len(set(domains)), "a domain twice would merge two rows into one"
    assert all(c.domain and c.website_url and c.name for c in companies)
    by_domain = {c.domain: c for c in companies}
    assert by_domain["juspay.io"].name == "JUSPAY"
    assert by_domain["juspay.io"].website_url == "https://juspay.io"


def test_the_data_file_carries_no_person_and_no_email() -> None:
    raw = hiring_list.DATA_FILE.read_text(encoding="utf-8")
    assert not _EMAIL.search(raw)
    for record in json.loads(raw)["companies"]:
        assert set(record) == {"name", "domain", "website_url"}


def test_the_list_is_not_part_of_the_curated_catalogue() -> None:
    # data/companies/*.json is seeded with curated precedence; this list is not curated.
    assert hiring_list.DATA_FILE.parent.name != "companies"


def test_parse_skips_a_record_without_a_usable_website() -> None:
    payload = {
        "companies": [
            {"name": "Acme", "domain": "acme.in", "website_url": "https://acme.in"},
            {"name": "No Site", "domain": None, "website_url": None},
            {"name": "Profile", "domain": "x", "website_url": "https://linkedin.com/company/x"},
            {"name": "", "domain": "blank.in", "website_url": "https://blank.in"},
        ]
    }
    assert [c.name for c in hiring_list.parse(payload)] == ["Acme"]


def test_the_source_ranks_below_every_source_that_says_more() -> None:
    rank = SOURCE_PRECEDENCE.index
    assert rank("hiring_list") > rank("github_org")
    assert rank("hiring_list") < rank("news")


# ---------------------------------------------------------------------------
# the refresh step, against Postgres
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def clean_companies():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE companies, company_news CASCADE"))
    yield
    await engine.dispose()


def test_the_step_is_registered() -> None:
    assert "hiring_list" in refresh.STEPS
    assert refresh.MAX_LIMIT["hiring_list"] >= 1000


@pytest.mark.integration
async def test_the_step_imports_each_employer_once_with_a_bengaluru_office(clean_companies) -> None:
    expected = len(hiring_list.load())
    first = await run_step("hiring_list", 5000)
    again = await run_step("hiring_list", 5000)

    assert first == {
        "step": "hiring_list",
        "seen": expected,
        "created": expected,
        "updated": 0,
        "skipped": 0,
    }
    assert again["created"] == 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Company))).scalars().all()
        contacts = (await session.execute(select(CompanyContact))).scalars().all()
    assert len(rows) == expected
    assert all("Bengaluru" in (r.office_cities or []) for r in rows)
    assert all(r.description is None for r in rows), "the list states no description"
    assert contacts == [], "the list's people are never imported"


@pytest.mark.integration
async def test_the_step_does_not_overwrite_what_a_better_source_said(clean_companies) -> None:
    async with AsyncSessionLocal() as session:
        await upsert_company_ex(
            session,
            ImportedCompany(
                name="Juspay Technologies", domain="juspay.io", website_url="https://juspay.io"
            ),
            "yc",
        )
        await session.commit()
    await run_step("hiring_list", 5000)
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(Company).where(Company.domain == "juspay.io"))
        ).scalar_one()
    assert row.name == "Juspay Technologies"
