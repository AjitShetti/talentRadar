"""
tests/test_event_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Migration 018 and the four event models (spec §5.1-5.4): the columns, the
foreign-key actions, the indexes that keep every child foreign key findable,
the migration chain, and the cascades. The cascades run on SQLite with
``PRAGMA foreign_keys=ON`` so the database itself, not the ORM, does the
deleting.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import UniqueConstraint, delete, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import Table

from storage.database import Base
from storage.models import (
    Company,
    Event,
    EventCompany,
    EventContact,
    EventSave,
    StringArray,
    User,
)

MIGRATION = (
    Path(__file__).resolve().parent.parent / "storage" / "migrations" / "versions" / "018_events.py"
)

EVENT_TABLES = ("events", "event_companies", "event_saves", "event_contacts")


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_018", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─────────────────────────────────────────────────────────────────────────────
# Columns, types and table names (spec §5.1-5.4)
# ─────────────────────────────────────────────────────────────────────────────


def test_table_names() -> None:
    assert Event.__tablename__ == "events"
    assert EventCompany.__tablename__ == "event_companies"
    assert EventSave.__tablename__ == "event_saves"
    assert EventContact.__tablename__ == "event_contacts"


def test_event_has_the_spec_columns() -> None:
    columns = set(Event.__table__.c.keys())
    assert columns >= {
        "source",
        "external_id",
        "url",
        "title",
        "snippet",
        "starts_at",
        "ends_at",
        "venue_name",
        "address",
        "area",
        "lat",
        "lng",
        "geo_precision",
        "is_online",
        "is_free",
        "organiser_name",
        "organiser_url",
        "hosts",
        "format",
        "topics",
        "listed",
        "reasons",
        "is_cancelled",
        "duplicate_of",
        "owner_user_id",
        "first_seen_at",
        "last_seen_at",
        "detail_fetched_at",
        "metadata",
        "created_at",
        "updated_at",
    }
    assert "id" in columns


def test_event_metadata_attribute_maps_to_the_metadata_column() -> None:
    assert isinstance(Event.__table__.c["metadata"].type, JSONB)
    assert Event.__mapper__.get_property("extra_metadata").columns[0].name == "metadata"


def test_event_list_columns_are_string_arrays() -> None:
    for name in ("hosts", "topics", "reasons"):
        assert isinstance(Event.__table__.c[name].type, StringArray), name


def test_event_companies_columns_and_composite_key() -> None:
    assert set(EventCompany.__table__.c.keys()) == {
        "event_id",
        "company_id",
        "role",
        "matched_on",
    }
    table = Base.metadata.tables["event_companies"]
    key = {column.name for column in table.primary_key.columns}
    assert key == {"event_id", "company_id"}


def test_event_saves_columns() -> None:
    assert set(EventSave.__table__.c.keys()) == {
        "id",
        "user_id",
        "event_id",
        "status",
        "note",
        "created_at",
        "updated_at",
    }


def test_event_contacts_columns() -> None:
    assert set(EventContact.__table__.c.keys()) == {
        "id",
        "user_id",
        "event_id",
        "name",
        "title",
        "company_name",
        "company_id",
        "linkedin_url",
        "x_url",
        "github_url",
        "email",
        "note",
        "created_at",
        "updated_at",
    }


def test_required_and_optional_columns() -> None:
    assert not Event.__table__.c.starts_at.nullable
    assert Event.__table__.c.ends_at.nullable
    assert not EventCompany.__table__.c.role.nullable
    assert not EventSave.__table__.c.user_id.nullable
    assert not EventSave.__table__.c.event_id.nullable
    assert not EventContact.__table__.c.name.nullable
    assert EventContact.__table__.c.company_id.nullable


# ─────────────────────────────────────────────────────────────────────────────
# Foreign keys: the target and the ON DELETE action (spec §5.1-5.4)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("table", "column", "target", "ondelete"),
    [
        ("events", "duplicate_of", "events.id", "SET NULL"),
        ("events", "owner_user_id", "users.id", "CASCADE"),
        ("event_companies", "event_id", "events.id", "CASCADE"),
        ("event_companies", "company_id", "companies.id", "CASCADE"),
        ("event_saves", "user_id", "users.id", "CASCADE"),
        ("event_saves", "event_id", "events.id", "CASCADE"),
        ("event_contacts", "user_id", "users.id", "CASCADE"),
        ("event_contacts", "event_id", "events.id", "CASCADE"),
        ("event_contacts", "company_id", "companies.id", "SET NULL"),
    ],
)
def test_foreign_key_targets_and_actions(
    table: str, column: str, target: str, ondelete: str
) -> None:
    foreign_keys = list(Base.metadata.tables[table].c[column].foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == target
    assert foreign_keys[0].ondelete == ondelete


# ─────────────────────────────────────────────────────────────────────────────
# Indexes and unique constraints in the ORM metadata
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
    "events": {
        "ix_events_starts_at": ("starts_at",),
        "ix_events_listed_starts_at": ("listed", "starts_at"),
        "ix_events_duplicate_of": ("duplicate_of",),
        "ix_events_owner_user_id": ("owner_user_id",),
        "uq_events_source_external_id": ("source", "external_id"),
    },
    "event_companies": {
        "ix_event_companies_company_id": ("company_id",),
    },
    "event_saves": {
        "uq_event_saves_user_event": ("user_id", "event_id"),
        "ix_event_saves_event_id": ("event_id",),
    },
    "event_contacts": {
        "ix_event_contacts_user_created": ("user_id", "created_at"),
        "ix_event_contacts_event_id": ("event_id",),
        "ix_event_contacts_company_id": ("company_id",),
    },
}


def _index_columns(table: Table) -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {
        str(index.name): tuple(column.name for column in index.columns) for index in table.indexes
    }
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.name:
            found[str(constraint.name)] = tuple(column.name for column in constraint.columns)
    return found


@pytest.mark.parametrize("table_name", EVENT_TABLES)
def test_expected_indexes_are_in_the_metadata(table_name: str) -> None:
    found = _index_columns(Base.metadata.tables[table_name])
    for name, columns in EXPECTED_INDEXES[table_name].items():
        assert found.get(name) == columns, name


@pytest.mark.parametrize("table_name", EVENT_TABLES)
def test_every_child_foreign_key_is_led_by_an_index(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    leading = {columns[0] for columns in _index_columns(table).values()}
    leading.add(next(iter(table.primary_key.columns)).name)
    for foreign_key in table.foreign_keys:
        column = foreign_key.parent.name
        assert column in leading, f"{table_name}.{column} has no leading index"


# ─────────────────────────────────────────────────────────────────────────────
# Migration 018: the chain, IF NOT EXISTS, and the downgrade order
# ─────────────────────────────────────────────────────────────────────────────


def test_migration_018_follows_016() -> None:
    module = _load_migration()
    assert module.revision == "018_events"
    assert module.down_revision == "016_company_is_tech"


class _RecordingOp:
    """Stands in for ``alembic.op`` and keeps each statement, whitespace normalised."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(" ".join(sql.split()))


