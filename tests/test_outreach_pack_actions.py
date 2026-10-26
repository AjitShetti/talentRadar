"""
tests/test_outreach_pack_actions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Acting on the daily pack: "I sent it", "Skip" and "I applied". Real services on
in-memory SQLite; nothing is ever sent, only recorded.

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

import services.agent_memory as agent_memory
from api.auth import create_access_token
from api.main import app
from domain.outreach import MAX_OPEN_THREADS
from services.cache_backend import CacheBackend
from services.outreach import pack, people, threads, touches
from services.outreach.errors import Conflict, NotFound
from services.outreach.suggest import suggestion_card_id
from storage.models import (
    Company,
    CompanyContact,
    JobApplication,
    OutreachPackItem,
    OutreachPerson,
    OutreachThread,
    OutreachTouch,
    Profile,
    User,
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
GUESS = "ravi.sample@fictional.test"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


async def _seed(factory: Factory) -> tuple[User, Company, dict[str, dict[str, Any]]]:
    """A user whose pack holds one company with Asha (found address) and Ravi (guessed)."""
    async with factory() as session:
        user = await make_user(session)
        session.add(Profile(user_id=user.id, target_roles=["Backend Engineer"]))
        await session.commit()
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
    return user, company, items


async def _count(factory: Factory, model: Any) -> int:
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def _item(factory: Factory, item_id: str) -> OutreachPackItem:
    async with factory() as session:
        found = await session.get(OutreachPackItem, uuid.UUID(item_id))
    assert found is not None
    return found


async def test_sent_creates_thread_person_touch_and_marks_item(outreach_db: Factory) -> None:
    user, company, items = await _seed(outreach_db)
    asha = items["Asha Example"]

    result = await pack.mark_sent(
        str(user.id), asha["id"], message="Hi Asha, a short note.", subject="Hello", now=NOW
    )

    assert result["item"]["status"] == "sent"
    thread_id = result["item"]["thread_id"]
    async with outreach_db() as session:
        thread = await session.get(OutreachThread, uuid.UUID(thread_id))
        assert thread is not None
        assert (thread.company_id, thread.source) == (company.id, "suggestion")
        (person,) = (await session.execute(select(OutreachPerson))).scalars().all()
        (touch,) = (await session.execute(select(OutreachTouch))).scalars().all()
    assert person.name == "Asha Example"
    assert (touch.channel, touch.direction, touch.kind) == ("email", "out", "first")
    assert touch.body == "Hi Asha, a short note."
    stored = await _item(outreach_db, asha["id"])
    assert (stored.status, stored.message, stored.subject) == (
        "sent",
        "Hi Asha, a short note.",
        "Hello",
    )
    assert stored.acted_at is not None


async def test_sent_twice_is_one_touch(outreach_db: Factory) -> None:
    user, _, items = await _seed(outreach_db)
    item_id = items["Asha Example"]["id"]
    first = await pack.mark_sent(str(user.id), item_id, message="Hi.", subject=None, now=NOW)
    second = await pack.mark_sent(str(user.id), item_id, message="Hi.", subject=None, now=NOW)

    assert first == second
    assert await _count(outreach_db, OutreachTouch) == 1
    assert await _count(outreach_db, OutreachPerson) == 1


async def test_sent_to_guessed_address_stores_no_address(outreach_db: Factory) -> None:
    user, _, items = await _seed(outreach_db)
    ravi = items["Ravi Sample"]
    assert ravi["address"]["basis"] == "guessed"

    await pack.mark_sent(str(user.id), ravi["id"], message="Hi Ravi.", subject="Hello", now=NOW)

    async with outreach_db() as session:
        values: list[str] = []
        for model in (CompanyContact, OutreachPerson, OutreachTouch, OutreachPackItem):
            for row in (await session.execute(select(model))).scalars():
                values.extend(str(getattr(row, c.key)) for c in model.__table__.c)
    assert not any(GUESS in value for value in values)


async def test_sent_at_thread_cap_is_409_and_item_unchanged(outreach_db: Factory) -> None:
    user, _, items = await _seed(outreach_db)
    async with outreach_db() as session:
        session.add_all(
            OutreachThread(user_id=user.id, company_name=f"Fictional Works {n}", source="typed")
            for n in range(MAX_OPEN_THREADS)
        )
        await session.commit()

    with pytest.raises(Conflict):
        await pack.mark_sent(
            str(user.id), items["Asha Example"]["id"], message="Hi.", subject=None, now=NOW
        )
    stored = await _item(outreach_db, items["Asha Example"]["id"])
    assert (stored.status, stored.thread_id, stored.acted_at) == ("new", None, None)
    assert await _count(outreach_db, OutreachTouch) == 0


async def test_follow_up_sent_records_follow_up_touch(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Dune Robotics")
        contact = await make_contact(session, company, "founder", "Dina Sample")
    summary, _ = await threads.create_thread(
        str(user.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    detail = await people.add_person(str(user.id), summary["id"], contact_id=str(contact.id))
    await touches.record_touch(
        str(user.id),
        summary["id"],
        person_id=detail["people"][0]["id"],
        channel="linkedin",
        direction="out",
        happened_at=NOW - timedelta(days=6),
        now=NOW - timedelta(days=6),
    )
    (item,) = (await pack.get_pack(str(user.id), now=NOW))["follow_ups"]

    result = await pack.mark_sent(
        str(user.id), item["id"], message="Just following up.", subject=None, now=NOW
    )

    assert result["item"]["status"] == "sent"
    async with outreach_db() as session:
        kinds = (await session.execute(select(OutreachTouch.kind))).scalars().all()
    assert sorted(kinds) == ["first", "follow_up"]
    assert await _count(outreach_db, OutreachThread) == 1


async def test_skip_all_dismisses_company_30_days(outreach_db: Factory) -> None:
    user, company, items = await _seed(outreach_db)
    for item in items.values():
        await pack.mark_skipped(str(user.id), item["id"], now=NOW)

    dismissed = await agent_memory.active_dismissals(user_id=str(user.id))
    assert suggestion_card_id(str(company.id)) in dismissed
    assert (await _item(outreach_db, items["Asha Example"]["id"])).status == "skipped"


async def test_skip_one_of_two_does_not_dismiss(outreach_db: Factory) -> None:
    user, company, items = await _seed(outreach_db)
    await pack.mark_skipped(str(user.id), items["Asha Example"]["id"], now=NOW)

    dismissed = await agent_memory.active_dismissals(user_id=str(user.id))
    assert suggestion_card_id(str(company.id)) not in dismissed


async def test_applied_creates_application_and_links_thread(outreach_db: Factory) -> None:
    user, company, items = await _seed(outreach_db)
    async with outreach_db() as session:
        job = await make_job(session, company, "Backend Engineer", city="Bengaluru")
    sent = await pack.mark_sent(
        str(user.id), items["Asha Example"]["id"], message="Hi.", subject=None, now=NOW
    )

    result = await pack.mark_applied(str(user.id), str(company.id), str(job.id), now=NOW)
    again = await pack.mark_applied(str(user.id), str(company.id), str(job.id), now=NOW)

    assert result["application_id"] == again["application_id"]
    async with outreach_db() as session:
        (application,) = (await session.execute(select(JobApplication))).scalars().all()
        thread = await session.get(OutreachThread, uuid.UUID(sent["item"]["thread_id"]))
    assert application.status.value == "applied"
    assert application.applied_at_explicit is not None
    assert thread is not None and thread.application_id == application.id
    assert (await _item(outreach_db, items["Ravi Sample"]["id"])).applied_at is not None
    payload = await pack.get_pack(str(user.id), now=NOW)
    assert payload["companies"][0]["applied"] is True


async def test_applied_job_of_other_company_is_404(outreach_db: Factory) -> None:
    user, company, _ = await _seed(outreach_db)
    async with outreach_db() as session:
        elsewhere = await make_company(session, "Elsewhere Inc")
        job = await make_job(session, elsewhere, "Backend Engineer", city="Bengaluru")

    with pytest.raises(NotFound):
        await pack.mark_applied(str(user.id), str(company.id), str(job.id), now=NOW)
    assert await _count(outreach_db, JobApplication) == 0


def _client(user_id: str) -> AsyncClient:
    token = create_access_token(
        data={"sub": user_id, "email": f"{user_id[:8]}@example.com", "role": "user"}
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_actions_on_other_users_items_are_404(outreach_db: Factory) -> None:
    owner, company, items = await _seed(outreach_db)
    async with outreach_db() as session:
        other = await make_user(session)
        job = await make_job(session, company, "Backend Engineer", city="Bengaluru")
    item_id = items["Asha Example"]["id"]

    async with _client(str(other.id)) as client:
        sent = await client.post(
            f"/api/v1/outreach/pack/items/{item_id}/sent", json={"message": "Hi."}
        )
        skipped = await client.post(f"/api/v1/outreach/pack/items/{item_id}/skip")
        applied = await client.post(
            f"/api/v1/outreach/pack/companies/{company.id}/applied", json={"job_id": str(job.id)}
        )
    assert (sent.status_code, skipped.status_code, applied.status_code) == (404, 404, 404)
    stored = await _item(outreach_db, item_id)
    assert (stored.status, stored.applied_at) == ("new", None)
    assert await _count(outreach_db, OutreachThread) == 0
    assert await _count(outreach_db, JobApplication) == 0

    async with _client(str(owner.id)) as client:
        empty = await client.post(
            f"/api/v1/outreach/pack/items/{item_id}/sent", json={"message": ""}
        )
        ok = await client.post(f"/api/v1/outreach/pack/items/{item_id}/skip")
    assert empty.status_code == 422
    assert ok.status_code == 204
