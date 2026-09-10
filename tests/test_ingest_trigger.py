"""
tests/test_ingest_trigger.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The admin ingestion trigger has to *persist*.

``RealtimeScraperEngine.search_all`` fans scrapers out and caches the result
in Redis for the live-search UI; it never touches Postgres. Wiring the trigger
to it meant an admin got "discovered 120 jobs" while the ``jobs`` table stayed
empty — the failure that left the deployed Find Roles page blank. The trigger
must go through ``dispatch_ingestion``, the parse → persist → embed path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
class TestIngestTriggerPersists:

    async def test_trigger_runs_the_persisting_pipeline(self, admin_client):
        """The handler must call dispatch_ingestion, not the cache-only engine."""
        fake = AsyncMock(return_value={
            "run_id": "run-1", "fetched": 40, "parsed": 38,
            "inserted": 30, "updated": 8, "skipped": 0, "embedded": 30,
            "sources": {"greenhouse": 40},
        })
        with patch("ingestion.dispatcher.dispatch_ingestion", fake):
            response = await admin_client.post(
                "/api/v1/ingest/trigger",
                json={"roles": ["Backend Engineer"], "locations": ["Bengaluru"], "max_results_per_query": 10},
            )

        assert response.status_code == 200
        assert fake.await_count == 1, "trigger did not run the persisting pipeline"
        body = response.json()
        assert body["success"] is True
        assert "30" in body["message"], f"insert count missing from: {body['message']}"

    async def test_trigger_forwards_the_request_parameters(self, admin_client):
        """roles/locations/max_results_per_query are the caller's, not defaults."""
        fake = AsyncMock(return_value={"run_id": "r", "fetched": 0, "parsed": 0,
                                       "inserted": 0, "updated": 0, "skipped": 0,
                                       "embedded": 0, "sources": {}})
        with patch("ingestion.dispatcher.dispatch_ingestion", fake):
            await admin_client.post(
                "/api/v1/ingest/trigger",
                json={"roles": ["Data Scientist"], "locations": ["Pune"], "max_results_per_query": 7},
            )

        kwargs = fake.await_args.kwargs
        assert kwargs["roles"] == ["Data Scientist"]
        assert kwargs["locations"] == ["Pune"]
        assert kwargs["max_results_per_query"] == 7

    async def test_trigger_reports_failure_when_the_pipeline_raises(self, admin_client):
        """A failed run must not answer success:true."""
        with patch("ingestion.dispatcher.dispatch_ingestion", AsyncMock(side_effect=RuntimeError("boom"))):
            response = await admin_client.post(
                "/api/v1/ingest/trigger", json={"roles": ["QA"]},
            )

        assert response.status_code == 200
        assert response.json()["success"] is False
