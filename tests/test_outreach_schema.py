"""
tests/test_outreach_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Migration 017: the five Outreach tables (threads, people, touches, issue
snapshots, settings), the partial unique indexes that keep a company to one
thread per user, and the foreign-key actions that let the database clean up
after a deleted thread.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Column, Index, String, Table, delete, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateIndex

from storage.database import Base
from storage.models import (
    Company,
    CompanyContact,
    OssIssueSnapshot,
    OutreachPerson,
    OutreachSettings,
    OutreachThread,
    OutreachTouch,
    User,
)

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "storage"
    / "migrations"
    / "versions"
    / "017_outreach.py"
)


def _table(model: type[Base]) -> Table:
    table = model.__table__
    assert isinstance(table, Table)
    return table


def _columns(model: type[Base]) -> set[str]:
    return set(_table(model).c.keys())


def _column(model: type[Base], name: str) -> Column[Any]:
    column = _table(model).c[name]
    assert isinstance(column, Column)
    return column


def _length(model: type[Base], name: str) -> int | None:
    column_type = _column(model, name).type
    assert isinstance(column_type, String)
    return column_type.length


def _default(model: type[Base], name: str) -> object:
    default = _column(model, name).default
    assert default is not None
    return getattr(default, "arg", None)


def _index(model: type[Base], name: str) -> Index:
    for index in _table(model).indexes:
        if index.name == name:
            return index
    raise AssertionError(f"{model.__name__} has no index {name}")


def test_tables_and_columns() -> None:
    assert _columns(OutreachThread) == {
        "id",
        "user_id",
        "company_id",
        "company_name",
        "job_id",
        "job_title",
        "application_id",
        "source",
        "note",
        "next_step",
        "next_step_at",
        "closed_at",
        "outcome",
        "created_at",
        "updated_at",
    }
    assert _columns(OutreachPerson) == {
        "id",
        "thread_id",
        "contact_id",
        "name",
        "title",
        "kind",
        "state",
        "first_sent_at",
        "last_outbound_at",
        "follow_ups_sent",
        "next_follow_up_at",
        "replied_at",
        "email",
        "linkedin_url",
        "created_at",
    }
    assert _columns(OutreachTouch) == {
        "id",
        "thread_id",
        "person_id",
        "user_id",
        "channel",
        "direction",
        "kind",
        "note",
        "body",
        "happened_at",
        "created_at",
    }
    assert _columns(OssIssueSnapshot) == {
        "company_id",
        "org",
        "status",
        "total_count",
        "issues",
        "fetched_at",
    }
    assert _columns(OutreachSettings) == {"user_id", "daily_target", "updated_at"}
    assert OutreachThread.__tablename__ == "outreach_threads"
    assert OutreachPerson.__tablename__ == "outreach_people"
    assert OutreachTouch.__tablename__ == "outreach_touches"
    assert OssIssueSnapshot.__tablename__ == "oss_issue_snapshots"
    assert OutreachSettings.__tablename__ == "outreach_settings"


def test_column_bounds_nullability_and_defaults() -> None:
    lengths = {
        (OutreachThread, "company_name"): 256,
        (OutreachThread, "job_title"): 256,
        (OutreachThread, "source"): 12,
        (OutreachThread, "note"): 500,
        (OutreachThread, "next_step"): 140,
        (OutreachThread, "outcome"): 16,
        (OutreachPerson, "name"): 128,
        (OutreachPerson, "title"): 128,
        (OutreachPerson, "kind"): 24,
        (OutreachPerson, "state"): 10,
        (OutreachPerson, "email"): 256,
        (OutreachPerson, "linkedin_url"): 512,
        (OutreachTouch, "channel"): 10,
        (OutreachTouch, "direction"): 3,
        (OutreachTouch, "kind"): 10,
        (OutreachTouch, "note"): 500,
        (OutreachTouch, "body"): 2000,
        (OssIssueSnapshot, "org"): 128,
        (OssIssueSnapshot, "status"): 12,
    }
    for (model, name), length in lengths.items():
        assert _length(model, name) == length, f"{model.__name__}.{name}"

    required = [
        (OutreachThread, "user_id"),
        (OutreachThread, "company_name"),
        (OutreachThread, "source"),
        (OutreachPerson, "thread_id"),
        (OutreachPerson, "name"),
        (OutreachPerson, "kind"),
        (OutreachPerson, "state"),
        (OutreachPerson, "follow_ups_sent"),
        (OutreachTouch, "thread_id"),
        (OutreachTouch, "user_id"),
        (OutreachTouch, "happened_at"),
        (OssIssueSnapshot, "fetched_at"),
        (OssIssueSnapshot, "total_count"),
        (OutreachSettings, "daily_target"),
    ]
    for model, name in required:
        assert not _column(model, name).nullable, f"{model.__name__}.{name}"

    optional = [
        (OutreachThread, "company_id"),  # a typed company has none
        (OutreachThread, "job_id"),
        (OutreachThread, "application_id"),
        (OutreachPerson, "contact_id"),  # a name-only thread has no contact rows
        (OutreachTouch, "person_id"),  # a thread-level touch
    ]
    for model, name in optional:
        assert _column(model, name).nullable, f"{model.__name__}.{name}"

    assert _default(OutreachPerson, "state") == "planned"
    assert _default(OutreachPerson, "follow_ups_sent") == 0
    assert _default(OssIssueSnapshot, "total_count") == 0
    assert _default(OutreachSettings, "daily_target") == 3
    assert _column(OssIssueSnapshot, "company_id").primary_key
    assert _column(OutreachSettings, "user_id").primary_key


def test_partial_unique_indexes() -> None:
    thread_index = _index(OutreachThread, "uq_outreach_threads_user_company")
    assert thread_index.unique
    assert [column.name for column in thread_index.columns] == ["user_id", "company_id"]
    for dialect in ("postgresql", "sqlite"):
        where = thread_index.dialect_options[dialect]["where"]
        assert str(where) == "company_id IS NOT NULL"

    people_index = _index(OutreachPerson, "uq_outreach_people_thread_contact")
    assert people_index.unique
    assert [column.name for column in people_index.columns] == ["thread_id", "contact_id"]
    for dialect in ("postgresql", "sqlite"):
        where = people_index.dialect_options[dialect]["where"]
        assert str(where) == "contact_id IS NOT NULL"

    ddl = str(CreateIndex(thread_index).compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert "UNIQUE INDEX uq_outreach_threads_user_company" in ddl
    assert ddl.rstrip().endswith("WHERE company_id IS NOT NULL")

    assert {"user_id"} == {c.name for c in _index(OutreachThread, "ix_outreach_threads_user").columns}
    touches_index = _index(OutreachTouch, "ix_outreach_touches_user_happened")
    assert [column.name for column in touches_index.columns] == ["user_id", "happened_at"]


def _action(model: type[Base], column: str) -> tuple[str, str | None]:
    (foreign_key,) = _column(model, column).foreign_keys
    return foreign_key.column.table.name, foreign_key.ondelete


def test_foreign_key_actions() -> None:
    # A company, a posting, a tracker row or a contact going away must not take
    # the user's conversation with it; the user or the thread going away must.
    assert _action(OutreachThread, "company_id") == ("companies", "SET NULL")
    assert _action(OutreachThread, "job_id") == ("jobs", "SET NULL")
    assert _action(OutreachThread, "application_id") == ("job_applications", "SET NULL")
    assert _action(OutreachThread, "user_id") == ("users", "CASCADE")

    assert _action(OutreachPerson, "contact_id") == ("company_contacts", "SET NULL")
    assert _action(OutreachPerson, "thread_id") == ("outreach_threads", "CASCADE")

    assert _action(OutreachTouch, "thread_id") == ("outreach_threads", "CASCADE")
    assert _action(OutreachTouch, "person_id") == ("outreach_people", "SET NULL")
    assert _action(OutreachTouch, "user_id") == ("users", "CASCADE")

    assert _action(OssIssueSnapshot, "company_id") == ("companies", "CASCADE")
    assert _action(OutreachSettings, "user_id") == ("users", "CASCADE")


def test_thread_children_are_left_to_the_database_to_delete() -> None:
    for relationship in (OutreachThread.people, OutreachThread.touches):
        prop = relationship.property
        assert prop.passive_deletes is True
        assert prop.lazy == "raise"
        assert prop.cascade.delete and prop.cascade.delete_orphan


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_017", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_ids() -> None:
    module = _load_migration()
    assert module.revision == "017_outreach"
    assert module.down_revision == "016_company_is_tech"


def test_migration_is_idempotent_and_drops_in_reverse_dependency_order() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade, downgrade = source.split("def downgrade()")

    assert "ALTER TABLE" not in source  # it only creates its own tables
    assert upgrade.count("CREATE TABLE IF NOT EXISTS") == 5
    assert upgrade.count("CREATE TABLE") == 5
    assert len(re.findall(r"CREATE (?:UNIQUE )?INDEX\s+IF NOT EXISTS", upgrade)) == 4
    assert upgrade.count("INDEX") == 4  # no index is created any other way
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_threads_user_company" in upgrade
    assert "WHERE company_id IS NOT NULL" in upgrade
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_people_thread_contact" in upgrade
    assert "WHERE contact_id IS NOT NULL" in upgrade

    dropped = [
        downgrade.index(f"DROP TABLE IF EXISTS {table}")
        for table in (
            "outreach_touches",
            "outreach_people",
            "outreach_threads",
            "oss_issue_snapshots",
            "outreach_settings",
        )
    ]
    assert dropped == sorted(dropped)


# ─────────────────────────────────────────────────────────────────────────────
# Against SQLite, with foreign keys enforced the way Postgres enforces them
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _user() -> User:
    return User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")


def _thread(user: User, company: Company | None = None, name: str = "Acme Labs") -> OutreachThread:
    return OutreachThread(
        id=uuid.uuid4(),
        user_id=user.id,
        company_id=company.id if company else None,
        company_name=name,
        source="typed",
    )


async def test_sqlite_create_all(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    user = _user()
    session.add(user)
    await session.flush()
    user_id = user.id
    thread = _thread(user)
    session.add(thread)
    await session.flush()
    person = OutreachPerson(id=uuid.uuid4(), thread_id=thread.id, name="Asha Rao", kind="founder")
    session.add(person)
    await session.flush()
    touch = OutreachTouch(
        id=uuid.uuid4(),
        thread_id=thread.id,
        person_id=person.id,
        user_id=user.id,
        channel="linkedin",
        direction="out",
        kind="first",
        happened_at=now,
    )
    session.add(touch)
    await session.commit()

    stored = (await session.execute(select(OutreachPerson))).scalar_one()
    assert stored.state == "planned"  # the column default
    assert stored.follow_ups_sent == 0
    assert stored.created_at is not None

    # Delete the thread: the people and touches go with it, with no ORM load of
    # the (lazy="raise") collections, because the database does the cascade.
    await session.delete(thread)
    await session.commit()
    session.expire_all()

    assert (await session.execute(select(OutreachThread))).first() is None
    assert (await session.execute(select(OutreachPerson))).first() is None
    assert (await session.execute(select(OutreachTouch))).first() is None
    assert (await session.execute(select(User))).scalar_one().id == user_id


async def test_deleting_a_thread_does_not_load_its_children(session: AsyncSession) -> None:
    user = _user()
    session.add(user)
    await session.flush()
    thread = _thread(user)
    session.add(thread)
    await session.flush()
    session.add(OutreachPerson(id=uuid.uuid4(), thread_id=thread.id, name="Asha Rao", kind="founder"))
    await session.commit()

    statements: list[str] = []
    assert isinstance(session.bind, AsyncEngine)

    @event.listens_for(session.bind.sync_engine, "before_cursor_execute")
    def _record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    await session.delete(thread)
    await session.commit()

    assert any(s.startswith("DELETE FROM outreach_threads") for s in statements)
    assert not [s for s in statements if "FROM outreach_people" in s or "FROM outreach_touches" in s]


async def test_deleting_a_person_keeps_their_touches(session: AsyncSession) -> None:
    user = _user()
    session.add(user)
    await session.flush()
    thread = _thread(user)
    session.add(thread)
    await session.flush()
    thread_id = thread.id
    person = OutreachPerson(id=uuid.uuid4(), thread_id=thread_id, name="Dev Nair", kind="engineer")
    session.add(person)
    await session.flush()
    session.add(
        OutreachTouch(
            id=uuid.uuid4(),
            thread_id=thread_id,
            person_id=person.id,
            user_id=user.id,
            channel="email",
            direction="out",
            kind="first",
            happened_at=datetime.now(UTC),
        )
    )
    await session.commit()

    await session.delete(person)
    await session.commit()
    session.expire_all()

    touch = (await session.execute(select(OutreachTouch))).scalar_one()
    assert touch.person_id is None
    assert touch.thread_id == thread_id


async def test_deleting_a_user_removes_their_outreach(session: AsyncSession) -> None:
    user = _user()
    session.add(user)
    await session.flush()
    session.add_all([_thread(user), OutreachSettings(user_id=user.id)])
    await session.commit()

    settings = (await session.execute(select(OutreachSettings))).scalar_one()
    assert settings.daily_target == 3  # no explicit target means the default

    await session.execute(delete(User))
    await session.commit()
    session.expire_all()

    assert (await session.execute(select(OutreachThread))).first() is None
    assert (await session.execute(select(OutreachSettings))).first() is None


async def test_a_company_gets_one_thread_per_user(session: AsyncSession) -> None:
    ada, ben = _user(), _user()
    company = Company(id=uuid.uuid4(), domain="acme.example", name="Acme Labs")
    session.add_all([ada, ben, company])
    await session.flush()
    ada_id = ada.id
    session.add(_thread(ada, company))
    await session.commit()

    session.add(_thread(ben, company))  # another user may open the same company
    await session.commit()

    session.add(_thread(ada, company))  # the same user may not
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # A typed company has no company_id: any number of them can coexist.
    session.add_all(
        [
            OutreachThread(
                id=uuid.uuid4(), user_id=ada_id, company_name=name, source="typed"
            )
            for name in ("Unlisted One", "Unlisted Two")
        ]
    )
    await session.commit()
    mine = await session.execute(select(OutreachThread).where(OutreachThread.user_id == ada_id))
    assert len(mine.all()) == 3


async def test_a_contact_joins_a_thread_once(session: AsyncSession) -> None:
    user = _user()
    company = Company(id=uuid.uuid4(), domain="acme.example", name="Acme Labs")
    session.add_all([user, company])
    await session.flush()
    contact = CompanyContact(id=uuid.uuid4(), company_id=company.id, kind="founder", name="Asha Rao")
    thread = _thread(user, company)
    session.add_all([contact, thread])
    await session.flush()
    contact_id, thread_id = contact.id, thread.id

    def person(**fields: Any) -> OutreachPerson:
        return OutreachPerson(id=uuid.uuid4(), thread_id=thread_id, kind="founder", **fields)

    session.add(person(contact_id=contact_id, name="Asha Rao"))
    await session.commit()

    session.add(person(contact_id=contact_id, name="Asha Rao"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # People who are not contact rows (a name-only thread) are not constrained.
    session.add_all([person(name="Friend One"), person(name="Friend Two")])
    await session.commit()

    # Removing the contact row keeps the person, with the snapshot columns.
    await session.execute(delete(CompanyContact).where(CompanyContact.id == contact_id))
    await session.commit()
    session.expire_all()
    people = (await session.execute(select(OutreachPerson))).scalars().all()
    assert len(people) == 3
    assert all(each.contact_id is None for each in people)
    assert "Asha Rao" in {each.name for each in people}
