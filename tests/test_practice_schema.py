"""
tests/test_practice_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Practice's four tables (spec §5.1–5.4) and migration ``019_practice``.

* The ORM metadata is checked against the spec: columns, nullability, types,
  uniques and indexes. Every child foreign-key column must be led by an index,
  a unique constraint or the primary key, so ON DELETE CASCADE can find rows.
* The migration module is loaded by path (its file name starts with a digit)
  and checked for its revision ids, idempotent DDL, drop order, and for naming
  only Practice's own tables.
* The cascade and uniqueness tests run on in-memory SQLite with
  ``PRAGMA foreign_keys=ON`` on every connection. They never touch the shared
  Postgres test database.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint, delete, event, func, select
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.elements import ColumnElement

from storage.database import Base
from storage.models import (
    ATTEMPT_STATUSES,
    PracticeAttempt,
    PracticeChallenge,
    PracticeDay,
    PracticeSettings,
    StringArray,
    User,
)

PRACTICE_TABLES = ("practice_days", "practice_challenges", "practice_attempts", "practice_settings")
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "storage" / "migrations" / "versions" / "019_practice.py"
)
TSTZ = "TIMESTAMP WITH TIME ZONE"

# ---------------------------------------------------------------------------
# The spec, written out (spec §5.1–5.4)
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "practice_days": (
        "id",
        "user_id",
        "day",
        "question_slug",
        "topic",
        "difficulty",
        "swaps",
        "done_at",
        "feel",
        "created_at",
    ),
    "practice_challenges": (
        "id",
        "slug",
        "brief_slug",
        "title",
        "track",
        "hours",
        "brief",
        "opens_at",
        "closes_at",
        "created_at",
    ),
    "practice_attempts": (
        "id",
        "user_id",
        "challenge_id",
        "status",
        "started_at",
        "deadline_at",
        "proof_code",
        "repo_owner",
        "repo_name",
        "writeup",
        "video_url",
        "submitted_at",
        "repo_created_at",
        "snapshot_sha",
        "snapshot_pushed_at",
        "signals",
        "scores",
        "total",
        "notes",
        "judge_runs",
        "judge_input_sha256",
        "judge_attempts",
        "next_judge_at",
        "reject_reason",
        "flags",
        "held",
        "appeal_reason",
        "appealed_at",
        "judged_at",
        "override_note",
    ),
    "practice_settings": (
        "user_id",
        "display_name",
        "show_repo",
        "contest_reminder",
        "updated_at",
    ),
}

# Nullable columns. A "started" attempt exists before any repo or write-up is
# saved, so repo_owner, repo_name and writeup are nullable even though the
# spec table does not say "null" for them.
NULLABLE: dict[str, frozenset[str]] = {
    "practice_days": frozenset({"done_at", "feel"}),
    "practice_challenges": frozenset(),
    "practice_attempts": frozenset(
        {
            "repo_owner",
            "repo_name",
            "writeup",
            "video_url",
            "submitted_at",
            "repo_created_at",
            "snapshot_sha",
            "snapshot_pushed_at",
            "signals",
            "scores",
            "total",
            "notes",
            "judge_runs",
            "judge_input_sha256",
            "next_judge_at",
            "reject_reason",
            "appeal_reason",
            "appealed_at",
            "judged_at",
            "override_note",
        }
    ),
    "practice_settings": frozenset({"display_name"}),
}

EXPECTED_TYPES: dict[str, dict[str, str]] = {
    "practice_days": {
        "id": "UUID",
        "user_id": "UUID",
        "day": "DATE",
        "question_slug": "VARCHAR(128)",
        "topic": "VARCHAR(48)",
        "difficulty": "VARCHAR(8)",
        "swaps": "SMALLINT",
        "done_at": TSTZ,
        "feel": "VARCHAR(8)",
        "created_at": TSTZ,
    },
    "practice_challenges": {
        "id": "UUID",
        "slug": "VARCHAR(96)",
        "brief_slug": "VARCHAR(80)",
        "title": "VARCHAR(160)",
        "track": "VARCHAR(16)",
        "hours": "SMALLINT",
        "brief": "JSONB",
        "opens_at": TSTZ,
        "closes_at": TSTZ,
        "created_at": TSTZ,
    },
    "practice_attempts": {
        "id": "UUID",
        "user_id": "UUID",
        "challenge_id": "UUID",
        "status": "VARCHAR(16)",
        "started_at": TSTZ,
        "deadline_at": TSTZ,
        "proof_code": "CHAR(10)",
        "repo_owner": "VARCHAR(39)",
        "repo_name": "VARCHAR(100)",
        "writeup": "JSONB",
        "video_url": "VARCHAR(300)",
        "submitted_at": TSTZ,
        "repo_created_at": TSTZ,
        "snapshot_sha": "CHAR(40)",
        "snapshot_pushed_at": TSTZ,
        "signals": "JSONB",
        "scores": "JSONB",
        "total": "NUMERIC(4, 1)",
        "notes": "JSONB",
        "judge_runs": "JSONB",
        "judge_input_sha256": "CHAR(64)",
        "judge_attempts": "SMALLINT",
        "next_judge_at": TSTZ,
        "reject_reason": "VARCHAR(64)",
        "flags": "VARCHAR[]",
        "held": "BOOLEAN",
        "appeal_reason": "VARCHAR(500)",
        "appealed_at": TSTZ,
        "judged_at": TSTZ,
        "override_note": "VARCHAR(500)",
    },
    "practice_settings": {
        "user_id": "UUID",
        "display_name": "VARCHAR(32)",
        "show_repo": "BOOLEAN",
        "contest_reminder": "BOOLEAN",
        "updated_at": TSTZ,
    },
}

EXPECTED_UNIQUES: dict[str, dict[str, tuple[str, ...]]] = {
    "practice_days": {"uq_practice_days_user_day": ("user_id", "day")},
    "practice_challenges": {"uq_practice_challenges_slug": ("slug",)},
    "practice_attempts": {
        "uq_practice_attempts_user_challenge": ("user_id", "challenge_id"),
        "uq_practice_attempts_proof_code": ("proof_code",),
    },
    "practice_settings": {},
}

EXPECTED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
    "practice_days": {"ix_practice_days_user_done": ("user_id", "done_at")},
    "practice_challenges": {"uq_practice_challenges_opens_at": ("opens_at",)},
    "practice_attempts": {
        "ix_practice_attempts_board": ("challenge_id", "status", "total"),
        "ix_practice_attempts_due": ("status", "next_judge_at"),
    },
    "practice_settings": {},
}

MODELS = {
    "practice_days": PracticeDay,
    "practice_challenges": PracticeChallenge,
    "practice_attempts": PracticeAttempt,
    "practice_settings": PracticeSettings,
}


def _table(name: str) -> Any:
    return MODELS[name].__table__


def _unique_constraints(name: str) -> dict[str, tuple[str, ...]]:
    uniques: dict[str, tuple[str, ...]] = {}
    for constraint in _table(name).constraints:
        if isinstance(constraint, UniqueConstraint):
            assert isinstance(constraint.name, str)
            uniques[constraint.name] = tuple(column.name for column in constraint.columns)
    return uniques


def _indexes(name: str) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns) for index in _table(name).indexes
    }


# SQLAlchemy does not annotate the dialect constructor; the call is safe.
_PG_DIALECT = PGDialect()  # type: ignore[no-untyped-call]


def _compiled_type(name: str, column: str) -> str:
    return str(_table(name).c[column].type.compile(dialect=_PG_DIALECT))


# ---------------------------------------------------------------------------
# ORM metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_table_has_exactly_the_spec_columns(table: str) -> None:
    assert tuple(_table(table).c.keys()) == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_nullability_matches_the_spec(table: str) -> None:
    nullable = {column.name for column in _table(table).c if column.nullable}
    assert nullable == NULLABLE[table]


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_column_types_match_the_spec(table: str) -> None:
    actual = {column: _compiled_type(table, column) for column in EXPECTED_TYPES[table]}
    assert actual == EXPECTED_TYPES[table]


def test_flags_is_a_string_array_and_json_columns_are_jsonb() -> None:
    assert isinstance(_table("practice_attempts").c.flags.type, StringArray)
    for table, column in (
        ("practice_attempts", "writeup"),
        ("practice_attempts", "signals"),
        ("practice_attempts", "notes"),
        ("practice_attempts", "judge_runs"),
        ("practice_challenges", "brief"),
    ):
        assert _compiled_type(table, column) == "JSONB"


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_uniques_match_the_spec(table: str) -> None:
    assert _unique_constraints(table) == EXPECTED_UNIQUES[table]


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_indexes_match_the_spec(table: str) -> None:
    assert _indexes(table) == EXPECTED_INDEXES[table]


def test_an_opening_is_unique_so_two_runs_cannot_make_two_editions() -> None:
    (index,) = _table("practice_challenges").indexes
    assert index.name == "uq_practice_challenges_opens_at"
    assert index.unique is True


def test_primary_keys_are_the_spec_keys() -> None:
    assert tuple(column.name for column in _table("practice_settings").primary_key) == ("user_id",)
    for table in ("practice_days", "practice_challenges", "practice_attempts"):
        assert tuple(column.name for column in _table(table).primary_key) == ("id",)


def _leading_columns(name: str) -> set[str]:
    """The first column of every index, unique constraint and primary key."""
    leads: set[str] = set()
    for index in _table(name).indexes:
        leads.add(next(iter(index.columns)).name)
    for constraint in _table(name).constraints:
        if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint):
            leads.add(next(iter(constraint.columns)).name)
    return leads


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_every_child_foreign_key_is_led_by_an_index(table: str) -> None:
    # ON DELETE CASCADE looks up child rows by their foreign key. Each one must
    # lead an existing index, unique constraint or primary key, so no extra
    # index is needed and no cascade falls back to a table scan.
    foreign_key_columns = {fk.parent.name for fk in _table(table).foreign_keys}
    assert foreign_key_columns <= _leading_columns(table)


@pytest.mark.parametrize("table", PRACTICE_TABLES)
def test_every_foreign_key_cascades_on_delete(table: str) -> None:
    for foreign_key in _table(table).foreign_keys:
        assert foreign_key.ondelete == "CASCADE", foreign_key.parent.name


def test_attempt_statuses_are_the_spec_statuses() -> None:
    assert ATTEMPT_STATUSES == (
        "started",
        "submitted",
        "queued",
        "judged",
        "rejected",
        "judge_failed",
        "missed",
    )


# ---------------------------------------------------------------------------
# Migration 019_practice
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("practice_migration_019", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain(migration: ModuleType) -> None:
    assert migration.revision == "019_practice"
    assert migration.down_revision == "018_events"


_TABLE_STATEMENT = re.compile(
    r"\b(?:CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?"
    r"|ALTER\s+TABLE(?:\s+IF\s+EXISTS)?"
    r"|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+(\w+)",
    re.IGNORECASE,
)
_INDEX_ON_TABLE = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)",
    re.IGNORECASE,
)


def test_migration_touches_only_practice_tables() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    named = set(_TABLE_STATEMENT.findall(source)) | set(_INDEX_ON_TABLE.findall(source))
    assert named, "the migration should name the practice tables"
    assert all(name.startswith("practice_") for name in named)
    assert named == set(PRACTICE_TABLES)


def test_every_create_and_drop_in_the_migration_is_idempotent() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    bare_creates = re.findall(
        r"\bCREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(?!IF\s+NOT\s+EXISTS)", source, re.IGNORECASE
    )
    bare_drops = re.findall(r"\bDROP\s+(?:TABLE|INDEX)\s+(?!IF\s+EXISTS)", source, re.IGNORECASE)
    assert bare_creates == []
    assert bare_drops == []


def test_upgrade_creates_the_parent_challenges_table_first(migration: ModuleType) -> None:
    created = re.findall(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", inspect.getsource(migration.upgrade)
    )
    assert created.index("practice_challenges") < created.index("practice_attempts")
    assert set(created) == set(PRACTICE_TABLES)


def test_upgrade_makes_the_opening_unique(
    migration: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda sql: statements.append(str(sql)))

    migration.upgrade()

    assert any(
        re.fullmatch(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_challenges_opens_at "
            r"ON practice_challenges \(opens_at\);",
            statement.strip(),
        )
        for statement in statements
    )
    assert not any("ix_practice_challenges_opens_at" in statement for statement in statements)


def test_downgrade_drops_the_child_attempts_table_first(migration: ModuleType) -> None:
    dropped = re.findall(
        r"DROP\s+TABLE\s+IF\s+EXISTS\s+(\w+)", inspect.getsource(migration.downgrade)
    )
    assert dropped.index("practice_attempts") < dropped.index("practice_challenges")
    assert set(dropped) == set(PRACTICE_TABLES)


# ---------------------------------------------------------------------------
# Behaviour on SQLite with foreign keys enforced
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [Base.metadata.tables[name] for name in ("users", *PRACTICE_TABLES)]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _add_user(db: AsyncSession, name: str) -> uuid.UUID:
    user = User(email=f"{name}@example.com", hashed_password="not-a-real-hash")
    db.add(user)
    await db.flush()
    return user.id


def _challenge(slug: str = "relay-2026-10-16") -> PracticeChallenge:
    # Opens Fri 16 Oct 2026, 18:00 IST (12:30 UTC); closes a week later.
    return PracticeChallenge(
        slug=slug,
        brief_slug="relay",
        title="Idempotent webhook relay",
        track="backend",
        hours=48,
        brief={"summary": "Receive, dedupe and replay payment webhooks."},
        opens_at=datetime(2026, 10, 16, 12, 30, tzinfo=UTC),
        closes_at=datetime(2026, 10, 23, 12, 30, tzinfo=UTC),
    )


def _attempt(user_id: uuid.UUID, challenge_id: uuid.UUID, proof_code: str) -> PracticeAttempt:
    return PracticeAttempt(
        user_id=user_id,
        challenge_id=challenge_id,
        status="started",
        started_at=datetime(2026, 10, 16, 13, 0, tzinfo=UTC),
        deadline_at=datetime(2026, 10, 18, 13, 0, tzinfo=UTC),
        proof_code=proof_code,
    )


def _day(user_id: uuid.UUID, day: date = date(2026, 10, 16)) -> PracticeDay:
    return PracticeDay(
        user_id=user_id,
        day=day,
        question_slug="course-schedule",
        topic="graph",
        difficulty="medium",
    )


async def _count(db: AsyncSession, model: Any, criterion: ColumnElement[bool] | None = None) -> int:
    statement = select(func.count()).select_from(model)
    if criterion is not None:
        statement = statement.where(criterion)
    return int((await db.execute(statement)).scalar_one())


async def test_deleting_a_user_removes_their_practice_rows(session: AsyncSession) -> None:
    ravi = await _add_user(session, "ravi")
    asha = await _add_user(session, "asha")
    challenge = _challenge()
    session.add(challenge)
    await session.flush()
    for user_id, code in ((ravi, "R3AV1PROOF"), (asha, "A5HA2PROOF")):
        session.add(_day(user_id))
        session.add(_attempt(user_id, challenge.id, code))
        session.add(PracticeSettings(user_id=user_id))
    await session.commit()

    await session.execute(delete(User).where(User.id == ravi))
    await session.commit()

    assert await _count(session, PracticeDay, PracticeDay.user_id == ravi) == 0
    assert await _count(session, PracticeAttempt, PracticeAttempt.user_id == ravi) == 0
    assert await _count(session, PracticeSettings, PracticeSettings.user_id == ravi) == 0
    assert await _count(session, PracticeDay, PracticeDay.user_id == asha) == 1
    assert await _count(session, PracticeAttempt, PracticeAttempt.user_id == asha) == 1
    assert await _count(session, PracticeSettings, PracticeSettings.user_id == asha) == 1
    assert await _count(session, PracticeChallenge) == 1


async def test_deleting_a_challenge_removes_its_attempts(session: AsyncSession) -> None:
    ravi = await _add_user(session, "ravi")
    challenge = _challenge()
    session.add(challenge)
    await session.flush()
    session.add(_attempt(ravi, challenge.id, "R3AV1PROOF"))
    await session.commit()

    await session.execute(delete(PracticeChallenge).where(PracticeChallenge.id == challenge.id))
    await session.commit()

    assert await _count(session, PracticeAttempt) == 0
    assert await _count(session, User) == 1


async def test_an_attempt_needs_an_existing_challenge(session: AsyncSession) -> None:
    ravi = await _add_user(session, "ravi")
    session.add(_attempt(ravi, uuid.uuid4(), "R3AV1PROOF"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_one_attempt_per_user_per_challenge(session: AsyncSession) -> None:
    ravi = await _add_user(session, "ravi")
    challenge = _challenge()
    session.add(challenge)
    await session.flush()
    session.add(_attempt(ravi, challenge.id, "R3AV1PROOF"))
    await session.flush()
    session.add(_attempt(ravi, challenge.id, "R3AV2PROOF"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_one_edition_per_opening(session: AsyncSession) -> None:
    session.add(_challenge("relay-2026-10-16"))
    await session.flush()
    session.add(_challenge("ledger-2026-10-16"))  # another brief, the same opening
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_one_practice_day_per_user_per_day(session: AsyncSession) -> None:
    ravi = await _add_user(session, "ravi")
    session.add(_day(ravi))
    await session.flush()
    session.add(_day(ravi))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_proof_code_belongs_to_one_attempt(session: AsyncSession) -> None:
    ravi = await _add_user(session, "ravi")
    asha = await _add_user(session, "asha")
    challenge = _challenge()
    session.add(challenge)
    await session.flush()
    session.add(_attempt(ravi, challenge.id, "SAMEPROOF1"))
    await session.flush()
    session.add(_attempt(asha, challenge.id, "SAMEPROOF1"))
    with pytest.raises(IntegrityError):
        await session.flush()
