"""
tests/test_application_typed_role.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A role found somewhere we never indexed can be tracked: what the user typed, with no job.
The agenda and the briefing name it by what was typed, and the Interview Lab link for a
posting we hold carries the posting.

Runs on in-memory SQLite (``tests/outreach_support.py``). Every role and company is fictional.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import create_access_token
from services import applications, copilot
from services.applications import day_agenda
from storage.database import get_db_dep
from storage.models import ApplicationStatus, JobApplication, User
from tests.outreach_support import make_user, outreach_db  # noqa: F401

Factory = async_sessionmaker[AsyncSession]
URL = "/api/v1/applications/"


@pytest.fixture
def db(outreach_db: Factory, monkeypatch: pytest.MonkeyPatch) -> Factory:  # noqa: F811
    monkeypatch.setattr(applications, "AsyncSessionLocal", outreach_db)
    return outreach_db


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


async def new_user(db: Factory) -> User:
    async with db() as session:
        return await make_user(session)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id), "email": user.email, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


async def test_a_typed_role_is_tracked(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    response = await client.post(
        URL,
        headers=auth(user),
        json={
            "role_title": "  Platform   Engineer ",
            "company_name": "Lumen Works",
            "posting_url": "https://jobs.example.com/lumen/123",
            "status": "applied",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["job_id"] is None and body["job"] is None
    assert (body["role_title"], body["company_name"]) == ("Platform Engineer", "Lumen Works")
    assert body["status"] == "applied"

    listed = (await client.get(URL, headers=auth(user))).json()
    assert [row["role_title"] for row in listed["applications"]] == ["Platform Engineer"]


async def test_two_typed_roles_can_sit_side_by_side(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    for title in ("Platform Engineer", "Data Engineer"):
        response = await client.post(
            URL, headers=auth(user), json={"role_title": title, "company_name": "Lumen Works"}
        )
        assert response.status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"role_title": "Platform Engineer"},
        {"company_name": "Lumen Works"},
        {"role_title": "Platform Engineer", "company_name": "   "},
        {"role_title": "Platform Engineer", "company_name": "Lumen Works", "posting_url": "javascript:alert(1)"},
        {"role_title": "Platform Engineer", "company_name": "Lumen Works", "posting_url": "https://a b.example"},
    ],
)
async def test_a_typed_role_needs_a_title_a_company_and_a_web_link(
    db: Factory, client: AsyncClient, body: dict[str, str]
) -> None:
    user = await new_user(db)
    assert (await client.post(URL, headers=auth(user), json=body)).status_code == 422


async def test_the_agenda_names_a_typed_role(db: Factory) -> None:
    now = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
    user = await new_user(db)
    async with db() as session:
        session.add(JobApplication(
            user_id=user.id,
            status=ApplicationStatus.INTERVIEW,
            role_title="Platform Engineer",
            company_name="Lumen Works",
            interview_at=now + timedelta(hours=5),
        ))
        await session.commit()

    [item] = await day_agenda(user_id=str(user.id), within_days=7, now=now)
    assert (item["role"], item["company"], item["job_id"]) == ("Platform Engineer", "Lumen Works", None)
    assert copilot.interview_prep_href(item) == "/interview"


def test_the_prep_link_carries_the_posting() -> None:
    href = copilot.interview_prep_href({"job_id": "j-1", "role": "Data & ML Engineer", "company": "Lumen Works"})
    parts = urlsplit(href)
    assert parts.path == "/interview"
    assert parse_qs(parts.query) == {"job": ["j-1"], "role": ["Data & ML Engineer"], "company": ["Lumen Works"]}
