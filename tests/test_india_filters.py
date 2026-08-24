"""
tests/test_india_filters.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the India-only job scoping and the experience filter bands.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.experience import (
    EXPERIENCE_BUCKETS,
    bucket_for_years,
    infer_seniority,
    parse_years,
    seniority_levels_for,
)
from domain.geo import (
    is_india,
    is_indian_job,
    mentions_foreign_country,
    resolve_city,
    resolve_location,
)
from ingestion.parsers.schemas import ParsedJobDescription
from storage.database import Base
from storage.models import Company, Job, JobStatus, SeniorityLevel
from storage.repository import JobRepository


# ---------------------------------------------------------------------------
# In-memory database fixture (no Postgres service required)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def filter_db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def located_jobs(filter_db_session):
    """Jobs spread across Indian cities, one foreign, one with no location."""
    company = Company(id=uuid.uuid4(), domain="acme.talentradar.internal", name="Acme")
    filter_db_session.add(company)
    await filter_db_session.commit()

    rows = [
        ("blr-1", "Backend Engineer", "Bangalore", "Bengaluru", "IN", SeniorityLevel.MID),
        ("blr-2", "Senior Backend Engineer", "Bengaluru, Karnataka", "Bengaluru", "IN", SeniorityLevel.SENIOR),
        ("ggn-1", "Backend Engineer", "Gurgaon", "Gurugram", "IN", SeniorityLevel.JUNIOR),
        ("pnq-1", "Backend Engineer", "Pune, Maharashtra", "Pune", "IN", SeniorityLevel.LEAD),
        ("sfo-1", "Backend Engineer", "San Francisco, CA", None, "US", SeniorityLevel.SENIOR),
        ("unk-1", "Backend Engineer", None, None, None, SeniorityLevel.MID),
    ]
    for external_id, title, location_raw, city, country, seniority in rows:
        filter_db_session.add(
            Job(
                id=uuid.uuid4(),
                external_id=external_id,
                source="test",
                title=title,
                company_id=company.id,
                description_clean=f"{title} role",
                location_raw=location_raw,
                city=city,
                country=country,
                seniority=seniority,
                status=JobStatus.ACTIVE,
                posted_at=datetime.now(tz=timezone.utc),
            )
        )
    await filter_db_session.commit()
    return filter_db_session


class TestJobRepositoryFilters:
    @pytest.mark.asyncio
    async def test_city_filter_matches_every_spelling(self, located_jobs) -> None:
        """Bengaluru, Bangalore and blr must all return the same postings."""
        repo = JobRepository(located_jobs)
        results = set()
        for spelling in ("Bengaluru", "Bangalore", "blr"):
            jobs, _ = await repo.search(query="backend engineer", city=spelling)
            results.add(tuple(sorted(job.external_id for job in jobs)))
        assert results == {("blr-1", "blr-2")}

    @pytest.mark.asyncio
    async def test_city_filter_excludes_other_cities(self, located_jobs) -> None:
        repo = JobRepository(located_jobs)
        jobs, _ = await repo.search(query="backend engineer", city="Gurugram")
        assert [job.external_id for job in jobs] == ["ggn-1"]

    @pytest.mark.asyncio
    async def test_india_only_drops_foreign_postings(self, located_jobs) -> None:
        repo = JobRepository(located_jobs)
        jobs, _ = await repo.search(query="backend engineer", india_only=True)
        ids = {job.external_id for job in jobs}
        assert "sfo-1" not in ids
        # A posting with no location data at all is kept: ingestion is what
        # keeps non-Indian rows out, the query filter only drops known-foreign.
        assert "unk-1" in ids

    @pytest.mark.asyncio
    async def test_experience_band_filters_by_seniority(self, located_jobs) -> None:
        repo = JobRepository(located_jobs)
        jobs, _ = await repo.search(
            query="backend engineer",
            seniority_levels=seniority_levels_for("lead"),
        )
        assert {job.external_id for job in jobs} == {"pnq-1"}

    @pytest.mark.asyncio
    async def test_filters_combine(self, located_jobs) -> None:
        repo = JobRepository(located_jobs)
        jobs, _ = await repo.search(
            query="backend engineer",
            city="Bangalore",
            india_only=True,
            seniority_levels=seniority_levels_for("senior"),
        )
        assert {job.external_id for job in jobs} == {"blr-2"}


class TestLocationResolution:
    @pytest.mark.parametrize(
        "raw,city",
        [
            ("Bengaluru, Karnataka", "Bengaluru"),
            ("Bangalore", "Bengaluru"),
            ("Gurgaon", "Gurugram"),
            ("Hybrid - Mumbai", "Mumbai"),
            ("Delhi NCR", "Delhi"),
            ("Trivandrum", "Thiruvananthapuram"),
            ("San Francisco, CA", None),
        ],
    )
    def test_resolve_city(self, raw: str, city: str | None) -> None:
        assert resolve_city(raw) == city

    @pytest.mark.parametrize(
        "raw",
        ["Pune, Maharashtra", "Remote - India", "Anywhere in India", "Kochi", "Noida, UP"],
    )
    def test_indian_locations_are_recognised(self, raw: str) -> None:
        assert is_india(raw) is True

    @pytest.mark.parametrize(
        "raw",
        ["New York, NY, USA", "London, United Kingdom", "Toronto, Canada", "Singapore"],
    )
    def test_foreign_locations_are_rejected(self, raw: str) -> None:
        assert is_india(raw) is False
        assert mentions_foreign_country(raw) is True

    def test_shared_city_name_with_foreign_country_is_not_india(self) -> None:
        """A Pakistani Hyderabad must not pass as the Indian one."""
        assert is_india("Hyderabad, Pakistan") is False

    def test_explicit_india_beats_a_foreign_marker(self) -> None:
        assert is_india("Remote - India/US") is True

    def test_resolve_location_maps_to_columns(self) -> None:
        assert resolve_location("Bengaluru, Karnataka") == ("IN", "Bengaluru")
        assert resolve_location("Austin, TX") == (None, None)


class TestJobRetentionRules:
    def test_indian_posting_is_kept(self) -> None:
        assert is_indian_job("Chennai") is True

    def test_foreign_posting_is_dropped(self) -> None:
        assert is_indian_job("Berlin, Germany", is_remote=True) is False

    def test_remote_posting_without_a_country_is_kept(self) -> None:
        assert is_indian_job("Remote", is_remote=True) is True

    def test_onsite_posting_without_any_signal_is_dropped(self) -> None:
        assert is_indian_job(None, is_remote=False) is False

    def test_description_supplies_the_missing_country(self) -> None:
        assert is_indian_job(None, context="Our Bengaluru office is hiring") is True
        assert is_indian_job(None, context="Based in our New York HQ") is False


class TestParsedJobDescriptionEnrichment:
    def test_country_and_city_are_populated(self) -> None:
        parsed = ParsedJobDescription(
            title="Backend Engineer",
            company="Swiggy",
            location="Bengaluru, Karnataka",
            raw_text="Swiggy is hiring a backend engineer.",
        )
        kwargs = parsed.to_job_kwargs()
        assert kwargs["country"] == "IN"
        assert kwargs["city"] == "Bengaluru"

    def test_seniority_is_backfilled_from_experience(self) -> None:
        parsed = ParsedJobDescription(
            title="Backend Engineer",
            company="Zomato",
            experience="6-9 years",
            raw_text="Zomato is hiring.",
        )
        assert parsed.seniority == "senior"

    def test_explicit_seniority_is_not_overwritten(self) -> None:
        parsed = ParsedJobDescription(
            title="Backend Engineer",
            company="Zomato",
            experience="6-9 years",
            seniority="lead",
            raw_text="Zomato is hiring.",
        )
        assert parsed.seniority == "lead"


class TestExperienceBands:
    @pytest.mark.parametrize(
        "text,years",
        [("3-5 years", 3.0), ("5+ years", 5.0), ("2 to 4 yrs", 2.0), ("Fresher", 0.0), (None, None)],
    )
    def test_parse_years(self, text: str | None, years: float | None) -> None:
        assert parse_years(text) == years

    def test_infer_seniority(self) -> None:
        assert infer_seniority("Internship") == "intern"
        assert infer_seniority("3-5 years") == "mid"
        assert infer_seniority("10+ years") == "lead"
        assert infer_seniority("great communication skills") is None

    @pytest.mark.parametrize(
        "years,key",
        [(0, "fresher"), (2, "junior"), (4, "mid"), (6, "senior"), (12, "lead")],
    )
    def test_bucket_for_years(self, years: float, key: str) -> None:
        assert bucket_for_years(years) == key

    def test_every_bucket_expands_to_levels(self) -> None:
        for bucket in EXPERIENCE_BUCKETS:
            assert seniority_levels_for(bucket.key), f"{bucket.key} has no seniority levels"

    def test_unknown_band_expands_to_nothing(self) -> None:
        assert seniority_levels_for("wizard") == []
        assert seniority_levels_for(None) == []
