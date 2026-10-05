"""
tests/test_liveness_in_search.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Search results carry the liveness verdict, and closed roles stay out of the
way unless someone asks for them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from agents.state import AgentResponse, IntentType, RetrievalResult
from services import liveness

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
OPEN_ID = str(uuid.uuid4())
CLOSED_ID = str(uuid.uuid4())
LIVE_ID = "greenhouse-live-123"  # a just-scraped result, not yet a stored row

OPEN = {"state": "verified_open", "headline": "Confirmed open 2 hours ago", "evidence": [], "open_days": 4}
CLOSED = {"state": "closed", "headline": "Closed at the source", "evidence": [], "open_days": 80}


def row(job_id: str, **overrides: Any) -> tuple[SimpleNamespace, int]:
    base: dict[str, Any] = {
        "id": uuid.UUID(job_id),
        "source": "greenhouse:stripe",
        "source_posted_at": NOW - timedelta(days=4),
        "first_seen_at": NOW - timedelta(days=4),
        "last_seen_at": NOW - timedelta(hours=2),
        "last_verified_at": NOW - timedelta(hours=2),
        "closed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base), 0


# ── loading verdicts for a page of results ───────────────────────────────────


async def test_verdicts_are_keyed_by_job_id(monkeypatch):
    async def fake_rows(ids: list[uuid.UUID]) -> list[tuple[Any, int]]:
        return [row(OPEN_ID), row(CLOSED_ID, closed_at=NOW)]

    monkeypatch.setattr(liveness, "_load_liveness_rows", fake_rows)
    verdicts = await liveness.load_verdicts([OPEN_ID, CLOSED_ID], now=NOW)
    assert verdicts[OPEN_ID]["state"] == "verified_open"
    assert verdicts[CLOSED_ID]["state"] == "closed"


async def test_ids_that_are_not_stored_rows_are_skipped_without_a_query(monkeypatch):
    asked: list[list[uuid.UUID]] = []

    async def fake_rows(ids: list[uuid.UUID]) -> list[tuple[Any, int]]:
        asked.append(ids)
        return []

    monkeypatch.setattr(liveness, "_load_liveness_rows", fake_rows)
    assert await liveness.load_verdicts([LIVE_ID, ""], now=NOW) == {}
    assert asked == []


async def test_a_database_failure_yields_no_verdicts_rather_than_an_error(monkeypatch):
    async def broken(ids: list[uuid.UUID]) -> list[tuple[Any, int]]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(liveness, "_load_liveness_rows", broken)
    assert await liveness.load_verdicts([OPEN_ID], now=NOW) == {}


# ── the semantic search endpoint ─────────────────────────────────────────────


def agent_response() -> AgentResponse:
    return AgentResponse(
        success=True,
        intent=IntentType.SEARCH_JOBS,
        results=[
            RetrievalResult(job_id=OPEN_ID, title="Backend Engineer", company="Stripe"),
            RetrievalResult(job_id=CLOSED_ID, title="Data Engineer", company="Figma"),
            RetrievalResult(job_id=LIVE_ID, title="Platform Engineer", company="Postman"),
        ],
        metadata={"total_found": 3},
    )


async def search(api_client, **body: Any) -> dict[str, Any]:
    with (
        patch("api.routers.search.Orchestrator") as orchestrator,
        patch(
            "services.liveness.load_verdicts",
            AsyncMock(return_value={OPEN_ID: OPEN, CLOSED_ID: CLOSED}),
        ),
    ):
        orchestrator.return_value.process_query = AsyncMock(return_value=agent_response())
        response = await api_client.post(
            "/api/v1/search/semantic", json={"query": "engineer", **body}
        )
    assert response.status_code == 200
    return response.json()


async def test_results_carry_their_verdict(api_client):
    data = await search(api_client)
    by_id = {job["id"]: job for job in data["results"]}
    assert by_id[OPEN_ID]["liveness"]["state"] == "verified_open"


async def test_a_result_with_no_stored_row_has_no_verdict(api_client):
    data = await search(api_client)
    by_id = {job["id"]: job for job in data["results"]}
    assert by_id[LIVE_ID]["liveness"] is None


async def test_closed_roles_are_hidden_by_default_and_counted(api_client):
    data = await search(api_client)
    assert CLOSED_ID not in {job["id"] for job in data["results"]}
    assert data["total_found"] == 2
    assert data["closed_hidden"] == 1


async def test_closed_roles_are_returned_when_asked_for(api_client):
    data = await search(api_client, include_closed=True)
    by_id = {job["id"]: job for job in data["results"]}
    assert by_id[CLOSED_ID]["liveness"]["state"] == "closed"
    assert data["closed_hidden"] == 0
