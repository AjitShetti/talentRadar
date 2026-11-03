"""
tests/test_routine.py
~~~~~~~~~~~~~~~~~~~~~
A user's daily routine: the blocks they set for the day and the ticks they make on it.

The rules are pure and run on plain values, so nothing here needs a database. The clock is
always passed in. The migration is read as text, the way ``test_interview_time.py`` reads 022.
"""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

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

VERSIONS = Path(__file__).resolve().parents[1] / "storage" / "migrations" / "versions"
TODAY = date(2026, 11, 3)


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
    ticked["blocks"][0]["label"] = "changed"
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
