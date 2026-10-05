"""
tests/test_reverify_endpoint.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Who may trigger a liveness re-check.

The endpoint makes ~150 outbound requests to employers' ATS boards, so it is
closed by default: an admin, or a scheduler holding the shared cron token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config.settings import get_settings

URL = "/api/v1/ingest/reverify"
COUNTS = {"checked": 3, "open": 2, "closed": 1, "errors": 0}


@pytest.fixture
def cron_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "cron_token", "a-long-shared-secret")
    return "a-long-shared-secret"


async def test_anonymous_caller_is_refused(api_client, cron_token):
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)) as run:
        response = await api_client.post(URL)
    assert response.status_code == 403
    assert run.await_count == 0


async def test_wrong_token_is_refused(api_client, cron_token):
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)) as run:
        response = await api_client.post(URL, headers={"X-Cron-Token": "guess"})
    assert response.status_code == 403
    assert run.await_count == 0


async def test_right_token_runs_the_batch(api_client, cron_token):
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)) as run:
        response = await api_client.post(URL, headers={"X-Cron-Token": cron_token})
    assert response.status_code == 200
    assert response.json() == COUNTS
    assert run.await_count == 1


async def test_no_token_configured_means_no_token_is_accepted(api_client, monkeypatch):
    # An unset secret must not be matched by an empty header.
    monkeypatch.setattr(get_settings(), "cron_token", None)
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)) as run:
        response = await api_client.post(URL, headers={"X-Cron-Token": ""})
    assert response.status_code == 403
    assert run.await_count == 0


async def test_admin_may_run_it_without_the_token(admin_client):
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)):
        response = await admin_client.post(URL)
    assert response.status_code == 200


async def test_ordinary_user_may_not(auth_client):
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)) as run:
        response = await auth_client.post(URL)
    assert response.status_code == 403
    assert run.await_count == 0


async def test_limit_is_forwarded_and_capped(api_client, cron_token):
    with patch("services.liveness.reverify_batch", AsyncMock(return_value=COUNTS)) as run:
        ok = await api_client.post(f"{URL}?limit=40", headers={"X-Cron-Token": cron_token})
        too_big = await api_client.post(f"{URL}?limit=5000", headers={"X-Cron-Token": cron_token})
    assert ok.status_code == 200
    assert run.await_args.kwargs == {"limit": 40}
    assert too_big.status_code == 422
