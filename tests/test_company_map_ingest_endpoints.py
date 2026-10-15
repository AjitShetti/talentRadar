"""
tests/test_company_map_ingest_endpoints.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Who may run the company-map refresh and the news ingest.

Both make outbound requests (homepages, Nominatim, Overpass, RSS feeds) and
one spends LLM budget, so they are closed the way ``/ingest/reverify`` is:
an admin, or a scheduler holding the shared cron token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config.settings import get_settings

REFRESH = "/api/v1/ingest/company-map/refresh"
NEWS = "/api/v1/ingest/news"
RUN_STEP = "services.company_map.refresh.run_step"
INGEST_NEWS = "services.company_map.news_ingest.ingest_news"


@pytest.fixture
def cron_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "cron_token", "a-long-shared-secret")
    return "a-long-shared-secret"


async def test_refresh_without_the_token_is_refused(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "osm"})) as run:
        response = await api_client.post(f"{REFRESH}?step=osm")
    assert response.status_code == 403
    assert run.await_count == 0


async def test_refresh_with_the_token_runs_the_step(api_client, cron_token):
    counts = {"step": "geocode", "due": 3, "placed": 2}
    with patch(RUN_STEP, AsyncMock(return_value=counts)) as run:
        response = await api_client.post(
            f"{REFRESH}?step=geocode&limit=40", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == counts
    assert run.await_args.args == ("geocode", 40)


@pytest.mark.parametrize(
    ("step", "limit"), [("yc", 2000), ("github_orgs", 60), ("wikidata", 2000), ("github", 30)]
)
async def test_refresh_accepts_every_scheduled_step(api_client, cron_token, step, limit):
    counts = {"step": step, "seen": 1, "created": 1, "updated": 0, "skipped": 0}
    with patch(RUN_STEP, AsyncMock(return_value=counts)) as run:
        response = await api_client.post(
            f"{REFRESH}?step={step}&limit={limit}", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == counts
    assert run.await_args.args == (step, limit)


async def test_refresh_rejects_an_unknown_step(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(
            f"{REFRESH}?step=everything", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 422
    assert run.await_count == 0


async def test_refresh_ordinary_user_may_not(auth_client):
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await auth_client.post(f"{REFRESH}?step=sites")
    assert response.status_code == 403
    assert run.await_count == 0


async def test_refresh_admin_may(admin_client):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "sites"})):
        response = await admin_client.post(f"{REFRESH}?step=sites")
    assert response.status_code == 200


async def test_news_without_the_token_is_refused(api_client, cron_token):
    with patch(INGEST_NEWS, AsyncMock(return_value={"new": 0})) as run:
        response = await api_client.post(NEWS)
    assert response.status_code == 403
    assert run.await_count == 0


async def test_news_with_the_token_ingests(api_client, cron_token):
    stats = {"fetched": 40, "new": 12, "funding": 3}
    with patch(INGEST_NEWS, AsyncMock(return_value=stats)) as run:
        response = await api_client.post(NEWS, headers={"X-Cron-Token": cron_token})
    assert response.status_code == 200
    assert response.json() == stats
    assert run.await_count == 1
