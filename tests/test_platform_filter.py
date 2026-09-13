"""
tests/test_platform_filter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The search page's "Posted on" filter: platform detection, the SQL predicate,
and both search endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.state import AgentResponse, IntentType, RetrievalResult
from api.dependencies import get_unit_of_work
from api.main import app
from api.schemas.job_schemas import JobFilterSchema, SearchRequestSchema
from domain.platforms import JOB_PLATFORMS, get_platform, platform_of
from storage.database import Base
from storage.models import Company, Job, JobStatus
from storage.repository import JobRepository, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TestPlatformOf:
    @pytest.mark.parametrize(
        ("source", "url", "expected"),
        [
            ("linkedin", "https://www.linkedin.com/jobs/view/1", "linkedin"),
            # The two producers disagree on Indeed's source name.
            ("indeed_india", None, "indeed"),
            ("indeed", None, "indeed"),
            ("greenhouse:razorpay", None, "company_sites"),
            ("lever:cred", "https://jobs.lever.co/cred/abc", "company_sites"),
            # Found by Tavily, lives on LinkedIn: the URL wins.
            ("tavily_search", "https://in.linkedin.com/jobs/view/2", "linkedin"),
            ("live_search", "https://in.indeed.com/viewjob?jk=1", "indeed"),
            ("tavily_search", "https://example.com/careers", None),
            (None, None, None),
        ],
    )
    def test_detects_platform(self, source: str | None, url: str | None, expected: str | None) -> None:
        platform = platform_of(source, url)
        assert (platform.key if platform else None) == expected

    def test_lookalike_host_is_not_matched(self) -> None:
        assert platform_of(None, "https://notlinkedin.com/jobs/1") is None

    def test_keys_are_unique(self) -> None:
        keys = [p.key for p in JOB_PLATFORMS]
        assert len(keys) == len(set(keys))

    def test_get_platform_normalises_key(self) -> None:
        assert get_platform(" LinkedIn ") is get_platform("linkedin")
        assert get_platform("myspace") is None


class TestSchemaValidation:
    def test_unknown_platform_is_rejected(self) -> None:
        # Dropping it silently would search every platform instead.
        with pytest.raises(ValidationError):
            JobFilterSchema(platforms=["myspace"])
        with pytest.raises(ValidationError):
            SearchRequestSchema(query="python", platforms=["myspace"])

    def test_platforms_are_normalised_and_deduplicated(self) -> None:
        schema = JobFilterSchema(platforms=["LinkedIn", "linkedin", "naukri"])
        assert schema.platforms == ["linkedin", "naukri"]

    def test_empty_list_means_no_filter(self) -> None:
        assert JobFilterSchema(platforms=[]).platforms is None


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> None:
    company = Company(id=uuid.uuid4(), domain="acme.talentradar.internal", name="Acme")
    session.add(company)
    await session.commit()

    rows = [
        ("linkedin", "https://www.linkedin.com/jobs/view/1"),
        ("indeed_india", "https://in.indeed.com/viewjob?jk=1"),
        ("greenhouse:acme", "https://boards.greenhouse.io/acme/jobs/1"),
        ("tavily_search", "https://in.linkedin.com/jobs/view/2"),
        ("naukri", "https://www.naukri.com/job-listings-1"),
    ]
    session.add_all(
        Job(
            id=uuid.uuid4(),
            external_id=f"ext-{i}",
            source=source,
            source_url=url,
            title="Python Developer",
            company_id=company.id,
            skills=["Python"],
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=UTC),
        )
        for i, (source, url) in enumerate(rows)
    )
    await session.commit()


class TestRepositoryPlatformFilter:
    @pytest.mark.asyncio
    async def test_filters_by_single_platform(self, session: AsyncSession, seeded: None) -> None:
        jobs, total = await JobRepository(session).search(platforms=["linkedin"])
        assert total == 2
        assert {j.source for j in jobs} == {"linkedin", "tavily_search"}

    @pytest.mark.asyncio
    async def test_filters_by_several_platforms(self, session: AsyncSession, seeded: None) -> None:
        jobs, total = await JobRepository(session).search(platforms=["indeed", "company_sites"])
        assert total == 2
        assert {j.source for j in jobs} == {"indeed_india", "greenhouse:acme"}

    @pytest.mark.asyncio
    async def test_no_platforms_returns_everything(self, session: AsyncSession, seeded: None) -> None:
        _, total = await JobRepository(session).search(platforms=None)
        assert total == 5


class TestSearchEndpoints:
    @pytest.mark.asyncio
    async def test_structured_search_filters_and_labels_platform(self, session: AsyncSession, seeded: None) -> None:
        app.dependency_overrides[get_unit_of_work] = lambda: UnitOfWork(session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/search/structured",
                    json={"query": "python", "platforms": ["naukri"], "india_only": False},
                )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200
        jobs = response.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["platform"] == "naukri"

    @pytest.mark.asyncio
    async def test_structured_search_rejects_unknown_platform(self) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/search/structured", json={"query": "python", "platforms": ["myspace"]}
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_semantic_search_filters_results_by_url(self) -> None:
        results = [
            RetrievalResult(job_id="1", title="A", company="X", source_url="https://www.linkedin.com/jobs/view/1"),
            RetrievalResult(job_id="2", title="B", company="Y", source_url="https://www.naukri.com/job-listings-2"),
            RetrievalResult(job_id="3", title="C", company="Z", source_url=None),
        ]
        agent_response = AgentResponse(success=True, intent=IntentType.SEARCH_JOBS, results=results)
        with patch(
            "api.routers.search.Orchestrator.process_query",
            new=AsyncMock(return_value=agent_response),
        ) as process_query:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/search/semantic",
                    json={"query": "python", "platforms": ["linkedin"], "limit": 10},
                )
        assert response.status_code == 200
        data = response.json()
        assert [r["id"] for r in data["results"]] == ["1"]
        assert data["total_found"] == 1
        assert data["filters_applied"]["platforms"] == ["linkedin"]
        # Over-fetches so the filter does not leave the page half-empty.
        assert process_query.await_args.kwargs["limit"] == 30
