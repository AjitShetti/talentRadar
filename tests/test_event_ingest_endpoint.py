"""
tests/test_event_ingest_endpoint.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Who may run the Events refresh, and that the route only passes the call on.

The refresh makes outbound requests (Luma, GDG, the Eventbrite API) and, for ``purge``,
deletes rows, so it is closed the way ``/ingest/reverify`` and the company-map refresh are:
an admin, or a scheduler holding the shared cron token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config.settings import get_settings

REFRESH = "/api/v1/ingest/events/refresh"
RUN_STEP = "services.events.refresh.run_step"


@pytest.fixture
def cron_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "cron_token", "a-long-shared-secret")
    return "a-long-shared-secret"


async def test_refresh_without_the_token_is_refused(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "luma"})) as run:
        response = await api_client.post(f"{REFRESH}?step=luma")
    assert response.status_code == 403
    assert run.await_count == 0


async def test_refresh_with_a_wrong_token_is_refused(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "luma"})) as run:
        response = await api_client.post(f"{REFRESH}?step=luma", headers={"X-Cron-Token": "nope"})
    assert response.status_code == 403
    assert run.await_count == 0


async def test_refresh_with_the_token_runs_the_step(api_client, cron_token):
    counts = {
        "step": "luma",
        "feeds": 3,
        "seen": 40,
        "created": 12,
        "updated": 20,
        "duplicates": 1,
        "skipped": 8,
        "listed": 9,
    }
    with patch(RUN_STEP, AsyncMock(return_value=counts)) as run:
        response = await api_client.post(
            f"{REFRESH}?step=luma&limit=26", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == counts
    assert run.await_count == 1
    assert run.await_args.args == ("luma", 26)
    assert run.await_args.kwargs == {"source": None}


@pytest.mark.parametrize(
    ("step", "limit"),
    [
        ("luma", 26),
        ("gdg", 30),
        ("eventbrite", 60),
        ("link", 500),
        ("relevance", 2000),
        ("prune", 1),
        ("purge", 1),
    ],
)
async def test_refresh_accepts_every_step(api_client, cron_token, step, limit):
    counts = {"step": step, "seen": 1}
    with patch(RUN_STEP, AsyncMock(return_value=counts)) as run:
        response = await api_client.post(
            f"{REFRESH}?step={step}&limit={limit}", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == counts
    assert run.await_args.args == (step, limit)


async def test_refresh_passes_the_source_on_for_a_purge(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "purge", "deleted": 4})) as run:
        response = await api_client.post(
            f"{REFRESH}?step=purge&source=eventbrite", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == {"step": "purge", "deleted": 4}
    assert run.await_args.args[0] == "purge"
    assert run.await_args.kwargs == {"source": "eventbrite"}


async def test_refresh_passes_a_failure_through_as_data(api_client, cron_token):
    failed = {"step": "gdg", "error": "RuntimeError: boom"}
    with patch(RUN_STEP, AsyncMock(return_value=failed)):
        response = await api_client.post(
            f"{REFRESH}?step=gdg", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200  # the workflow reads the body, not the status
    assert response.json() == failed


async def test_refresh_defaults_the_limit(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "link"})) as run:
        await api_client.post(f"{REFRESH}?step=link", headers={"X-Cron-Token": cron_token})
    assert run.await_args.args == ("link", 30)


async def test_refresh_rejects_an_unknown_step(api_client, cron_token):
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(
            f"{REFRESH}?step=everything", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 422
    assert run.await_count == 0


@pytest.mark.parametrize("limit", ["0", "-1", "2001", "many"])
async def test_refresh_rejects_a_limit_out_of_range(api_client, cron_token, limit):
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(
            f"{REFRESH}?step=luma&limit={limit}", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 422
    assert run.await_count == 0


async def test_refresh_ordinary_user_may_not(auth_client):
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await auth_client.post(f"{REFRESH}?step=luma")
    assert response.status_code == 403
    assert run.await_count == 0


async def test_refresh_admin_may(admin_client):
    with patch(RUN_STEP, AsyncMock(return_value={"step": "luma"})) as run:
        response = await admin_client.post(f"{REFRESH}?step=luma")
    assert response.status_code == 200
    assert run.await_count == 1
