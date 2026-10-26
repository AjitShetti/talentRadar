"""
tests/test_outreach_pack_write.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Writing the daily pack's messages: one model call per company, the three budgets in
order, idempotency, templates on any failure, and the follow-up write. The model is a
stub; the cache is the in-process one.

Every person and company here is fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import create_access_token
from api.main import app
from services.cache_backend import CacheBackend
from services.llm_budget import (
    PER_USER_SHARED_DRAFTS,
    claim_user_shared,
    used,
    user_draft_key,
    user_shared_key,
)
from services.outreach import drafts, pack, people, threads, touches
from services.outreach.drafts import global_key
from services.outreach.errors import NotFound
from storage.models import Profile, User
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 11:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
MARKER = "ZEBRA-MARKER-7"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


@pytest.fixture(autouse=True)
def _no_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafts, "get_active_resume", AsyncMock(return_value=None))


def _reply(*messages: dict[str, Any]) -> str:
    return json.dumps({"messages": list(messages)})


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(
        return_value=_reply(
            {"i": 0, "subject": "Hello from a builder", "message": f"Hi Asha, {MARKER} one."},
            {"i": 1, "subject": None, "message": f"Hi Ravi, {MARKER} two."},
        )
    )
    monkeypatch.setattr(drafts, "_chat", mock)
    return mock


async def _seed(factory: Factory, *, domain: str | None = "fictional.test") -> tuple[User, str]:
    """A user and a company with two recipients: Asha (a found address) and Ravi
    (guessed when the company has a domain, LinkedIn otherwise)."""
    async with factory() as session:
        user = await make_user(session)
        session.add(Profile(user_id=user.id, target_roles=["Backend Engineer"]))
        await session.commit()
        company = await make_company(
            session, "Fictional Labs", domain=domain, last_funding_at=NOW - timedelta(days=9)
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
    await pack.get_pack(str(user.id), now=NOW)
    return user, str(company.id)


def _by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["person"]["name"]: item for item in result["items"]}


async def test_write_spends_one_shared_unit_and_one_user_draft_per_message(
    outreach_db: Factory, chat: AsyncMock
) -> None:
    user, company_id = await _seed(outreach_db)
    result = await pack.write_company(str(user.id), company_id, now=NOW)

    assert chat.await_count == 1
    assert chat.await_args is not None and chat.await_args.kwargs["json_mode"] is True
    items = _by_name(result)
    assert items["Asha Example"]["message"] == f"Hi Asha, {MARKER} one."
    assert items["Asha Example"]["subject"] == "Hello from a builder"
    assert items["Asha Example"]["generated"] is True
    assert items["Asha Example"]["reason"] is None
    assert await used(user_draft_key(str(user.id))) == 2
    assert await used(user_shared_key(str(user.id))) == 1
    assert await used(global_key(NOW)) == 1


async def test_write_is_idempotent(outreach_db: Factory, chat: AsyncMock) -> None:
    user, company_id = await _seed(outreach_db)
    first = await pack.write_company(str(user.id), company_id, now=NOW)
    second = await pack.write_company(str(user.id), company_id, now=NOW)

    assert first == second
    assert chat.await_count == 1
    assert await used(user_draft_key(str(user.id))) == 2
    reread = await pack.get_pack(str(user.id), now=NOW)
    assert reread["counts"]["messages_ready"] == 2


async def test_concurrent_write_spends_once(outreach_db: Factory, chat: AsyncMock) -> None:
    user, company_id = await _seed(outreach_db)
    first, second = await asyncio.gather(
        pack.write_company(str(user.id), company_id, now=NOW),
        pack.write_company(str(user.id), company_id, now=NOW),
    )
    assert first == second
    assert chat.await_count == 1
    assert await used(global_key(NOW)) == 1


async def test_user_shared_share_caps_at_8_then_templates(
    outreach_db: Factory, chat: AsyncMock
) -> None:
    user, company_id = await _seed(outreach_db)
    for _ in range(PER_USER_SHARED_DRAFTS):
        assert await claim_user_shared(str(user.id))

    result = await pack.write_company(str(user.id), company_id, now=NOW)
    assert chat.await_count == 0
    assert await used(global_key(NOW)) == 0
    for item in result["items"]:
        assert item["generated"] is False
        assert item["reason"] == "daily_cap"
        assert item["message"]


async def test_thread_draft_also_claims_user_shared_share(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drafts, "_chat", AsyncMock(return_value="Hi Asha, a note."))
    user, company_id = await _seed(outreach_db)
    summary, _ = await threads.create_thread(
        str(user.id), company_id=company_id, company_name=None, job_id=None, source="drawer"
    )
    detail = await people.get_thread(str(user.id), summary["id"])
    contact_id = next(
        p["contact_id"] for p in detail["suggested_people"] if p["name"] == "Asha Example"
    )
    added = await people.add_person(str(user.id), summary["id"], contact_id=contact_id)
    person_id = added["people"][0]["id"]

    await drafts.draft(str(user.id), summary["id"], person_id=person_id, fmt="linkedin_note")
    assert await used(user_shared_key(str(user.id))) == 1

    for _ in range(PER_USER_SHARED_DRAFTS):
        await claim_user_shared(str(user.id))
    capped = await drafts.draft(
        str(user.id), summary["id"], person_id=person_id, fmt="linkedin_note"
    )
    assert capped["generated"] is False
    assert capped["reason"] == "daily_cap"


async def test_model_failure_gives_templates_with_reason(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drafts, "_chat", AsyncMock(side_effect=RuntimeError("down")))
    user, company_id = await _seed(outreach_db)
    result = await pack.write_company(str(user.id), company_id, now=NOW)

    for item in result["items"]:
        assert item["generated"] is False
        assert item["reason"] == "model_unavailable"
        assert item["message"]
    assert _by_name(result)["Asha Example"]["subject"]


async def test_partial_reply_templates_only_missing_message(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = _reply({"i": 0, "subject": "Hello", "message": "Hi Asha, a real note."})
    monkeypatch.setattr(drafts, "_chat", AsyncMock(return_value=reply))
    user, company_id = await _seed(outreach_db)
    items = _by_name(await pack.write_company(str(user.id), company_id, now=NOW))

    assert items["Asha Example"]["generated"] is True
    assert items["Asha Example"]["message"] == "Hi Asha, a real note."
    assert items["Ravi Sample"]["generated"] is False
    assert items["Ravi Sample"]["reason"] == "model_unavailable"
    assert items["Ravi Sample"]["message"]


async def test_email_has_subject_linkedin_note_within_200_chars(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_note = "Hello Ravi. " + "I build backend systems for a living. " * 20
    reply = _reply(
        {"i": 0, "subject": None, "message": "Hi Asha, a short email body."},
        {"i": 1, "subject": "Not for a note", "message": long_note},
    )
    monkeypatch.setattr(drafts, "_chat", AsyncMock(return_value=reply))
    user, company_id = await _seed(outreach_db, domain=None)
    items = _by_name(await pack.write_company(str(user.id), company_id, now=NOW))

    email, note = items["Asha Example"], items["Ravi Sample"]
    assert (email["format"], note["format"]) == ("email", "linkedin_note")
    assert email["subject"]  # the template's, when the model gave none
    assert note["subject"] is None
    assert len(note["message"]) <= 200
    assert note["generated"] is True


async def _due_follow_up(factory: Factory) -> tuple[User, str]:
    async with factory() as session:
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
    payload = await pack.get_pack(str(user.id), now=NOW)
    (item,) = payload["follow_ups"]
    return user, item["id"]


async def test_follow_up_write_stored_and_reused(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value="Hi Dina, just following up on my note.")
    monkeypatch.setattr(drafts, "_chat", mock)
    user, item_id = await _due_follow_up(outreach_db)

    first = await pack.write_follow_up(str(user.id), item_id, now=NOW)
    second = await pack.write_follow_up(str(user.id), item_id, now=NOW)

    assert first == second
    assert mock.await_count == 1
    assert first["item"]["message"] == "Hi Dina, just following up on my note."
    assert first["item"]["format"] == "linkedin_message"
    assert first["item"]["generated"] is True
    reread = await pack.get_pack(str(user.id), now=NOW)
    assert reread["follow_ups"][0]["message"] == first["item"]["message"]


def _client(user_id: str) -> AsyncClient:
    token = create_access_token(
        data={"sub": user_id, "email": f"{user_id[:8]}@example.com", "role": "user"}
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_write_other_users_company_or_item_is_404(
    outreach_db: Factory, chat: AsyncMock
) -> None:
    owner, company_id = await _seed(outreach_db)
    _, item_id = await _due_follow_up(outreach_db)
    async with outreach_db() as session:
        other = await make_user(session)

    with pytest.raises(NotFound):
        await pack.write_company(str(other.id), company_id, now=NOW)
    with pytest.raises(NotFound):
        await pack.write_follow_up(str(other.id), item_id, now=NOW)
    with pytest.raises(NotFound):
        await pack.write_follow_up(str(owner.id), item_id, now=NOW)  # not this user's item

    async with _client(str(other.id)) as client:
        company = await client.post(f"/api/v1/outreach/pack/companies/{company_id}/write")
        item = await client.post(f"/api/v1/outreach/pack/items/{item_id}/write")
    assert (company.status_code, item.status_code) == (404, 404)
    assert chat.await_count == 0


async def test_logs_contain_no_message_text(
    outreach_db: Factory, chat: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    user, company_id = await _seed(outreach_db)
    await pack.write_company(str(user.id), company_id, now=NOW)

    # Our own loggers only: the SQLite driver's DEBUG lines echo statement parameters.
    ours = " | ".join(
        r.getMessage() for r in caplog.records if r.name.startswith(("services", "api"))
    )
    assert "outreach pack messages" in ours
    assert MARKER not in ours
    assert "Asha" not in ours
    assert "fictional.test" not in ours
