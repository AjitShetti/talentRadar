"""
tests/test_practice_board.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Practice leaderboards, handles and settings: ``domain/practice_board.py``,
``services/practice/board.py`` and the board, settings and held-list routes of
``/api/v1/practice``.

* The pure rules (ranking, ties, best four of twelve, the viewer's row, handles, names) run on
  plain values.
* Services run on SQLite in a temporary file with ``PRAGMA foreign_keys=ON`` on every
  connection and the module's ``AsyncSessionLocal`` pointed at it. The shared Postgres test
  database is never touched.
* The cache is the in-process fallback, empty for every test.
* Every producer test pins its payload's key set to the type the Practice pages declare in
  ``frontend/lib/api.ts`` (plan Task 15).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.practice_board import (
    Entry,
    OverallEntry,
    board_view,
    handle,
    overall_scores,
    rank_entries,
    rank_overall,
    valid_display_name,
)
from domain.practice_judge import CRITERIA
from services import cache_backend
from services.cache_backend import CacheBackend
from services.practice import board, judge
from services.practice.errors import PracticeInvalid, PracticeNotFound
from storage.database import Base
from storage.models import PracticeAttempt, PracticeChallenge, PracticeSettings, User

# --------------------------------------------------------------------------- the clock

OPENS = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)  # Friday 18:00 IST
CLOSES = OPENS + timedelta(days=7)
DURING = OPENS + timedelta(days=2)  # the edition is open
AFTER = CLOSES + timedelta(days=2)  # the edition has closed
STARTED = OPENS + timedelta(hours=20)  # Saturday 14:30 IST

# --------------------------------------------------------------------------- Task 15 shapes

BOARD_KEYS = {"provisional", "total_entries", "queued", "held", "rows", "me"}
ROW_KEYS = {"rank", "name", "total", "hours_used", "repo_url", "you"}
OVERALL_KEYS = {"window_challenges", "rows", "me"}
OVERALL_ROW_KEYS = {"rank", "name", "score", "entries", "you"}
OVERALL_ME_KEYS = {"rank", "name", "score", "entries"}
SETTINGS_KEYS = {"display_name", "show_repo", "contest_reminder", "handle"}
HELD_KEYS = {"id", "challenge", "title", "status", "flags", "held", "cleared", "submitted_at"}

# =========================================================================== pure rules


def entry(
    user: str,
    total: float,
    pushed: datetime | None = None,
    *,
    name: str | None = None,
    hours: float | None = 10.0,
    repo: str | None = None,
) -> Entry:
    return Entry(user, name or handle(user), total, pushed, hours, repo)


class TestHandle:
    def test_it_is_stable(self) -> None:
        user = str(uuid.uuid4())

        assert handle(user) == handle(user)

    def test_it_has_the_published_shape(self) -> None:
        import re

        for _ in range(20):
            assert re.fullmatch(r"builder-[0-9a-f]{4}", handle(str(uuid.uuid4())))

    def test_it_is_the_first_four_hex_characters_of_the_sha256_of_the_id(self) -> None:
        user = "0f6c1a9e-3a47-4b8e-9d62-5c1d6f7a8b90"

        assert handle(user) == "builder-" + hashlib.sha256(user.encode()).hexdigest()[:4]

    def test_different_ids_mostly_differ(self) -> None:
        assert len({handle(str(uuid.uuid4())) for _ in range(50)}) > 40

    def test_it_never_contains_the_id(self) -> None:
        user = str(uuid.uuid4())

        assert user not in handle(user)
        assert user[:8] not in handle(user)


class TestDisplayName:
    @pytest.mark.parametrize(
        "name",
        [
            "Ajit S.",
            "ab",
            "a" * 32,
            "Asha_K-2",
            "J. R. Rao",
            "Priya 2",
            "अजित",  # a Devanagari name: letters and vowel signs
            "Zoë",
            "J.R.",  # trailing dot, but not a host name: the last part is one letter
            "Builder Bob",  # only "builder-" is a handle's prefix
            "builders-guild",
        ],
    )
    def test_a_good_name_is_accepted(self, name: str) -> None:
        assert valid_display_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "a",
            "",
            "a" * 33,
            "me@x.com",
            "https://x",
            "<b>x</b>",
            " Ajit",
            "Ajit ",
            "www.example.com",
            "ajit.dev",
            "linkedin.com/in/x",
            "--",
            "x\ny",
            "tab\tname",
            "A" + chr(0x200B) + "B",  # a zero-width space
            "ab\N{GRINNING FACE}",
            "ab;drop",
            "a" + "\N{COMBINING ACUTE ACCENT}" * 6,
            # a host name hidden behind trailing punctuation is still a host name
            "ajit.dev.",
            "ajit.dev-",
            "ajit.dev_",
            "www.example.com.",
            "www.example.com.-_",
            ".ajit.dev",
            "Ajit ajit.dev. Rao",
            # a name that starts like a handle would impersonate one, whatever its case
            "builder-7f3a",
            "Builder-7f3a",
            "BUILDER-ABCD",
            "builder-",
            "bUiLdEr-x",
            # ... nor do fullwidth look-alikes of its letters: NFKC folds them to ASCII first
            "\N{FULLWIDTH LATIN SMALL LETTER B}uilder-7f3a",
            "\N{FULLWIDTH LATIN SMALL LETTER B}\N{FULLWIDTH LATIN SMALL LETTER U}"
            "\N{FULLWIDTH LATIN SMALL LETTER I}\N{FULLWIDTH LATIN SMALL LETTER L}"
            "\N{FULLWIDTH LATIN SMALL LETTER D}\N{FULLWIDTH LATIN SMALL LETTER E}"
            "\N{FULLWIDTH LATIN SMALL LETTER R}-7f3a",
            "\N{FULLWIDTH LATIN CAPITAL LETTER B}UILDER-7f3a",
        ],
    )
    def test_a_bad_name_is_refused(self, name: str) -> None:
        assert not valid_display_name(name)


class TestRankEntries:
    def test_equal_totals_share_a_rank_and_the_earlier_snapshot_is_first(self) -> None:
        early = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
        late = early + timedelta(hours=5)
        entries = [entry("c", 80.0, early), entry("b", 90.0, late), entry("a", 90.0, early)]

        ranked = rank_entries(entries)

        assert [(rank, e.user_id) for rank, e in ranked] == [(1, "a"), (1, "b"), (3, "c")]

    def test_ranks_are_one_one_three(self) -> None:
        ranked = rank_entries([entry("a", 90.0), entry("b", 90.0), entry("c", 80.0)])

        assert [rank for rank, _ in ranked] == [1, 1, 3]

    def test_ranks_skip_after_a_tie_in_the_middle(self) -> None:
        ranked = rank_entries(
            [entry("a", 90.0), entry("b", 80.0), entry("c", 80.0), entry("d", 70.0)]
        )

        assert [rank for rank, _ in ranked] == [1, 2, 2, 4]

    def test_a_missing_snapshot_time_sorts_after_a_known_one_among_equals(self) -> None:
        when = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)

        ranked = rank_entries([entry("a", 90.0, None), entry("b", 90.0, when)])

        assert [e.user_id for _, e in ranked] == ["b", "a"]

    def test_the_order_does_not_depend_on_the_input_order(self) -> None:
        when = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
        entries = [entry(user, 90.0, when) for user in "dbace"]

        assert rank_entries(entries) == rank_entries(list(reversed(entries)))
        assert [e.user_id for _, e in rank_entries(entries)] == list("abcde")

    def test_nothing_ranks_nothing(self) -> None:
        assert rank_entries([]) == []

    def test_higher_totals_come_first(self) -> None:
        ranked = rank_entries([entry("a", 55.5), entry("b", 99.0), entry("c", 70.0)])

        assert [e.user_id for _, e in ranked] == ["b", "c", "a"]


class TestOverallScores:
    def test_it_sums_the_best_four_and_counts_every_entry(self) -> None:
        assert overall_scores({"u": [50, 90, 70, 80, 60]}) == [("u", 300.0, 5)]

    def test_fewer_than_four_entries_sum_what_there_is(self) -> None:
        assert overall_scores({"u": [90.5, 60.0]}) == [("u", 150.5, 2)]

    def test_it_is_sorted_with_the_highest_first(self) -> None:
        scores = overall_scores({"a": [50.0], "b": [90.0, 80.0], "c": [70.0]})

        assert [user for user, _, _ in scores] == ["b", "c", "a"]

    def test_a_person_with_no_entry_is_left_out(self) -> None:
        assert overall_scores({"a": [], "b": [10.0]}) == [("b", 10.0, 1)]

    def test_floating_point_noise_is_rounded_away(self) -> None:
        assert overall_scores({"u": [84.5, 70.1, 70.1, 66.3]})[0][1] == 291.0

    def test_best_can_be_changed(self) -> None:
        assert overall_scores({"u": [1.0, 2.0, 3.0]}, best=2) == [("u", 5.0, 3)]

    def test_equal_scores_keep_a_fixed_order(self) -> None:
        scores = overall_scores({"b": [10.0], "a": [10.0]})

        assert [user for user, _, _ in scores] == ["a", "b"]

    def test_ranks_share_on_equal_scores(self) -> None:
        ranked = rank_overall(
            [
                OverallEntry("a", "x", 300.0, 5),
                OverallEntry("b", "y", 300.0, 4),
                OverallEntry("c", "z", 90.0, 1),
            ]
        )

        assert [rank for rank, _ in ranked] == [1, 1, 3]


class TestBoardView:
    def ranked(self, count: int) -> list[tuple[int, Entry]]:
        return rank_entries([entry(f"u{i:02d}", 100.0 - i) for i in range(count)])

    def test_a_viewer_below_the_cut_gets_the_top_and_their_own_row(self) -> None:
        ranked = self.ranked(48)

        rows, me = board_view(ranked, "u30", 25)

        assert len(rows) == 25
        assert [rank for rank, _ in rows] == list(range(1, 26))
        assert me is not None
        assert me[0] == 31
        assert me[1].user_id == "u30"
        assert all(e.user_id != "u30" for _, e in rows)

    def test_nobody_else_below_the_cut_is_shown(self) -> None:
        rows, _ = board_view(self.ranked(48), "u30", 25)

        assert {e.user_id for _, e in rows} == {f"u{i:02d}" for i in range(25)}

    def test_a_viewer_inside_the_top_has_that_row(self) -> None:
        ranked = self.ranked(48)

        rows, me = board_view(ranked, "u03", 25)

        assert me == ranked[3]
        assert me in rows

    def test_a_viewer_with_no_entry_has_no_row(self) -> None:
        rows, me = board_view(self.ranked(10), "nobody", 25)

        assert len(rows) == 10
        assert me is None

    def test_it_works_for_the_overall_board_too(self) -> None:
        ranked = rank_overall([OverallEntry(f"u{i}", f"n{i}", 400.0 - i, 4) for i in range(60)])

        rows, me = board_view(ranked, "u55", 50)

        assert len(rows) == 50
        assert me is not None
        assert me[0] == 56


# =========================================================================== the database


@dataclass
class Db:
    engine: Any
    factory: async_sessionmaker[AsyncSession]
    path: Path


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    path = tmp_path / "practice_board.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        Base.metadata.tables[name]
        for name in (
            "users",
            "practice_challenges",
            "practice_attempts",
            "practice_settings",
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(board, "AsyncSessionLocal", factory)
    monkeypatch.setattr(judge, "AsyncSessionLocal", factory)
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


async def add_user(db: Db, name: str = "asha") -> str:
    async with db.factory() as session:
        user = User(email=f"{name}@example.com", hashed_password="not-a-real-hash")
        session.add(user)
        await session.commit()
        return str(user.id)


async def add_edition(
    db: Db, *, opens_at: datetime = OPENS, brief_slug: str = "relay-sample"
) -> PracticeChallenge:
    row = PracticeChallenge(
        slug=f"{brief_slug}-{opens_at.date().isoformat()}",
        brief_slug=brief_slug,
        title=f"Sample build {brief_slug}",
        track="backend",
        hours=48,
        brief={"slug": brief_slug},
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
    status: str = "judged",
    total: float | None = 80.0,
    pushed_after: timedelta | None = timedelta(hours=10),
    **fields: Any,
) -> uuid.UUID:
    """An attempt of ``edition``: by default judged, unheld, pushed ten hours after Start."""
    started_at = edition.opens_at + timedelta(hours=20)
    fields.setdefault("proof_code", uuid.uuid4().hex[:10].upper())
    row = PracticeAttempt(
        user_id=uuid.UUID(user_id),
        challenge_id=edition.id,
        status=status,
        started_at=started_at,
        deadline_at=started_at + timedelta(hours=edition.hours),
        total=total,
        snapshot_pushed_at=started_at + pushed_after if pushed_after is not None else None,
        **fields,
    )
    async with db.factory() as session:
        session.add(row)
        await session.commit()
        return row.id


async def set_settings(db: Db, user_id: str, **values: Any) -> None:
    async with db.factory() as session:
        session.add(PracticeSettings(user_id=uuid.UUID(user_id), **values))
        await session.commit()


async def settings_rows(db: Db) -> list[PracticeSettings]:
    async with db.factory() as session:
        return list((await session.execute(select(PracticeSettings))).scalars().all())


@pytest_asyncio.fixture
async def asha(db: Db) -> str:
    return await add_user(db, "asha")


@pytest_asyncio.fixture
async def bina(db: Db) -> str:
    return await add_user(db, "bina")


@pytest_asyncio.fixture
async def edition(db: Db) -> PracticeChallenge:
    """One edition, opened Friday 16 October 2026."""
    return await add_edition(db)


def names(payload: dict[str, Any]) -> list[str]:
    return [row["name"] for row in payload["rows"]]


# =========================================================================== the challenge board


class TestChallengeBoard:
    async def test_it_has_the_page_shape(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=88.5)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert set(payload) == BOARD_KEYS
        assert set(payload["rows"][0]) == ROW_KEYS
        assert set(payload["me"]) == ROW_KEYS
        assert payload["total_entries"] == 1
        assert payload["rows"][0]["rank"] == 1
        assert payload["rows"][0]["total"] == 88.5

    async def test_held_attempt_is_absent_from_leaderboard(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        shown = await add_user(db, "shown")
        held = await add_user(db, "held")
        queued = await add_user(db, "queued")
        rejected = await add_user(db, "rejected")
        failed = await add_user(db, "failed")
        waiting = await add_user(db, "waiting")
        missed = await add_user(db, "missed")
        started = await add_user(db, "started")
        await add_attempt(db, shown, edition, total=70.0)
        await add_attempt(db, held, edition, total=99.0, held=True, flags=["injection_text"])
        await add_attempt(db, queued, edition, status="queued", total=None)
        await add_attempt(db, rejected, edition, status="rejected", total=None)
        await add_attempt(db, failed, edition, status="judge_failed", total=None)
        await add_attempt(db, waiting, edition, status="submitted", total=None)
        await add_attempt(db, missed, edition, status="missed", total=None)
        await add_attempt(db, started, edition, status="started", total=None)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["total_entries"] == 1
        assert [row["total"] for row in payload["rows"]] == [70.0]
        assert payload["me"] is None

    async def test_a_requeued_attempt_keeps_its_old_total_off_the_board(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        judged = await add_attempt(db, asha, edition, total=80.0)
        await add_attempt(db, bina, edition, total=70.0)

        await judge.admin_requeue(judged, now=AFTER)  # status back to queued; the old total stays
        payload = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert [row["total"] for row in payload["rows"]] == [70.0]
        assert payload["queued"] == 1
        assert payload["provisional"] is True

    async def test_a_held_viewer_does_not_see_a_row_either(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=99.0, held=True)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["rows"] == []
        assert payload["me"] is None

    async def test_a_judged_row_without_a_total_is_not_ranked(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=None)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["rows"] == []

    async def test_an_admin_override_ranks_with_the_overridden_total(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=60.0)
        waiting = await add_attempt(db, bina, edition, status="queued", total=None)
        scores = dict.fromkeys(CRITERIA, 9)  # 90.0

        await judge.admin_override(waiting, scores, "Read by hand.", now=AFTER)
        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert [row["total"] for row in payload["rows"]] == [90.0, 60.0]
        assert payload["queued"] == 0

    async def test_equal_totals_share_a_rank_and_the_earlier_snapshot_is_first(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        cleo = await add_user(db, "cleo")
        await add_attempt(db, asha, edition, total=90.0, pushed_after=timedelta(hours=30))
        await add_attempt(db, bina, edition, total=90.0, pushed_after=timedelta(hours=5))
        await add_attempt(db, cleo, edition, total=80.0)
        await set_settings(db, asha, display_name="Asha")
        await set_settings(db, bina, display_name="Bina")

        payload = await board.challenge_board(cleo, edition.slug, now=AFTER)

        assert [(row["rank"], row["name"]) for row in payload["rows"][:2]] == [
            (1, "Bina"),
            (1, "Asha"),
        ]
        assert payload["rows"][2]["rank"] == 3

    async def test_hours_used_is_start_to_snapshot(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, pushed_after=timedelta(hours=10, minutes=30))
        await add_attempt(db, bina, edition, total=70.0, pushed_after=None)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert [row["hours_used"] for row in payload["rows"]] == [10.5, None]

    async def test_the_top_25_and_the_viewers_own_row(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        for number in range(30):
            rival = await add_user(db, f"rival{number}")
            await add_attempt(db, rival, edition, total=90.0 - number * 0.5)
        await add_attempt(db, asha, edition, total=10.0)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert board.CHALLENGE_TOP == 25
        assert payload["total_entries"] == 31
        assert len(payload["rows"]) == 25
        assert [row["rank"] for row in payload["rows"]] == list(range(1, 26))
        assert not any(row["you"] for row in payload["rows"])
        assert payload["me"]["rank"] == 31
        assert payload["me"]["you"] is True
        assert payload["me"]["total"] == 10.0

    async def test_a_viewer_inside_the_top_is_marked_in_the_rows(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=95.0)
        await add_attempt(db, bina, edition, total=85.0)

        payload = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert [row["you"] for row in payload["rows"]] == [False, True]
        assert payload["me"] == payload["rows"][1]

    async def test_a_viewer_with_no_entry_has_no_me(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        payload = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert payload["me"] is None
        assert [row["you"] for row in payload["rows"]] == [False]

    async def test_an_empty_board_is_empty(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload == {
            "provisional": False,
            "total_entries": 0,
            "queued": 0,
            "held": 0,
            "rows": [],
            "me": None,
        }

    # ------------------------------------------------------------------ provisional

    async def test_the_board_is_provisional_while_the_edition_is_open(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        payload = await board.challenge_board(asha, edition.slug, now=DURING)

        assert payload["provisional"] is True

    async def test_the_board_is_final_once_closed_with_nothing_queued(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["provisional"] is False

    async def test_a_queued_attempt_keeps_a_closed_board_provisional_and_is_counted(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        cleo = await add_user(db, "cleo")
        await add_attempt(db, asha, edition)
        await add_attempt(db, bina, edition, status="queued", total=None)
        await add_attempt(db, cleo, edition, status="queued", total=None, held=True)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["provisional"] is True
        assert payload["queued"] == 1  # the held one is waiting for a person, not for the judge
        assert payload["held"] == 1

    async def test_a_held_attempt_alone_does_not_keep_a_closed_board_provisional(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        """A hold can last as long as nobody clears it: it must not leave "Provisional" on for good."""
        await add_attempt(db, asha, edition)
        await add_attempt(
            db, bina, edition, status="queued", total=None, held=True, flags=["injection_text"]
        )

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["provisional"] is False
        assert payload["queued"] == 0
        assert payload["held"] == 1

    async def test_a_judged_attempt_held_again_by_its_appeal_is_counted_as_held(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)
        await add_attempt(db, bina, edition, total=88.0, held=True)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["total_entries"] == 1
        assert payload["queued"] == 0
        assert payload["held"] == 1

    async def test_a_submission_still_to_be_queued_keeps_a_closed_board_provisional(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)
        await add_attempt(db, bina, edition, status="submitted", total=None)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["provisional"] is True
        assert payload["queued"] == 0

    async def test_the_last_hour_of_saves_is_still_provisional(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        payload = await board.challenge_board(
            asha, edition.slug, now=CLOSES + timedelta(minutes=30)
        )

        assert payload["provisional"] is True

    async def test_rejected_and_missed_attempts_do_not_keep_a_board_provisional(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        cleo = await add_user(db, "cleo")
        await add_attempt(db, asha, edition)
        await add_attempt(db, bina, edition, status="rejected", total=None)
        await add_attempt(db, cleo, edition, status="missed", total=None)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["provisional"] is False

    # ------------------------------------------------------------------ names and privacy

    async def test_the_default_name_is_the_handle(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["rows"][0]["name"] == handle(asha)
        assert payload["rows"][0]["name"].startswith("builder-")

    async def test_a_display_name_shows_only_when_set(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0)
        await add_attempt(db, bina, edition, total=80.0)
        await set_settings(db, asha, display_name="Asha K.")
        await set_settings(db, bina, display_name=None, show_repo=True)

        payload = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert names(payload) == ["Asha K.", handle(bina)]

    async def test_a_stored_name_that_would_not_pass_today_falls_back_to_the_handle(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)
        await set_settings(db, asha, display_name="asha@example.com")

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["rows"][0]["name"] == handle(asha)

    async def test_the_repo_link_shows_only_when_the_owner_opted_in(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0, repo_owner="asha-dev", repo_name="relay")
        await add_attempt(db, bina, edition, total=80.0, repo_owner="bina-dev", repo_name="relay")
        await set_settings(db, asha, show_repo=True)
        await set_settings(db, bina, show_repo=False)

        payload = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert [row["repo_url"] for row in payload["rows"]] == [
            "https://github.com/asha-dev/relay",
            None,
        ]
        assert "bina-dev" not in json.dumps(payload)

    async def test_opting_in_without_a_repo_gives_no_link(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)
        await set_settings(db, asha, show_repo=True)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["rows"][0]["repo_url"] is None

    async def test_the_payload_carries_nothing_that_identifies_anyone(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        code = "ABCDE23456"
        await add_attempt(
            db,
            asha,
            edition,
            total=90.0,
            repo_owner="asha-secret-dev",
            repo_name="relay",
            snapshot_sha="a" * 40,
            scores={"thinking": 9},
            notes={"summary": "A private sentence about this build."},
            signals={"commits": 12},
            appeal_reason="my private reason",
            writeup={"decisions": "my private decisions"},
            proof_code=code,
        )
        await add_attempt(db, bina, edition, total=70.0)

        for viewer in (asha, bina):
            payload = await board.challenge_board(viewer, edition.slug, now=AFTER)
            text = json.dumps(payload)

            for secret in (
                asha,
                bina,
                "asha@example.com",
                "bina@example.com",
                code,
                "asha-secret-dev",
                "a" * 40,
                "private",
                "thinking",
                "commits",
            ):
                assert secret not in text
            for row in payload["rows"]:
                assert set(row) == ROW_KEYS

    # ------------------------------------------------------------------ the edition

    async def test_an_unknown_edition_is_not_found(self, db: Db, asha: str) -> None:
        with pytest.raises(PracticeNotFound):
            await board.challenge_board(asha, "no-such-edition", now=AFTER)

    async def test_an_edition_that_has_not_opened_is_not_found(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await board.challenge_board(asha, edition.slug, now=OPENS - timedelta(hours=1))

    async def test_a_user_id_that_is_not_an_id_is_not_found(
        self, db: Db, edition: PracticeChallenge
    ) -> None:
        with pytest.raises(PracticeNotFound):
            await board.challenge_board("not-a-uuid", edition.slug, now=AFTER)

    # ------------------------------------------------------------------ the cache

    async def test_the_board_is_cached_for_ten_minutes_and_the_viewer_is_not(
        self,
        db: Db,
        asha: str,
        bina: str,
        edition: PracticeChallenge,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0)
        await add_attempt(db, bina, edition, total=80.0)
        await board.challenge_board(asha, edition.slug, now=AFTER)

        key = f"tr:practice:board:{edition.slug}"
        assert key in cache_backend._MEMORY
        remaining = cache_backend._MEMORY[key][0] - time.time()
        assert 590 < remaining <= 600

        def no_database() -> None:
            raise AssertionError("a cached board must not read the database")

        monkeypatch.setattr(board, "AsyncSessionLocal", no_database)
        second = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert [row["you"] for row in second["rows"]] == [False, True]
        assert second["me"]["rank"] == 2

    async def test_a_new_judgement_appears_after_the_cache_is_gone(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0)
        await board.challenge_board(asha, edition.slug, now=AFTER)
        await add_attempt(db, bina, edition, total=95.0)

        assert (await board.challenge_board(asha, edition.slug, now=AFTER))["total_entries"] == 1
        cache_backend._MEMORY.clear()
        assert (await board.challenge_board(asha, edition.slug, now=AFTER))["total_entries"] == 2

    async def test_a_damaged_cache_entry_is_rebuilt(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0)
        await CacheBackend.set_json(f"tr:practice:board:{edition.slug}", {"entries": "junk"}, 600)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["total_entries"] == 1

    async def test_a_cache_entry_from_before_held_was_counted_is_rebuilt(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0)
        old_shape = {"provisional": False, "queued": 0, "entries": []}
        await CacheBackend.set_json(f"tr:practice:board:{edition.slug}", old_shape, 600)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["total_entries"] == 1
        assert payload["held"] == 0


# =========================================================================== the overall board


async def weekly_editions(db: Db, count: int) -> list[PracticeChallenge]:
    """``count`` editions, oldest first, the newest opened Friday 16 October 2026."""
    editions = []
    for week in range(count):
        opens = OPENS - timedelta(weeks=count - 1 - week)
        editions.append(await add_edition(db, opens_at=opens, brief_slug=f"brief-{week:02d}"))
    return editions


class TestOverallBoard:
    async def test_it_has_the_page_shape(self, db: Db, asha: str, bina: str) -> None:
        editions = await weekly_editions(db, 3)
        await add_attempt(db, asha, editions[0], total=70.0)
        await add_attempt(db, asha, editions[1], total=80.0)
        await add_attempt(db, bina, editions[2], total=90.0)

        payload = await board.overall_board(asha, now=AFTER)

        assert set(payload) == OVERALL_KEYS
        assert set(payload["rows"][0]) == OVERALL_ROW_KEYS
        assert set(payload["me"]) == OVERALL_ME_KEYS
        assert payload["window_challenges"] == 3
        assert [(row["rank"], row["score"], row["entries"]) for row in payload["rows"]] == [
            (1, 150.0, 2),
            (2, 90.0, 1),
        ]
        assert payload["me"] == {"rank": 1, "name": handle(asha), "score": 150.0, "entries": 2}

    async def test_a_score_is_the_best_four_totals(self, db: Db, asha: str) -> None:
        editions = await weekly_editions(db, 5)
        for edition, total in zip(editions, [50.0, 90.0, 70.0, 80.0, 60.0], strict=True):
            await add_attempt(db, asha, edition, total=total)

        payload = await board.overall_board(asha, now=AFTER)

        assert payload["rows"][0]["score"] == 300.0
        assert payload["rows"][0]["entries"] == 5

    async def test_overall_only_counts_the_last_12_editions(self, db: Db, asha: str) -> None:
        editions = await weekly_editions(db, 14)
        # The two oldest editions are outside the window, so their big totals do not count.
        await add_attempt(db, asha, editions[0], total=100.0)
        await add_attempt(db, asha, editions[1], total=100.0)
        await add_attempt(db, asha, editions[2], total=40.0)
        await add_attempt(db, asha, editions[13], total=50.0)

        payload = await board.overall_board(asha, now=AFTER)

        assert board.OVERALL_WINDOW == 12
        assert payload["window_challenges"] == 12
        assert payload["rows"][0]["score"] == 90.0
        assert payload["rows"][0]["entries"] == 2

    async def test_an_edition_that_has_not_opened_is_not_in_the_window(
        self, db: Db, asha: str
    ) -> None:
        editions = await weekly_editions(db, 2)
        future = await add_edition(db, opens_at=AFTER + timedelta(days=3), brief_slug="later")
        await add_attempt(db, asha, editions[0], total=60.0)
        await add_attempt(db, asha, future, total=99.0)

        payload = await board.overall_board(asha, now=AFTER)

        assert payload["window_challenges"] == 2
        assert payload["rows"][0]["score"] == 60.0

    async def test_held_queued_and_rejected_attempts_do_not_count(
        self, db: Db, asha: str, bina: str
    ) -> None:
        editions = await weekly_editions(db, 4)
        await add_attempt(db, asha, editions[0], total=70.0)
        await add_attempt(db, asha, editions[1], total=99.0, held=True)
        await add_attempt(db, asha, editions[2], status="queued", total=None)
        await add_attempt(db, asha, editions[3], status="rejected", total=None)

        payload = await board.overall_board(bina, now=AFTER)

        assert payload["rows"][0]["score"] == 70.0
        assert payload["rows"][0]["entries"] == 1

    async def test_the_top_50_and_the_viewers_own_row(self, db: Db, asha: str) -> None:
        (edition,) = await weekly_editions(db, 1)
        for number in range(55):
            rival = await add_user(db, f"rival{number}")
            await add_attempt(db, rival, edition, total=99.0 - number * 0.5)
        await add_attempt(db, asha, edition, total=5.0)

        payload = await board.overall_board(asha, now=AFTER)

        assert board.OVERALL_TOP == 50
        assert len(payload["rows"]) == 50
        assert not any(row["you"] for row in payload["rows"])
        assert payload["me"]["rank"] == 56
        assert payload["me"]["score"] == 5.0

    async def test_a_viewer_inside_the_top_is_marked(self, db: Db, asha: str, bina: str) -> None:
        (edition,) = await weekly_editions(db, 1)
        await add_attempt(db, asha, edition, total=90.0)
        await add_attempt(db, bina, edition, total=80.0)

        payload = await board.overall_board(bina, now=AFTER)

        assert [row["you"] for row in payload["rows"]] == [False, True]

    async def test_equal_scores_share_a_rank(self, db: Db, asha: str, bina: str) -> None:
        (edition,) = await weekly_editions(db, 1)
        await add_attempt(db, asha, edition, total=80.0)
        await add_attempt(db, bina, edition, total=80.0)

        payload = await board.overall_board(asha, now=AFTER)

        assert [row["rank"] for row in payload["rows"]] == [1, 1]

    async def test_names_follow_the_settings(self, db: Db, asha: str, bina: str) -> None:
        (edition,) = await weekly_editions(db, 1)
        await add_attempt(db, asha, edition, total=90.0)
        await add_attempt(db, bina, edition, total=80.0)
        await set_settings(db, asha, display_name="Asha K.")

        payload = await board.overall_board(bina, now=AFTER)

        assert names(payload) == ["Asha K.", handle(bina)]

    async def test_the_payload_carries_nothing_that_identifies_anyone(
        self, db: Db, asha: str, bina: str
    ) -> None:
        (edition,) = await weekly_editions(db, 1)
        await add_attempt(
            db, asha, edition, total=90.0, repo_owner="asha-secret-dev", repo_name="relay"
        )
        await add_attempt(db, bina, edition, total=80.0, scores={"thinking": 9})

        text = json.dumps(await board.overall_board(bina, now=AFTER))

        for secret in (asha, bina, "asha@example.com", "asha-secret-dev", "thinking", "repo"):
            assert secret not in text

    async def test_it_is_cached_under_its_own_key(self, db: Db, asha: str) -> None:
        (edition,) = await weekly_editions(db, 1)
        await add_attempt(db, asha, edition, total=90.0)
        await board.overall_board(asha, now=AFTER)

        key = "tr:practice:board:overall"
        assert key in cache_backend._MEMORY
        assert 590 < cache_backend._MEMORY[key][0] - time.time() <= 600

    async def test_with_no_editions_it_is_empty(self, db: Db, asha: str) -> None:
        payload = await board.overall_board(asha, now=AFTER)

        assert payload == {"window_challenges": 0, "rows": [], "me": None}


# =========================================================================== settings


class TestSettings:
    async def test_the_defaults_for_someone_who_never_saved(self, db: Db, asha: str) -> None:
        payload = await board.get_settings(asha)

        assert payload == {
            "display_name": None,
            "show_repo": False,
            "contest_reminder": True,
            "handle": handle(asha),
        }
        assert set(payload) == SETTINGS_KEYS
        assert await settings_rows(db) == []  # a read writes nothing

    async def test_a_saved_row_is_read_back(self, db: Db, asha: str) -> None:
        saved = await board.put_settings(
            asha, display_name="Asha K.", show_repo=True, contest_reminder=False
        )

        assert saved == {
            "display_name": "Asha K.",
            "show_repo": True,
            "contest_reminder": False,
            "handle": handle(asha),
        }
        assert await board.get_settings(asha) == saved

    async def test_saving_again_replaces_the_one_row(self, db: Db, asha: str) -> None:
        await board.put_settings(asha, display_name="Asha", show_repo=True, contest_reminder=True)
        await board.put_settings(asha, display_name=None, show_repo=False, contest_reminder=False)

        rows = await settings_rows(db)
        assert len(rows) == 1
        assert (rows[0].display_name, rows[0].show_repo, rows[0].contest_reminder) == (
            None,
            False,
            False,
        )

    async def test_a_name_is_trimmed(self, db: Db, asha: str) -> None:
        saved = await board.put_settings(
            asha, display_name="  Asha K.  ", show_repo=False, contest_reminder=True
        )

        assert saved["display_name"] == "Asha K."

    @pytest.mark.parametrize("blank", ["", "   ", None])
    async def test_a_blank_name_clears_it(self, db: Db, asha: str, blank: str | None) -> None:
        await board.put_settings(asha, display_name="Asha", show_repo=False, contest_reminder=True)

        saved = await board.put_settings(
            asha, display_name=blank, show_repo=False, contest_reminder=True
        )

        assert saved["display_name"] is None

    @pytest.mark.parametrize(
        "bad", ["a", "a" * 33, "me@x.com", "https://x", "<b>x</b>", "ajit.dev"]
    )
    async def test_an_invalid_name_is_refused_and_nothing_is_stored(
        self, db: Db, asha: str, bad: str
    ) -> None:
        with pytest.raises(PracticeInvalid):
            await board.put_settings(asha, display_name=bad, show_repo=True, contest_reminder=True)

        assert await settings_rows(db) == []

    async def test_an_unknown_account_is_not_found(self, db: Db) -> None:
        with pytest.raises(PracticeNotFound):
            await board.put_settings(
                str(uuid.uuid4()), display_name=None, show_repo=False, contest_reminder=True
            )

    async def test_an_id_that_is_not_an_id_is_not_found(self, db: Db) -> None:
        with pytest.raises(PracticeNotFound):
            await board.get_settings("nope")
        with pytest.raises(PracticeNotFound):
            await board.put_settings(
                "nope", display_name=None, show_repo=False, contest_reminder=True
            )

    async def test_turning_the_repo_link_off_takes_effect_at_once(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0, repo_owner="asha-dev", repo_name="relay")
        await board.put_settings(asha, display_name="Asha", show_repo=True, contest_reminder=True)
        shown = await board.challenge_board(bina, edition.slug, now=AFTER)
        assert shown["rows"][0]["repo_url"] == "https://github.com/asha-dev/relay"

        await board.put_settings(asha, display_name="Asha", show_repo=False, contest_reminder=True)
        hidden = await board.challenge_board(bina, edition.slug, now=AFTER)

        assert hidden["rows"][0]["repo_url"] is None
        assert hidden["rows"][0]["name"] == "Asha"

    async def test_removing_the_display_name_takes_effect_at_once_on_both_boards(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=90.0)
        await board.put_settings(asha, display_name="Asha", show_repo=False, contest_reminder=True)
        assert names(await board.challenge_board(bina, edition.slug, now=AFTER)) == ["Asha"]
        assert names(await board.overall_board(bina, now=AFTER)) == ["Asha"]

        await board.put_settings(asha, display_name=None, show_repo=False, contest_reminder=True)

        assert names(await board.challenge_board(bina, edition.slug, now=AFTER)) == [handle(asha)]
        assert names(await board.overall_board(bina, now=AFTER)) == [handle(asha)]

    @pytest.mark.parametrize("broken", ["cache", "database"])
    async def test_a_failure_forgetting_the_boards_is_logged_and_never_raised(
        self,
        db: Db,
        asha: str,
        edition: PracticeChallenge,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        broken: str,
    ) -> None:
        # It runs after the commit: the settings are saved, so the caller must still get them.
        await add_attempt(db, asha, edition, total=90.0)

        async def refuse_cache(key: str) -> None:
            raise RuntimeError("cache is down")

        def refuse_database() -> Any:
            raise RuntimeError("database is down")

        if broken == "cache":
            monkeypatch.setattr(CacheBackend, "delete", refuse_cache)
        else:
            monkeypatch.setattr(board, "AsyncSessionLocal", refuse_database)

        with caplog.at_level("WARNING", logger=board.logger.name):
            await board._forget_boards(uuid.UUID(asha))

        assert any("forget" in record.getMessage().lower() for record in caplog.records)

    async def test_saved_settings_are_returned_when_forgetting_the_boards_fails(
        self, db: Db, asha: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refuse_cache(key: str) -> None:
            raise RuntimeError("cache is down")

        monkeypatch.setattr(CacheBackend, "delete", refuse_cache)

        saved = await board.put_settings(
            asha, display_name="Asha", show_repo=True, contest_reminder=False
        )

        assert saved["display_name"] == "Asha"
        (row,) = await settings_rows(db)
        assert (row.display_name, row.show_repo, row.contest_reminder) == ("Asha", True, False)

    async def test_a_saved_name_is_never_a_row_for_someone_who_never_competed(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await board.put_settings(asha, display_name="Asha", show_repo=True, contest_reminder=True)

        payload = await board.challenge_board(asha, edition.slug, now=AFTER)

        assert payload["rows"] == []


# =========================================================================== the held list


class TestHeldAttempts:
    async def test_it_lists_a_held_attempt_with_what_an_admin_needs(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        attempt = await add_attempt(
            db,
            asha,
            edition,
            status="queued",
            total=None,
            held=True,
            flags=["injection_text"],
            submitted_at=STARTED + timedelta(hours=5),
            repo_owner="asha-secret-dev",
            repo_name="relay",
            writeup={"decisions": "private write-up text"},
            proof_code="ABCDE23456",
        )

        payload = await board.held_attempts()

        (row,) = payload["attempts"]
        assert set(row) == HELD_KEYS
        assert row["id"] == str(attempt)
        assert row["challenge"] == edition.slug
        assert row["title"] == edition.title
        assert row["status"] == "queued"
        assert row["flags"] == ["injection_text"]
        assert row["held"] is True
        assert row["cleared"] is False
        assert row["submitted_at"] == "2026-10-17T13:30:00+00:00"
        text = json.dumps(payload)
        for secret in (asha, "asha@example.com", "asha-secret-dev", "private", "ABCDE23456"):
            assert secret not in text

    async def test_a_cleared_hold_is_listed_as_cleared(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        attempt = await add_attempt(
            db, asha, edition, status="queued", total=None, held=True, flags=["injection_text"]
        )
        await judge.admin_clear(attempt, now=AFTER)

        (row,) = (await board.held_attempts())["attempts"]

        assert row["held"] is False
        assert row["cleared"] is True
        assert row["flags"] == ["injection_text", "hold_cleared"]

    async def test_the_hash_of_a_cleared_input_is_not_shown(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        digest = "f" * 64
        attempt = await add_attempt(
            db,
            asha,
            edition,
            status="queued",
            total=None,
            held=True,
            flags=["injection_text"],
            judge_input_sha256=digest,
        )
        await judge.admin_clear(attempt, now=AFTER)

        text = json.dumps(await board.held_attempts())

        assert digest not in text

    async def test_attempts_that_were_never_held_are_not_listed(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition)
        await add_attempt(db, bina, edition, status="queued", total=None)

        assert (await board.held_attempts())["attempts"] == []

    async def test_a_held_attempt_without_the_flag_is_still_listed(
        self, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, status="queued", total=None, held=True)

        (row,) = (await board.held_attempts())["attempts"]

        assert row["held"] is True
        assert row["flags"] == []

    async def test_the_newest_come_first(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        older = await add_attempt(
            db, asha, edition, held=True, flags=["injection_text"], submitted_at=STARTED
        )
        newer = await add_attempt(
            db,
            bina,
            edition,
            held=True,
            flags=["injection_text"],
            submitted_at=STARTED + timedelta(hours=3),
        )

        ids = [row["id"] for row in (await board.held_attempts())["attempts"]]

        assert ids == [str(newer), str(older)]

    async def test_nothing_held_is_an_empty_list(self, db: Db) -> None:
        assert await board.held_attempts() == {"attempts": []}

    async def test_a_hold_still_in_force_comes_before_a_newer_cleared_one(
        self, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        held = await add_attempt(
            db, asha, edition, held=True, flags=["injection_text"], submitted_at=STARTED
        )
        cleared = await add_attempt(
            db,
            bina,
            edition,
            held=False,
            flags=["injection_text", "hold_cleared"],
            submitted_at=STARTED + timedelta(hours=3),
        )

        ids = [row["id"] for row in (await board.held_attempts())["attempts"]]

        assert ids == [str(held), str(cleared)]

    async def test_the_cap_keeps_the_newest_held_rows(
        self, db: Db, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(board, "HELD_LIMIT", 2)
        ids: list[str] = []
        for hours in range(3):
            user = await add_user(db, f"held{hours}")
            attempt = await add_attempt(
                db,
                user,
                edition,
                held=True,
                flags=["injection_text"],
                submitted_at=STARTED + timedelta(hours=hours),
            )
            ids.append(str(attempt))

        listed = [row["id"] for row in (await board.held_attempts())["attempts"]]

        assert listed == [ids[2], ids[1]]

    async def test_rows_the_filter_drops_do_not_use_up_the_cap(
        self, db: Db, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The database only matches the flag as text, so a lookalike flag on a row that was never
        # held comes back from it and is dropped afterwards. Newer ones must not crowd out a
        # real, cleared hold.
        monkeypatch.setattr(board, "HELD_LIMIT", 2)
        cleared = await add_attempt(
            db,
            await add_user(db, "cleared"),
            edition,
            held=False,
            flags=["injection_text", "hold_cleared"],
            submitted_at=STARTED,
        )
        for hours in (1, 2):
            await add_attempt(
                db,
                await add_user(db, f"lookalike{hours}"),
                edition,
                held=False,
                flags=["not_injection_text_at_all"],
                submitted_at=STARTED + timedelta(hours=hours),
            )

        listed = [row["id"] for row in (await board.held_attempts())["attempts"]]

        assert listed == [str(cleared)]

    async def test_the_query_reads_at_most_ten_times_the_cap(
        self, db: Db, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(board, "HELD_LIMIT", 3)
        statements: list[tuple[str, Any]] = []

        @event.listens_for(db.engine.sync_engine, "before_cursor_execute")
        def _record(_conn: Any, _cursor: Any, statement: str, parameters: Any, *_rest: Any) -> None:
            statements.append((statement, parameters))

        await board.held_attempts()

        (selected,) = [(sql, args) for sql, args in statements if "practice_attempts" in sql]
        assert "LIMIT" in selected[0]
        assert 30 in tuple(selected[1])

    async def test_a_lookalike_flood_cannot_make_the_query_unbounded(
        self, db: Db, edition: PracticeChallenge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ten rows the post-filter will drop are newer than a real, cleared hold: with a cap of 1
        # the scan stops at ten rows, so the older hold is not read. Nine of them leave room.
        monkeypatch.setattr(board, "HELD_LIMIT", 1)
        cleared = await add_attempt(
            db,
            await add_user(db, "cleared"),
            edition,
            held=False,
            flags=["injection_text", "hold_cleared"],
            submitted_at=STARTED,
        )
        for hours in range(1, 10):
            await add_attempt(
                db,
                await add_user(db, f"lookalike{hours}"),
                edition,
                held=False,
                flags=["not_injection_text_at_all"],
                submitted_at=STARTED + timedelta(hours=hours),
            )

        within = [row["id"] for row in (await board.held_attempts())["attempts"]]
        await add_attempt(
            db,
            await add_user(db, "lookalike10"),
            edition,
            held=False,
            flags=["not_injection_text_at_all"],
            submitted_at=STARTED + timedelta(hours=10),
        )
        beyond = [row["id"] for row in (await board.held_attempts())["attempts"]]

        assert within == [str(cleared)]
        assert beyond == []


# =========================================================================== the routes

API = "/api/v1/practice"


def _token(user_id: str, role: str = "user") -> str:
    from api.auth import create_access_token

    return create_access_token(data={"sub": user_id, "email": "x@example.com", "role": role})


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests that do not pass ``now`` see two days after the edition closed."""
    real = board._moment

    def fixed(now: datetime | None = None) -> datetime:
        return real(now if now is not None else AFTER)

    monkeypatch.setattr(board, "_moment", fixed)


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


