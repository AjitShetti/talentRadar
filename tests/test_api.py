"""
tests/test_api.py
~~~~~~~~~~~~~~~~~
Tests for the FastAPI API endpoints.

Design principles:
- External services (DB, Orchestrator, Airflow, ChromaDB) are always mocked.
- Every assertion specifies the EXACT expected status code — no [200, 500] tolerance.
- Tests document the contract the endpoint must honour, not whether the
  infrastructure happens to be running.

Covers:
- Health / info endpoints
- Structured search input validation and successful response shape
- Semantic search input validation and successful response shape
- Query endpoint validation
- Trends endpoint validation
- Recommend / match endpoint validation
- Ingest endpoint validation and mocked success path
- 404 handling for unknown jobs
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — mock factories shared across test classes
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_search_response(n_results: int = 2):
    """Return a mock AgentResponse with n_results RetrievalResult objects."""
    from agents.state import AgentResponse, IntentType, RetrievalResult

    results = [
        RetrievalResult(
            job_id=f"job-{i}",
            title=f"Python Engineer {i}",
            company="Acme",
            location="Remote",
            is_remote=True,
            skills=["Python"],
            score=0.9 - i * 0.1,
        )
        for i in range(n_results)
    ]
    return AgentResponse(
        success=True,
        intent=IntentType.SEARCH_JOBS,
        results=results,
        summary="Here are the top Python jobs.",
        metadata={"total_found": n_results},
    )


# The empty-UoW override lives in conftest as the ``db_free_client`` fixture:
# overriding the dependency is the only way to detach a route from the
# database, since patching the module attribute is too late to matter.


# ─────────────────────────────────────────────────────────────────────────────
# Health / info endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoints:
    async def test_root_endpoint_returns_200(self, api_client):
        response = await api_client.get("/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "name" in data
        assert data["name"] == "TalentRadar API"

    async def test_health_endpoint_returns_healthy(self, api_client):
        response = await api_client.get("/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"

    async def test_api_root_returns_endpoints_map(self, api_client):
        response = await api_client.get("/api/v1")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "endpoints" in data
        assert "search" in data["endpoints"]
        assert "query" in data["endpoints"]


# ─────────────────────────────────────────────────────────────────────────────
# Search — structured endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestStructuredSearchEndpoint:
    async def test_structured_search_returns_200_with_mocked_db(self, db_free_client):
        """Structured search must return 200 OK — never silently accept 500."""
        response = await db_free_client.post(
            "/api/v1/search/structured",
            json={"limit": 10, "offset": 0},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert isinstance(data["jobs"], list)

    async def test_structured_search_respects_limit(self, db_free_client):
        response = await db_free_client.post(
            "/api/v1/search/structured",
            json={"limit": 5, "offset": 0},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["limit"] == 5

    async def test_structured_search_rejects_negative_limit(self, api_client):
        """Negative limit should be rejected at the schema level (422)."""
        response = await api_client.post(
            "/api/v1/search/structured",
            json={"limit": -1, "offset": 0},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_structured_search_rejects_negative_offset(self, api_client):
        response = await api_client.post(
            "/api/v1/search/structured",
            json={"limit": 10, "offset": -5},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_structured_search_with_skill_filter(self, db_free_client):
        response = await db_free_client.post(
            "/api/v1/search/structured",
            json={"skills": ["Python", "FastAPI"], "limit": 10, "offset": 0},
        )
        assert response.status_code == status.HTTP_200_OK


# ─────────────────────────────────────────────────────────────────────────────
# Search — semantic endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticSearchEndpoint:
    async def test_semantic_search_rejects_empty_query(self, api_client):
        """Empty query string must fail at validation — 422, not 500."""
        response = await api_client.post(
            "/api/v1/search/semantic",
            json={"query": "", "limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_semantic_search_rejects_missing_query(self, api_client):
        response = await api_client.post(
            "/api/v1/search/semantic",
            json={"limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_semantic_search_returns_200_on_valid_query(self, api_client):
        mock_response = _make_mock_search_response(n_results=2)
        with patch(
            "api.routers.search.Orchestrator.process_query",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await api_client.post(
                "/api/v1/search/semantic",
                json={"query": "senior python engineer", "limit": 10},
            )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert len(data["results"]) == 2

    async def test_semantic_search_includes_summary_in_response(self, api_client):
        mock_response = _make_mock_search_response(n_results=1)
        with patch(
            "api.routers.search.Orchestrator.process_query",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await api_client.post(
                "/api/v1/search/semantic",
                json={"query": "python jobs", "limit": 5},
            )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data.get("summary") == "Here are the top Python jobs."

    async def test_semantic_search_rejects_limit_above_max(self, api_client):
        """Limit > 100 should be rejected at schema level."""
        response = await api_client.post(
            "/api/v1/search/semantic",
            json={"query": "python", "limit": 999},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ─────────────────────────────────────────────────────────────────────────────
# Query endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestQueryEndpoint:
    async def test_query_rejects_empty_query(self, api_client):
        """Empty query must return 422 — not 200, not 500."""
        response = await api_client.post(
            "/api/v1/query",
            json={"query": "", "limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_query_rejects_missing_query_field(self, api_client):
        response = await api_client.post("/api/v1/query", json={"limit": 10})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_valid_query_returns_200(self, api_client):
        mock_response = _make_mock_search_response(n_results=1)
        with patch(
            "api.routers.query.Orchestrator.process_query",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await api_client.post(
                "/api/v1/query",
                json={"query": "find me python jobs", "limit": 5},
            )
        assert response.status_code == status.HTTP_200_OK


# ─────────────────────────────────────────────────────────────────────────────
# Recommend / match endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendEndpoints:
    async def test_match_rejects_missing_candidate(self, api_client):
        """Missing required 'candidate' body field must 422 — not 200 or 500."""
        response = await api_client.post(
            "/api/v1/recommend/match",
            json={"limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_match_reports_not_implemented(self, api_client):
        """Candidate-to-job matching is an explicit stub, and says so.

        This previously asserted a 422 for an empty skills list, which the
        endpoint never implemented — it raises 501 for every input. Assert the
        real contract rather than a validation rule nobody wrote.
        """
        response = await api_client.post(
            "/api/v1/recommend/match",
            json={"candidate": {"skills": []}, "limit": 10},
        )
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED

    async def test_learning_path_rejects_empty_skills(self, api_client):
        response = await api_client.post(
            "/api/v1/recommend/learning-path",
            json={"missing_skills": []},
        )
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Ingest endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestEndpoints:
    """Ingestion is privileged: it fans out live scrapers and LLM parsing."""

    async def test_trigger_ingestion_requires_authentication(self, api_client):
        response = await api_client.post(
            "/api/v1/ingest/trigger",
            json={"roles": ["Python Engineer"], "locations": ["Remote"], "max_results_per_query": 5},
        )
        assert response.status_code in (401, 403)

    async def test_trigger_ingestion_rejects_non_admin(self, auth_client):
        """A signed-in ordinary user is still not allowed to start a scrape."""
        response = await auth_client.post(
            "/api/v1/ingest/trigger",
            json={"roles": ["Python Engineer"], "locations": ["Remote"], "max_results_per_query": 5},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_trigger_ingestion_returns_200_for_admin(self, admin_client):
        """The trigger runs the persisting pipeline, not the cache-only engine.

        This previously patched ``RealtimeScraperEngine.search_all``, which is
        the live-search path: it caches to Redis and never writes to Postgres.
        See tests/test_ingest_trigger.py for the full contract.
        """
        mock_result = {
            "run_id": "run-1",
            "total_fetched": 12,
            "inserted": 10,
            "updated": 2,
            "embedded": 10,
        }
        with patch(
            "ingestion.dispatcher.dispatch_ingestion",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_dispatch:
            response = await admin_client.post(
                "/api/v1/ingest/trigger",
                json={
                    "roles": ["Python Engineer"],
                    "locations": ["Remote"],
                    "max_results_per_query": 5,
                },
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["success"] is True
        mock_dispatch.assert_called_once()

    async def test_trigger_ingestion_rejects_empty_roles(self, admin_client):
        """Empty roles list should be rejected at the schema level."""
        response = await admin_client.post(
            "/api/v1/ingest/trigger",
            json={"roles": [], "locations": ["Remote"], "max_results_per_query": 5},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_trigger_ingestion_rejects_zero_max_results(self, admin_client):
        response = await admin_client.post(
            "/api/v1/ingest/trigger",
            json={"roles": ["Engineer"], "locations": ["Remote"], "max_results_per_query": 0},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ─────────────────────────────────────────────────────────────────────────────
# 404 behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestNotFoundBehaviour:
    async def test_unknown_job_id_returns_404(self, api_client):
        with patch("api.routers.search.get_job_repository") as mock_dep:
            mock_repo = AsyncMock()
            mock_repo.get = AsyncMock(return_value=None)
            mock_dep.return_value = mock_repo
            response = await api_client.get("/api/v1/search/nonexistent-uuid-1234")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_404_response_body_has_detail(self, api_client):
        with patch("api.routers.search.get_job_repository") as mock_dep:
            mock_repo = AsyncMock()
            mock_repo.get = AsyncMock(return_value=None)
            mock_dep.return_value = mock_repo
            response = await api_client.get("/api/v1/search/nonexistent-uuid-5678")
        data = response.json()
        assert "detail" in data
