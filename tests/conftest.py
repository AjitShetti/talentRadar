"""
tests/conftest.py
~~~~~~~~~~~~~~~~~
Pytest fixtures for the TalentRadar test suite.

Provides:
- Database session fixtures
- Mock API clients
- Sample data factories
- Async test support
"""

from __future__ import annotations

import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config.settings import Settings, get_settings
from storage.database import AsyncSessionLocal, Base, engine


# ─────────────────────────────────────────────────────────────────────────────
# Settings override for tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def test_settings():
    """Override settings for testing."""
    os.environ["POSTGRES_HOST"] = "localhost"
    os.environ["POSTGRES_DB"] = "talentradar_test"
    os.environ["GROQ_API_KEY"] = "test_key"
    os.environ["TAVILY_API_KEY"] = "test_key"
    os.environ["JWT_SECRET_KEY"] = "test_secret_key_for_testing_min_32_chars"

    # Clear cached settings
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    yield

    # Cleanup
    os.environ.pop("POSTGRES_HOST", None)
    os.environ.pop("POSTGRES_DB", None)


# ─────────────────────────────────────────────────────────────────────────────
# Database fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Create a fresh database session for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# API client fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def api_client():
    """Async HTTP client for testing the FastAPI app."""
    from api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ─────────────────────────────────────────────────────────────────────────────
# Mock services
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_groq_client():
    """Mock Groq API client."""
    mock = MagicMock()
    mock.chat.completions.create = MagicMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"intent": "search_jobs", "keywords": ["python"], "skills": ["Python"]}'
                    )
                )
            ]
        )
    )
    return mock


@pytest.fixture
def mock_tavily_client():
    """Mock Tavily API client."""
    mock = MagicMock()
    mock.search_jobs = MagicMock(
        return_value=[
            {
                "title": "Software Engineer",
                "url": "https://example.com/job1",
                "content": "We're looking for a Python engineer...",
                "score": 0.95,
                "published_date": "2026-04-01",
            }
        ]
    )
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Sample data factories
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_parsed_job():
    """Sample ParsedJobDescription for testing."""
    from ingestion.parsers.schemas import ParsedJobDescription

    return ParsedJobDescription(
        title="Senior Python Engineer",
        company="TechCorp",
        skills=["Python", "FastAPI", "PostgreSQL", "Redis"],
        experience="5+ years",
        location="San Francisco, CA",
        is_remote=True,
        salary="$150k - $200k / year",
        salary_min=150000,
        salary_max=200000,
        salary_currency="USD",
        employment_type="full_time",
        seniority="senior",
        source_url="https://example.com/job1",
        raw_text="We're hiring a senior Python engineer...",
    )


@pytest.fixture
def sample_candidate_profile():
    """Sample CandidateProfile for testing."""
    from agents.state import CandidateProfile

    return CandidateProfile(
        name="John Doe",
        skills=["Python", "FastAPI", "SQL", "Docker"],
        experience_years=5,
        desired_title="Senior Software Engineer",
        is_remote=True,
        seniority="senior",
    )
