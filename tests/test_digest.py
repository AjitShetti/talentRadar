"""
tests/test_digest.py
~~~~~~~~~~~~~~~~~~~~
The morning email (``services/digest.py``): only to users who turned it on, only when the
briefing has something to say, once per IST day, and a link in it turns it off.

Runs on in-memory SQLite (``tests/outreach_support.py``). The briefing is replaced with a
fixed one and mail never leaves the process. Every person and company is fictional.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services import copilot, digest
from services.cache_backend import CacheBackend
from storage.models import Profile, User
from tests.outreach_support import make_user, outreach_db  # noqa: F401

Factory = async_sessionmaker[AsyncSession]

CARD = {
    "id": "scheduled_interview:1",
    "kind": "scheduled_interview",
    "title": "Platform Engineer interview at Lumen Works — tomorrow",
    "detail": "Scheduled for Tue 3 Nov, 14:30. Run one mock round.",
    "actions": [{"label": "Prep with a mock", "href": "/interview?job=j-1", "style": "primary"}],
}


@pytest.fixture
def db(outreach_db: Factory, monkeypatch: pytest.MonkeyPatch) -> Factory:  # noqa: F811
    monkeypatch.setattr(digest, "AsyncSessionLocal", outreach_db)
    return outreach_db


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def _send(**message: Any) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(digest, "email_enabled", lambda: True)
    monkeypatch.setattr(digest, "send_email", _send)
    return sent


@pytest_asyncio.fixture(autouse=True)
async def fresh_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    yield
    await CacheBackend.reset()


def briefing_with(cards: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def _build(*, user_id: str) -> dict[str, Any]:
        return {"headline": "1 interview tomorrow", "cards": cards}

    monkeypatch.setattr(copilot, "build_briefing", _build)


async def person(db: Factory, *, digest_on: bool, name: str = "Asha Verma") -> User:
    async with db() as session:
        user = await make_user(session)
        session.add(Profile(user_id=user.id, full_name=name, digest_email=digest_on))
        await session.commit()
    return user


async def test_only_those_who_asked_are_mailed(
    db: Factory, outbox: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    briefing_with([CARD], monkeypatch)
    wanted = await person(db, digest_on=True)
    await person(db, digest_on=False)

    counts = await digest.send_daily_digests()

    assert counts["sent"] == 1
    assert [message["to"] for message in outbox] == [wanted.email]
    text = outbox[0]["text"]
    assert text.startswith("Good morning, Asha.")
    assert CARD["title"] in text and "http://localhost:3000/interview?job=j-1" in text
    assert "/unsubscribe?token=" in text


async def test_a_second_run_the_same_day_sends_nothing(
    db: Factory, outbox: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    briefing_with([CARD], monkeypatch)
    await person(db, digest_on=True)

    await digest.send_daily_digests()
    again = await digest.send_daily_digests()

    assert len(outbox) == 1 and again["skipped"] == 1


async def test_nothing_to_say_sends_nothing(
    db: Factory, outbox: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    briefing_with([], monkeypatch)
    await person(db, digest_on=True)

    counts = await digest.send_daily_digests()

    assert outbox == [] and counts["empty"] == 1


async def test_mail_off_sends_nothing(db: Factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digest, "email_enabled", lambda: False)
    await person(db, digest_on=True)
    assert (await digest.send_daily_digests())["recipients"] == 0


async def test_the_link_in_the_mail_turns_it_off(
    db: Factory, outbox: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.main import app

    briefing_with([CARD], monkeypatch)
    user = await person(db, digest_on=True)
    await digest.send_daily_digests()
    token = outbox[0]["text"].split("token=")[1].split()[0]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        bad = await http.post("/api/v1/profile/digest/unsubscribe", json={"token": "not-a-token"})
        good = await http.post("/api/v1/profile/digest/unsubscribe", json={"token": token})

    assert bad.status_code == 400 and good.status_code == 200
    async with db() as session:
        flag = (await session.execute(select(Profile.digest_email).where(Profile.user_id == user.id))).scalar_one()
    assert flag is False


def test_an_off_link_is_no_session() -> None:
    from api.auth import decode_access_token

    with pytest.raises(HTTPException):
        decode_access_token(digest.unsubscribe_token("6f1c2b1e-0000-4000-8000-000000000000"))
