"""
tests/test_event_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Migration 018 and the four event models (spec §5.1-5.4): the columns, the
foreign-key actions (checked in both the ORM and the migration's SQL), the
indexes that keep every child foreign key findable, the migration chain, and
the cascades. The cascades run on SQLite with ``PRAGMA foreign_keys=ON`` so
the database itself, not the ORM, does the deleting.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import UniqueConstraint, delete, event, select
from sqlalchemy.dialects import postgresql
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

# Every foreign key of the four tables, with its ON DELETE action. The ORM test
# and the migration test both check against this one table, so the two cannot
# drift apart: a change to either side has to change this table too.
FOREIGN_KEY_ACTIONS: tuple[tuple[str, str, str, str | None], ...] = (
    ("events", "duplicate_of", "events.id", "SET NULL"),
    ("events", "owner_user_id", "users.id", "CASCADE"),
    ("event_companies", "event_id", "events.id", "CASCADE"),
    ("event_companies", "company_id", "companies.id", "CASCADE"),
    ("event_saves", "user_id", "users.id", "CASCADE"),
    ("event_saves", "event_id", "events.id", "CASCADE"),
    ("event_contacts", "user_id", "users.id", "CASCADE"),
    ("event_contacts", "event_id", "events.id", "CASCADE"),
    ("event_contacts", "company_id", "companies.id", "SET NULL"),
)


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


def test_event_url_is_optional() -> None:
    # A manual event may have no link (spec §7: POST /events/manual, url optional).
    assert Event.__table__.c.url.nullable


# ─────────────────────────────────────────────────────────────────────────────
# Foreign keys: the target and the ON DELETE action (spec §5.1-5.4)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("table", "column", "target", "ondelete"), FOREIGN_KEY_ACTIONS)
def test_foreign_key_targets_and_actions(
    table: str, column: str, target: str, ondelete: str | None
) -> None:
    foreign_keys = list(Base.metadata.tables[table].c[column].foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == target
    assert foreign_keys[0].ondelete == ondelete


def test_orm_foreign_keys_are_exactly_the_table() -> None:
    found = {
        (name, fk.parent.name, fk.target_fullname, fk.ondelete)
        for name in EVENT_TABLES
        for fk in Base.metadata.tables[name].foreign_keys
    }
    assert found == set(FOREIGN_KEY_ACTIONS)


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
# Migration 018: the chain, IF NOT EXISTS, the downgrade order, and the SQL
# ─────────────────────────────────────────────────────────────────────────────


def test_migration_018_follows_017() -> None:
    module = _load_migration()
    assert module.revision == "018_events"
    assert module.down_revision == "017_outreach"


class _RecordingOp:
    """Stands in for ``alembic.op`` and keeps each statement, whitespace normalised."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(" ".join(sql.split()))


def _upgrade_statements(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    module = _load_migration()
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)
    module.upgrade()
    return recorder.statements