@pytest_asyncio.fixture
async def admin(db: Db, clock: None) -> AsyncIterator[AsyncClient]:
    from api.main import app

    boss = await add_user(db, "boss")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {_token(boss, 'admin')}"},
    ) as signed_in:
        yield signed_in


class TestRoutes:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/challenges/some-slug/leaderboard"),
            ("GET", "/leaderboard"),
            ("GET", "/settings"),
            ("PUT", "/settings"),
            ("GET", "/admin/attempts/held"),
        ],
    )
    async def test_every_board_route_needs_a_signed_in_user(
        self, anonymous: AsyncClient, method: str, path: str
    ) -> None:
        response = await anonymous.request(method, API + path)

        assert response.status_code in (401, 403)

    async def test_the_challenge_board_has_the_page_shape(
        self, client: AsyncClient, db: Db, asha: str, bina: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=88.5, repo_owner="asha-dev", repo_name="relay")
        await add_attempt(db, bina, edition, total=70.0)

        response = await client.get(f"{API}/challenges/{edition.slug}/leaderboard")

        assert response.status_code == 200
        data = response.json()
        assert set(data) == BOARD_KEYS
        assert [set(row) for row in data["rows"]] == [ROW_KEYS, ROW_KEYS]
        assert set(data["me"]) == ROW_KEYS
        assert data["rows"][0]["you"] is True
        assert data["rows"][0]["repo_url"] is None
        assert data["rows"][1]["name"] == handle(bina)
        assert data["provisional"] is False
        assert "asha-dev" not in response.text

    async def test_an_unknown_edition_is_404(self, client: AsyncClient) -> None:
        response = await client.get(f"{API}/challenges/no-such-edition/leaderboard")

        assert response.status_code == 404

    async def test_the_overall_board_has_the_page_shape(
        self, client: AsyncClient, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        await add_attempt(db, asha, edition, total=88.5)

        response = await client.get(f"{API}/leaderboard")

        assert response.status_code == 200
        data = response.json()
        assert set(data) == OVERALL_KEYS
        assert set(data["rows"][0]) == OVERALL_ROW_KEYS
        assert set(data["me"]) == OVERALL_ME_KEYS
        assert data["window_challenges"] == 1

    async def test_the_overall_board_with_no_one_ranked_has_a_null_me(
        self, client: AsyncClient
    ) -> None:
        data = (await client.get(f"{API}/leaderboard")).json()

        assert data == {"window_challenges": 0, "rows": [], "me": None}

    async def test_settings_are_read_and_saved(self, client: AsyncClient, asha: str) -> None:
        first = await client.get(f"{API}/settings")
        saved = await client.put(
            f"{API}/settings",
            json={"display_name": " Asha K. ", "show_repo": True, "contest_reminder": False},
        )
        again = await client.get(f"{API}/settings")

        assert first.status_code == 200
        assert first.json() == {
            "display_name": None,
            "show_repo": False,
            "contest_reminder": True,
            "handle": handle(asha),
        }
        assert saved.status_code == 200
        assert saved.json() == {
            "display_name": "Asha K.",
            "show_repo": True,
            "contest_reminder": False,
            "handle": handle(asha),
        }
        assert again.json() == saved.json()

    async def test_a_bad_name_is_422_and_says_what_is_allowed(self, client: AsyncClient) -> None:
        response = await client.put(
            f"{API}/settings",
            json={"display_name": "me@x.com", "show_repo": False, "contest_reminder": True},
        )

        assert response.status_code == 422
        assert "letters" in response.json()["detail"]

    @pytest.mark.parametrize(
        "payload",
        [
            {"show_repo": False, "contest_reminder": True},
            {"display_name": None, "contest_reminder": True},
            {"display_name": None, "show_repo": "yes", "contest_reminder": True},
            {"display_name": 5, "show_repo": False, "contest_reminder": True},
            {"display_name": "x" * 500, "show_repo": False, "contest_reminder": True},
        ],
    )
    async def test_a_malformed_settings_body_is_422(
        self, client: AsyncClient, payload: dict[str, Any]
    ) -> None:
        response = await client.put(f"{API}/settings", json=payload)

        assert response.status_code == 422

    async def test_a_user_cannot_read_the_held_list(self, client: AsyncClient) -> None:
        assert (await client.get(f"{API}/admin/attempts/held")).status_code == 403

    async def test_an_admin_reads_the_held_list(
        self, admin: AsyncClient, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        attempt = await add_attempt(
            db, asha, edition, status="queued", total=None, held=True, flags=["injection_text"]
        )

        response = await admin.get(f"{API}/admin/attempts/held")

        assert response.status_code == 200
        (row,) = response.json()["attempts"]
        assert set(row) == HELD_KEYS
        assert row["id"] == str(attempt)

    async def test_the_held_route_does_not_swallow_the_admin_actions(
        self, admin: AsyncClient, db: Db, asha: str, edition: PracticeChallenge
    ) -> None:
        attempt = await add_attempt(
            db, asha, edition, status="queued", total=None, held=True, flags=["injection_text"]
        )

        response = await admin.post(f"{API}/admin/attempts/{attempt}/clear")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
