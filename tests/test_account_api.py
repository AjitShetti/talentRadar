"""
tests/test_account_api.py
~~~~~~~~~~~~~~~~~~~~~~~~~
The account itself (``api/routers/auth.py``): change the password, reset a forgotten one by
email, and delete the account with what it owns.

Runs on in-memory SQLite with ``PRAGMA foreign_keys=ON``, so a delete cascades as it does on
Postgres. Mail never leaves the process: ``send_email`` is replaced and the Resend key is set
or cleared per test. Each path is called fewer than ten times in this file, the strict
credential budget being ten per five minutes per path. Every address is fictional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import auth as auth_module
from api.auth import create_access_token, create_reset_token, get_password_hash, verify_password
from api.routers import auth as auth_router
from storage.database import Base, get_db_dep
from storage.models import OutreachSettings, User

TABLES = ("users", "outreach_settings")
PASSWORD = "first-password-1"

Factory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[Factory]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all, tables=[Base.metadata.tables[name] for name in TABLES]
        )
    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def me(factory: Factory) -> User:
    person = User(id=uuid.uuid4(), email="asha@example.com", hashed_password=get_password_hash(PASSWORD))
    async with factory() as session:
        session.add(person)
        await session.commit()
    return person


@pytest_asyncio.fixture
async def client(factory: Factory) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db_dep] = _session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db_dep, None)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Mail is on, and every message is kept here instead of sent."""
    outbox: list[dict[str, Any]] = []

    async def _send(**message: Any) -> bool:
        outbox.append(message)
        return True

    monkeypatch.setattr(auth_router, "email_enabled", lambda: True)
    monkeypatch.setattr(auth_router, "send_email", _send)
    return outbox


def bearer(person: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(person.id), "email": person.email, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


async def stored(factory: Factory, person: User) -> User | None:
    async with factory() as session:
        return (await session.execute(select(User).where(User.id == person.id))).scalars().first()


def token_from(message: dict[str, Any]) -> str:
    return str(message["text"]).split("token=")[1].split()[0]


async def test_change_password_needs_the_current_one(client: AsyncClient, factory: Factory, me: User) -> None:
    wrong = await client.post(
        "/api/auth/password", headers=bearer(me),
        json={"current_password": "not-it-at-all", "new_password": "second-password-2"},
    )
    assert wrong.status_code == 400

    right = await client.post(
        "/api/auth/password", headers=bearer(me),
        json={"current_password": PASSWORD, "new_password": "second-password-2"},
    )
    assert right.status_code == 204
    row = await stored(factory, me)
    assert row is not None and verify_password("second-password-2", row.hashed_password)


async def test_change_password_needs_a_session(client: AsyncClient, me: User) -> None:
    response = await client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "second-password-2"}
    )
    assert response.status_code in (401, 403)


async def test_delete_account_removes_what_it_owns(client: AsyncClient, factory: Factory, me: User) -> None:
    async with factory() as session:
        session.add(OutreachSettings(user_id=me.id, daily_target=4))
        await session.commit()

    wrong = await client.post("/api/auth/account/delete", headers=bearer(me), json={"password": "not-it-at-all"})
    assert wrong.status_code == 400
    assert await stored(factory, me) is not None

    gone = await client.post("/api/auth/account/delete", headers=bearer(me), json={"password": PASSWORD})
    assert gone.status_code == 204
    assert await stored(factory, me) is None
    async with factory() as session:
        assert (await session.execute(select(OutreachSettings))).scalars().first() is None


async def test_forgot_password_answers_alike_and_mails_only_an_account(
    client: AsyncClient, me: User, sent: list[dict[str, Any]]
) -> None:
    known = await client.post("/api/auth/forgot-password", json={"email": "Asha@Example.com"})
    unknown = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [message["to"] for message in sent] == ["asha@example.com"]
    assert "/reset-password?token=" in sent[0]["text"]


async def test_forgot_password_says_when_mail_is_off(
    client: AsyncClient, me: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_router, "email_enabled", lambda: False)
    response = await client.post("/api/auth/forgot-password", json={"email": "asha@example.com"})
    assert response.status_code == 503


async def test_a_reset_link_works_once(
    client: AsyncClient, factory: Factory, me: User, sent: list[dict[str, Any]]
) -> None:
    await client.post("/api/auth/forgot-password", json={"email": "asha@example.com"})
    token = token_from(sent[0])

    first = await client.post("/api/auth/reset-password", json={"token": token, "new_password": "third-password-3"})
    assert first.status_code == 204
    row = await stored(factory, me)
    assert row is not None and verify_password("third-password-3", row.hashed_password)

    again = await client.post("/api/auth/reset-password", json={"token": token, "new_password": "fourth-password-4"})
    assert again.status_code == 400


async def test_an_expired_or_forged_link_is_refused(client: AsyncClient, me: User) -> None:
    expired = jwt.encode(
        {"sub": str(me.id), "pfp": auth_module.password_fingerprint(me.hashed_password),
         "exp": datetime.now(tz=UTC) - timedelta(minutes=1)},
        auth_module._reset_key(), algorithm="HS256",
    )
    response = await client.post("/api/auth/reset-password", json={"token": expired, "new_password": "fifth-password-5"})
    assert response.status_code == 400

    # A session token is signed with another key, so it is no reset link.
    session = create_access_token(data={"sub": str(me.id)})
    response = await client.post("/api/auth/reset-password", json={"token": session, "new_password": "fifth-password-5"})
    assert response.status_code == 400


def test_a_reset_token_is_no_session() -> None:
    token = create_reset_token(str(uuid.uuid4()), "hash")
    with pytest.raises(HTTPException):
        auth_module.decode_access_token(token)
