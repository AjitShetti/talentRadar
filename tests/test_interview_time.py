"""
tests/test_interview_time.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
No surface states an interview time the user did not enter.

``interview_scheduled_at`` is stamped when a status moves to interview, so it says when the
row moved. ``interview_at`` is what the user typed, and the agenda and the briefing read only
that. A role at interview stage with no time ahead says so, naming no time and no day.

Everything runs on in-memory SQLite and the clock is passed in or pinned. Every role and
company is fictional.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import create_access_token
from services import applications, copilot
from services.applications import day_agenda, update_application
from storage.database import get_db_dep
from storage.models import ApplicationStatus, JobApplication, User
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_job,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)  # Mon 2 Nov, 11:30 IST
VERSIONS = Path(__file__).resolve().parents[1] / "storage" / "migrations" / "versions"


def hours(count: float) -> datetime:
    return NOW + timedelta(hours=count)


@pytest.fixture
def db(outreach_db: Factory, monkeypatch: pytest.MonkeyPatch) -> Factory:  # noqa: F811
    monkeypatch.setattr(applications, "AsyncSessionLocal", outreach_db)
    monkeypatch.setattr(copilot, "AsyncSessionLocal", outreach_db)
    monkeypatch.setattr(copilot, "_now", lambda: NOW)
    return outreach_db


async def seed(db: Factory, **fields: Any) -> tuple[User, JobApplication]:
    """A user and one application for a fictional role, in the stage a test asks for."""
    async with db() as session:
        user = await make_user(session)
        company = await make_company(session, "Lumen Works")
        job = await make_job(session, company, "Platform Engineer")
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "user_id": user.id,
            "job_id": job.id,
            "status": ApplicationStatus.INTERVIEW,
        }
        values.update(fields)
        app = JobApplication(**values)
        session.add(app)
        await session.commit()
    return user, app


async def agenda_for(user: User, *, within_days: int = 7) -> list[dict[str, Any]]:
    return await day_agenda(user_id=str(user.id), within_days=within_days, now=NOW)


# --- the agenda -------------------------------------------------------------------


async def test_a_status_stamp_alone_puts_nothing_on_the_agenda(db: Factory) -> None:
    # The bug: moving a role to interview made it "today at <the moment it moved>".
    user, _ = await seed(db, interview_scheduled_at=NOW)
    assert await agenda_for(user) == []


async def test_the_agenda_is_what_the_user_entered(db: Factory) -> None:
    entered = hours(3)
    user, app = await seed(db, interview_scheduled_at=hours(-48), interview_at=entered)

    [item] = await agenda_for(user)

    assert item["application_id"] == str(app.id)
    assert datetime.fromisoformat(item["scheduled_at"]).replace(tzinfo=UTC) == entered
    assert item["days_away"] == 0
    assert item["when_label"] == "Today"
    assert item["time_label"] == "Mon 2 Nov · 14:30 IST"


async def test_a_day_is_an_ist_day(db: Factory) -> None:
    # 00:30 IST on Tuesday is tomorrow, though at 11:30 IST on Monday both share a UTC date.
    user, _ = await seed(db, interview_at=datetime(2026, 11, 2, 19, 0, tzinfo=UTC))
    [item] = await agenda_for(user)
    assert (item["days_away"], item["when_label"]) == (1, "Tomorrow")


async def test_an_interview_stays_on_while_it_may_be_running(db: Factory) -> None:
    user, _ = await seed(db, interview_at=hours(-1))
    assert [item["days_away"] for item in await agenda_for(user)] == [0]


async def test_an_interview_that_is_over_leaves_the_agenda(db: Factory) -> None:
    user, _ = await seed(db, interview_at=hours(-3))
    assert await agenda_for(user) == []


async def test_a_time_beyond_the_window_is_not_on_the_agenda(db: Factory) -> None:
    user, _ = await seed(db, interview_at=hours(24 * 9))
    assert await agenda_for(user) == []


@pytest.mark.parametrize(
    "status",
    [ApplicationStatus.OFFER, ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN],
)
async def test_a_finished_role_is_off_the_agenda(db: Factory, status: ApplicationStatus) -> None:
    user, _ = await seed(db, status=status, interview_at=hours(3))
    assert await agenda_for(user) == []


async def test_another_users_interview_is_not_on_mine(db: Factory) -> None:
    await seed(db, interview_at=hours(3))
    async with db() as session:
        me = await make_user(session)
    assert await agenda_for(me) == []


# --- the status move --------------------------------------------------------------


async def test_moving_to_interview_stamps_the_move_and_enters_no_time(db: Factory) -> None:
    user, app = await seed(db, status=ApplicationStatus.SCREENING)

    payload = await update_application(
        application_id=str(app.id), user_id=str(user.id), status="interview"
    )

    assert payload is not None
    assert payload["reached_interview_at"] is not None
    assert payload["interview_at"] is None
    assert "interview_scheduled_at" not in payload
    assert await agenda_for(user) == []


async def test_moving_to_interview_keeps_a_time_already_entered(db: Factory) -> None:
    entered = hours(5)
    user, app = await seed(db, status=ApplicationStatus.SCREENING, interview_at=entered)

    payload = await update_application(
        application_id=str(app.id), user_id=str(user.id), status="interview"
    )

    assert payload is not None
    assert datetime.fromisoformat(payload["interview_at"]).replace(tzinfo=UTC) == entered


# --- the briefing -----------------------------------------------------------------


async def scheduled_cards(user: User) -> list[dict[str, Any]]:
    return await copilot._scheduled_interview_cards(str(user.id))


async def application_cards(user: User) -> list[dict[str, Any]]:
    return await copilot._application_cards(str(user.id))


async def test_the_card_says_the_entered_time_in_ist(db: Factory) -> None:
    entered = hours(3)
    user, _ = await seed(db, interview_scheduled_at=NOW, interview_at=entered)

    [card] = await scheduled_cards(user)

    assert card["kind"] == "scheduled_interview"
    assert "Scheduled for Mon 2 Nov · 14:30 IST." in card["detail"]
    assert "today" in card["title"]
    assert datetime.fromisoformat(card["meta"]["scheduled_at"]).replace(tzinfo=UTC) == entered


async def test_no_entered_time_means_no_scheduled_card(db: Factory) -> None:
    user, _ = await seed(db, interview_scheduled_at=NOW)
    assert await scheduled_cards(user) == []


async def test_interview_stage_names_no_time_and_no_day(db: Factory) -> None:
    user, app = await seed(db, interview_scheduled_at=NOW)

    [card] = await application_cards(user)

    assert card["id"] == f"interview_stage:{app.id}"
    assert card["kind"] == "interview_stage"
    assert card["title"] == "Platform Engineer at Lumen Works is at interview stage"
    words = f"{card['title']} {card['detail']}".lower()
    assert not re.search(r"\d", words)
    for word in ("today", "tomorrow", "tonight", "scheduled"):
        assert word not in words
    assert not {"scheduled_at", "days_away"} & set(card["meta"])
    assert card["actions"][0]["href"] == "/applications"


async def test_an_upcoming_time_replaces_the_stage_card(db: Factory) -> None:
    user, _ = await seed(db, interview_at=hours(24 * 5))
    assert await application_cards(user) == []


async def test_a_time_that_has_passed_asks_again(db: Factory) -> None:
    # A next round has no time on it, and the one that passed says nothing about it.
    user, _ = await seed(db, interview_at=hours(-3))
    assert [card["kind"] for card in await application_cards(user)] == ["interview_stage"]


async def test_a_stale_role_is_said_once(db: Factory) -> None:
    user, _ = await seed(db, updated_at=datetime.now(tz=UTC) - timedelta(days=30))
    assert [card["kind"] for card in await application_cards(user)] == ["stale_application"]


async def test_only_interview_stage_is_asked(db: Factory) -> None:
    user, _ = await seed(db, status=ApplicationStatus.APPLIED)
    assert await application_cards(user) == []


async def test_the_headline_counts_only_entered_times() -> None:
    stage = copilot._card(card_id="i", kind="interview_stage", title="t", detail="d")
    entered = copilot._card(
        card_id="s", kind="scheduled_interview", title="t", detail="d", meta={"days_away": 0}
    )
    assert "today" not in copilot._headline([stage], {"total_applications": 1})
    assert "1 interview today" in copilot._headline([entered, stage], {"total_applications": 1})


# --- the PATCH --------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db: Factory) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async def _session() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db_dep] = _session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db_dep, None)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id), "email": user.email, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


def instant(body: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(body["interview_at"]).replace(tzinfo=UTC)


async def test_patch_stores_the_instant_that_was_typed(db: Factory, client: AsyncClient) -> None:
    user, app = await seed(db)

    response = await client.patch(
        f"/api/v1/applications/{app.id}",
        json={"interview_at": "2026-11-03T14:30:00+05:30"},
        headers=auth(user),
    )

    assert response.status_code == 200
    assert instant(response.json()) == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    listed = (await client.get("/api/v1/applications/", headers=auth(user))).json()
    assert instant(listed["applications"][0]) == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


async def test_patch_without_the_key_leaves_it_alone(db: Factory, client: AsyncClient) -> None:
    user, app = await seed(db, interview_at=hours(3))

    response = await client.patch(
        f"/api/v1/applications/{app.id}", json={"status": "screening"}, headers=auth(user)
    )

    assert response.status_code == 200
    assert instant(response.json()) == hours(3)


async def test_patch_null_clears_it(db: Factory, client: AsyncClient) -> None:
    user, app = await seed(db, interview_at=hours(3))

    response = await client.patch(
        f"/api/v1/applications/{app.id}", json={"interview_at": None}, headers=auth(user)
    )

    assert response.status_code == 200
    assert response.json()["interview_at"] is None


async def test_patch_refuses_a_time_without_an_offset(db: Factory, client: AsyncClient) -> None:
    user, app = await seed(db)

    response = await client.patch(
        f"/api/v1/applications/{app.id}",
        json={"interview_at": "2026-11-03T14:30:00"},
        headers=auth(user),
    )

    assert response.status_code == 422


async def test_patch_on_another_users_application_is_a_404(
    db: Factory, client: AsyncClient
) -> None:
    _, app = await seed(db)
    async with db() as session:
        other = await make_user(session)

    response = await client.patch(
        f"/api/v1/applications/{app.id}",
        json={"interview_at": "2026-11-03T14:30:00+05:30"},
        headers=auth(other),
    )

    assert response.status_code == 404


# --- the migration ----------------------------------------------------------------


def test_022_follows_021_and_only_adds_the_column() -> None:
    path = VERSIONS / "022_application_interview_at.py"
    spec = importlib.util.spec_from_file_location("migration_022", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "022_application_interview_at"
    assert module.down_revision == "021_practice_design"
    text = path.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS interview_at TIMESTAMPTZ" in text
    assert "DROP COLUMN IF EXISTS interview_at" in text
    assert "UPDATE " not in text  # no stamp is copied in: none of them is a time anybody entered


def test_022_is_the_only_head() -> None:
    revisions: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision(?:: str)? = ["\']([^"\']+)', source, re.M)
        down = re.search(r'^down_revision(?:: [^=]+)? = ["\']([^"\']+)', source, re.M)
        if revision:
            revisions[revision.group(1)] = down.group(1) if down else None
    assert set(revisions) - set(revisions.values()) == {"022_application_interview_at"}
