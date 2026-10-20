"""
tests/outreach_support.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Shared support for the Outreach service tests: an in-memory SQLite database
wired in where the services open their sessions, and seed helpers.

Import the fixture and the helpers like this::

    from tests.outreach_support import (  # noqa: F401
        make_company, make_contact, make_user, outreach_db,
    )

The seed data is fictional on purpose. No real person is ever attached to a
real company in a test.
"""

from __future__ import annotations

import importlib
import pkgutil
import uuid
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import services.agent_memory as agent_memory
import services.outreach as outreach_package
from storage.database import Base
from storage.models import Company, CompanyContact, Job, User


def outreach_modules() -> list[ModuleType]:
    """Every ``services.outreach.*`` submodule, imported now.

    Importing them all, rather than patching only the ones a test happened to
    import, is what lets a later module that is imported lazily (the GitHub
    step, the draft service) still find the test database: a session opened
    through an unpatched ``AsyncSessionLocal`` would reach for Postgres.
    """
    return [
        importlib.import_module(f"{outreach_package.__name__}.{info.name}")
        for info in pkgutil.iter_modules(outreach_package.__path__)
    ]


@pytest_asyncio.fixture
async def outreach_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh in-memory SQLite database, yielded as a session factory.

    * Foreign keys are switched on for every connection. The thread's people and
      touches are ``passive_deletes``: deleting a thread is left to the
      database's ``ON DELETE CASCADE``, which SQLite only honours with the
      pragma on.
    * ``AsyncSessionLocal`` is replaced, for the test, in every Outreach module
      and in ``services.agent_memory`` (where dismissals live), so no session
      they open can reach Postgres.
    * The factory mirrors production: ``expire_on_commit`` and ``autoflush`` are
      both off, so a service that forgets to flush or to reload fails here too.
    """
    modules = outreach_modules()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    for module in modules:
        if hasattr(module, "AsyncSessionLocal"):
            monkeypatch.setattr(module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(agent_memory, "AsyncSessionLocal", factory)

    yield factory
    await engine.dispose()


# --- seed helpers --------------------------------------------------------------
# Each one commits, so the row is visible to the service's own session.


async def make_user(session: AsyncSession, **kw: Any) -> User:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "email": f"{uuid.uuid4().hex[:10]}@example.com",
        "hashed_password": "not-a-real-hash",
    }
    values.update(kw)
    user = User(**values)
    session.add(user)
    await session.commit()
    return user


async def make_company(session: AsyncSession, name: str, **kw: Any) -> Company:
    """A listed (``is_tech``) Bengaluru company with a one-line summary."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": name,
        "is_tech": True,
        "summary": f"What {name} makes, in one line.",
        "office_cities": ["Bengaluru"],
    }
    values.update(kw)
    company = Company(**values)
    session.add(company)
    await session.commit()
    return company


async def make_contact(
    session: AsyncSession, company: Company, kind: str, name: str, **kw: Any
) -> CompanyContact:
    """A curated person at ``company`` that cites a public source page."""
    slug = "-".join(name.lower().split())
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "company_id": company.id,
        "kind": kind,
        "name": name,
        "source_url": f"https://example.com/team/{slug}",
        "source_kind": "curated",
    }
    values.update(kw)
    contact = CompanyContact(**values)
    session.add(contact)
    await session.commit()
    return contact


async def make_job(
    session: AsyncSession, company: Company, title: str = "Platform Engineer", **kw: Any
) -> Job:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "company_id": company.id,
        "source": "greenhouse",
        "title": title,
    }
    values.update(kw)
    job = Job(**values)
    session.add(job)
    await session.commit()
    return job
