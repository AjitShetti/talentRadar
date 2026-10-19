"""
tests/test_practice_challenges.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The weekly take-home: ``services/practice/challenges.py``, ``services/practice/github_reader.py``
and the challenge routes of ``/api/v1/practice``.

* Services run on SQLite in a temporary file with ``PRAGMA foreign_keys=ON`` on every
  connection and the module's ``AsyncSessionLocal`` pointed at it. The shared Postgres test
  database is never touched.
* GitHub is a ``httpx.MockTransport`` serving the ``submission/`` fixtures. Nothing here
  requests a real host.
* The briefs are three synthetic, fictional files in a temporary directory; the twelve real
  ones are a later task.
* Every producer test pins its payload's key set to the type the Practice pages declare in
  ``frontend/lib/api.ts`` (plan Task 15).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.practice_repo import CommitLine, Push, RepoRef, TreeEntry
from domain.practice_schedule import GRACE, last_start
from services import cache_backend
from services.cache_backend import CacheBackend
from services.practice import challenges, github_reader
from services.practice.errors import (
    PracticeConflict,
    PracticeInvalid,
    PracticeNotFound,
    PracticeRateLimited,
)
from storage.database import Base
from storage.models import PracticeAttempt, PracticeChallenge, PracticeDay, User
from tests.test_practice_fixtures import load_fixture

# --------------------------------------------------------------------------- the clock

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # Thursday 11:30 IST
OPENS = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)  # Friday 18:00 IST
CLOSES = OPENS + timedelta(days=7)
STARTED = datetime(2026, 10, 17, 4, 30, tzinfo=UTC)  # Saturday 10:00 IST
SAVE_AT = STARTED + timedelta(hours=28)
DEADLINE_48 = STARTED + timedelta(hours=48)

# --------------------------------------------------------------------------- Task 15 shapes

CHALLENGE_KEYS = {"slug", "opens_at", "closes_at", "last_start_at", "brief", "attempt"}
BRIEF_KEYS = {
    "slug",
    "title",
    "track",
    "hours",
    "summary",
    "context",
    "must_have",
    "nice_to_have",
    "constraints",
    "deliverables",
    "judge_focus",
    "bengaluru_context",
}
ATTEMPT_KEYS = {
    "status",
    "started_at",
    "deadline_at",
    "proof_code",
    "repo_url",
    "writeup",
    "video_url",
    "submitted_at",
    "editable_until",
    "total",
}
WRITEUP_KEYS = {"decisions", "tradeoffs", "ai_usage", "next_steps"}
OVERVIEW_KEYS = {"current", "next_opens_at", "past"}
PAST_KEYS = {"slug", "title", "track", "opens_at", "status", "total"}

OWNER, NAME = "demo-builder", "webhook-relay"
REPO_URL = f"https://github.com/{OWNER}/{NAME}"
REPO_API = f"/repos/{OWNER}/{NAME}"


# --------------------------------------------------------------------------- briefs


def make_brief(slug: str, *, track: str = "backend", hours: int = 48) -> dict[str, Any]:
    """A synthetic, fictional brief that satisfies the schema."""
    return {
        "slug": slug,
        "title": f"Sample build {slug}",
        "track": track,
        "hours": hours,
        "summary": f"One line about {slug}.",
        "context": ["A fictional team needs this.", "Keep it small."],
        "must_have": ["Does the thing.", "Says how to run it."],
        "nice_to_have": ["A second thing."],
        "constraints": ["No paid APIs."],
        "deliverables": ["Public repo with a README."],
        "judge_focus": ["How the edge cases are handled."],
        "bengaluru_context": "Teams at fictional Examplepay-like companies own this work.",
    }


def write_briefs(directory: Path, briefs: list[dict[str, Any]]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, brief in enumerate(briefs, start=1):
        path = directory / f"{number:02d}-{brief['slug']}.json"
        path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
        paths.append(path)
    return paths


BRIEFS = [
    make_brief("relay-sample", track="backend", hours=48),
    make_brief("grid-sample", track="frontend", hours=24),
    make_brief("tracker-sample", track="data", hours=48),
]


@pytest.fixture
def briefs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "briefs"
    write_briefs(directory, BRIEFS)
    monkeypatch.setattr(challenges, "BRIEFS_DIR", directory)
    return directory


# --------------------------------------------------------------------------- the database


@dataclass
class Db:
    engine: Any
    factory: async_sessionmaker[AsyncSession]
    path: Path


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    path = tmp_path / "practice.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        Base.metadata.tables[name]
        for name in ("users", "practice_challenges", "practice_attempts", "practice_days")
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(challenges, "AsyncSessionLocal", factory)
    yield Db(engine=engine, factory=factory, path=path)
    await engine.dispose()


@pytest.fixture(autouse=True)
def memory_cache(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The cache is the in-process fallback, empty, and never tries to reach Redis."""
    monkeypatch.setattr(CacheBackend, "_client", None)
    monkeypatch.setattr(CacheBackend, "_initialised", True)
    cache_backend._MEMORY.clear()
    yield
    cache_backend._MEMORY.clear()


def utc(moment: datetime | None) -> datetime | None:
    """SQLite hands timezone-aware columns back naive; every stored instant here is UTC."""
    return moment.replace(tzinfo=UTC) if moment is not None and moment.tzinfo is None else moment


async def add_user(db: Db, name: str = "asha") -> str:
    async with db.factory() as session:
        user = User(email=f"{name}@example.com", hashed_password="not-a-real-hash")
        session.add(user)
        await session.commit()
        return str(user.id)


async def add_edition(
    db: Db, brief: dict[str, Any] | None = None, *, opens_at: datetime = OPENS
) -> PracticeChallenge:
    brief = brief or BRIEFS[0]
    row = PracticeChallenge(
        slug=f"{brief['slug']}-{opens_at.date().isoformat()}",
        brief_slug=brief["slug"],
        title=brief["title"],
        track=brief["track"],
        hours=brief["hours"],
        brief=brief,
        opens_at=opens_at,
        closes_at=opens_at + timedelta(days=7),
    )
    async with db.factory() as session:
        session.add(row)
        await session.commit()
    return row


async def add_attempt(
    db: Db,
    user_id: str,
    edition: PracticeChallenge,
    *,
    status: str = "started",
    started_at: datetime = STARTED,
    deadline_at: datetime | None = None,
    **fields: Any,
) -> uuid.UUID:
    row = PracticeAttempt(
        user_id=uuid.UUID(user_id),
        challenge_id=edition.id,
        status=status,
        started_at=started_at,
        deadline_at=deadline_at or started_at + timedelta(hours=edition.hours),
        proof_code=uuid.uuid4().hex[:10].upper(),
        **fields,
    )
    async with db.factory() as session:
        session.add(row)
        await session.commit()
        return row.id


async def attempts_of(db: Db, user_id: str | None = None) -> list[PracticeAttempt]:
    async with db.factory() as session:
        query = select(PracticeAttempt).order_by(PracticeAttempt.started_at)
        if user_id is not None:
            query = query.where(PracticeAttempt.user_id == uuid.UUID(user_id))
        return list((await session.execute(query)).scalars().all())


async def editions_of(db: Db) -> list[PracticeChallenge]:
    async with db.factory() as session:
        rows = await session.execute(select(PracticeChallenge).order_by(PracticeChallenge.opens_at))
        return list(rows.scalars().all())


async def set_attempt(db: Db, attempt_id: uuid.UUID, **values: Any) -> None:
    async with db.factory() as session:
        await session.execute(
            update(PracticeAttempt).where(PracticeAttempt.id == attempt_id).values(**values)
        )
        await session.commit()


@pytest_asyncio.fixture
async def asha(db: Db) -> str:
    return await add_user(db)


@pytest_asyncio.fixture
async def edition(db: Db) -> PracticeChallenge:
    """The open edition: a 48-hour backend build opening Friday 16 October 2026."""
    return await add_edition(db)


# --------------------------------------------------------------------------- a fake GitHub


