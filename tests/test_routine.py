"""
tests/test_routine.py
~~~~~~~~~~~~~~~~~~~~~
A user's daily routine: the blocks they set for the day and the ticks they make on it.

The rules are pure and run on plain values, so nothing here needs a database. The clock is
always passed in. The migration is read as text, the way ``test_interview_time.py`` reads 022.

The endpoints run through the real app on in-memory SQLite, with the service's clock pinned.
Every user and block is fictional.
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import create_access_token
from domain.routine import (
    DEFAULT_LABELS,
    KINDS,
    LABEL_MAX,
    MAX_BLOCKS,
    Block,
    RoutineInvalid,
    ist_day,
    ticks_today,
    validate_blocks,
    with_blocks,
    with_tick,
)
from services import routine as routine_service
from storage.database import get_db_dep
from storage.models import Profile, User
from tests.outreach_support import make_user, outreach_db  # noqa: F401

Factory = async_sessionmaker[AsyncSession]

VERSIONS = Path(__file__).resolve().parents[1] / "storage" / "migrations" / "versions"
TODAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)  # Tue 3 Nov, 11:30 IST


def raw(block_id: str, at: str, kind: str = "apply", label: str = "") -> dict[str, object]:
    return {"id": block_id, "at": at, "kind": kind, "label": label}


# --- the day ----------------------------------------------------------------------


def test_ist_day_turns_at_ist_midnight() -> None:
    assert ist_day(datetime(2026, 11, 3, 18, 29, tzinfo=UTC)) == date(2026, 11, 3)
    assert ist_day(datetime(2026, 11, 3, 18, 30, tzinfo=UTC)) == date(2026, 11, 4)


def test_ist_day_reads_a_naive_time_as_utc() -> None:
    # The database hands back naive datetimes; they are UTC, as everywhere else here.
    assert ist_day(datetime(2026, 11, 3, 18, 30)) == date(2026, 11, 4)


def test_the_vocabulary() -> None:
    assert KINDS == ("apply", "connect", "applications", "practice", "mock", "events", "custom")
    assert set(DEFAULT_LABELS) == set(KINDS) - {"custom"}
    assert DEFAULT_LABELS["practice"] == "Solve today's problem"
    assert (MAX_BLOCKS, LABEL_MAX) == (8, 60)


# --- validating the blocks --------------------------------------------------------


def test_validate_sorts_and_fills_default_labels() -> None:
    blocks = validate_blocks(
        [raw("late", "22:00", "mock", "Mock round"), raw("early", "09:00", "apply", " ")]
    )

    assert [b.at for b in blocks] == ["09:00", "22:00"]
    assert blocks[0] == Block(id="early", at="09:00", kind="apply", label="Find roles and apply")
    assert blocks[1].label == "Mock round"


def test_every_default_label_is_used_for_its_kind() -> None:
    kinds = [kind for kind in KINDS if kind != "custom"]
    blocks = validate_blocks([raw(f"b{i}", f"0{i}:00", kind) for i, kind in enumerate(kinds)])
    assert {b.kind: b.label for b in blocks} == DEFAULT_LABELS


def test_validate_strips_and_collapses_the_label() -> None:
    [block] = validate_blocks([raw("a", "09:00", "custom", "  Read   the\tdocs \n")])
    assert block.label == "Read the docs"


def test_a_label_of_exactly_the_limit_is_kept() -> None:
    [block] = validate_blocks([raw("a", "09:00", "custom", "x" * LABEL_MAX)])
    assert len(block.label) == LABEL_MAX


def test_no_blocks_is_a_routine() -> None:
    assert validate_blocks([]) == []


def test_eight_blocks_are_allowed() -> None:
    blocks = validate_blocks([raw(f"b{i}", f"0{i}:00", "custom", f"Step {i}") for i in range(8)])
    assert len(blocks) == 8


def test_as_dict_names_the_four_fields() -> None:
    block = Block(id="a", at="09:00", kind="apply", label="Find roles and apply")
    assert block.as_dict() == {
        "id": "a",
        "at": "09:00",
        "kind": "apply",
        "label": "Find roles and apply",
    }


@pytest.mark.parametrize(
    "blocks",
    [
        pytest.param([raw(f"b{i}", f"0{i}:00", "custom", "x") for i in range(9)], id="nine-blocks"),
        pytest.param([raw("a", "9:00")], id="hour-not-padded"),
        pytest.param([raw("a", "24:00")], id="hour-24"),
        pytest.param([raw("a", "09:60")], id="minute-60"),
        pytest.param([raw("a", "09:00\n")], id="trailing-newline"),
        pytest.param([raw("a", "09:00", "sleep")], id="unknown-kind"),
        pytest.param([raw("a", "09:00", "custom", "   ")], id="blank-custom"),
        pytest.param([raw("a", "09:00", "custom", "x" * (LABEL_MAX + 1))], id="label-61"),
        pytest.param([raw("a", "09:00"), raw("b", "09:00", "mock")], id="same-minute"),
        pytest.param([raw("a", "09:00"), raw("a", "10:00")], id="duplicate-id"),
        pytest.param([raw("a b", "09:00")], id="id-with-space"),
        pytest.param([raw("", "09:00")], id="empty-id"),
        pytest.param([raw("x" * 37, "09:00")], id="id-37-chars"),
        pytest.param(
            [{"id": "a", "at": "09:00", "kind": "apply", "label": 5}], id="label-not-text"
        ),
        pytest.param([{"id": 7, "at": "09:00", "kind": "apply", "label": ""}], id="id-not-text"),
        pytest.param([{"id": "a", "kind": "apply", "label": ""}], id="missing-time"),
    ],
)
def test_validate_refuses(blocks: list[dict[str, object]]) -> None:
    with pytest.raises(RoutineInvalid):
        validate_blocks(blocks)


def test_a_missing_label_is_a_blank_one() -> None:
    [block] = validate_blocks([{"id": "a", "at": "09:00", "kind": "events"}])
    assert block.label == DEFAULT_LABELS["events"]


def test_the_limit_counts_the_label_after_it_is_trimmed() -> None:
    # Spaces are collapsed first, so only what the user will see is counted.
    padded = "  " + "x" * LABEL_MAX + "  "
    [block] = validate_blocks([raw("a", "09:00", "custom", padded)])
    assert block.label == "x" * LABEL_MAX


@pytest.mark.parametrize(
    ("blocks", "sentence"),
    [
        (
            [raw("a", "09:00"), raw("b", "09:00", "mock")],
            "Two blocks are at 09:00. Move one of them.",
        ),
        ([raw("a", "09:00", "custom", "")], "Your own block needs a name."),
        (
            [raw(f"b{i}", f"0{i}:00", "custom", "x") for i in range(9)],
            "A routine holds at most 8 blocks.",
        ),
    ],
)
def test_the_refusal_is_a_sentence_the_user_can_act_on(
    blocks: list[dict[str, object]], sentence: str
) -> None:
    with pytest.raises(RoutineInvalid) as refused:
        validate_blocks(blocks)
    assert str(refused.value) == sentence


def test_a_refusal_is_a_value_error() -> None:
    assert issubclass(RoutineInvalid, ValueError)


# --- the ticks --------------------------------------------------------------------


def stored_with(ids: list[str], day: str = "2026-11-03") -> dict[str, Any]:
    return {
        "blocks": [raw("a", "09:00").copy(), raw("b", "10:00", "mock").copy()],
        "done": {"day": day, "ids": ids},
    }


def test_ticks_from_another_day_are_none() -> None:
    stored = {"done": {"day": "2026-11-02", "ids": ["a"]}}
    assert ticks_today(stored, date(2026, 11, 3)) == []


def test_ticks_of_today_are_listed() -> None:
    assert ticks_today(stored_with(["a", "b"]), TODAY) == ["a", "b"]


@pytest.mark.parametrize(
    "stored",
    [
        None,
        {},
        {"done": None},
        {"done": "2026-11-03"},
        {"done": {"day": "2026-11-03"}},
        {"done": {"day": "2026-11-03", "ids": "a"}},
        {"done": {"day": 20261103, "ids": ["a"]}},
    ],
)
def test_nothing_ticked_or_a_stored_shape_that_is_not_ours(stored: dict[str, Any] | None) -> None:
    assert ticks_today(stored, TODAY) == []


def test_only_text_ids_are_read_back() -> None:
    stored = {"done": {"day": "2026-11-03", "ids": ["a", 3, None, "a", "b"]}}
    assert ticks_today(stored, TODAY) == ["a", "b"]


def test_with_tick_sets_and_clears_without_mutating() -> None:
    original = stored_with([])
    snapshot = deepcopy(original)

    ticked = with_tick(original, "a", True, TODAY)

    assert ticks_today(ticked, TODAY) == ["a"]
    assert ticked["blocks"] == snapshot["blocks"]
    assert original == snapshot

    cleared = with_tick(ticked, "a", False, TODAY)

    assert ticks_today(cleared, TODAY) == []
    assert ticks_today(ticked, TODAY) == ["a"]  # the one it was made from is untouched


def test_ticking_twice_ticks_once_and_clearing_a_clear_block_is_quiet() -> None:
    once = with_tick(stored_with([]), "a", True, TODAY)
    twice = with_tick(once, "a", True, TODAY)
    assert ticks_today(twice, TODAY) == ["a"]
    assert ticks_today(with_tick(twice, "b", False, TODAY), TODAY) == ["a"]


def test_a_tick_on_a_new_day_starts_that_day_afresh() -> None:
    yesterday = stored_with(["a", "b"], day="2026-11-02")

    ticked = with_tick(yesterday, "b", True, TODAY)

    assert ticked["done"] == {"day": "2026-11-03", "ids": ["b"]}
    assert ticked["blocks"] == yesterday["blocks"]


def test_a_tick_on_nothing_stored_makes_a_routine_with_no_blocks() -> None:
    assert with_tick(None, "a", True, TODAY) == {
        "blocks": [],
        "done": {"day": "2026-11-03", "ids": ["a"]},
    }


def test_the_result_does_not_share_its_blocks_with_the_input() -> None:
    original = stored_with([])
    ticked = with_tick(original, "a", True, TODAY)
    blocks = ticked["blocks"]
    assert isinstance(blocks, list)
    blocks[0]["label"] = "changed"
    assert original["blocks"][0]["label"] == ""


# --- replacing the blocks ---------------------------------------------------------


def test_with_blocks_keeps_ticks_of_ids_still_present() -> None:
    stored = stored_with(["a", "b"])
    snapshot = deepcopy(stored)
    new = validate_blocks([raw("c", "08:00", "events"), raw("a", "21:15", "apply")])

    result = with_blocks(stored, new, TODAY)

    assert ticks_today(result, TODAY) == ["a"]
    assert result["blocks"] == [block.as_dict() for block in new]
    assert stored == snapshot


def test_with_blocks_on_nothing_stored_starts_with_no_ticks() -> None:
    new = validate_blocks([raw("a", "09:00")])

    assert with_blocks(None, new, TODAY) == {
        "blocks": [new[0].as_dict()],
        "done": {"day": "2026-11-03", "ids": []},
    }


def test_with_blocks_drops_ticks_from_another_day() -> None:
    new = validate_blocks([raw("a", "09:00")])
    result = with_blocks(stored_with(["a"], day="2026-11-02"), new, TODAY)
    assert ticks_today(result, TODAY) == []


def test_clearing_every_block_leaves_an_empty_routine() -> None:
    result = with_blocks(stored_with(["a"]), [], TODAY)
    assert result == {"blocks": [], "done": {"day": "2026-11-03", "ids": []}}


# --- the migration ----------------------------------------------------------------


def test_023_follows_022_and_backfills_tour_for_the_onboarded() -> None:
    path = VERSIONS / "023_profile_routine.py"
    spec = importlib.util.spec_from_file_location("migration_023", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "023_profile_routine"
    assert module.down_revision == "022_application_interview_at"
    text = path.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS routine JSONB" in text
    assert "ADD COLUMN IF NOT EXISTS tour_seen_at TIMESTAMPTZ" in text
    # The tour is for people who have not used the app yet; whoever is onboarded has.
    assert "SET tour_seen_at = now()" in text
    assert "onboarding_completed IS TRUE" in text
    assert "DROP COLUMN IF EXISTS routine" in text
    assert "DROP COLUMN IF EXISTS tour_seen_at" in text


# --- the endpoints ----------------------------------------------------------------

ROUTINE = "/api/v1/routine"
EMPTY = {
    "has_routine": False,
    "blocks": [],
    "done_today": [],
    "day": "2026-11-03",
    "tour_seen": False,
    "onboarding_completed": False,
}


@pytest.fixture
def db(outreach_db: Factory, monkeypatch: pytest.MonkeyPatch) -> Factory:  # noqa: F811
    monkeypatch.setattr(routine_service, "AsyncSessionLocal", outreach_db)
    monkeypatch.setattr(routine_service, "_now", lambda: NOW)
    return outreach_db


@pytest_asyncio.fixture
async def client(db: Factory) -> AsyncIterator[AsyncClient]:
    from api.main import app

    async def _session() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db_dep] = _session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db_dep, None)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id), "email": user.email, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


async def new_user(db: Factory) -> User:
    async with db() as session:
        return await make_user(session)


async def profile_of(db: Factory, user: User) -> Profile | None:
    async with db() as session:
        found = await session.execute(select(Profile).where(Profile.user_id == user.id))
        return found.scalar_one_or_none()


async def count_profiles(db: Factory) -> int:
    async with db() as session:
        return (await session.execute(select(func.count()).select_from(Profile))).scalar_one()


async def save(client: AsyncClient, user: User, *blocks: dict[str, object]) -> Any:
    return await client.put(ROUTINE, json={"blocks": list(blocks)}, headers=auth(user))


async def tick(client: AsyncClient, user: User, block_id: str, done: bool = True) -> Any:
    return await client.put(
        f"{ROUTINE}/blocks/{block_id}/done", json={"done": done}, headers=auth(user)
    )


async def test_get_with_no_profile_is_empty_and_creates_no_row(
    db: Factory, client: AsyncClient
) -> None:
    user = await new_user(db)

    response = await client.get(ROUTINE, headers=auth(user))

    assert response.status_code == 200
    assert response.json() == EMPTY
    assert await count_profiles(db) == 0


async def test_put_then_get_round_trips_sorted(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)

    saved = await save(
        client,
        user,
        raw("late", "22:00", "mock", "Mock round"),
        raw("early", "09:00", "apply", " "),
    )
    read = await client.get(ROUTINE, headers=auth(user))

    assert saved.status_code == 200
    expected = {
        **EMPTY,
        "has_routine": True,
        "blocks": [
            {"id": "early", "at": "09:00", "kind": "apply", "label": "Find roles and apply"},
            {"id": "late", "at": "22:00", "kind": "mock", "label": "Mock round"},
        ],
    }
    assert read.json() == expected
    assert saved.json() == expected  # a save answers what a read would


async def test_put_creates_the_profile_row_when_there_is_none(
    db: Factory, client: AsyncClient
) -> None:
    user = await new_user(db)

    await save(client, user, raw("a", "09:00"))

    profile = await profile_of(db, user)
    assert profile is not None
    assert profile.onboarding_completed is False


async def test_put_invalid_is_422_with_the_reason(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)

    response = await save(client, user, raw("a", "09:00"), raw("b", "09:00", "mock"))

    assert response.status_code == 422
    assert response.json() == {"detail": "Two blocks are at 09:00. Move one of them."}
    assert (await client.get(ROUTINE, headers=auth(user))).json() == EMPTY


async def test_a_block_missing_fields_gets_a_sentence_not_a_schema_dump(
    db: Factory, client: AsyncClient
) -> None:
    user = await new_user(db)

    no_kind = await save(client, user, {"id": "a", "at": "09:00"})
    no_id = await save(client, user, {"at": "09:00", "kind": "apply"})
    no_time = await save(client, user, {"id": "a", "kind": "apply"})

    assert no_kind.status_code == no_id.status_code == no_time.status_code == 422
    assert no_kind.json() == {"detail": "Pick what each block is for."}
    assert isinstance(no_id.json()["detail"], str)
    assert "time" in no_time.json()["detail"]


async def test_saved_empty_routine_has_routine_true(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)

    saved = await save(client, user)

    assert saved.status_code == 200
    assert saved.json() == {**EMPTY, "has_routine": True}
    assert (await client.get(ROUTINE, headers=auth(user))).json()["has_routine"] is True


async def test_saving_again_replaces_the_blocks(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    await save(client, user, raw("a", "09:00"), raw("b", "10:00", "mock"))

    saved = await save(client, user, raw("c", "08:00", "events"))

    assert [block["id"] for block in saved.json()["blocks"]] == ["c"]


async def test_tick_then_untick(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    await save(client, user, raw("a", "09:00"), raw("b", "10:00", "mock"))

    ticked = await tick(client, user, "a")

    assert ticked.status_code == 200
    assert ticked.json()["done_today"] == ["a"]
    assert (await client.get(ROUTINE, headers=auth(user))).json()["done_today"] == ["a"]

    again = await tick(client, user, "a")
    assert again.json()["done_today"] == ["a"]  # ticking a ticked block ticks it once

    cleared = await tick(client, user, "a", done=False)
    assert cleared.status_code == 200
    assert cleared.json()["done_today"] == []
    assert (await client.get(ROUTINE, headers=auth(user))).json()["done_today"] == []


@pytest.mark.parametrize("done", [True, False])
async def test_tick_unknown_block_is_404(db: Factory, client: AsyncClient, done: bool) -> None:
    user = await new_user(db)
    await save(client, user, raw("a", "09:00"))

    response = await tick(client, user, "nope", done=done)

    assert response.status_code == 404
    assert response.json() == {"detail": "That block is not in your routine."}


async def test_tick_with_no_routine_is_404_and_creates_no_row(
    db: Factory, client: AsyncClient
) -> None:
    user = await new_user(db)

    response = await tick(client, user, "a")

    assert response.status_code == 404
    assert await count_profiles(db) == 0


async def test_tick_needs_a_boolean(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    await save(client, user, raw("a", "09:00"))

    response = await client.put(f"{ROUTINE}/blocks/a/done", json={}, headers=auth(user))

    assert response.status_code == 422


async def test_ticks_reset_at_ist_midnight(
    db: Factory, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await new_user(db)
    await save(client, user, raw("a", "09:00"))
    await tick(client, user, "a")

    # 18:30 UTC is 00:00 IST on the 4th. 18:29 is still the 3rd.
    monkeypatch.setattr(routine_service, "_now", lambda: datetime(2026, 11, 3, 18, 29, tzinfo=UTC))
    before = (await client.get(ROUTINE, headers=auth(user))).json()
    assert (before["day"], before["done_today"]) == ("2026-11-03", ["a"])

    monkeypatch.setattr(routine_service, "_now", lambda: datetime(2026, 11, 3, 18, 31, tzinfo=UTC))
    after = (await client.get(ROUTINE, headers=auth(user))).json()
    assert (after["day"], after["done_today"]) == ("2026-11-04", [])

    again = await tick(client, user, "a")  # the new day starts afresh
    assert again.json()["done_today"] == ["a"]


async def test_moving_a_block_keeps_its_tick_and_removing_drops_it(
    db: Factory, client: AsyncClient
) -> None:
    user = await new_user(db)
    await save(client, user, raw("a", "09:00"), raw("b", "10:00", "mock"))
    await tick(client, user, "a")
    await tick(client, user, "b")

    moved = await save(client, user, raw("a", "21:15"))  # a moved, b removed

    assert [(b["id"], b["at"]) for b in moved.json()["blocks"]] == [("a", "21:15")]
    assert moved.json()["done_today"] == ["a"]

    back = await save(client, user, raw("a", "21:15"), raw("b", "10:00", "mock"))
    assert back.json()["done_today"] == ["a"]  # b came back unticked


async def test_another_users_routine_is_never_read(db: Factory, client: AsyncClient) -> None:
    owner = await new_user(db)
    other = await new_user(db)
    await save(client, owner, raw("a", "09:00"))

    read = await client.get(ROUTINE, headers=auth(other))
    ticked = await tick(client, other, "a")

    assert read.json() == EMPTY
    assert ticked.status_code == 404
    assert (await client.get(ROUTINE, headers=auth(owner))).json()["done_today"] == []
    assert await profile_of(db, other) is None


async def test_get_reports_onboarding_and_the_tour(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    async with db() as session:
        session.add(Profile(user_id=user.id, onboarding_completed=True, tour_seen_at=NOW))
        await session.commit()

    body = (await client.get(ROUTINE, headers=auth(user))).json()

    assert (body["onboarding_completed"], body["tour_seen"], body["has_routine"]) == (
        True,
        True,
        False,
    )


async def test_saving_leaves_the_rest_of_the_profile_alone(
    db: Factory, client: AsyncClient
) -> None:
    user = await new_user(db)
    async with db() as session:
        session.add(Profile(user_id=user.id, full_name="Asha Rao", onboarding_completed=True))
        await session.commit()

    saved = await save(client, user, raw("a", "09:00"))

    assert saved.json()["onboarding_completed"] is True
    profile = await profile_of(db, user)
    assert profile is not None and profile.full_name == "Asha Rao"
    assert await count_profiles(db) == 1


async def test_tour_seen_is_idempotent(
    db: Factory, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await new_user(db)

    first = await client.post(f"{ROUTINE}/tour-seen", headers=auth(user))

    assert first.status_code == 200
    assert first.json() == {**EMPTY, "tour_seen": True}
    stamped = await profile_of(db, user)
    assert stamped is not None and stamped.tour_seen_at is not None
    assert stamped.tour_seen_at.replace(tzinfo=UTC) == NOW

    monkeypatch.setattr(routine_service, "_now", lambda: NOW + timedelta(hours=3))
    second = await client.post(f"{ROUTINE}/tour-seen", headers=auth(user))

    assert second.json()["tour_seen"] is True
    unchanged = await profile_of(db, user)
    assert unchanged is not None and unchanged.tour_seen_at is not None
    assert unchanged.tour_seen_at.replace(tzinfo=UTC) == NOW
    assert await count_profiles(db) == 1


async def test_the_tour_stamp_survives_saving_the_routine(db: Factory, client: AsyncClient) -> None:
    user = await new_user(db)
    await client.post(f"{ROUTINE}/tour-seen", headers=auth(user))

    saved = await save(client, user, raw("a", "09:00"))

    assert saved.json()["tour_seen"] is True
    assert saved.json()["has_routine"] is True


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", ROUTINE, None),
        ("PUT", ROUTINE, {"blocks": []}),
        ("PUT", f"{ROUTINE}/blocks/a/done", {"done": True}),
        ("POST", f"{ROUTINE}/tour-seen", None),
    ],
)
async def test_unauthenticated_is_401(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = await client.request(method, path, json=body)
    assert response.status_code == 401
