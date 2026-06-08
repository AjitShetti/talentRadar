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


def _make_empty_uow():
    """UoW mock that returns an empty job list (no DB required)."""
    mock_uow = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.jobs.search = AsyncMock(return_value=([], 0))
    return mock_uow


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
    async def test_structured_search_returns_200_with_mocked_db(self, api_client):
        """Structured search must return 200 OK — never silently accept 500."""
        with patch("api.routers.search.get_unit_of_work", return_value=_make_empty_uow()):
            response = await api_client.post(
                "/api/v1/search/structured",
                json={"limit": 10, "offset": 0},
            )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert isinstance(data["jobs"], list)

    async def test_structured_search_respects_limit(self, api_client):
        with patch("api.routers.search.get_unit_of_work", return_value=_make_empty_uow()):
            response = await api_client.post(
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

    async def test_structured_search_with_skill_filter(self, api_client):
        with patch("api.routers.search.get_unit_of_work", return_value=_make_empty_uow()):
            response = await api_client.post(
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
# Trends endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestTrendsEndpoints:
    async def test_get_trends_returns_200_with_mocked_agent(self, api_client):
        """Trends endpoint must return 200 when agent is mocked — never 500."""
        from agents.state import AgentResponse, IntentType
        mock_response = AgentResponse(
            success=True,
            intent=IntentType.MARKET_TRENDS,
            summary="Python is trending.",
            results=[],
            metadata={"top_skills": ["Python", "Rust"]},
        )
        with patch(
            "api.routers.trends.Orchestrator.process_query",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await api_client.post(
                "/api/v1/trends",
                json={"query": "Market trends in AI", "days": 30},
            )
        assert response.status_code == status.HTTP_200_OK

    async def test_get_trends_rejects_empty_query(self, api_client):
        response = await api_client.post(
            "/api/v1/trends",
            json={"query": "", "days": 30},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_get_top_skills_returns_200_with_mocked_db(self, api_client):
        """Top skills endpoint must return 200 with mocked storage — never 500."""
        mock_skills = [{"skill": "Python", "count": 500}, {"skill": "SQL", "count": 420}]
        with patch(
            "api.routers.trends.get_job_repository",
        ) as mock_repo_dep:
            mock_repo = AsyncMock()
            mock_repo.get_top_skills = AsyncMock(return_value=mock_skills)
            mock_repo_dep.return_value = mock_repo
            response = await api_client.get("/api/v1/trends/skills?days=30")
        assert response.status_code == status.HTTP_200_OK

    async def test_get_top_skills_rejects_negative_days(self, api_client):
        response = await api_client.get("/api/v1/trends/skills?days=-5")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


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

    async def test_match_rejects_empty_skills_list(self, api_client):
        """Candidate with no skills is semantically invalid."""
        response = await api_client.post(
            "/api/v1/recommend/match",
            json={"candidate": {"skills": []}, "limit": 10},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_match_returns_200_with_mocked_orchestrator(self, api_client):
        mock_response = _make_mock_search_response(n_results=3)
        with patch(
            "api.routers.recommend.Orchestrator.match_candidate_to_jobs",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await api_client.post(
                "/api/v1/recommend/match",
                json={
                    "candidate": {
                        "name": "Jane Doe",
                        "skills": ["Python", "FastAPI"],
                        "experience_years": 4,
                    },
                    "limit": 10,
                },
            )
        assert response.status_code == status.HTTP_200_OK


# ─────────────────────────────────────────────────────────────────────────────
# Ingest endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestEndpoints:
    async def test_trigger_ingestion_returns_202_with_mocked_pipeline(self, api_client):
        """
        Trigger must return 202 Accepted when the pipeline is mocked.
        It must NEVER return 500 — if Airflow is down the API should
        return a clear error code (e.g. 503), not an uncaught server error.
        """
        with patch("api.routers.ingest.run_ingestion_pipeline", new_callable=AsyncMock):
            response = await api_client.post(
                "/api/v1/ingest/trigger",
                json={
                    "roles": ["Python Engineer"],
                    "locations": ["Remote"],
                    "max_results_per_query": 5,
                },
            )
        # Accept 200 or 202; 500 is never acceptable
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ]

    async def test_trigger_ingestion_rejects_empty_roles(self, api_client):
        """Empty roles list should be rejected at the schema level."""
        response = await api_client.post(
            "/api/v1/ingest/trigger",
            json={"roles": [], "locations": ["Remote"], "max_results_per_query": 5},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_trigger_ingestion_rejects_zero_max_results(self, api_client):
        response = await api_client.post(
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