class FakeGitHub:
    """``api.github.com`` serving the ``submission/`` fixtures. Records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.repo: dict[str, Any] = dict(load_fixture("submission/repo.json"))
        self.forced: dict[str, httpx.Response | Exception] = {}
        self.outage: int | None = None
        self.before_reply: Callable[[], Awaitable[None]] | None = None

    def body(self, path: str) -> httpx.Response:
        if re.fullmatch(r"/repos/[^/]+/[^/]+", path):  # any repo, answered with the one payload
            return httpx.Response(200, json=self.repo)
        fixtures = {
            f"{REPO_API}/activity": "submission/activity.json",
            f"{REPO_API}/commits": "submission/commits.json",
            f"{REPO_API}/git/trees/c35e92a7b0f64d1c83e2a9075bd4f61e8c0a3d29": (
                "submission/tree.json"
            ),
        }
        if path in fixtures:
            return httpx.Response(200, json=load_fixture(fixtures[path]))
        texts = {
            f"{REPO_API}/readme": "submission/readme.md",
            f"{REPO_API}/contents/.talentradar": "submission/talentradar.txt",
            f"{REPO_API}/contents/src/app.py": "submission/src_app.py",
        }
        if path in texts:
            return httpx.Response(200, text=load_fixture(texts[path]))
        return httpx.Response(404, json={"message": "Not Found"})

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "api.github.com", request.url
        assert request.url.scheme == "https", request.url
        if self.before_reply is not None:
            await self.before_reply()
        if self.outage is not None:
            return httpx.Response(self.outage)
        forced = self.forced.get(request.url.path)
        if isinstance(forced, Exception):
            raise forced
        if forced is not None:
            return forced
        return self.body(request.url.path)

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), **kwargs)

    def down(self, status: int = 503) -> None:
        """Every path answers ``status``."""
        self.outage = status

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(challenges, "github_client", fake.client)
    return fake


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def text(length: int) -> str:
    """Plain prose of exactly ``length`` characters that neither starts nor ends in a space."""
    unit = "weighed two options and kept the simpler one "
    return (unit * (length // 40 + 2))[: length - 1] + "."


def writeup(**overrides: str) -> dict[str, str]:
    fields = {
        "decisions": text(300),
        "tradeoffs": text(120),
        "ai_usage": text(80),
        "next_steps": text(80),
    }
    fields.update(overrides)
    return fields


async def save(
    user_id: str,
    slug: str,
    *,
    repo_url: str = REPO_URL,
    fields: dict[str, str] | None = None,
    video_url: str | None = None,
    now: datetime = SAVE_AT,
) -> dict[str, Any]:
    return await challenges.save_submission(
        user_id,
        slug,
        repo_url=repo_url,
        writeup=fields if fields is not None else writeup(),
        video_url=video_url,
        now=now,
    )


@pytest_asyncio.fixture
async def started(db: Db, asha: str, edition: PracticeChallenge) -> dict[str, Any]:
    """Asha has pressed Start at ``STARTED`` on the open 48-hour edition."""
    return await challenges.start(asha, edition.slug, now=STARTED)


# =========================================================================== the module's constants


class TestConstants:
    def test_writeup_bounds_are_the_specs(self) -> None:
        assert challenges.WRITEUP_BOUNDS == {
            "decisions": (200, 1500),
            "tradeoffs": (80, 1000),
            "ai_usage": (40, 1000),
            "next_steps": (40, 800),
        }

    def test_briefs_dir_is_anchored_on_the_repository_not_the_working_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)

        expected = (
            Path(challenges.__file__).resolve().parents[2] / "data" / "practice" / "challenges"
        )
        assert expected == challenges.BRIEFS_DIR
        assert challenges.BRIEFS_DIR.is_absolute()

    def test_rate_limited_is_a_conflict(self) -> None:
        assert issubclass(PracticeRateLimited, PracticeConflict)


# =========================================================================== load_briefs


class TestLoadBriefs:
    def test_returns_valid_briefs_sorted_by_file_name_with_their_content_unchanged(
        self, tmp_path: Path
    ) -> None:
        later = make_brief("zeta-sample")
        earlier = make_brief("alpha-sample", track="ai", hours=24)
        directory = tmp_path / "b"
        directory.mkdir()
        (directory / "02-zeta-sample.json").write_text(json.dumps(later), encoding="utf-8")
        (directory / "01-alpha-sample.json").write_text(json.dumps(earlier), encoding="utf-8")
        (directory / "notes.txt").write_text("not a brief", encoding="utf-8")

        loaded = challenges.load_briefs(directory)

        assert loaded == [earlier, later]

    def test_a_72_hour_brief_is_refused_and_the_file_is_named(self, tmp_path: Path) -> None:
        write_briefs(tmp_path, [make_brief("fine-sample")])
        (tmp_path / "02-long-sample.json").write_text(
            json.dumps(make_brief("long-sample", hours=72)), encoding="utf-8"
        )

        with pytest.raises(PracticeInvalid, match=r"02-long-sample\.json"):
            challenges.load_briefs(tmp_path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("track", "mobile"),
            ("track", 3),
            ("hours", 36),
            ("hours", "48"),
            ("hours", 48.0),
            ("hours", True),
            ("slug", "Has Capitals"),
            ("slug", ""),
            ("slug", "a" * 81),
            ("title", ""),
            ("title", "t" * 161),
            ("summary", ""),
            ("context", []),
            ("context", "a paragraph, not a list"),
            ("must_have", [1, 2]),
            ("deliverables", []),
            ("judge_focus", [""]),
            ("nice_to_have", "text"),
            ("bengaluru_context", ""),
        ],
    )
    def test_a_field_the_schema_forbids_is_refused_with_the_file_named(
        self, tmp_path: Path, field: str, value: Any
    ) -> None:
        brief = make_brief("odd-sample")
        brief[field] = value
        (tmp_path / "01-odd-sample.json").write_text(json.dumps(brief), encoding="utf-8")

        with pytest.raises(PracticeInvalid, match=r"01-odd-sample\.json"):
            challenges.load_briefs(tmp_path)

    @pytest.mark.parametrize("missing", sorted(BRIEF_KEYS))
    def test_a_missing_key_is_refused_and_named(self, tmp_path: Path, missing: str) -> None:
        brief = make_brief("odd-sample")
        del brief[missing]
        (tmp_path / "01-odd-sample.json").write_text(json.dumps(brief), encoding="utf-8")

        with pytest.raises(PracticeInvalid, match=missing):
            challenges.load_briefs(tmp_path)

    def test_a_file_that_is_not_json_or_not_an_object_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "01-broken.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(PracticeInvalid, match=r"01-broken\.json"):
            challenges.load_briefs(tmp_path)

        (tmp_path / "01-broken.json").write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(PracticeInvalid, match=r"01-broken\.json"):
            challenges.load_briefs(tmp_path)

    def test_a_brief_over_8_kb_is_refused(self, tmp_path: Path) -> None:
        brief = make_brief("big-sample")
        brief["context"] = ["x" * 1000] * 9
        write_briefs(tmp_path, [brief])

        with pytest.raises(PracticeInvalid, match=r"01-big-sample\.json"):
            challenges.load_briefs(tmp_path)

    def test_two_briefs_with_one_slug_are_refused(self, tmp_path: Path) -> None:
        write_briefs(tmp_path, [make_brief("same-sample"), make_brief("same-sample")])

        with pytest.raises(PracticeInvalid, match="same-sample"):
            challenges.load_briefs(tmp_path)

    def test_a_missing_directory_has_no_briefs(self, tmp_path: Path) -> None:
        assert challenges.load_briefs(tmp_path / "does-not-exist") == []


# =========================================================================== ensure_editions


class TestEnsureEditions:
    async def test_creates_this_weeks_edition_and_next_weeks(
        self, db: Db, briefs_dir: Path
    ) -> None:
        created = await challenges.ensure_editions(now=NOW)

        rows = await editions_of(db)
        assert created == 2
        assert [utc(row.opens_at) for row in rows] == [
            datetime(2026, 10, 9, 12, 30, tzinfo=UTC),
            datetime(2026, 10, 16, 12, 30, tzinfo=UTC),
        ]
        assert [utc(row.closes_at) for row in rows] == [
            datetime(2026, 10, 16, 12, 30, tzinfo=UTC),
            datetime(2026, 10, 23, 12, 30, tzinfo=UTC),
        ]

    async def test_a_second_call_creates_nothing(self, db: Db, briefs_dir: Path) -> None:
        await challenges.ensure_editions(now=NOW)

        again = await challenges.ensure_editions(now=NOW + timedelta(hours=3))

        assert again == 0
        assert len(await editions_of(db)) == 2

    async def test_an_edition_copies_the_brief_file_and_names_itself_by_its_opening(
        self, db: Db, briefs_dir: Path
    ) -> None:
        await challenges.ensure_editions(now=NOW)

        first = (await editions_of(db))[0]
        file_content = json.loads((briefs_dir / "01-relay-sample.json").read_text(encoding="utf-8"))
        assert first.brief == file_content
        assert first.slug == "relay-sample-2026-10-09"
        assert first.brief_slug == "relay-sample"
        assert (first.title, first.track, first.hours) == (
            file_content["title"],
            "backend",
            48,
        )

    async def test_editing_the_file_later_never_changes_a_created_edition(
        self, db: Db, briefs_dir: Path
    ) -> None:
        await challenges.ensure_editions(now=NOW)
        edited = make_brief("relay-sample", hours=24)
        edited["title"] = "Edited later"
        (briefs_dir / "01-relay-sample.json").write_text(json.dumps(edited), encoding="utf-8")

        await challenges.ensure_editions(now=NOW + timedelta(hours=3))

        first = (await editions_of(db))[0]
        assert first.title == "Sample build relay-sample"
        assert first.hours == 48

    async def test_the_least_recently_run_brief_goes_first(self, db: Db, briefs_dir: Path) -> None:
        await challenges.ensure_editions(now=NOW)  # Oct 9 -> relay, Oct 16 -> grid
        friday = datetime(2026, 10, 23, 13, 0, tzinfo=UTC)  # 30 minutes into the Oct 23 edition

        created = await challenges.ensure_editions(now=friday)  # Oct 23, Oct 30

        assert created == 2
        assert [row.brief_slug for row in await editions_of(db)] == [
            "relay-sample",
            "grid-sample",
            "tracker-sample",  # never run, so ahead of the two that have
            "relay-sample",  # all three have run: back to the one that ran longest ago
        ]

    async def test_a_rerun_is_a_new_row_with_its_own_slug(self, db: Db, briefs_dir: Path) -> None:
        await challenges.ensure_editions(now=NOW)
        await challenges.ensure_editions(now=datetime(2026, 10, 23, 13, 0, tzinfo=UTC))

        slugs = [row.slug for row in await editions_of(db)]
        assert slugs[0] == "relay-sample-2026-10-09"
        assert slugs[3] == "relay-sample-2026-10-30"
        assert len(set(slugs)) == 4

    async def test_with_one_brief_it_runs_every_week(
        self, db: Db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        only = tmp_path / "only"
        write_briefs(only, [BRIEFS[1]])
        monkeypatch.setattr(challenges, "BRIEFS_DIR", only)

        created = await challenges.ensure_editions(now=NOW)

        assert created == 2
        assert {row.brief_slug for row in await editions_of(db)} == {"grid-sample"}

    async def test_no_briefs_means_no_editions(
        self, db: Db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(challenges, "BRIEFS_DIR", tmp_path / "empty")

        created = await challenges.ensure_editions(now=NOW)

        assert created == 0
        assert await editions_of(db) == []

    async def test_an_invalid_brief_file_stops_the_step_and_names_the_file(
        self, db: Db, briefs_dir: Path
    ) -> None:
        (briefs_dir / "04-bad-sample.json").write_text(
            json.dumps(make_brief("bad-sample", hours=72)), encoding="utf-8"
        )

        with pytest.raises(PracticeInvalid, match=r"04-bad-sample\.json"):
            await challenges.ensure_editions(now=NOW)
        assert await editions_of(db) == []

    async def test_losing_the_insert_to_a_concurrent_run_is_not_an_error(
        self, db: Db, briefs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await challenges.ensure_editions(now=NOW)

        async def blind(*_args: Any, **_kwargs: Any) -> list[tuple[str, datetime]]:
            return []  # this run believes no edition exists yet

        monkeypatch.setattr(challenges, "_scheduled_editions", blind)

        created = await challenges.ensure_editions(now=NOW)

        assert created == 0
        assert len(await editions_of(db)) == 2

    async def test_a_run_that_picks_another_brief_for_a_taken_opening_creates_nothing(
        self, db: Db, briefs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two runs whose view of "least recently run" differs would name the same opening with
        # different slugs; only the unique opening stops the second from becoming a duplicate.
        await challenges.ensure_editions(now=NOW)
        first, second = await editions_of(db)

        async def blind(*_args: Any, **_kwargs: Any) -> list[tuple[str, datetime]]:
            return []  # this run believes no edition exists yet

        def another_brief(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return BRIEFS[2]  # not the one stored for either opening

        monkeypatch.setattr(challenges, "_scheduled_editions", blind)
        monkeypatch.setattr(challenges, "_least_recently_run", another_brief)

        created = await challenges.ensure_editions(now=NOW)

        assert created == 0
        rows = await editions_of(db)
        assert [row.slug for row in rows] == [first.slug, second.slug]

    async def test_losing_one_opening_does_not_stop_the_next(
        self, db: Db, briefs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This week's edition was created a moment ago by another run; next week's is missing.
        await add_edition(db, BRIEFS[2], opens_at=datetime(2026, 10, 9, 12, 30, tzinfo=UTC))

        async def blind(*_args: Any, **_kwargs: Any) -> list[tuple[str, datetime]]:
            return []

        monkeypatch.setattr(challenges, "_scheduled_editions", blind)

        created = await challenges.ensure_editions(now=NOW)

        assert created == 1
        assert [utc(row.opens_at) for row in await editions_of(db)] == [
            datetime(2026, 10, 9, 12, 30, tzinfo=UTC),
            datetime(2026, 10, 16, 12, 30, tzinfo=UTC),
        ]

    async def test_any_other_integrity_error_still_fails(
        self, db: Db, briefs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = challenges._edition_row

        def without_a_title(brief: dict[str, Any], opens_at: datetime) -> PracticeChallenge:
            row = real(brief, opens_at)
            row.title = None  # type: ignore[assignment]  # NOT NULL, not a lost race
            return row

        monkeypatch.setattr(challenges, "_edition_row", without_a_title)

        with pytest.raises(IntegrityError):
            await challenges.ensure_editions(now=NOW)
        assert await editions_of(db) == []

    def test_a_lost_race_is_told_from_other_integrity_errors_by_constraint(self) -> None:
        def error(message: str, constraint: str | None = None) -> IntegrityError:
            driver_error = RuntimeError(message)
            if constraint is not None:  # asyncpg: the constraint is on the exception it wraps
                cause = RuntimeError(message)
                cause.constraint_name = constraint  # type: ignore[attr-defined]
                driver_error.__cause__ = cause
            return IntegrityError("INSERT", {}, driver_error)

        for lost in (
            error("x", "uq_practice_challenges_opens_at"),
            error(
                'duplicate key value violates unique constraint "uq_practice_challenges_opens_at"'
            ),
            error("UNIQUE constraint failed: practice_challenges.opens_at"),
            # The same opening named twice: PostgreSQL reports whichever index it checks first.
            error('duplicate key value violates unique constraint "uq_practice_challenges_slug"'),
            error("UNIQUE constraint failed: practice_challenges.slug"),
        ):
            assert challenges._is_opening_conflict(lost), lost.orig
        for other in (
            error("NOT NULL constraint failed: practice_challenges.title"),
            error('null value in column "title" violates not-null constraint', "title"),
            error('violates foreign key constraint "practice_attempts_user_id_fkey"'),
            error("UNIQUE constraint failed: practice_attempts.proof_code"),
            error(
                'duplicate key value violates unique constraint "uq_practice_attempts_proof_code"'
            ),
        ):
            assert not challenges._is_opening_conflict(other), other.orig

    async def test_the_schedule_is_the_ist_week(self, db: Db, briefs_dir: Path) -> None:
        # Friday 17:59 IST is still last week's edition; 18:00 IST opens the new one.
        before = datetime(2026, 10, 16, 12, 29, tzinfo=UTC)
        await challenges.ensure_editions(now=before)
        assert [utc(r.opens_at) for r in await editions_of(db)] == [
            datetime(2026, 10, 9, 12, 30, tzinfo=UTC),
            datetime(2026, 10, 16, 12, 30, tzinfo=UTC),
        ]

        await challenges.ensure_editions(now=OPENS)
        assert [utc(r.opens_at) for r in await editions_of(db)][-1] == datetime(
            2026, 10, 23, 12, 30, tzinfo=UTC
        )


# =========================================================================== overview and detail


class TestOverview:
    async def test_only_an_edition_that_has_opened_is_current(
        self, db: Db, asha: str, briefs_dir: Path
    ) -> None:
        await challenges.ensure_editions(now=NOW)  # Oct 9 (open) and Oct 16 (not yet)

        overview = await challenges.challenges_overview(asha, now=NOW)

        assert overview["current"]["slug"] == "relay-sample-2026-10-09"
        assert overview["next_opens_at"] == "2026-10-16T12:30:00+00:00"
        assert overview["past"] == []

    async def test_the_next_edition_becomes_current_at_its_opening_instant(
        self, db: Db, asha: str, briefs_dir: Path
    ) -> None:
        await challenges.ensure_editions(now=NOW)

        before = await challenges.challenges_overview(asha, now=OPENS - timedelta(seconds=1))
        at = await challenges.challenges_overview(asha, now=OPENS)

        assert before["current"]["slug"] == "relay-sample-2026-10-09"
        assert at["current"]["slug"] == "grid-sample-2026-10-16"
        assert [p["slug"] for p in at["past"]] == ["relay-sample-2026-10-09"]
        assert at["next_opens_at"] == "2026-10-23T12:30:00+00:00"

    async def test_next_opens_at_comes_from_the_schedule_when_no_row_exists(
        self, db: Db, asha: str
    ) -> None:
        overview = await challenges.challenges_overview(asha, now=NOW)

        assert overview["current"] is None
        assert overview["past"] == []
        assert overview["next_opens_at"] == "2026-10-16T12:30:00+00:00"

    async def test_a_closed_edition_is_not_current_even_if_the_next_was_never_created(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        overview = await challenges.challenges_overview(asha, now=CLOSES + timedelta(days=1))

        assert overview["current"] is None
        assert [p["slug"] for p in overview["past"]] == [edition.slug]

    async def test_my_attempt_is_summarised_on_the_current_edition(
        self, db: Db, asha: str, edition: PracticeChallenge, started: dict[str, Any]
    ) -> None:
        overview = await challenges.challenges_overview(asha, now=SAVE_AT)

        attempt = overview["current"]["attempt"]
        assert attempt["status"] == "started"
        assert attempt["started_at"] == "2026-10-17T04:30:00+00:00"
        assert attempt["deadline_at"] == "2026-10-19T04:30:00+00:00"

    async def test_another_users_attempt_and_proof_code_are_invisible(
        self, db: Db, asha: str, edition: PracticeChallenge, started: dict[str, Any]
    ) -> None:
        other = await add_user(db, "bela")

        overview = await challenges.challenges_overview(other, now=SAVE_AT)

        assert overview["current"]["attempt"] is None
        assert started["attempt"]["proof_code"] not in json.dumps(overview)

    async def test_past_lists_the_last_twelve_closed_editions_newest_first(
        self, db: Db, asha: str
    ) -> None:
        base = datetime(2026, 7, 10, 12, 30, tzinfo=UTC)  # a Friday
        opened = [base + timedelta(weeks=n) for n in range(14)]
        for number, moment in enumerate(opened):
            await add_edition(db, make_brief(f"week-{number:02d}-sample"), opens_at=moment)
        now = opened[-1] + timedelta(days=1)  # the last one is current

        overview = await challenges.challenges_overview(asha, now=now)

        assert overview["current"]["slug"].startswith("week-13-sample")
        assert len(overview["past"]) == 12
        assert [p["slug"][:14] for p in overview["past"]][:2] == [
            "week-12-sample",
            "week-11-sample",
        ]
        assert overview["past"][-1]["slug"].startswith("week-01-sample")

    async def test_past_rows_carry_my_status_and_total(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="judged", total=Decimal("78.5"))
        other_week = await add_edition(
            db, make_brief("quiet-sample"), opens_at=OPENS - timedelta(weeks=1)
        )

        overview = await challenges.challenges_overview(asha, now=CLOSES + timedelta(days=1))

        by_slug = {p["slug"]: p for p in overview["past"]}
        assert by_slug[edition.slug]["status"] == "judged"
        assert by_slug[edition.slug]["total"] == 78.5
        assert by_slug[other_week.slug]["status"] is None
        assert by_slug[other_week.slug]["total"] is None

    async def test_the_payload_has_exactly_the_keys_the_page_types(
        self, db: Db, asha: str, edition: PracticeChallenge, started: dict[str, Any]
    ) -> None:
        await add_edition(db, make_brief("old-sample"), opens_at=OPENS - timedelta(weeks=2))

        overview = await challenges.challenges_overview(asha, now=SAVE_AT)

        assert set(overview) == OVERVIEW_KEYS
        assert set(overview["current"]) == CHALLENGE_KEYS
        assert set(overview["current"]["brief"]) == BRIEF_KEYS
        assert set(overview["current"]["attempt"]) == ATTEMPT_KEYS
        assert overview["current"]["attempt"]["writeup"] is None
        assert overview["past"] and all(set(p) == PAST_KEYS for p in overview["past"])
        json.dumps(overview)  # plain JSON values only


class TestDetail:
    async def test_the_brief_and_my_attempt(
        self, db: Db, asha: str, edition: PracticeChallenge, started: dict[str, Any]
    ) -> None:
        detail = await challenges.challenge_detail(asha, edition.slug, now=SAVE_AT)

        assert set(detail) == CHALLENGE_KEYS
        assert set(detail["brief"]) == BRIEF_KEYS
        assert set(detail["attempt"]) == ATTEMPT_KEYS
        assert detail["brief"]["slug"] == "relay-sample"
        assert detail["brief"]["hours"] == 48
        assert detail["opens_at"] == "2026-10-16T12:30:00+00:00"
        assert detail["closes_at"] == "2026-10-23T12:30:00+00:00"
        assert detail["last_start_at"] == "2026-10-21T12:30:00+00:00"
        assert re.fullmatch(r"[A-Z2-9]{10}", detail["attempt"]["proof_code"])
        json.dumps(detail)

    async def test_a_brief_the_reader_has_not_started_has_no_attempt(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        detail = await challenges.challenge_detail(asha, edition.slug, now=SAVE_AT)

        assert detail["attempt"] is None
        assert set(detail["brief"]) == BRIEF_KEYS

    async def test_the_brief_payload_is_projected_onto_the_documented_keys(
        self, db: Db, asha: str
    ) -> None:
        odd = make_brief("odd-sample")
        odd["internal_note"] = "never sent"
        row = await add_edition(db, odd)

        detail = await challenges.challenge_detail(asha, row.slug, now=SAVE_AT)

        assert set(detail["brief"]) == BRIEF_KEYS
        assert "never sent" not in json.dumps(detail)

    async def test_an_edition_that_has_not_opened_does_not_exist_yet(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await challenges.challenge_detail(asha, edition.slug, now=OPENS - timedelta(seconds=1))

    async def test_an_unknown_slug_is_not_found(self, db: Db, asha: str) -> None:
        with pytest.raises(PracticeNotFound):
            await challenges.challenge_detail(asha, "no-such-edition", now=SAVE_AT)

    async def test_a_closed_edition_is_still_readable(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        detail = await challenges.challenge_detail(
            asha, edition.slug, now=CLOSES + timedelta(days=30)
        )

        assert detail["slug"] == edition.slug

    async def test_an_attempt_total_is_a_number(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="judged", total=Decimal("64.0"))

        detail = await challenges.challenge_detail(asha, edition.slug, now=SAVE_AT)

        assert detail["attempt"]["total"] == 64.0
        assert isinstance(detail["attempt"]["total"], float)

    async def test_the_attempt_shows_the_repo_as_a_link_built_from_the_stored_pieces(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(
            db,
            asha,
            edition,
            status="submitted",
            repo_owner=OWNER,
            repo_name=NAME,
            writeup=writeup(),
            video_url="https://youtu.be/abc",
            submitted_at=SAVE_AT,
        )

        attempt = (await challenges.challenge_detail(asha, edition.slug, now=SAVE_AT))["attempt"]

        assert attempt["repo_url"] == REPO_URL
        assert set(attempt["writeup"]) == WRITEUP_KEYS
        assert attempt["video_url"] == "https://youtu.be/abc"
        assert attempt["submitted_at"] == "2026-10-18T08:30:00+00:00"
        assert attempt["editable_until"] == "2026-10-19T05:30:00+00:00"


# =========================================================================== start


class TestStart:
    async def test_starts_a_personal_clock(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        payload = await challenges.start(asha, edition.slug, now=STARTED)

        (row,) = await attempts_of(db, asha)
        assert utc(row.started_at) == STARTED
        assert utc(row.deadline_at) == STARTED + timedelta(hours=48)
        assert row.status == "started"
        assert re.fullmatch(r"[A-Z2-9]{10}", row.proof_code)
        assert (row.repo_owner, row.repo_name, row.writeup) == (None, None, None)
        assert payload["attempt"]["proof_code"] == row.proof_code
        assert payload["attempt"]["status"] == "started"
        assert payload["attempt"]["editable_until"] == (DEADLINE_48 + GRACE).isoformat()

    async def test_the_deadline_follows_the_briefs_hours(self, db: Db, asha: str) -> None:
        short = await add_edition(db, make_brief("short-sample", hours=24))

        await challenges.start(asha, short.slug, now=STARTED)

        (row,) = await attempts_of(db, asha)
        assert utc(row.deadline_at) == STARTED + timedelta(hours=24)

    async def test_the_payload_has_exactly_the_keys_the_page_types(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        payload = await challenges.start(asha, edition.slug, now=STARTED)

        assert set(payload) == CHALLENGE_KEYS
        assert set(payload["attempt"]) == ATTEMPT_KEYS
        assert payload["attempt"]["writeup"] is None
        assert payload["attempt"]["repo_url"] is None
        assert payload["attempt"]["total"] is None
        json.dumps(payload)

    async def test_every_attempt_gets_its_own_code(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        codes = set()
        for name in ("asha", "bela", "chitra", "dev"):
            user = await add_user(db, name)
            codes.add(
                (await challenges.start(user, edition.slug, now=STARTED))["attempt"]["proof_code"]
            )

        assert len(codes) == 4

    async def test_a_second_start_is_a_conflict_and_changes_nothing(
        self, db: Db, asha: str, edition: PracticeChallenge, started: dict[str, Any]
    ) -> None:
        before = (await attempts_of(db, asha))[0]

        with pytest.raises(PracticeConflict):
            await challenges.start(asha, edition.slug, now=STARTED + timedelta(hours=5))

        (after,) = await attempts_of(db, asha)
        assert (after.proof_code, utc(after.started_at)) == (
            before.proof_code,
            utc(before.started_at),
        )

    async def test_a_double_click_that_slips_past_the_check_is_still_one_attempt(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = challenges._find_attempt
        misses = {"left": 1}

        async def blind(*args: Any, **kwargs: Any) -> Any:
            if misses["left"]:
                misses["left"] -= 1
                return None  # the other request's row is not visible yet
            return await real(*args, **kwargs)

        monkeypatch.setattr(challenges, "_find_attempt", blind)

        with pytest.raises(PracticeConflict):
            await challenges.start(asha, edition.slug, now=STARTED + timedelta(seconds=1))

        (row,) = await attempts_of(db, asha)
        assert row.proof_code == started["attempt"]["proof_code"]

    async def test_start_is_allowed_up_to_and_including_the_last_start(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        latest = last_start(CLOSES, 48)

        payload = await challenges.start(asha, edition.slug, now=latest)

        assert payload["attempt"]["deadline_at"] == CLOSES.isoformat()
        assert payload["last_start_at"] == latest.isoformat()

    async def test_start_after_the_last_start_is_refused(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        late = last_start(CLOSES, 48) + timedelta(seconds=1)

        with pytest.raises(PracticeInvalid, match="time"):
            await challenges.start(asha, edition.slug, now=late)

        assert await attempts_of(db, asha) == []

    async def test_a_24_hour_brief_can_start_later_than_a_48_hour_one(
        self, db: Db, asha: str
    ) -> None:
        short = await add_edition(db, make_brief("short-sample", hours=24))
        moment = last_start(CLOSES, 48) + timedelta(hours=10)

        payload = await challenges.start(asha, short.slug, now=moment)

        assert payload["attempt"]["status"] == "started"

    async def test_an_edition_that_has_not_opened_cannot_be_started(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await challenges.start(asha, edition.slug, now=OPENS - timedelta(seconds=1))
        assert await attempts_of(db) == []

    async def test_an_unknown_edition_is_not_found(self, db: Db, asha: str) -> None:
        with pytest.raises(PracticeNotFound):
            await challenges.start(asha, "no-such-edition", now=STARTED)

    async def test_a_closed_edition_cannot_be_started(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeInvalid):
            await challenges.start(asha, edition.slug, now=CLOSES + timedelta(hours=1))

    async def test_a_missing_account_is_not_found_and_writes_nothing(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await challenges.start(str(uuid.uuid4()), edition.slug, now=STARTED)
        assert await attempts_of(db) == []

    async def test_a_malformed_user_id_is_not_found(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await challenges.start("not-a-uuid", edition.slug, now=STARTED)

    async def test_two_users_start_independently(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        bela = await add_user(db, "bela")

        await challenges.start(asha, edition.slug, now=STARTED)
        await challenges.start(bela, edition.slug, now=STARTED + timedelta(hours=2))

        mine, theirs = await attempts_of(db, asha), await attempts_of(db, bela)
        assert utc(mine[0].deadline_at) == DEADLINE_48
        assert utc(theirs[0].deadline_at) == DEADLINE_48 + timedelta(hours=2)

    async def test_a_naive_clock_is_refused(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(ValueError, match="naive"):
            await challenges.start(asha, edition.slug, now=datetime(2026, 10, 17, 4, 30))


# =========================================================================== save_submission


class TestSaveSubmission:
    async def test_saves_the_repo_the_writeup_and_the_video(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        payload = await save(asha, edition.slug, video_url="https://youtu.be/abc123")

        (row,) = await attempts_of(db, asha)
        assert row.status == "submitted"
        assert (row.repo_owner, row.repo_name) == (OWNER, NAME)
        assert row.writeup == writeup()
        assert row.video_url == "https://youtu.be/abc123"
        assert utc(row.submitted_at) == SAVE_AT
        assert utc(row.repo_created_at) == datetime(2026, 10, 17, 4, 20, tzinfo=UTC)
        assert set(payload) == CHALLENGE_KEYS
        assert payload["attempt"]["status"] == "submitted"
        assert payload["attempt"]["repo_url"] == REPO_URL
        assert set(payload["attempt"]["writeup"]) == WRITEUP_KEYS
        assert payload["attempt"]["submitted_at"] == SAVE_AT.isoformat()

    async def test_the_save_time_check_is_one_request_to_the_repo_endpoint(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        await save(asha, edition.slug)

        assert github.paths == [REPO_API]
        assert all(r.url.host == "api.github.com" for r in github.requests)

    @pytest.mark.parametrize(
        "typed",
        [
            f"{REPO_URL}/",
            f"{REPO_URL}.git",
            f"  {REPO_URL}  ",
        ],
    )
    async def test_accepts_the_forms_a_person_pastes(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        typed: str,
    ) -> None:
        await save(asha, edition.slug, repo_url=typed)

        (row,) = await attempts_of(db, asha)
        assert (row.repo_owner, row.repo_name) == (OWNER, NAME)
        assert github.paths == [REPO_API]

    @pytest.mark.parametrize(
        "typed",
        [
            "",
            "demo-builder/webhook-relay",
            "http://github.com/demo-builder/webhook-relay",
            "https://gitlab.com/demo-builder/webhook-relay",
            "https://github.com.evil.example/demo-builder/webhook-relay",
            "https://github.com/demo-builder",
            "https://github.com/demo-builder/webhook-relay/tree/main",
            "https://github.com/demo-builder/webhook-relay?tab=readme",
            "https://github.com/demo-builder/webhook-relay#readme",
            "https://github.com/../webhook-relay",
            "https://github.com/demo-builder/..",
            "https://user@github.com/demo-builder/webhook-relay",
        ],
    )
    async def test_refuses_a_link_that_is_not_a_repo_and_asks_nothing_of_github(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        typed: str,
    ) -> None:
        with pytest.raises(PracticeInvalid, match=r"github.com"):
            await save(asha, edition.slug, repo_url=typed)

        assert github.requests == []
        (row,) = await attempts_of(db, asha)
        assert row.status == "started" and row.repo_owner is None

    @pytest.mark.parametrize(
        ("field", "minimum", "maximum"),
        [
            ("decisions", 200, 1500),
            ("tradeoffs", 80, 1000),
            ("ai_usage", 40, 1000),
            ("next_steps", 40, 800),
        ],
    )
    async def test_each_writeup_field_has_its_bounds(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        field: str,
        minimum: int,
        maximum: int,
    ) -> None:
        for bad in (minimum - 1, maximum + 1):
            with pytest.raises(PracticeInvalid, match=field):
                await save(asha, edition.slug, fields=writeup(**{field: text(bad)}))
        assert github.requests == []

        for good in (minimum, maximum):
            await save(asha, edition.slug, fields=writeup(**{field: text(good)}))
        (row,) = await attempts_of(db, asha)
        assert len(row.writeup[field]) == maximum

    async def test_spaces_around_a_field_do_not_count_toward_its_length(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        padded = "   " + text(199) + "   "
        with pytest.raises(PracticeInvalid, match="decisions"):
            await save(asha, edition.slug, fields=writeup(decisions=padded))

        await save(asha, edition.slug, fields=writeup(decisions="  " + text(200) + "\n"))

        (row,) = await attempts_of(db, asha)
        assert row.writeup["decisions"] == text(200)

    async def test_a_missing_or_non_text_field_is_refused(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        missing = writeup()
        del missing["ai_usage"]
        with pytest.raises(PracticeInvalid, match="ai_usage"):
            await save(asha, edition.slug, fields=missing)

        wrong: dict[str, Any] = dict(writeup())
        wrong["tradeoffs"] = 12345
        with pytest.raises(PracticeInvalid, match="tradeoffs"):
            await challenges.save_submission(
                asha, edition.slug, repo_url=REPO_URL, writeup=wrong, video_url=None, now=SAVE_AT
            )

    async def test_only_the_four_fields_are_stored(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        extra: dict[str, Any] = dict(writeup())
        extra["secret"] = "do not store me"
        await challenges.save_submission(
            asha, edition.slug, repo_url=REPO_URL, writeup=extra, video_url=None, now=SAVE_AT
        )

        (row,) = await attempts_of(db, asha)
        assert set(row.writeup) == WRITEUP_KEYS

    async def test_control_characters_are_removed_before_storage(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        nul, bell = chr(0), chr(7)
        dirty = text(200) + nul + "kept\r\nline two" + bell + "\tand a tab"

        await save(asha, edition.slug, fields=writeup(decisions=dirty))

        (row,) = await attempts_of(db, asha)
        stored = row.writeup["decisions"]
        assert nul not in stored and bell not in stored and "\r" not in stored
        assert "kept\nline two\tand a tab" in stored

    @pytest.mark.parametrize(
        "video",
        [
            "https://vimeo.com/123",
            "http://youtube.com/watch?v=abc",
            "https://evil.example/youtube.com",
            "https://youtube.com.evil.example/watch",
            "https://user@youtube.com/watch",
            "ftp://youtu.be/abc",
            "javascript:alert(1)",
            "https://youtu.be/" + "a" * 290,
        ],
    )
    async def test_a_video_link_on_another_host_or_over_300_characters_is_refused(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        video: str,
    ) -> None:
        with pytest.raises(PracticeInvalid, match="video"):
            await save(asha, edition.slug, video_url=video)

        assert github.requests == []

    @pytest.mark.parametrize(
        "video",
        [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://www.loom.com/share/abc",
            "https://drive.google.com/file/d/abc/view",
        ],
    )
    async def test_the_four_video_hosts_are_accepted_and_never_fetched(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        video: str,
    ) -> None:
        await save(asha, edition.slug, video_url=video)

        (row,) = await attempts_of(db, asha)
        assert row.video_url == video
        assert github.paths == [REPO_API]

    async def test_an_empty_video_is_no_video(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        await save(asha, edition.slug, video_url="https://youtu.be/abc")
        await save(asha, edition.slug, video_url="  ")

        (row,) = await attempts_of(db, asha)
        assert row.video_url is None

    # -- the repo ------------------------------------------------------------------------------

    async def test_save_refuses_repo_created_before_start(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.repo["created_at"] = iso(STARTED - timedelta(hours=1))

        with pytest.raises(PracticeInvalid, match="Create a new repo after you press Start"):
            await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "started" and row.repo_owner is None and row.writeup is None

    async def test_a_repo_created_within_the_fifteen_minute_grace_is_accepted(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.repo["created_at"] = iso(STARTED - timedelta(minutes=15))

        await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "submitted"

    async def test_a_repo_created_one_second_past_the_grace_is_refused(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.repo["created_at"] = iso(STARTED - timedelta(minutes=15, seconds=1))

        with pytest.raises(PracticeInvalid, match="Create a new repo"):
            await save(asha, edition.slug)

    async def test_the_created_at_is_read_as_an_instant_with_any_offset(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        # 09:45 +05:30 is 04:15 UTC, exactly the grace before Start. Read as UTC it would be 09:45.
        github.repo["created_at"] = "2026-10-17T09:45:00+05:30"

        await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert utc(row.repo_created_at) == datetime(2026, 10, 17, 4, 15, tzinfo=UTC)

    async def test_save_refuses_fork(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.repo["fork"] = True

        with pytest.raises(PracticeInvalid, match="fork"):
            await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "started"

    async def test_save_refuses_a_private_repo(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.repo["private"] = True

        with pytest.raises(PracticeInvalid, match="public"):
            await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "started"

    async def test_a_repo_github_cannot_find_is_refused_as_not_public(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.forced[REPO_API] = httpx.Response(404, json={"message": "Not Found"})

        with pytest.raises(PracticeInvalid, match="public"):
            await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "started"

    async def test_a_renamed_repo_is_refused_and_the_redirect_is_not_followed(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.forced[REPO_API] = httpx.Response(
            301, headers={"Location": "https://evil.example/repositories/1"}
        )

        with pytest.raises(PracticeInvalid):
            await save(asha, edition.slug)

        assert github.paths == [REPO_API]

    @pytest.mark.parametrize("status", [403, 429, 500, 503])
    async def test_github_unreachable_at_save_saves_and_defers_the_check(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        status: int,
    ) -> None:
        github.down(status)

        payload = await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "submitted"
        assert (row.repo_owner, row.repo_name) == (OWNER, NAME)
        assert row.repo_created_at is None
        assert payload["attempt"]["status"] == "submitted"

    async def test_a_network_failure_at_save_also_defers_the_check(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.forced[REPO_API] = httpx.ConnectError("no route")

        await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "submitted" and row.repo_created_at is None

    async def test_a_payload_with_no_readable_creation_time_defers_the_check(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        github.repo["created_at"] = "yesterday-ish"

        await save(asha, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "submitted" and row.repo_created_at is None

    # -- the window ----------------------------------------------------------------------------

    async def test_a_second_save_replaces_the_first_and_there_is_one_row(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        await save(asha, edition.slug, video_url="https://youtu.be/one")
        await save(
            asha,
            edition.slug,
            repo_url="https://github.com/demo-builder/webhook-relay-v2",
            fields=writeup(decisions=text(400)),
            video_url=None,
            now=SAVE_AT + timedelta(hours=3),
        )

        (row,) = await attempts_of(db, asha)
        assert row.repo_name == "webhook-relay-v2"
        assert row.writeup["decisions"] == text(400)
        assert row.video_url is None
        assert utc(row.submitted_at) == SAVE_AT + timedelta(hours=3)
        assert row.proof_code == started["attempt"]["proof_code"]
        assert utc(row.started_at) == STARTED

    async def test_the_last_minute_before_the_grace_hour_ends_still_saves(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        await save(asha, edition.slug, now=DEADLINE_48 + GRACE - timedelta(seconds=1))

        (row,) = await attempts_of(db, asha)
        assert row.status == "submitted"

    async def test_save_after_the_grace_hour_is_a_conflict(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        with pytest.raises(PracticeConflict):
            await save(asha, edition.slug, now=DEADLINE_48 + timedelta(minutes=60))
        with pytest.raises(PracticeConflict):
            await save(asha, edition.slug, now=DEADLINE_48 + timedelta(minutes=61))

        (row,) = await attempts_of(db, asha)
        assert row.status == "started" and row.repo_owner is None
        assert github.requests == []

    async def test_a_frozen_edit_does_not_touch_the_saved_submission(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        await save(asha, edition.slug)

        with pytest.raises(PracticeConflict):
            await save(
                asha,
                edition.slug,
                fields=writeup(decisions=text(500)),
                now=DEADLINE_48 + timedelta(hours=2),
            )

        (row,) = await attempts_of(db, asha)
        assert row.writeup == writeup()

    @pytest.mark.parametrize("status", ["queued", "judged", "rejected", "judge_failed", "missed"])
    async def test_an_attempt_the_system_has_moved_on_from_cannot_be_edited(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
        status: str,
    ) -> None:
        (row,) = await attempts_of(db, asha)
        await set_attempt(db, row.id, status=status)

        with pytest.raises(PracticeConflict):
            await save(asha, edition.slug)

        (after,) = await attempts_of(db, asha)
        assert after.status == status and after.writeup is None

    async def test_the_window_closing_during_the_github_call_loses_the_save(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        (row,) = await attempts_of(db, asha)

        async def advance_meanwhile() -> None:
            await set_attempt(db, row.id, status="missed")

        github.before_reply = advance_meanwhile

        with pytest.raises(PracticeConflict):
            await save(asha, edition.slug)

        (after,) = await attempts_of(db, asha)
        assert after.status == "missed" and after.repo_owner is None

    async def test_saving_before_pressing_start_is_a_conflict(
        self, db: Db, asha: str, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        with pytest.raises(PracticeConflict, match="Start"):
            await save(asha, edition.slug)

        assert await attempts_of(db) == []
        assert github.requests == []

    async def test_saving_to_an_edition_that_has_not_opened_is_not_found(
        self, db: Db, asha: str, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await save(asha, edition.slug, now=OPENS - timedelta(minutes=1))
        with pytest.raises(PracticeNotFound):
            await save(asha, "no-such-edition")

    async def test_a_save_never_touches_another_users_attempt(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        bela = await add_user(db, "bela")

        with pytest.raises(PracticeConflict):
            await save(bela, edition.slug)

        (row,) = await attempts_of(db, asha)
        assert row.status == "started" and row.repo_owner is None

    async def test_resaving_while_github_is_down_keeps_a_known_creation_time_for_the_same_repo(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        await save(asha, edition.slug)
        github.down()

        await save(asha, edition.slug, fields=writeup(decisions=text(350)))
        (same,) = await attempts_of(db, asha)
        assert utc(same.repo_created_at) == datetime(2026, 10, 17, 4, 20, tzinfo=UTC)

        await save(asha, edition.slug, repo_url="https://github.com/demo-builder/another-repo")
        (other,) = await attempts_of(db, asha)
        assert other.repo_created_at is None

    async def test_a_missing_account_is_not_found(
        self, db: Db, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await save(str(uuid.uuid4()), edition.slug)

    # -- the rate limit ------------------------------------------------------------------------

    async def test_the_eleventh_save_in_a_minute_is_rate_limited(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        for number in range(10):
            await save(asha, edition.slug, now=SAVE_AT + timedelta(seconds=number))
        calls = len(github.requests)

        with pytest.raises(PracticeRateLimited):
            await save(asha, edition.slug, now=SAVE_AT + timedelta(seconds=10))

        assert len(github.requests) == calls

    async def test_the_limit_is_a_conflict_for_a_caller_that_only_knows_conflicts(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        key = f"tr:practice:submit:{asha}:{int(SAVE_AT.timestamp() // 60)}"
        for _ in range(10):
            await CacheBackend.incr(key, 120)

        with pytest.raises(PracticeConflict):
            await save(asha, edition.slug)

    async def test_the_tenth_save_of_a_minute_is_allowed(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        key = f"tr:practice:submit:{asha}:{int(SAVE_AT.timestamp() // 60)}"
        for _ in range(9):
            await CacheBackend.incr(key, 120)

        payload = await save(asha, edition.slug)

        assert payload["attempt"]["status"] == "submitted"

    async def test_the_limit_resets_with_the_minute(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        key = f"tr:practice:submit:{asha}:{int(SAVE_AT.timestamp() // 60)}"
        for _ in range(10):
            await CacheBackend.incr(key, 120)

        payload = await save(asha, edition.slug, now=SAVE_AT + timedelta(minutes=1))

        assert payload["attempt"]["status"] == "submitted"

    async def test_one_users_saves_do_not_limit_another(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        bela = await add_user(db, "bela")
        await challenges.start(bela, edition.slug, now=STARTED)
        for _ in range(10):
            await CacheBackend.incr(
                f"tr:practice:submit:{asha}:{int(SAVE_AT.timestamp() // 60)}", 120
            )

        payload = await save(bela, edition.slug)

        assert payload["attempt"]["status"] == "submitted"

    async def test_invalid_saves_count_toward_the_limit(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        started: dict[str, Any],
        github: FakeGitHub,
    ) -> None:
        for _ in range(10):
            with pytest.raises(PracticeInvalid):
                await save(asha, edition.slug, repo_url="not a repo")

        with pytest.raises(PracticeRateLimited):
            await save(asha, edition.slug, repo_url="not a repo")


# =========================================================================== advance_statuses


class TestAdvanceStatuses:
    async def test_after_the_grace_hour_submitted_is_queued_and_started_is_missed(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        submitter = await add_user(db, "sana")
        idler = await add_user(db, "ira")
        await add_attempt(
            db, submitter, edition, status="submitted", repo_owner=OWNER, repo_name=NAME
        )
        await add_attempt(db, idler, edition, status="started")
        now = DEADLINE_48 + timedelta(minutes=61)

        counts = await challenges.advance_statuses(now=now)

        assert counts["queued"] == 1 and counts["missed"] == 1
        (queued,) = await attempts_of(db, submitter)
        assert queued.status == "queued"
        assert utc(queued.next_judge_at) == now
        assert queued.judge_attempts == 0
        (missed,) = await attempts_of(db, idler)
        assert missed.status == "missed"
        assert missed.next_judge_at is None

    async def test_nothing_moves_while_the_grace_hour_is_running(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="submitted", repo_owner=OWNER, repo_name=NAME)

        counts = await challenges.advance_statuses(now=DEADLINE_48 + timedelta(minutes=59))

        assert counts["queued"] == 0 and counts["missed"] == 0
        assert (await attempts_of(db, asha))[0].status == "submitted"

    async def test_the_grace_hour_ends_exactly_sixty_minutes_after_the_deadline(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        sana = await add_user(db, "sana")
        ira = await add_user(db, "ira")
        await add_attempt(db, sana, edition, status="submitted", repo_owner=OWNER, repo_name=NAME)
        await add_attempt(db, ira, edition, status="started")

        just_before = await challenges.advance_statuses(
            now=DEADLINE_48 + GRACE - timedelta(seconds=1)
        )
        at = await challenges.advance_statuses(now=DEADLINE_48 + GRACE)

        assert just_before["queued"] == 0 and just_before["missed"] == 0
        assert at["queued"] == 1 and at["missed"] == 1

    async def test_it_uses_each_attempts_own_deadline(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        early = await add_user(db, "early")
        late = await add_user(db, "late")
        await add_attempt(db, early, edition, status="started", started_at=STARTED)
        await add_attempt(
            db, late, edition, status="started", started_at=STARTED + timedelta(hours=10)
        )

        await challenges.advance_statuses(now=DEADLINE_48 + timedelta(hours=2))

        assert (await attempts_of(db, early))[0].status == "missed"
        assert (await attempts_of(db, late))[0].status == "started"

    async def test_it_leaves_every_other_status_alone(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        for number, status in enumerate(("queued", "judged", "rejected", "judge_failed", "missed")):
            user = await add_user(db, f"user{number}")
            await add_attempt(db, user, edition, status=status)

        counts = await challenges.advance_statuses(now=DEADLINE_48 + timedelta(days=3))

        assert counts["queued"] == 0 and counts["missed"] == 0
        assert sorted(a.status for a in await attempts_of(db)) == sorted(
            ["queued", "judged", "rejected", "judge_failed", "missed"]
        )

    async def test_running_twice_moves_each_row_once(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="submitted", repo_owner=OWNER, repo_name=NAME)
        now = DEADLINE_48 + timedelta(hours=2)

        first = await challenges.advance_statuses(now=now)
        second = await challenges.advance_statuses(now=now + timedelta(hours=2))

        assert first["queued"] == 1 and second["queued"] == 0
        (row,) = await attempts_of(db, asha)
        assert utc(row.next_judge_at) == now

    async def test_a_row_someone_else_just_moved_is_not_moved_again(
        self, db: Db, asha: str, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempt_id = await add_attempt(
            db, asha, edition, status="submitted", repo_owner=OWNER, repo_name=NAME
        )
        real = challenges._due_attempts

        async def stale(*args: Any, **kwargs: Any) -> Any:
            rows = await real(*args, **kwargs)
            await set_attempt(db, attempt_id, status="judged")  # the judge got there first
            return rows

        monkeypatch.setattr(challenges, "_due_attempts", stale)

        counts = await challenges.advance_statuses(now=DEADLINE_48 + timedelta(hours=2))

        assert counts["queued"] == 0
        assert (await attempts_of(db, asha))[0].status == "judged"

    async def test_one_failing_row_is_skipped_and_the_others_still_move(
        self, db: Db, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sana = await add_user(db, "sana")
        ira = await add_user(db, "ira")
        bad = await add_attempt(
            db, sana, edition, status="submitted", repo_owner=OWNER, repo_name=NAME
        )
        await add_attempt(db, ira, edition, status="started")
        real = challenges._advance_one

        async def flaky(attempt_id: uuid.UUID, *args: Any, **kwargs: Any) -> bool:
            if attempt_id == bad:
                raise RuntimeError("database hiccup")
            return await real(attempt_id, *args, **kwargs)

        monkeypatch.setattr(challenges, "_advance_one", flaky)

        counts = await challenges.advance_statuses(now=DEADLINE_48 + timedelta(hours=2))

        assert counts == {"queued": 0, "missed": 1}
        assert (await attempts_of(db, sana))[0].status == "submitted"
        assert (await attempts_of(db, ira))[0].status == "missed"

    async def test_with_nothing_due_every_count_is_zero(self, db: Db) -> None:
        assert await challenges.advance_statuses(now=NOW) == {"queued": 0, "missed": 0}


# =========================================================================== prune


class TestPrune:
    async def test_a_missed_row_goes_thirty_days_after_its_deadline(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        old = await add_user(db, "old")
        recent = await add_user(db, "recent")
        deadline = DEADLINE_48
        await add_attempt(db, old, edition, status="missed", deadline_at=deadline)
        await add_attempt(
            db, recent, edition, status="missed", deadline_at=deadline + timedelta(days=5)
        )

        counts = await challenges.prune(now=deadline + timedelta(days=31))

        assert counts["pruned_attempts"] == 1
        assert await attempts_of(db, old) == []
        assert len(await attempts_of(db, recent)) == 1

    async def test_an_unsubmitted_started_row_goes_with_it(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="started")

        counts = await challenges.prune(now=DEADLINE_48 + timedelta(days=31))

        assert counts["pruned_attempts"] == 1
        assert await attempts_of(db) == []

    async def test_a_missed_row_inside_the_thirty_days_stays(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="missed")

        counts = await challenges.prune(now=DEADLINE_48 + timedelta(days=29))

        assert counts["pruned_attempts"] == 0
        assert len(await attempts_of(db)) == 1

    @pytest.mark.parametrize(
        "status", ["submitted", "queued", "judged", "rejected", "judge_failed"]
    )
    async def test_a_row_with_a_submission_is_kept_past_thirty_days(
        self, db: Db, asha: str, edition: PracticeChallenge, status: str
    ) -> None:
        await add_attempt(
            db, asha, edition, status=status, repo_owner=OWNER, repo_name=NAME, submitted_at=SAVE_AT
        )

        counts = await challenges.prune(now=DEADLINE_48 + timedelta(days=60))

        assert counts["pruned_attempts"] == 0
        assert len(await attempts_of(db)) == 1

    async def test_a_judged_attempt_700_days_old_is_kept(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(
            db,
            asha,
            edition,
            status="judged",
            total=Decimal("80.0"),
            repo_owner=OWNER,
            repo_name=NAME,
            submitted_at=SAVE_AT,
        )

        counts = await challenges.prune(now=DEADLINE_48 + timedelta(days=700))

        assert counts["pruned_attempts"] == 0
        assert len(await attempts_of(db)) == 1

    async def test_everything_else_goes_after_two_years(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(
            db,
            asha,
            edition,
            status="judged",
            total=Decimal("80.0"),
            repo_owner=OWNER,
            repo_name=NAME,
        )

        counts = await challenges.prune(now=DEADLINE_48 + timedelta(days=731))

        assert counts["pruned_attempts"] == 1
        assert await attempts_of(db) == []

    async def test_editions_are_kept_forever(self, db: Db, edition: PracticeChallenge) -> None:
        await challenges.prune(now=CLOSES + timedelta(days=5000))

        assert len(await editions_of(db)) == 1

    async def test_a_practice_day_401_days_old_is_deleted_and_one_400_days_old_stays(
        self, db: Db, asha: str
    ) -> None:
        today = datetime(2026, 10, 15, 6, 0, tzinfo=UTC).date()
        async with db.factory() as session:
            for age in (401, 400, 5):
                session.add(
                    PracticeDay(
                        user_id=uuid.UUID(asha),
                        day=today - timedelta(days=age),
                        question_slug="fixture-question",
                        topic="array",
                        difficulty="easy",
                    )
                )
            await session.commit()

        counts = await challenges.prune(now=NOW)

        assert counts["pruned_days"] == 1
        async with db.factory() as session:
            ages = sorted(
                (today - day).days
                for day in (await session.execute(select(PracticeDay.day))).scalars().all()
            )
        assert ages == [5, 400]

    async def test_the_practice_day_cutoff_is_the_ist_date(self, db: Db, asha: str) -> None:
        # 20:00 UTC on 15 October is already 16 October in IST.
        late_utc = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
        boundary = datetime(2026, 10, 16, 0, 0).date() - timedelta(days=401)
        async with db.factory() as session:
            session.add(
                PracticeDay(
                    user_id=uuid.UUID(asha),
                    day=boundary,
                    question_slug="fixture-question",
                    topic="array",
                    difficulty="easy",
                )
            )
            await session.commit()

        counts = await challenges.prune(now=late_utc)

        assert counts["pruned_days"] == 1

    async def test_nothing_to_prune_is_all_zero(self, db: Db) -> None:
        assert await challenges.prune(now=NOW) == {"pruned_days": 0, "pruned_attempts": 0}

    async def test_a_failing_rule_is_skipped_and_the_others_still_run(
        self, db: Db, asha: str, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await add_attempt(db, asha, edition, status="missed")

        async def broken(*_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("database hiccup")

        monkeypatch.setattr(challenges, "_prune_days", broken)

        counts = await challenges.prune(now=DEADLINE_48 + timedelta(days=31))

        assert counts == {"pruned_days": 0, "pruned_attempts": 1}


# =========================================================================== github_reader


REF = RepoRef(OWNER, NAME)
SNAPSHOT = "c35e92a7b0f64d1c83e2a9075bd4f61e8c0a3d29"


class Counting(httpx.AsyncByteStream):
    """A body of ``count`` chunks that records how many bytes were pulled from it."""

    def __init__(self, chunk: bytes, count: int) -> None:
        self.chunk, self.count, self.pulled = chunk, count, 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.pulled += len(self.chunk)
            yield self.chunk


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


class TestReaderClient:
    def test_github_client_points_at_the_api_with_a_six_second_timeout(self) -> None:
        client = github_reader.github_client()

        assert str(client.base_url).rstrip("/") == "https://api.github.com"
        assert client.timeout == httpx.Timeout(6.0)
        assert client.follow_redirects is False
        assert client.headers["Accept"] == "application/vnd.github+json"
        assert client.headers["User-Agent"]

    def test_the_token_is_sent_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Configured:
            github_token = "test-token-not-real"

        monkeypatch.setattr("services.github.get_settings", lambda: Configured())

        assert (
            github_reader.github_client().headers["Authorization"] == "Bearer test-token-not-real"
        )

    def test_no_token_means_no_authorization_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Anonymous:
            github_token = ""

        monkeypatch.setattr("services.github.get_settings", lambda: Anonymous())

        assert "Authorization" not in github_reader.github_client().headers


class TestFetchRepo:
    async def test_returns_the_repo_payload_from_the_api(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            repo = await github_reader.fetch_repo(client, REF)

        assert repo["created_at"] == "2026-10-17T04:20:00Z"
        assert repo["fork"] is False and repo["private"] is False
        assert gh.paths == [REPO_API]

    async def test_a_404_is_repo_gone(self, gh: FakeGitHub) -> None:
        gh.forced[REPO_API] = httpx.Response(404)
        async with gh.client() as client:
            with pytest.raises(github_reader.RepoGone):
                await github_reader.fetch_repo(client, REF)

    @pytest.mark.parametrize("status", [403, 429, 500, 502, 503])
    async def test_rate_limits_and_server_errors_are_unavailable(
        self, gh: FakeGitHub, status: int
    ) -> None:
        gh.forced[REPO_API] = httpx.Response(status)
        async with gh.client() as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_repo(client, REF)

    @pytest.mark.parametrize("error", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
    async def test_a_network_failure_is_unavailable(self, gh: FakeGitHub, error: Exception) -> None:
        gh.forced[REPO_API] = error
        async with gh.client() as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_repo(client, REF)

    @pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'"text"', b"null", b""])
    async def test_a_reply_that_is_not_a_json_object_is_unavailable(
        self, gh: FakeGitHub, body: bytes
    ) -> None:
        gh.forced[REPO_API] = httpx.Response(200, content=body)
        async with gh.client() as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_repo(client, REF)

    @pytest.mark.parametrize("status", [301, 302, 307, 308])
    async def test_a_redirect_is_refused_as_gone_and_never_followed(
        self, gh: FakeGitHub, status: int
    ) -> None:
        gh.forced[REPO_API] = httpx.Response(status, headers={"Location": "https://evil.example/x"})
        async with gh.client(follow_redirects=True) as client:  # even a client that would follow
            with pytest.raises(github_reader.RepoGone):
                await github_reader.fetch_repo(client, REF)

        assert gh.paths == [REPO_API]

    async def test_a_reply_over_the_cap_is_unavailable_and_not_read_to_the_end(self) -> None:
        stream = Counting(b" " * 100_000, 100)  # 10 MB of whitespace
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_repo(client, REF)

        assert stream.pulled <= github_reader.JSON_LIMIT + 100_000

    async def test_a_declared_length_over_the_cap_is_not_read_at_all(self) -> None:
        stream = Counting(b" " * 100_000, 100)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Length": "10000000"}, stream=stream
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_repo(client, REF)

        assert stream.pulled == 0

    async def test_the_request_asks_for_an_uncompressed_body(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            await github_reader.fetch_repo(client, REF)

        assert gh.requests[0].headers["Accept-Encoding"] == "identity"

    @pytest.mark.parametrize(
        "ref",
        [
            RepoRef("evil.example/x", "r"),
            RepoRef("a", "b/../../c"),
            RepoRef("a", "r?x=1"),
            RepoRef("a", "r#frag"),
            RepoRef("a", ".."),
            RepoRef("a", "r.git"),
            RepoRef("a b", "r"),
            RepoRef("a", "r\n"),
            RepoRef("", "r"),
        ],
    )
    async def test_a_ref_that_is_not_a_strict_repo_is_refused_before_any_request(
        self, gh: FakeGitHub, ref: RepoRef
    ) -> None:
        async with gh.client() as client:
            with pytest.raises(ValueError, match="repo"):
                await github_reader.fetch_repo(client, ref)

        assert gh.requests == []

    async def test_the_url_is_the_https_api_host_whatever_the_clients_base_url(
        self, gh: FakeGitHub
    ) -> None:
        async with gh.client(base_url="https://elsewhere.example") as client:
            await github_reader.fetch_repo(client, REF)

        assert str(gh.requests[0].url) == f"https://api.github.com{REPO_API}"


class TestFetchPushes:
    async def test_returns_the_pushes_on_the_branch_newest_first(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            pushes = await github_reader.fetch_pushes(client, REF, "main")

        assert [p.sha[:4] for p in pushes] == ["d40e", "c35e", "b27c", "a1f3"]
        assert all(isinstance(p, Push) for p in pushes)
        request = gh.requests[0]
        assert request.url.path == f"{REPO_API}/activity"
        assert request.url.params["ref"] == "refs/heads/main"
        assert request.url.params["per_page"] == "100"

    async def test_a_404_is_repo_gone(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/activity"] = httpx.Response(404)
        async with gh.client() as client:
            with pytest.raises(github_reader.RepoGone):
                await github_reader.fetch_pushes(client, REF, "main")

    async def test_a_403_is_unavailable(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/activity"] = httpx.Response(403, json={"message": "rate limit"})
        async with gh.client() as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_pushes(client, REF, "main")

    async def test_it_follows_the_next_cursor_up_to_the_page_limit(self, gh: FakeGitHub) -> None:
        page = load_fixture("submission/activity.json")

        def handler(request: httpx.Request) -> httpx.Response:
            gh.requests.append(request)
            number = int(request.url.params.get("after", "page0").removeprefix("page")) + 1
            nxt = f'<https://api.github.com{REPO_API}/activity?after=page{number}>; rel="next"'
            return httpx.Response(200, json=page, headers={"Link": nxt})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pushes = await github_reader.fetch_pushes(client, REF, "main", pages=3)

        assert len(gh.requests) == 3
        assert len(pushes) == 12  # four pushes on the branch in each of three pages
        assert [r.url.params.get("after") for r in gh.requests] == [None, "page1", "page2"]

    async def test_it_stops_when_there_is_no_next_page(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            await github_reader.fetch_pushes(client, REF, "main", pages=3)

        assert len(gh.requests) == 1

    async def test_the_next_link_is_only_a_source_of_the_cursor_never_a_url_to_request(
        self, gh: FakeGitHub
    ) -> None:
        page = load_fixture("submission/activity.json")

        def handler(request: httpx.Request) -> httpx.Response:
            gh.requests.append(request)
            hostile = '<https://evil.example/steal?after=abc%26x%23y&token=1>; rel="next"'
            return httpx.Response(200, json=page, headers={"Link": hostile})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await github_reader.fetch_pushes(client, REF, "main", pages=2)

        assert [r.url.host for r in gh.requests] == ["api.github.com", "api.github.com"]
        second = gh.requests[1]
        assert second.url.path == f"{REPO_API}/activity"
        assert second.url.params["after"] == "abc&x#y"  # data, percent-encoded, not syntax
        assert "token" not in second.url.params

    async def test_a_branch_name_is_data_not_url_syntax(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            await github_reader.fetch_pushes(client, REF, "feature/a&b#c=d")

        request = gh.requests[0]
        assert request.url.params["ref"] == "refs/heads/feature/a&b#c=d"
        assert request.url.fragment == ""
        assert request.url.path == f"{REPO_API}/activity"

    @pytest.mark.parametrize("branch", ["", "x" * 300, "line\nbreak"])
    async def test_an_impossible_branch_name_is_refused_before_any_request(
        self, gh: FakeGitHub, branch: str
    ) -> None:
        async with gh.client() as client:
            with pytest.raises(ValueError, match="branch"):
                await github_reader.fetch_pushes(client, REF, branch)

        assert gh.requests == []


class TestFetchCommitsAndTree:
    async def test_commits_are_parsed_into_lines(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            commits = await github_reader.fetch_commits(client, REF, SNAPSHOT)

        assert commits[0] == CommitLine(
            sha=SNAPSHOT,
            message="Add retry and signature tests",
            claimed_at=datetime(2026, 10, 19, 3, 50, tzinfo=UTC),
        )
        request = gh.requests[0]
        assert request.url.path == f"{REPO_API}/commits"
        assert request.url.params["sha"] == SNAPSHOT
        assert request.url.params["per_page"] == "100"

    async def test_the_tree_is_parsed_into_entries(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            tree = await github_reader.fetch_tree(client, REF, SNAPSHOT)

        assert TreeEntry(path="src/app.py", size=0) in tree
        request = gh.requests[0]
        assert request.url.path == f"{REPO_API}/git/trees/{SNAPSHOT}"
        assert request.url.params["recursive"] == "1"

    async def test_a_tree_that_github_no_longer_serves_is_repo_gone(self, gh: FakeGitHub) -> None:
        other = "e" * 40
        async with gh.client() as client:
            with pytest.raises(github_reader.RepoGone):
                await github_reader.fetch_tree(client, REF, other)

    @pytest.mark.parametrize("sha", ["", "main", "c35e92a", "C" * 40, "g" * 40, SNAPSHOT + "\n"])
    async def test_a_sha_that_is_not_forty_lowercase_hex_is_refused_before_any_request(
        self, gh: FakeGitHub, sha: str
    ) -> None:
        async with gh.client() as client:
            with pytest.raises(ValueError, match="sha"):
                await github_reader.fetch_commits(client, REF, sha)
            with pytest.raises(ValueError, match="sha"):
                await github_reader.fetch_tree(client, REF, sha)

        assert gh.requests == []

    async def test_commits_404_and_403(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            gh.forced[f"{REPO_API}/commits"] = httpx.Response(404)
            with pytest.raises(github_reader.RepoGone):
                await github_reader.fetch_commits(client, REF, SNAPSHOT)
            gh.forced[f"{REPO_API}/commits"] = httpx.Response(403)
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_commits(client, REF, SNAPSHOT)


class TestFetchText:
    async def test_none_reads_the_readme_at_the_snapshot_as_raw_text(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            readme = await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=5000)

        assert readme is not None and readme.startswith("# webhook-relay")
        request = gh.requests[0]
        assert request.url.path == f"{REPO_API}/readme"
        assert request.url.params["ref"] == SNAPSHOT
        assert request.headers["Accept"] == "application/vnd.github.raw+json"
        assert request.headers["Accept-Encoding"] == "identity"

    async def test_a_path_reads_the_contents_at_the_snapshot(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            proof = await github_reader.fetch_text(client, REF, ".talentradar", SNAPSHOT, limit=100)

        assert proof is not None and proof.strip() == "K7QX2M9PLA"
        assert gh.requests[0].url.path == f"{REPO_API}/contents/.talentradar"
        assert gh.requests[0].url.query == f"ref={SNAPSHOT}".encode()

    async def test_path_segments_are_percent_encoded_and_the_ref_is_kept(
        self, gh: FakeGitHub
    ) -> None:
        async with gh.client() as client:
            await github_reader.fetch_text(client, REF, "src/my file é.py", SNAPSHOT, limit=100)

        request = gh.requests[0]
        assert request.url.raw_path.decode() == (
            f"{REPO_API}/contents/src/my%20file%20%C3%A9.py?ref={SNAPSHOT}"
        )

    @pytest.mark.parametrize("path", ["a#b.py", "a?b.py", "100%.py", "back\\slash.py"])
    async def test_characters_that_would_rewrite_the_request_stay_in_the_path(
        self, gh: FakeGitHub, path: str
    ) -> None:
        async with gh.client() as client:
            await github_reader.fetch_text(client, REF, path, SNAPSHOT, limit=100)

        request = gh.requests[0]
        assert request.url.path == f"{REPO_API}/contents/{path}"
        assert dict(request.url.params) == {"ref": SNAPSHOT}
        assert request.url.fragment == ""

    @pytest.mark.parametrize("path", ["../x", "a/../../b", "a/./b", "..", "/abs", "a//b"])
    async def test_a_path_that_climbs_or_is_not_a_clean_relative_path_is_refused(
        self, gh: FakeGitHub, path: str
    ) -> None:
        async with gh.client() as client:
            with pytest.raises(ValueError, match="path"):
                await github_reader.fetch_text(client, REF, path, SNAPSHOT, limit=100)

        assert gh.requests == []

    async def test_a_missing_file_is_none(self, gh: FakeGitHub) -> None:
        async with gh.client() as client:
            assert (
                await github_reader.fetch_text(client, REF, "nope.py", SNAPSHOT, limit=100) is None
            )

    async def test_a_missing_readme_is_none(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/readme"] = httpx.Response(404)
        async with gh.client() as client:
            assert await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=100) is None

    async def test_rate_limits_are_unavailable_not_a_missing_file(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/contents/src/app.py"] = httpx.Response(403)
        async with gh.client() as client:
            with pytest.raises(github_reader.GitHubUnavailable):
                await github_reader.fetch_text(client, REF, "src/app.py", SNAPSHOT, limit=100)

    async def test_a_redirect_is_gone(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/readme"] = httpx.Response(
            301, headers={"Location": "https://api.github.com/repositories/1/readme"}
        )
        async with gh.client() as client:
            with pytest.raises(github_reader.RepoGone):
                await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=100)

        assert len(gh.requests) == 1

    @pytest.mark.parametrize("limit", [1, 7, 100, 1000, 2000])
    async def test_never_returns_more_than_limit_characters(
        self, gh: FakeGitHub, limit: int
    ) -> None:
        gh.forced[f"{REPO_API}/readme"] = httpx.Response(200, content=("é" * 5000).encode())
        async with gh.client() as client:
            readme = await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=limit)

        assert readme is not None and len(readme) <= limit

    async def test_a_character_cut_in_half_by_the_limit_is_dropped_not_replaced(
        self, gh: FakeGitHub
    ) -> None:
        gh.forced[f"{REPO_API}/readme"] = httpx.Response(200, content=("é" * 600).encode())
        async with gh.client() as client:
            readme = await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=1001)

        assert readme == "é" * 500
        assert chr(0xFFFD) not in readme

    async def test_text_that_fits_comes_back_whole(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/readme"] = httpx.Response(200, content="héllo wörld".encode())
        async with gh.client() as client:
            assert (
                await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=500)
                == "héllo wörld"
            )

    async def test_it_stops_reading_at_the_limit(self) -> None:
        stream = Counting(b"a" * 1000, 1000)  # a megabyte on offer
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
        async with httpx.AsyncClient(transport=transport) as client:
            readme = await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=2500)

        assert readme == "a" * 2500
        assert stream.pulled <= 2500 + 1000

    async def test_a_byte_that_is_not_text_is_replaced_not_raised(self, gh: FakeGitHub) -> None:
        gh.forced[f"{REPO_API}/readme"] = httpx.Response(200, content=b"ok \xff\xfe done")
        async with gh.client() as client:
            readme = await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=100)

        assert readme is not None and readme.startswith("ok ") and readme.endswith(" done")

    @pytest.mark.parametrize("limit", [0, -1])
    async def test_a_limit_below_one_is_refused(self, gh: FakeGitHub, limit: int) -> None:
        async with gh.client() as client:
            with pytest.raises(ValueError, match="limit"):
                await github_reader.fetch_text(client, REF, None, SNAPSHOT, limit=limit)

        assert gh.requests == []

    @pytest.mark.parametrize("sha", ["main", "", "c35e92a"])
    async def test_the_ref_must_be_a_full_sha(self, gh: FakeGitHub, sha: str) -> None:
        async with gh.client() as client:
            with pytest.raises(ValueError, match="sha"):
                await github_reader.fetch_text(client, REF, None, sha, limit=100)


class TestUrlBuilderAndDeadline:
    def test_the_built_url_is_asserted_to_be_the_api_host_and_the_intended_path(self) -> None:
        url = github_reader._url(REF, "/contents/ok.py", {"ref": SNAPSHOT})

        assert (url.scheme, url.host, url.path) == (
            "https",
            "api.github.com",
            f"{REPO_API}/contents/ok.py",
        )

    @pytest.mark.parametrize("tail", ["/contents/a/../../../orgs/x", "/contents/./x/../../.."])
    def test_a_tail_that_normalisation_would_move_out_of_the_repo_is_refused(
        self, tail: str
    ) -> None:
        with pytest.raises(ValueError, match="refusing"):
            github_reader._url(REF, tail)

    async def test_a_reply_that_never_finishes_is_unavailable_after_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github_reader, "REQUEST_DEADLINE", 0.05)

        async def stalls(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(2)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(stalls)) as client:
            with pytest.raises(github_reader.GitHubUnavailable, match="in time"):
                await github_reader.fetch_repo(client, REF)


# =========================================================================== the routes


API = "/api/v1/practice"


def _token(user_id: str) -> str:
    from api.auth import create_access_token

    return create_access_token(data={"sub": user_id, "email": "x@example.com", "role": "user"})


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests that do not pass ``now`` see ``SAVE_AT``."""
    real = challenges._moment

    def fixed(now: datetime | None = None) -> datetime:
        return real(now if now is not None else SAVE_AT)

    monkeypatch.setattr(challenges, "_moment", fixed)


