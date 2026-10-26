"""
tests/test_outreach_pack_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Migration 020: the daily pack's two tables, the email provenance column on
``company_contacts``, and the foreign-key actions that clean up after a deleted pack,
thread or contact. Cascades run on SQLite with ``PRAGMA foreign_keys=ON``; the column
types are compared with the migration's SQL for PostgreSQL, as for Events.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Table, delete, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from storage.database import Base
from storage.models import (
    Company,
    CompanyContact,
    OutreachPack,
    OutreachPackItem,
    OutreachThread,
    User,
)

VERSIONS = Path(__file__).resolve().parent.parent / "storage" / "migrations" / "versions"
MIGRATION = VERSIONS / "020_outreach_pack.py"
PACK_TABLES = ("outreach_packs", "outreach_pack_items")


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_020", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(" ".join(sql.split()))


def _statements(monkeypatch: pytest.MonkeyPatch, step: str = "upgrade") -> list[str]:
    module = _load_migration()
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)
    getattr(module, step)()
    return recorder.statements


def test_migration_020_chain() -> None:
    module = _load_migration()
    assert module.revision == "020_outreach_pack"
    assert module.down_revision == "019_practice"
    pointing_at_019 = [
        path.name
        for path in VERSIONS.glob("*.py")
        if re.search(r'down_revision[^=]*=\s*"019_practice"', path.read_text(encoding="utf-8"))
    ]
    assert pointing_at_019 == ["020_outreach_pack.py"]


def test_upgrade_is_idempotent_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _statements(monkeypatch)
    creates = [s for s in statements if s.startswith("CREATE")]
    assert creates and all("IF NOT EXISTS" in s for s in creates)
    assert any(
        s.startswith("ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS email_source_url")
        for s in statements
    )


def test_downgrade_drops_what_upgrade_made(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _statements(monkeypatch, "downgrade")
    assert "DROP TABLE IF EXISTS outreach_pack_items" in statements
    assert "DROP TABLE IF EXISTS outreach_packs" in statements
    assert any("DROP COLUMN IF EXISTS email_source_url" in s for s in statements)


# --- PostgreSQL types: migration against model -----------------------------------------

_COLUMN_END = re.compile(r"\s+(?:NOT NULL|NULL|DEFAULT|PRIMARY KEY|REFERENCES|UNIQUE)\b.*$")
_SAME_TYPE = {"TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE"}


def _top_level_parts(body: str) -> list[str]:
    parts: list[str] = []
    depth = start = 0
    for index, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index].strip())
            start = index + 1
    parts.append(body[start:].strip())
    return [part for part in parts if part]


def _canonical(spelling: str) -> str:
    text = " ".join(spelling.upper().split()).replace("CHARACTER VARYING", "VARCHAR")
    return _SAME_TYPE.get(text, text)


def _migration_types(statements: list[str]) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for statement in statements:
        created = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*)\);?$", statement)
        if created is None:
            continue
        for part in _top_level_parts(created.group(2)):
            if re.match(r"(?:CONSTRAINT|PRIMARY KEY|UNIQUE|FOREIGN KEY)\b", part):
                continue
            name, _, rest = part.partition(" ")
            found[(created.group(1), name)] = _canonical(_COLUMN_END.sub("", rest))
    return found


def _model_types() -> dict[tuple[str, str], str]:
    dialect = postgresql.dialect()
    return {
        (table, column.name): _canonical(column.type.compile(dialect=dialect))
        for table in PACK_TABLES
        for column in Base.metadata.tables[table].columns
    }


def test_drift_pack_tables_postgres_types(monkeypatch: pytest.MonkeyPatch) -> None:
    in_migration = _migration_types(_statements(monkeypatch))
    in_model = _model_types()
    assert set(in_migration) == set(in_model)
    differing = {
        k: (in_migration[k], in_model[k]) for k in in_migration if in_migration[k] != in_model[k]
    }
    assert differing == {}
    assert in_model[("outreach_pack_items", "reasons")] == "TEXT[]"


def test_contact_has_email_source_url() -> None:
    column = CompanyContact.__table__.c["email_source_url"]
    assert column.nullable
    assert column.type.compile(dialect=postgresql.dialect()) == "VARCHAR(512)"


# --- constraints and cascades on SQLite ------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def fk_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    names = (
        "users",
        "companies",
        "company_contacts",
        "jobs",
        "job_applications",
        "outreach_threads",
        "outreach_people",
        *PACK_TABLES,
    )
    tables: list[Table] = [Base.metadata.tables[name] for name in names]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[User, Company, OutreachPack]:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        hashed_password="not-a-real-hash",
    )
    company = Company(id=uuid.uuid4(), name="Fictional Labs", slug=f"fl-{uuid.uuid4().hex[:6]}")
    session.add_all([user, company])
    await session.flush()
    pack = OutreachPack(user_id=user.id, day=date(2026, 10, 26))
    session.add(pack)
    await session.flush()
    return user, company, pack


def _item(pack: OutreachPack, company: Company, **fields: Any) -> OutreachPackItem:
    values: dict[str, Any] = {
        "pack_id": pack.id,
        "kind": "first",
        "company_id": company.id,
        "company_name": company.name,
        "position": 0,
        "person_name": "Asha Example",
        "channel": "linkedin",
        "format": "linkedin_note",
    }
    values.update(fields)
    return OutreachPackItem(**values)


async def test_pack_unique_per_user_day(fk_session: AsyncSession) -> None:
    user, _, _ = await _seed(fk_session)
    fk_session.add(OutreachPack(user_id=user.id, day=date(2026, 10, 26)))
    with pytest.raises(IntegrityError):
        await fk_session.flush()


async def test_item_defaults(fk_session: AsyncSession) -> None:
    _, company, pack = await _seed(fk_session)
    item = _item(pack, company)
    fk_session.add(item)
    await fk_session.flush()
    await fk_session.refresh(item)
    assert item.status == "new"
    assert list(item.reasons or []) == []


async def test_items_cascade_with_pack(fk_session: AsyncSession) -> None:
    _, company, pack = await _seed(fk_session)
    fk_session.add(_item(pack, company))
    await fk_session.flush()
    await fk_session.execute(delete(OutreachPack).where(OutreachPack.id == pack.id))
    left = (await fk_session.execute(select(OutreachPackItem))).scalars().all()
    assert left == []


async def test_thread_delete_sets_item_thread_null(fk_session: AsyncSession) -> None:
    user, company, pack = await _seed(fk_session)
    thread = OutreachThread(
        user_id=user.id, company_id=company.id, company_name=company.name, source="suggestion"
    )
    fk_session.add(thread)
    await fk_session.flush()
    item = _item(pack, company, thread_id=thread.id, status="sent")
    fk_session.add(item)
    await fk_session.flush()
    await fk_session.execute(delete(OutreachThread).where(OutreachThread.id == thread.id))
    fk_session.expire_all()
    stored = (await fk_session.execute(select(OutreachPackItem))).scalar_one()
    assert stored.thread_id is None


async def test_contact_delete_sets_item_contact_null(fk_session: AsyncSession) -> None:
    _, company, pack = await _seed(fk_session)
    contact = CompanyContact(
        company_id=company.id,
        kind="founder",
        name="Asha Example",
        source_url="https://fictional.test/team",
    )
    fk_session.add(contact)
    await fk_session.flush()
    fk_session.add(_item(pack, company, contact_id=contact.id))
    await fk_session.flush()
    await fk_session.execute(delete(CompanyContact).where(CompanyContact.id == contact.id))
    fk_session.expire_all()
    stored = (await fk_session.execute(select(OutreachPackItem))).scalar_one()
    assert stored.contact_id is None
