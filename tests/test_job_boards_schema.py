"""
tests/test_job_boards_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Migration 026: the ``job_boards`` table, the roster of public applicant-tracking-system
boards. Its place in the chain, idempotent and reversible SQL, the PostgreSQL types and the
index expression compared with the ORM model, and the rules the roster leans on: a region
makes a board unique, a missing region is one value, a company's delete clears the link.
The constraints run on SQLite with ``PRAGMA foreign_keys=ON``; the column types are compared
with the migration's SQL for PostgreSQL, as for the other schema tests.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Table, delete, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex

from domain.job_boards import BoardRef
from storage.database import Base
from storage.models import Company, JobBoard

VERSIONS = Path(__file__).resolve().parent.parent / "storage" / "migrations" / "versions"
MIGRATION = VERSIONS / "026_job_boards.py"
TABLE = "job_boards"
UNIQUE_INDEX = "uq_job_boards_ats_board_region"
EXPECTED_COLUMNS = {
    "id",
    "ats",
    "board",
    "region",
    "company_id",
    "company_name",
    "discovered_via",
    "status",
    "india_jobs",
    "total_jobs",
    "first_seen_at",
    "last_read_at",
    "last_india_at",
    "next_read_at",
    "failures",
    "zero_india_reads",
    "not_found_reads",
}


_PG_DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_026", MIGRATION)
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


# --- the chain and the SQL --------------------------------------------------------------


def test_migration_026_chain() -> None:
    module = _load_migration()
    assert module.revision == "026_job_boards"
    assert module.down_revision == "025_profile_routine"
    pointing_at_025 = [
        path.name
        for path in VERSIONS.glob("*.py")
        if re.search(
            r'down_revision[^=]*=\s*"025_profile_routine"', path.read_text(encoding="utf-8")
        )
    ]
    assert pointing_at_025 == ["026_job_boards.py"]


def test_upgrade_is_idempotent_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _statements(monkeypatch)
    creates = [s for s in statements if s.startswith("CREATE")]
    assert creates and all("IF NOT EXISTS" in s for s in creates)
    assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {TABLE} ") for s in statements)
    assert any(
        s.startswith(f"CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_INDEX} ") for s in statements
    )
    assert any(
        s.startswith("CREATE INDEX IF NOT EXISTS ix_job_boards_status_next_read ")
        for s in statements
    )
    assert any(
        s.startswith("CREATE INDEX IF NOT EXISTS ix_job_boards_company_id ") for s in statements
    )


def test_upgrade_changes_no_existing_table(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _statements(monkeypatch)
    assert not any(s.startswith(("ALTER", "UPDATE", "DELETE", "DROP")) for s in statements)
    assert not any(s.startswith("CREATE") and f" {TABLE} " not in s for s in statements)


def test_downgrade_drops_what_upgrade_made(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _statements(monkeypatch, "downgrade")
    assert statements == [f"DROP TABLE IF EXISTS {TABLE}"]


# --- PostgreSQL types and the index: migration against model ------------------------

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
    return {
        (TABLE, column.name): _canonical(column.type.compile(dialect=_PG_DIALECT))
        for column in Base.metadata.tables[TABLE].columns
    }


def test_drift_job_boards_postgres_types(monkeypatch: pytest.MonkeyPatch) -> None:
    in_migration = _migration_types(_statements(monkeypatch))
    in_model = _model_types()
    assert {name for _, name in in_model} == EXPECTED_COLUMNS
    assert set(in_migration) == set(in_model)
    differing = {
        k: (in_migration[k], in_model[k]) for k in in_migration if in_migration[k] != in_model[k]
    }
    assert differing == {}


def test_unique_index_expression_matches_model(monkeypatch: pytest.MonkeyPatch) -> None:
    index = next(i for i in Base.metadata.tables[TABLE].indexes if i.name == UNIQUE_INDEX)
    model_ddl = str(CreateIndex(index).compile(dialect=_PG_DIALECT))
    assert model_ddl == (
        f"CREATE UNIQUE INDEX {UNIQUE_INDEX} ON {TABLE} (ats, board, coalesce(region, ''))"
    )
    statement = next(
        s for s in _statements(monkeypatch) if f"INDEX IF NOT EXISTS {UNIQUE_INDEX}" in s
    )
    assert statement.endswith(f"ON {TABLE} (ats, board, coalesce(region, ''))")


# --- constraints and cascades on SQLite ---------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def board_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables: list[Table] = [Base.metadata.tables[name] for name in ("companies", TABLE)]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _board(**fields: Any) -> JobBoard:
    values: dict[str, Any] = {
        "ats": "greenhouse",
        "board": "fictionallabs",
        "discovered_via": "manual",
    }
    values.update(fields)
    return JobBoard(**values)


async def test_defaults(board_session: AsyncSession) -> None:
    row = _board()
    board_session.add(row)
    await board_session.flush()
    await board_session.refresh(row)
    assert row.status == "candidate"
    assert row.failures == 0
    assert row.zero_india_reads == 0
    assert row.not_found_reads == 0
    assert row.first_seen_at is not None
    assert row.region is None and row.company_id is None


async def test_same_board_with_no_region_is_one_row(board_session: AsyncSession) -> None:
    board_session.add(_board())
    await board_session.flush()
    board_session.add(_board())
    with pytest.raises(IntegrityError):
        await board_session.flush()


async def test_missing_and_empty_region_collide(board_session: AsyncSession) -> None:
    board_session.add(_board(region=None))
    await board_session.flush()
    board_session.add(_board(region=""))
    with pytest.raises(IntegrityError):
        await board_session.flush()


async def test_personio_hosts_are_two_boards(board_session: AsyncSession) -> None:
    board_session.add_all(
        [
            _board(ats="personio", region="de"),
            _board(ats="personio", region="com"),
        ]
    )
    await board_session.flush()
    rows = (await board_session.execute(select(JobBoard))).scalars().all()
    assert len(rows) == 2


async def test_company_delete_clears_the_link(board_session: AsyncSession) -> None:
    company = Company(id=uuid.uuid4(), name="Fictional Labs", slug="fl-board")
    board_session.add(company)
    await board_session.flush()
    board_session.add(_board(company_id=company.id, company_name=company.name))
    await board_session.flush()
    await board_session.execute(delete(Company).where(Company.id == company.id))
    board_session.expire_all()
    stored = (await board_session.execute(select(JobBoard))).scalar_one()
    assert stored.company_id is None
    assert stored.company_name == "Fictional Labs"


def test_ref_and_source_name_the_board() -> None:
    row = _board(ats="personio", board="acme", region="de")
    assert row.source == "personio:acme"
    assert row.ref == BoardRef(ats="personio", board="acme", region="de")
    assert _board().ref == BoardRef(ats="greenhouse", board="fictionallabs", region=None)