def test_migration_018_upgrade_creates_each_table_if_not_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements = _upgrade_statements(monkeypatch)
    creates = [s for s in statements if s.startswith("CREATE")]
    assert creates
    assert all("IF NOT EXISTS" in statement for statement in creates)
    for table in EVENT_TABLES:
        assert any(
            statement.startswith(f"CREATE TABLE IF NOT EXISTS {table} (")
            for statement in statements
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


def test_migration_018_event_url_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _upgrade_statements(monkeypatch)
    events_sql = next(s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS events ("))
    assert "url VARCHAR(1024), " in events_sql
    assert "url VARCHAR(1024) NOT NULL" not in events_sql


_REFERENCES = re.compile(
    r"(\w+) UUID[^,]*? REFERENCES (\w+)\((\w+)\)(?: ON DELETE (CASCADE|SET NULL))?"
)


def _migration_foreign_keys(statements: list[str]) -> set[tuple[str, str, str, str | None]]:
    """Every column REFERENCES clause in the recorded CREATE TABLE statements."""
    found: set[tuple[str, str, str, str | None]] = set()
    for statement in statements:
        created = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \(", statement)
        if created is None:
            continue
        for column, target_table, target_column, ondelete in _REFERENCES.findall(statement):
            found.add(
                (created.group(1), column, f"{target_table}.{target_column}", ondelete or None)
            )
    return found


def test_migration_on_delete_clauses_match_the_orm(monkeypatch: pytest.MonkeyPatch) -> None:
    in_migration = _migration_foreign_keys(_upgrade_statements(monkeypatch))
    assert in_migration == set(FOREIGN_KEY_ACTIONS)


# The migration and a schema built from the model (``create_all``: ``make seed`` on a fresh
# database, and the pytest ``db_session``) must give a column the same PostgreSQL type, or a
# query that is right on one is a 500 on the other (``varchar[] @> text[]`` has no operator).

_COLUMN_END = re.compile(
    r"\s+(?:NOT NULL|NULL|DEFAULT|PRIMARY KEY|REFERENCES|UNIQUE|CONSTRAINT)\b.*$"
)
# The spellings SQLAlchemy and the migration use for one and the same PostgreSQL type.
_SAME_TYPE = {
    "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE",
    "DOUBLE PRECISION": "FLOAT",
}


def _top_level_parts(body: str) -> list[str]:
    """Split a CREATE TABLE body at the commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    start = 0
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


def _canonical_type(spelling: str) -> str:
    text = " ".join(spelling.upper().split()).replace("CHARACTER VARYING", "VARCHAR")
    return _SAME_TYPE.get(text, text)


def _migration_column_types(statements: list[str]) -> dict[tuple[str, str], str]:
    """``{(table, column): type}`` for every column of the CREATE TABLE statements."""
    found: dict[tuple[str, str], str] = {}
    for statement in statements:
        created = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*)\);?$", statement)
        if created is None:
            continue
        for part in _top_level_parts(created.group(2)):
            if re.match(r"(?:CONSTRAINT|PRIMARY KEY|UNIQUE|FOREIGN KEY)\b", part):
                continue
            name, _, rest = part.partition(" ")
            found[(created.group(1), name)] = _canonical_type(_COLUMN_END.sub("", rest))
    return found


def _model_column_types() -> dict[tuple[str, str], str]:
    dialect = postgresql.dialect()
    return {
        (table, column.name): _canonical_type(column.type.compile(dialect=dialect))
        for table in EVENT_TABLES
        for column in Base.metadata.tables[table].columns
    }


def test_migration_column_types_match_the_orm(monkeypatch: pytest.MonkeyPatch) -> None:
    in_migration = _migration_column_types(_upgrade_statements(monkeypatch))
    in_model = _model_column_types()

    assert set(in_migration) == set(in_model)  # the same columns...
    differing = {
        key: (in_migration[key], in_model[key])
        for key in in_migration
        if in_migration[key] != in_model[key]
    }
    assert differing == {}, "(migration, model) type per column"  # ...of the same types


def test_event_array_columns_are_text_arrays_in_both() -> None:
    for name in ("hosts", "topics", "reasons"):
        assert _model_column_types()[("events", name)] == "TEXT[]", name


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


def _manual_event(owner_id: uuid.UUID) -> Event:
    """A private event a user added by hand: no link, visible only to its owner."""
    event_id = uuid.uuid4()
    return Event(
        id=event_id,
        source="user",
        external_id=str(event_id),
        url=None,
        title="Fictional dinner with the Zeta team",
        starts_at=datetime(2026, 10, 22, 19, 0, tzinfo=UTC),
        is_online=False,
        listed=False,
        owner_user_id=owner_id,
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
    user, ev = _user(), _event()
    fk_session.add_all([user, ev])
    await fk_session.commit()
    fk_session.add_all(
        [
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=ev.id, status="saved"),
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=ev.id, status="going"),
        ]
    )
    with pytest.raises(IntegrityError):
        await fk_session.commit()
    await fk_session.rollback()


async def test_a_manual_event_without_a_link_inserts(fk_session: AsyncSession) -> None:
    owner = _user()
    fk_session.add(owner)
    await fk_session.commit()
    manual = _manual_event(owner.id)
    fk_session.add(manual)
    await fk_session.commit()

    stored = (await fk_session.execute(select(Event).where(Event.id == manual.id))).scalar_one()
    assert stored.url is None
    assert stored.owner_user_id == owner.id


async def test_deleting_the_owner_deletes_their_private_event(fk_session: AsyncSession) -> None:
    owner, other = _user(), _user()
    shared = _event()
    fk_session.add_all([owner, other, shared])
    await fk_session.commit()
    private = _manual_event(owner.id)
    fk_session.add(private)
    await fk_session.commit()
    fk_session.add_all(
        [
            EventSave(id=uuid.uuid4(), user_id=owner.id, event_id=private.id, status="going"),
            EventSave(id=uuid.uuid4(), user_id=other.id, event_id=shared.id, status="saved"),
        ]
    )
    await fk_session.commit()

    await fk_session.execute(delete(User).where(User.id == owner.id))
    await fk_session.commit()

    # Privacy: the owner's private event is gone with them, and nobody else's row is touched.
    remaining = (await fk_session.execute(select(Event))).scalars().all()
    assert [e.id for e in remaining] == [shared.id]
    saves = (await fk_session.execute(select(EventSave))).scalars().all()
    assert [s.user_id for s in saves] == [other.id]


async def test_deleting_a_primary_clears_its_duplicates_link(fk_session: AsyncSession) -> None:
    primary = _event(title="Swift Bengaluru x Okta")
    duplicate = _event(title="Swift Bengaluru Meetup at Okta")
    duplicate.source = "eventbrite"
    duplicate.listed = False
    duplicate.duplicate_of = primary.id
    fk_session.add(primary)
    await fk_session.commit()
    fk_session.add(duplicate)
    await fk_session.commit()

    await fk_session.execute(delete(Event).where(Event.id == primary.id))
    await fk_session.commit()

    # The duplicate row is kept; refresh so the held object is not read from the identity map.
    await fk_session.refresh(duplicate)
    assert duplicate.duplicate_of is None


async def test_deleting_a_user_cascades_their_saves_and_contacts(
    fk_session: AsyncSession,
) -> None:
    user, ev = _user(), _event()
    fk_session.add_all([user, ev])
    await fk_session.commit()
    fk_session.add_all(
        [
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=ev.id, status="going"),
            EventContact(
                id=uuid.uuid4(),
                user_id=user.id,
                event_id=ev.id,
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
    assert [e.id for e in (await fk_session.execute(select(Event))).scalars().all()] == [ev.id]


async def test_deleting_an_event_cascades_its_links_saves_and_contacts(
    fk_session: AsyncSession,
) -> None:
    user, ev, company = _user(), _event(), _company()
    fk_session.add_all([user, ev, company])
    await fk_session.commit()
    fk_session.add_all(
        [
            EventCompany(
                event_id=ev.id,
                company_id=company.id,
                role="host",
                matched_on="host_name",
            ),
            EventSave(id=uuid.uuid4(), user_id=user.id, event_id=ev.id, status="saved"),
            EventContact(id=uuid.uuid4(), user_id=user.id, event_id=ev.id, name="Asha Example"),
        ]
    )
    await fk_session.commit()

    await fk_session.execute(delete(Event).where(Event.id == ev.id))
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
    user, ev, company = _user(), _event(), _company()
    fk_session.add_all([user, ev, company])
    await fk_session.commit()
    contact_id = uuid.uuid4()
    fk_session.add(
        EventContact(
            id=contact_id,
            user_id=user.id,
            event_id=ev.id,
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