def test_migration_018_upgrade_creates_each_table_if_not_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration()
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)

    module.upgrade()

    creates = [s for s in recorder.statements if s.startswith("CREATE")]
    assert creates
    assert all("IF NOT EXISTS" in statement for statement in creates)
    for table in EVENT_TABLES:
        assert any(
            statement.startswith(f"CREATE TABLE IF NOT EXISTS {table} (")
            for statement in recorder.statements
        ), table


def test_migration_018_downgrade_drops_children_before_parents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration()
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)

    module.downgrade()

    dropped = [
        statement.removeprefix("DROP TABLE IF EXISTS ").rstrip(";")
        for statement in recorder.statements
        if statement.startswith("DROP TABLE")
    ]
    assert dropped == ["event_contacts", "event_saves", "event_companies", "events"]


# ─────────────────────────────────────────────────────────────────────────────
# Cascades, on SQLite with the foreign keys actually enforced
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def fk_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables: list[Table] = [
        Base.metadata.tables[name] for name in ("users", "companies", *EVENT_TABLES)
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        hashed_password="not-a-real-hash",
    )


def _event(title: str = "Fictional Rust Meetup") -> Event:
    return Event(
        id=uuid.uuid4(),
        source="luma",
        external_id=f"evt-{uuid.uuid4().hex[:10]}",
        url="https://lu.ma/fictional-rust-meetup",
        title=title,
        starts_at=datetime(2026, 10, 20, 13, 0, tzinfo=UTC),
        is_online=False,
        listed=True,
    )