@pytest_asyncio.fixture
async def anonymous() -> AsyncIterator[AsyncClient]:
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def client(db: Db, asha: str, clock: None) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {_token(asha)}"},
    ) as signed_in:
        yield signed_in


def body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"repo_url": REPO_URL, "writeup": writeup(), "video_url": None}
    payload.update(overrides)
    return payload


class TestRoutes:
    @pytest.fixture
    def github(self, github: FakeGitHub) -> FakeGitHub:
        """Start is pressed at ``SAVE_AT`` here, so the repo was made a few minutes before it."""
        github.repo["created_at"] = iso(SAVE_AT - timedelta(minutes=5))
        return github

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/challenges"),
            ("GET", "/challenges/some-slug"),
            ("POST", "/challenges/some-slug/start"),
            ("PUT", "/challenges/some-slug/submission"),
        ],
    )
    async def test_every_challenge_route_needs_a_signed_in_user(
        self, anonymous: AsyncClient, method: str, path: str
    ) -> None:
        response = await anonymous.request(method, API + path)

        assert response.status_code in (401, 403)

    async def test_the_overview_has_the_page_shape(
        self, client: AsyncClient, edition: PracticeChallenge
    ) -> None:
        response = await client.get(f"{API}/challenges")

        assert response.status_code == 200
        data = response.json()
        assert set(data) == OVERVIEW_KEYS
        assert set(data["current"]) == CHALLENGE_KEYS
        assert set(data["current"]["brief"]) == BRIEF_KEYS
        assert data["current"]["attempt"] is None
        assert data["next_opens_at"] == "2026-10-23T12:30:00+00:00"

    async def test_one_edition_by_slug(
        self, client: AsyncClient, edition: PracticeChallenge
    ) -> None:
        response = await client.get(f"{API}/challenges/{edition.slug}")

        assert response.status_code == 200
        assert set(response.json()) == CHALLENGE_KEYS

    async def test_an_unknown_slug_is_404(self, client: AsyncClient) -> None:
        assert (await client.get(f"{API}/challenges/no-such-edition")).status_code == 404
        assert (await client.post(f"{API}/challenges/no-such-edition/start")).status_code == 404
        put = await client.put(f"{API}/challenges/no-such-edition/submission", json=body())
        assert put.status_code == 404

    async def test_start_then_start_again_is_409(
        self, client: AsyncClient, edition: PracticeChallenge
    ) -> None:
        first = await client.post(f"{API}/challenges/{edition.slug}/start")
        second = await client.post(f"{API}/challenges/{edition.slug}/start")

        assert first.status_code == 200
        assert set(first.json()["attempt"]) == ATTEMPT_KEYS
        assert re.fullmatch(r"[A-Z2-9]{10}", first.json()["attempt"]["proof_code"])
        assert second.status_code == 409
        assert second.json()["detail"]

    async def test_start_after_the_last_start_is_422(self, client: AsyncClient, db: Db) -> None:
        # Open at SAVE_AT (it closes the next day), but its last start for 48 hours has passed.
        late = await add_edition(db, BRIEFS[0], opens_at=OPENS - timedelta(days=5))
        assert late.closes_at > SAVE_AT > last_start(late.closes_at, 48)

        response = await client.post(f"{API}/challenges/{late.slug}/start")

        assert response.status_code == 422
        assert response.json()["detail"]

    async def test_a_full_save_over_http(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")

        response = await client.put(
            f"{API}/challenges/{edition.slug}/submission",
            json=body(video_url="https://youtu.be/abc"),
        )

        assert response.status_code == 200
        data = response.json()
        assert set(data) == CHALLENGE_KEYS
        assert data["attempt"]["status"] == "submitted"
        assert data["attempt"]["repo_url"] == REPO_URL
        assert data["attempt"]["video_url"] == "https://youtu.be/abc"
        assert set(data["attempt"]["writeup"]) == WRITEUP_KEYS

    async def test_a_save_before_start_is_409(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        response = await client.put(f"{API}/challenges/{edition.slug}/submission", json=body())

        assert response.status_code == 409

    async def test_a_bad_link_a_short_field_and_a_bad_video_are_422_with_a_message(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")
        url = f"{API}/challenges/{edition.slug}/submission"

        bad_link = await client.put(url, json=body(repo_url="https://example.com/x/y"))
        short = await client.put(url, json=body(writeup=writeup(decisions="too short")))
        bad_video = await client.put(url, json=body(video_url="https://vimeo.com/1"))

        for response in (bad_link, short, bad_video):
            assert response.status_code == 422
            assert isinstance(response.json()["detail"], str) and response.json()["detail"]

    async def test_a_repo_older_than_start_is_422(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")
        github.repo["created_at"] = iso(STARTED - timedelta(days=3))

        response = await client.put(f"{API}/challenges/{edition.slug}/submission", json=body())

        assert response.status_code == 422
        assert "Create a new repo" in response.json()["detail"]

    async def test_a_missing_field_or_wrong_type_is_422(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")
        url = f"{API}/challenges/{edition.slug}/submission"

        no_writeup = await client.put(url, json={"repo_url": REPO_URL})
        not_text = await client.put(url, json=body(repo_url=12))
        huge = await client.put(url, json=body(writeup=writeup(decisions="x" * 200_000)))

        assert [r.status_code for r in (no_writeup, not_text, huge)] == [422, 422, 422]

    async def test_after_the_grace_hour_a_save_is_409(
        self,
        client: AsyncClient,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        github: FakeGitHub,
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")
        (row,) = await attempts_of(db, asha)
        await set_attempt(db, row.id, deadline_at=SAVE_AT - timedelta(minutes=61))

        response = await client.put(f"{API}/challenges/{edition.slug}/submission", json=body())

        assert response.status_code == 409

    async def test_the_eleventh_save_in_a_minute_is_429_with_retry_after(
        self, client: AsyncClient, asha: str, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")
        key = f"tr:practice:submit:{asha}:{int(SAVE_AT.timestamp() // 60)}"
        for _ in range(10):
            await CacheBackend.incr(key, 120)

        limited = await client.put(f"{API}/challenges/{edition.slug}/submission", json=body())

        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "60"
        assert limited.json()["detail"]
        assert github.requests == []

    async def test_the_closed_window_conflict_is_409_not_429(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        response = await client.put(f"{API}/challenges/{edition.slug}/submission", json=body())

        assert response.status_code == 409
        assert "Retry-After" not in response.headers

    async def test_github_being_down_still_saves_over_http(
        self, client: AsyncClient, edition: PracticeChallenge, github: FakeGitHub
    ) -> None:
        await client.post(f"{API}/challenges/{edition.slug}/start")
        github.down(503)

        response = await client.put(f"{API}/challenges/{edition.slug}/submission", json=body())

        assert response.status_code == 200
        assert response.json()["attempt"]["status"] == "submitted"

    async def test_each_user_sees_only_their_own_attempt_and_proof_code(
        self, db: Db, asha: str, edition: PracticeChallenge, clock: None
    ) -> None:
        from api.main import app

        bela = await add_user(db, "bela")
        async with (
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {_token(asha)}"},
            ) as mine,
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {_token(bela)}"},
            ) as theirs,
        ):
            started = await mine.post(f"{API}/challenges/{edition.slug}/start")
            their_detail = await theirs.get(f"{API}/challenges/{edition.slug}")
            their_overview = await theirs.get(f"{API}/challenges")
            their_save = await theirs.put(
                f"{API}/challenges/{edition.slug}/submission", json=body()
            )

        code = started.json()["attempt"]["proof_code"]
        assert their_detail.json()["attempt"] is None
        assert code not in their_detail.text and code not in their_overview.text
        assert their_save.status_code == 409

    async def test_a_token_for_a_deleted_account_cannot_start(
        self, db: Db, edition: PracticeChallenge, clock: None
    ) -> None:
        from api.main import app

        ghost = _token(str(uuid.uuid4()))
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {ghost}"},
        ) as signed_in:
            response = await signed_in.post(f"{API}/challenges/{edition.slug}/start")

        assert response.status_code == 404
        assert await attempts_of(db) == []

    async def test_a_practice_error_message_reaches_the_person(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise PracticeInvalid("Said for the person.")

        monkeypatch.setattr(challenges, "start", refuse)

        response = await client.post(f"{API}/challenges/anything/start")

        assert response.status_code == 422
        assert response.json()["detail"] == "Said for the person."
