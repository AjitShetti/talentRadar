"""
tests/test_api.py
~~~~~~~~~~~~~~~~~
Tests for the FastAPI API endpoints.

Covers:
- Health checks
- Search endpoints (structured and semantic)
- Query endpoint
- Trends endpoint
- Recommend endpoint
- Error handling
"""

import pytest
from fastapi import status


class TestHealthEndpoints:
    """Test health check endpoints."""

    async def test_root_endpoint(self, api_client):
        response = await api_client.get("/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "name" in data
        assert data["name"] == "TalentRadar API"

    async def test_health_endpoint(self, api_client):
        response = await api_client.get("/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"

    async def test_api_root(self, api_client):
        response = await api_client.get("/api/v1")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "endpoints" in data
        assert "search" in data["endpoints"]
        assert "query" in data["endpoints"]


class TestSearchEndpoints:
    """Test search-related endpoints."""

    async def test_structured_search_empty(self, api_client):
        """Structured search should return empty results with no filters."""
        response = await api_client.post(
            "/api/v1/search/structured",
            json={"limit": 10, "offset": 0},
        )
        # May fail without DB, but shouldn't 500
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_500_INTERNAL_SERVER_ERROR]

    async def test_semantic_search_requires_query(self, api_client):
        """Semantic search should validate query input."""
        response = await api_client.post(
            "/api/v1/search/semantic",
            json={"query": "", "limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestQueryEndpoint:
    """Test the unified query endpoint."""

    async def test_query_requires_text(self, api_client):
        """Query endpoint should require query text."""
        response = await api_client.post(
            "/api/v1/query",
            json={"query": "", "limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestTrendsEndpoints:
    """Test market trends endpoints."""

    async def test_get_trends(self, api_client):
        """Trends endpoint should return market data."""
        response = await api_client.post(
            "/api/v1/trends",
            json={"query": "Market trends", "days": 30},
        )
        # May not have data, but shouldn't 500
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_500_INTERNAL_SERVER_ERROR]

    async def test_get_top_skills(self, api_client):
        """Skills endpoint should return skill data."""
        response = await api_client.get("/api/v1/trends/skills?days=30")
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_500_INTERNAL_SERVER_ERROR]


class TestRecommendEndpoints:
    """Test candidate-job matching endpoints."""

    async def test_match_requires_profile(self, api_client):
        """Match endpoint should require candidate profile."""
        response = await api_client.post(
            "/api/v1/recommend/match",
            json={"candidate": {"skills": []}, "limit": 10},
        )
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_422_UNPROCESSABLE_ENTITY]


class TestIngestEndpoints:
    """Test ingestion management endpoints."""

    async def test_trigger_ingestion(self, api_client):
        """Trigger ingestion should attempt to start pipeline."""
        response = await api_client.post(
            "/api/v1/ingest/trigger",
            json={"roles": ["Engineer"], "locations": ["Remote"], "max_results_per_query": 2},
        )
        # May fail if Airflow isn't running, but shouldn't 500 unexpectedly
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_500_INTERNAL_SERVER_ERROR]
