"""
tests/test_search.py
~~~~~~~~~~~~~~~~~~~~
Comprehensive unit and integration tests for Requirement R1: Job Search Relevance.

Covers:
- Query tokenization & synonym/abbreviation expansion logic
- Multi-field matching across Job fields with AND-to-OR fallback in JobRepository.search()
- Broad technology role queries ("java dev", "software engineer", "fullstack dev") returning >= 3 relevant jobs
- Role keyword sanitization in RAGAgent._apply_filters()
- Database search fallback in RAGAgent.search_jobs()
- API endpoint integration tests for structured search and semantic search
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.rag_agent import RAGAgent
from agents.state import IntentType, QueryContext, RetrievalResult
from api.dependencies import get_unit_of_work
from api.main import app
from storage.database import Base
from storage.models import Company, EmploymentType, Job, JobStatus, SeniorityLevel
from storage.repository import JobRepository, UnitOfWork, tokenize_and_expand_query


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Database Fixtures (No Postgres service required)
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def search_db_session():
    """Create a fresh in-memory SQLite database session for search tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def seed_jobs(search_db_session):
    """Seed the in-memory test database with a realistic set of job postings."""
    c1 = Company(id=uuid.uuid4(), domain="fintech.talentradar.internal", name="FinTech Global")
    c2 = Company(id=uuid.uuid4(), domain="cloudscale.talentradar.internal", name="CloudScale Systems")
    c3 = Company(id=uuid.uuid4(), domain="webscale.talentradar.internal", name="WebScale Apps")

    search_db_session.add_all([c1, c2, c3])
    await search_db_session.commit()

    jobs_data = [
        # Java roles (4 jobs)
        Job(
            id=uuid.uuid4(),
            external_id="ext-java-01",
            source="ats_crawler",
            title="Senior Java Developer",
            company_id=c1.id,
            description_clean="FinTech Global is seeking a Senior Java Developer to build high-throughput microservices using Java 17, Spring Boot, PostgreSQL, and Kafka.",
            skills=["Java", "Spring Boot", "PostgreSQL", "Kafka"],
            tags=["backend", "microservices"],
            is_remote=True,
            seniority=SeniorityLevel.SENIOR,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-java-02",
            source="ats_crawler",
            title="Java Backend Engineer",
            company_id=c2.id,
            description_clean="CloudScale Systems is looking for a Java Backend Engineer to design scalable cloud services with Java, Spring Cloud, AWS, and REST APIs.",
            skills=["Java", "Spring Cloud", "AWS"],
            tags=["backend", "cloud"],
            is_remote=True,
            seniority=SeniorityLevel.MID,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-java-03",
            source="ats_crawler",
            title="Fullstack Java Engineer",
            company_id=c3.id,
            description_clean="Seeking a Fullstack Java Engineer proficient in Java Spring Boot backend and React/TypeScript frontend development.",
            skills=["Java", "Spring Boot", "React", "TypeScript"],
            tags=["fullstack", "web"],
            is_remote=True,
            seniority=SeniorityLevel.MID,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-java-04",
            source="ats_crawler",
            title="Lead Java Software Developer",
            company_id=c1.id,
            description_clean="PayTech Systems is hiring a Lead Java Software Developer to architect distributed transaction platforms using Java, Hibernate, and Kubernetes.",
            skills=["Java", "Hibernate", "Kubernetes"],
            tags=["lead", "distributed"],
            is_remote=False,
            seniority=SeniorityLevel.LEAD,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        # Software Engineer roles (3 jobs)
        Job(
            id=uuid.uuid4(),
            external_id="ext-swe-01",
            source="ats_crawler",
            title="Senior Software Engineer",
            company_id=c2.id,
            description_clean="Hiring a Senior Software Engineer to lead distributed systems design using Python, Go, and Kubernetes.",
            skills=["Python", "Go", "Kubernetes"],
            tags=["backend", "infrastructure"],
            is_remote=True,
            seniority=SeniorityLevel.SENIOR,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-swe-02",
            source="ats_crawler",
            title="Software Developer - Platform",
            company_id=c1.id,
            description_clean="Nexus Infrastructure is hiring a Software Developer to construct core platform APIs and microservices.",
            skills=["Python", "Microservices", "Docker"],
            tags=["platform"],
            is_remote=True,
            seniority=SeniorityLevel.MID,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-swe-03",
            source="ats_crawler",
            title="Lead Software Engineer",
            company_id=c3.id,
            description_clean="Lead Software Engineer needed to drive cloud architecture and full-stack software development.",
            skills=["Python", "FastAPI", "React"],
            tags=["lead", "cloud"],
            is_remote=False,
            seniority=SeniorityLevel.LEAD,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        # Fullstack Developer roles (3 jobs)
        Job(
            id=uuid.uuid4(),
            external_id="ext-fs-01",
            source="ats_crawler",
            title="Senior Fullstack Developer",
            company_id=c3.id,
            description_clean="WebScale Apps needs a Senior Fullstack Developer skilled in React, Node.js, TypeScript, and PostgreSQL.",
            skills=["React", "Node.js", "TypeScript", "PostgreSQL"],
            tags=["fullstack", "web"],
            is_remote=True,
            seniority=SeniorityLevel.SENIOR,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-fs-02",
            source="ats_crawler",
            title="Full Stack Dev",
            company_id=c2.id,
            description_clean="NextGen Digital seeks a Full Stack Dev experienced in Python, Django, React, and AWS.",
            skills=["Python", "Django", "React", "AWS"],
            tags=["fullstack", "django"],
            is_remote=True,
            seniority=SeniorityLevel.MID,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
        Job(
            id=uuid.uuid4(),
            external_id="ext-fs-03",
            source="ats_crawler",
            title="Principal Fullstack Engineer",
            company_id=c1.id,
            description_clean="Apex Solutions is hiring a Principal Fullstack Engineer for next-generation web applications using Next.js and Go.",
            skills=["Next.js", "Go", "TypeScript"],
            tags=["fullstack", "principal"],
            is_remote=True,
            seniority=SeniorityLevel.PRINCIPAL,
            employment_type=EmploymentType.FULL_TIME,
            status=JobStatus.ACTIVE,
            posted_at=datetime.now(tz=timezone.utc),
        ),
    ]

    search_db_session.add_all(jobs_data)
    await search_db_session.commit()
    return jobs_data


# ─────────────────────────────────────────────────────────────────────────────
# 1. Unit Tests for Tokenization & Synonym Expansion
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenizationAndExpansion:
    """Test tokenize_and_expand_query logic in storage/repository.py."""

    def test_tokenize_query_basic(self):
        groups = tokenize_and_expand_query("java dev")
        assert len(groups) == 2
        assert "java" in groups[0]
        assert any(term in groups[1] for term in ["dev", "developer", "engineer"])

    def test_tokenize_query_strips_stop_words(self):
        groups = tokenize_and_expand_query("software engineer in new york")
        terms = [g[0] for g in groups]
        assert "in" not in terms
        assert "software" in terms
        assert "engineer" in terms

    def test_synonym_expansion_abbreviations(self):
        groups = tokenize_and_expand_query("swe")
        assert len(groups) == 1
        expanded = groups[0]
        assert "swe" in expanded
        assert "software engineer" in expanded or "software developer" in expanded


# ─────────────────────────────────────────────────────────────────────────────
# 2. Unit / Repository Tests for Broad Queries
# ─────────────────────────────────────────────────────────────────────────────

class TestJobRepositorySearchBroadQueries:
    """Test JobRepository.search() matching for broad technology role queries."""

    @pytest.mark.asyncio
    async def test_search_java_dev_returns_at_least_3_jobs(self, search_db_session, seed_jobs):
        repo = JobRepository(search_db_session)
        jobs, total = await repo.search(query="java dev")
        assert total >= 3
        assert len(jobs) >= 3
        titles = [j.title.lower() for j in jobs]
        assert all("java" in t or "developer" in t or "engineer" in t for t in titles)

    @pytest.mark.asyncio
    async def test_search_software_engineer_returns_at_least_3_jobs(self, search_db_session, seed_jobs):
        repo = JobRepository(search_db_session)
        jobs, total = await repo.search(query="software engineer")
        assert total >= 3
        assert len(jobs) >= 3

    @pytest.mark.asyncio
    async def test_search_fullstack_dev_returns_at_least_3_jobs(self, search_db_session, seed_jobs):
        repo = JobRepository(search_db_session)
        jobs, total = await repo.search(query="fullstack dev")
        assert total >= 3
        assert len(jobs) >= 3

    @pytest.mark.asyncio
    async def test_and_to_or_fallback_matching(self, search_db_session, seed_jobs):
        repo = JobRepository(search_db_session)
        jobs, total = await repo.search(query="java frontend developer", min_results=3)
        assert total >= 1
        assert len(jobs) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Unit Tests for RAGAgent Filter Sanitization
# ─────────────────────────────────────────────────────────────────────────────

class TestRAGAgentRoleKeywordFiltering:
    """Test RAGAgent._apply_filters() role keyword sanitization."""

    def test_apply_filters_strips_role_keywords(self):
        sample_results = [
            RetrievalResult(
                job_id="j1",
                title="Senior Java Developer",
                company="FinTech Corp",
                skills=["Java", "Spring Boot"],
                score=0.9,
            ),
            RetrievalResult(
                job_id="j2",
                title="Python Software Engineer",
                company="DataTech",
                skills=["Python", "FastAPI"],
                score=0.8,
            ),
        ]
        ctx = QueryContext(raw_query="java dev", skills=["java", "dev"])
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 1
        assert filtered[0].job_id == "j1"

    def test_apply_filters_with_only_role_keywords_retains_all(self):
        sample_results = [
            RetrievalResult(
                job_id="j1",
                title="Senior Java Developer",
                company="FinTech Corp",
                skills=["Java", "Spring Boot"],
                score=0.9,
            ),
            RetrievalResult(
                job_id="j2",
                title="Python Software Engineer",
                company="DataTech",
                skills=["Python", "FastAPI"],
                score=0.8,
            ),
        ]
        ctx = QueryContext(raw_query="developer", skills=["developer"])
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. Integration Tests for Search API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestStructuredSearchEndpointBroadQueries:
    """Integration test for POST /api/v1/search/structured with broad queries."""

    @pytest.mark.asyncio
    async def test_structured_search_java_dev(self, search_db_session, seed_jobs):
        uow = UnitOfWork(search_db_session)
        app.dependency_overrides[get_unit_of_work] = lambda: uow

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"query": "java dev", "limit": 10}
            response = await client.post("/api/v1/search/structured", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert "jobs" in data
            assert len(data["jobs"]) >= 3

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_structured_search_software_engineer(self, search_db_session, seed_jobs):
        uow = UnitOfWork(search_db_session)
        app.dependency_overrides[get_unit_of_work] = lambda: uow

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"query": "software engineer", "limit": 10}
            response = await client.post("/api/v1/search/structured", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert "jobs" in data
            assert len(data["jobs"]) >= 3

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_structured_search_fullstack_dev(self, search_db_session, seed_jobs):
        uow = UnitOfWork(search_db_session)
        app.dependency_overrides[get_unit_of_work] = lambda: uow

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"query": "fullstack dev", "limit": 10}
            response = await client.post("/api/v1/search/structured", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert "jobs" in data
            assert len(data["jobs"]) >= 3

        app.dependency_overrides.clear()


class TestSemanticSearchEndpointBroadQueries:
    """Integration test for POST /api/v1/search/semantic with relational DB fallback."""

    @pytest.mark.asyncio
    async def test_semantic_search_java_dev_with_db_fallback(self, search_db_session, seed_jobs):
        uow = UnitOfWork(search_db_session)

        with patch("agents.rag_agent.ChromaJobStore") as MockChroma, \
             patch("agents.rag_agent.embed_texts", return_value=[[0.1] * 384]), \
             patch("agents.orchestrator.AsyncGroq"), \
             patch("agents.rag_agent.AsyncSessionLocal", return_value=AsyncMock(__aenter__=AsyncMock(return_value=search_db_session), __aexit__=AsyncMock(return_value=False))):

            mock_store = MockChroma.return_value
            mock_store.search.return_value = []

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                payload = {"query": "java dev", "limit": 10}
                response = await client.post("/api/v1/search/semantic", json=payload)
                assert response.status_code == 200
                data = response.json()
                assert "results" in data
                assert len(data["results"]) >= 3
