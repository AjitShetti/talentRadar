"""
tests/test_outreach_pack.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Building and reading the daily outreach pack (``services/outreach/pack.py``) and its
two read routes. Real services on in-memory SQLite; no model and no network.

Every person and company here is fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import create_access_token
from api.main import app
from services.cache_backend import CacheBackend
from services.outreach import pack, people, suggest, threads, touches
from storage.models import (
    Company,
    JobApplication,
    OutreachPack,
    OutreachPackItem,
    OutreachThread,
    Profile,
    User,
    UserSkill,
)
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_job,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 11:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
BACKEND = "Backend Engineer"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


async def _user(session: AsyncSession) -> User:
    user = await make_user(session)
    session.add(Profile(user_id=user.id, target_roles=[BACKEND]))
    session.add(UserSkill(user_id=user.id, skill_name="Python"))
    await session.commit()
    return user


async def _funded(session: AsyncSession, name: str, days: int, **kw: Any) -> Company:
    """A listed company that raised ``days`` before ``NOW``, with a named founder."""
    company = await make_company(session, name, last_funding_at=NOW - timedelta(days=days), **kw)
    await make_contact(session, company, "founder", f"{name.split()[0]} Founder")
    return company


async def _seed(factory: Factory, *names: str) -> tuple[User, list[Company]]:
    async with factory() as session:
        user = await _user(session)
        companies = [await _funded(session, name, 10 + i) for i, name in enumerate(names)]
    return user, companies


def _ids(payload: dict[str, Any]) -> list[str]:
    return [c["company_id"] for c in payload["companies"]]


def _item_ids(payload: dict[str, Any]) -> list[str]:
    return [item["id"] for c in payload["companies"] for item in c["items"]]


async def test_first_get_builds_second_reads_same_pack(outreach_db: Factory) -> None:
    user, (a, b) = await _seed(outreach_db, "Alder Labs", "Birch Works")
    first = await pack.get_pack(str(user.id), now=NOW)
    second = await pack.get_pack(str(user.id), now=NOW + timedelta(hours=2))

    assert first["day"] == "2026-10-15"
    assert _ids(first) == [str(a.id), str(b.id)]
    assert first == second
    company = first["companies"][0]
    assert company["name"] == "Alder Labs"
    assert company["reasons"]
    (item,) = company["items"]
    assert item["kind"] == "first"
    assert item["person"] == {"name": "Alder Founder", "title": None, "kind": "founder"}
    assert item["channel"] == "linkedin" and item["format"] == "linkedin_note"
    assert item["linkedin_url"].startswith("https://www.linkedin.com/")
    assert item["message"] is None and item["status"] == "new"
    assert first["counts"] == {"companies": 2, "messages_ready": 0}
    async with outreach_db() as session:
        packs = (await session.execute(select(func.count()).select_from(OutreachPack))).scalar()
    assert packs == 1


async def test_pack_rolls_over_at_ist_midnight_and_unseen_does_not_count(
    outreach_db: Factory,
) -> None:
    user, (a,) = await _seed(outreach_db, "Alder Labs")
    late = datetime(2026, 10, 15, 18, 29, tzinfo=UTC)  # 23:59 IST, 15 Oct
    after_midnight = datetime(2026, 10, 15, 18, 40, tzinfo=UTC)  # 00:10 IST, 16 Oct

    morning = await pack.get_pack(str(user.id), now=NOW)
    evening = await pack.get_pack(str(user.id), now=late)
    tomorrow = await pack.get_pack(str(user.id), now=after_midnight)

    assert morning == evening
    assert tomorrow["day"] == "2026-10-16"
    # Built but never displayed: the company is not held back.
    assert _ids(tomorrow) == [str(a.id)]
    assert _item_ids(tomorrow) != _item_ids(morning)


async def test_seen_untouched_company_not_repeated_within_14_days(outreach_db: Factory) -> None:
    user, (_, b) = await _seed(outreach_db, "Alder Labs", "Birch Works")
    today = await pack.get_pack(str(user.id), now=NOW)
    seen = [item["id"] for item in today["companies"][0]["items"]]
    await pack.mark_seen(str(user.id), seen, now=NOW)

    later = await pack.get_pack(str(user.id), now=NOW + timedelta(days=13))
    assert _ids(later) == [str(b.id)]


async def test_seen_company_returns_after_14_days(outreach_db: Factory) -> None:
    user, (a,) = await _seed(outreach_db, "Alder Labs")
    today = await pack.get_pack(str(user.id), now=NOW)
    await pack.mark_seen(str(user.id), _item_ids(today), now=NOW)

    assert _ids(await pack.get_pack(str(user.id), now=NOW + timedelta(days=15))) == [str(a.id)]


async def test_applied_company_excluded_30_days(outreach_db: Factory) -> None:
    user, (a, b) = await _seed(outreach_db, "Alder Labs", "Birch Works")
    async with outreach_db() as session:
        recent = await make_job(session, a, BACKEND, city="Bengaluru")
        old = await make_job(session, b, BACKEND, city="Bengaluru")
        session.add_all(
            [
                JobApplication(
                    user_id=user.id, job_id=recent.id, created_at=NOW - timedelta(days=5)
                ),
                JobApplication(user_id=user.id, job_id=old.id, created_at=NOW - timedelta(days=31)),
            ]
        )
        await session.commit()

    assert _ids(await pack.get_pack(str(user.id), now=NOW)) == [str(b.id)]


async def test_threaded_and_dismissed_companies_excluded(outreach_db: Factory) -> None:
    user, (a, b, c) = await _seed(outreach_db, "Alder Labs", "Birch Works", "Cedar Systems")
    async with outreach_db() as session:
        session.add(
            OutreachThread(user_id=user.id, company_id=a.id, company_name=a.name, source="drawer")
        )
        await session.commit()
    await suggest.dismiss_suggestion(str(user.id), str(b.id))

    assert _ids(await pack.get_pack(str(user.id), now=NOW)) == [str(c.id)]


async def test_get_pack_never_calls_model(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.llm as llm
    from services.outreach import drafts

    async def no_model(*args: object, **kwargs: object) -> str:
        raise AssertionError("GET /outreach/pack called the model")

    monkeypatch.setattr(llm, "_chat", no_model, raising=False)
    monkeypatch.setattr(drafts, "_chat", no_model)
    user, _ = await _seed(outreach_db, "Alder Labs")

    payload = await pack.get_pack(str(user.id), now=NOW)
    assert all(item["message"] is None for c in payload["companies"] for item in c["items"])


async def test_follow_up_items_added_for_due_people(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await _user(session)
        company = await make_company(session, "Dune Robotics")
        contact = await make_contact(session, company, "founder", "Dina Sample")
    summary, _ = await threads.create_thread(
        str(user.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    detail = await people.add_person(str(user.id), summary["id"], contact_id=str(contact.id))
    person_id = detail["people"][0]["id"]
    await touches.record_touch(
        str(user.id),
        summary["id"],
        person_id=person_id,
        channel="email",
        direction="out",
        happened_at=NOW - timedelta(days=6),
        now=NOW - timedelta(days=6),
    )

    first = await pack.get_pack(str(user.id), now=NOW)
    again = await pack.get_pack(str(user.id), now=NOW + timedelta(hours=1))

    (item,) = first["follow_ups"]
    assert item["kind"] == "follow_up"
    assert item["thread_id"] == summary["id"]
    assert item["person"]["name"] == "Dina Sample"
    assert (item["channel"], item["format"]) == ("email", "email")
    assert again["follow_ups"] == first["follow_ups"]


async def test_guessed_address_in_payload_not_in_item_row(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await _user(session)
        company = await make_company(
            session,
            "Fictional Labs",
            domain="fictional.test",
            last_funding_at=NOW - timedelta(days=9),
        )
        await make_contact(
            session,
            company,
            "founder",
            "Asha Example",
            email="asha.example@fictional.test",
            email_source_url="https://fictional.test/about",
        )
        await make_contact(session, company, "engineer", "Ravi Sample")

    payload = await pack.get_pack(str(user.id), now=NOW)
    items = {item["person"]["name"]: item for item in payload["companies"][0]["items"]}
    assert items["Asha Example"]["address"] == {
        "value": "asha.example@fictional.test",
        "basis": "found",
        "source_host": "fictional.test",
        "learned_from": None,
    }
    assert items["Ravi Sample"]["channel"] == "email"
    assert items["Ravi Sample"]["address"] == {
        "value": "ravi.sample@fictional.test",
        "basis": "guessed",
        "source_host": None,
        "learned_from": "asha.example@fictional.test",
    }

    async with outreach_db() as session:
        rows = list((await session.execute(select(OutreachPackItem))).scalars())
    stored = " ".join(
        str(getattr(row, column.key)) for row in rows for column in OutreachPackItem.__table__.c
    )
    assert "ravi.sample@" not in stored


def _client(user_id: str | None) -> AsyncClient:
    headers = {}
    if user_id is not None:
        token = create_access_token(
            data={"sub": user_id, "email": f"{user_id[:8]}@example.com", "role": "user"}
        )
        headers = {"Authorization": f"Bearer {token}"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


async def test_other_users_items_cannot_be_marked_seen(outreach_db: Factory) -> None:
    owner, _ = await _seed(outreach_db, "Alder Labs")
    async with outreach_db() as session:
        other = await make_user(session)

    async with _client(str(owner.id)) as client:
        response = await client.get("/api/v1/outreach/pack")
    assert response.status_code == 200
    item_ids = _item_ids(response.json())

    async with _client(str(other.id)) as client:
        refused = await client.post("/api/v1/outreach/pack/seen", json={"item_ids": item_ids})
        unknown = await client.post(
            "/api/v1/outreach/pack/seen", json={"item_ids": [str(uuid.uuid4())]}
        )
    assert refused.status_code == 404
    assert unknown.status_code == 404
    async with outreach_db() as session:
        seen = (await session.execute(select(OutreachPackItem.seen_at))).scalars().all()
    assert seen == [None]

    async with _client(str(owner.id)) as client:
        ok = await client.post("/api/v1/outreach/pack/seen", json={"item_ids": item_ids})
        too_many = await client.post(
            "/api/v1/outreach/pack/seen", json={"item_ids": [str(uuid.uuid4())] * 21}
        )
    assert ok.status_code == 204
    assert too_many.status_code == 422


async def test_pack_route_requires_auth() -> None:
    async with _client(None) as client:
        response = await client.get("/api/v1/outreach/pack")
        seen = await client.post("/api/v1/outreach/pack/seen", json={"item_ids": []})
    assert response.status_code in (401, 403)
    assert seen.status_code in (401, 403)