def _company() -> Company:
    return Company(
        id=uuid.uuid4(),
        name="Acme Fictional Labs",
        domain=f"{uuid.uuid4().hex[:8]}.example.test",
    )


async def test_foreign_keys_are_enforced_by_the_test_engine(fk_session: AsyncSession) -> None:
    fk_session.add(
        EventSave(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            status="saved",
        )
    )
    with pytest.raises(IntegrityError):
        await fk_session.commit()
    await fk_session.rollback()


async def test_one_save_per_user_per_event(fk_session: AsyncSession) -> None:
    user, event = _user(), _event()
    fk_session.add_all([user, event])
    await fk_session.commit()
    fk_session.add_all(
        [
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=event.id, status="saved"),
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=event.id, status="going"),
        ]
    )
    with pytest.raises(IntegrityError):
        await fk_session.commit()
    await fk_session.rollback()


async def test_deleting_a_user_cascades_their_saves_and_contacts(
    fk_session: AsyncSession,
) -> None:
    user, event = _user(), _event()
    fk_session.add_all([user, event])
    await fk_session.commit()
    fk_session.add_all(
        [
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=event.id, status="going"),
            EventContact(
                id=uuid.uuid4(),
                user_id=user.id,
                event_id=event.id,
                name="Ravi Example",
                note="Met by the demo table",
            ),
        ]
    )
    await fk_session.commit()

    await fk_session.execute(delete(User).where(User.id == user.id))
    await fk_session.commit()

    assert (await fk_session.execute(select(EventSave))).scalars().all() == []
    assert (await fk_session.execute(select(EventContact))).scalars().all() == []
    # The event is shared, not the user's: it survives their deletion.
    assert [e.id for e in (await fk_session.execute(select(Event))).scalars().all()] == [event.id]


async def test_deleting_an_event_cascades_its_links_saves_and_contacts(
    fk_session: AsyncSession,
) -> None:
    user, event, company = _user(), _event(), _company()
    fk_session.add_all([user, event, company])
    await fk_session.commit()
    fk_session.add_all(
        [
            EventCompany(
                event_id=event.id,
                company_id=company.id,
                role="host",
                matched_on="host_name",
            ),
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=event.id, status="saved"),
            EventContact(id=uuid.uuid4(), user_id=user.id, event_id=event.id, name="Asha Example"),
        ]
    )
    await fk_session.commit()

    await fk_session.execute(delete(Event).where(Event.id == event.id))
    await fk_session.commit()

    assert (await fk_session.execute(select(EventCompany))).scalars().all() == []
    assert (await fk_session.execute(select(EventSave))).scalars().all() == []
    assert (await fk_session.execute(select(EventContact))).scalars().all() == []
    # The company on the map is not the event's: it survives.
    assert [c.id for c in (await fk_session.execute(select(Company))).scalars().all()] == [
        company.id
    ]


async def test_deleting_a_company_keeps_the_contact_and_clears_the_link(
    fk_session: AsyncSession,
) -> None:
    user, event, company = _user(), _event(), _company()
    fk_session.add_all([user, event, company])
    await fk_session.commit()
    contact_id = uuid.uuid4()
    fk_session.add(
        EventContact(
            id=contact_id,
            user_id=user.id,
            event_id=event.id,
            name="Meera Example",
            company_name="Acme Fictional Labs",
            company_id=company.id,
        )
    )
    await fk_session.commit()

    await fk_session.execute(delete(Company).where(Company.id == company.id))
    await fk_session.commit()

    contact = (
        await fk_session.execute(select(EventContact).where(EventContact.id == contact_id))
    ).scalar_one()
    assert contact.company_id is None
    assert contact.company_name == "Acme Fictional Labs"
