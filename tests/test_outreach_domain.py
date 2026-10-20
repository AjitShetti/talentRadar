"""
tests/test_outreach_domain.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The follow-up clock is a pure function of one person's touches.

Every transition, every boundary and every day edge is pinned here because the
clock is what tells a job seeker who to write to today - and what must never
nag them about someone who has already answered.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pytest

from domain.outreach import (
    CHANNELS,
    DIRECTIONS,
    OUTCOMES,
    SOURCES,
    DueItem,
    PersonClock,
    PersonState,
    apply_touch,
    can_draft,
    clean_text,
    effective_state,
    is_due,
    ist_day_bounds,
    ist_today,
    pacing_note,
    snooze,
    sort_due,
    thread_stage,
    validate_happened_at,
)

T = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def days(n: int) -> timedelta:
    return timedelta(days=n)


def waiting_clock() -> PersonClock:
    """A person written to once at T: waiting, first follow-up due at T+5d."""
    return apply_touch(PersonClock.new(), direction="out", channel="linkedin", at=T).clock


def exhausted_clock() -> PersonClock:
    """Written to at T, T+5d and T+12d: both follow-ups used, clock ended."""
    clock = waiting_clock()
    clock = apply_touch(clock, direction="out", channel="linkedin", at=T + days(5)).clock
    return apply_touch(clock, direction="out", channel="linkedin", at=T + days(12)).clock


def due_item(
    kind: Literal["follow_up", "next_step"], due_at: datetime, *, thread_id: str = "t1"
) -> DueItem:
    return DueItem(
        thread_id=thread_id,
        person_id="p1" if kind == "follow_up" else None,
        kind=kind,
        due_at=due_at,
        company_name="Tessel AI",
        person_name="Asha Verma" if kind == "follow_up" else None,
        follow_up_number=1 if kind == "follow_up" else None,
        last_outbound_at=T if kind == "follow_up" else None,
        next_step="Send the repo link" if kind == "next_step" else None,
    )


def test_vocabularies() -> None:
    assert CHANNELS == ("linkedin", "email", "x", "github", "call", "event", "other")
    assert DIRECTIONS == ("out", "in")
    assert OUTCOMES == ("referred", "interviewing", "offer", "not_now", "not_a_fit", "other")
    assert SOURCES == ("suggestion", "drawer", "list", "typed", "overview")


def test_new_clock_is_planned_and_empty() -> None:
    clock = PersonClock.new()
    assert clock.state == "planned"
    assert clock.first_sent_at is None
    assert clock.last_outbound_at is None
    assert clock.follow_ups_sent == 0
    assert clock.next_follow_up_at is None
    assert clock.replied_at is None


def test_first_message_starts_the_clock() -> None:
    effect = apply_touch(PersonClock.new(), direction="out", channel="linkedin", at=T)
    assert effect.kind == "first"
    assert effect.clock.state == "waiting"
    assert effect.clock.first_sent_at == effect.clock.last_outbound_at == T
    assert effect.clock.next_follow_up_at == T + days(5)
    assert effect.clock.follow_ups_sent == 0


def test_first_follow_up_waits_seven_days() -> None:
    effect = apply_touch(waiting_clock(), direction="out", channel="email", at=T + days(5))
    assert effect.kind == "follow_up"
    assert effect.clock.state == "waiting"
    assert effect.clock.follow_ups_sent == 1
    assert effect.clock.last_outbound_at == T + days(5)
    assert effect.clock.first_sent_at == T
    assert effect.clock.next_follow_up_at == T + days(12)


def test_second_follow_up_ends_the_clock() -> None:
    clock = apply_touch(waiting_clock(), direction="out", channel="email", at=T + days(5)).clock
    effect = apply_touch(clock, direction="out", channel="email", at=T + days(12))
    assert effect.kind == "follow_up"
    assert effect.clock.follow_ups_sent == 2
    assert effect.clock.next_follow_up_at is None
    assert effect.clock.state == "waiting"


def test_cold_is_derived_ten_days_after_the_last_follow_up() -> None:
    clock = exhausted_clock()
    assert effective_state(clock, T + days(21)) == "waiting"
    assert effective_state(clock, T + days(22)) == "cold"
    assert clock.state == "waiting"


def test_cold_needs_both_follow_ups() -> None:
    # One follow-up and a long silence is still waiting: the second nudge is owed.
    clock = apply_touch(waiting_clock(), direction="out", channel="email", at=T + days(5)).clock
    assert effective_state(clock, T + days(200)) == "waiting"


def test_effective_state_passes_stored_states_through() -> None:
    assert effective_state(PersonClock.new(), T) == "planned"
    replied = apply_touch(waiting_clock(), direction="in", channel="linkedin", at=T + days(1)).clock
    assert effective_state(replied, T + days(400)) == "replied"


def test_third_out_is_logged_without_moving_the_clock() -> None:
    clock = exhausted_clock()
    effect = apply_touch(clock, direction="out", channel="email", at=T + days(13))
    assert effect.kind == "other"
    assert effect.clock == clock


def test_recontact_after_ninety_days_is_a_first_message() -> None:
    cold = exhausted_clock()
    assert cold.last_outbound_at == T + days(12)
    at = T + days(12) + days(90)
    effect = apply_touch(cold, direction="out", channel="linkedin", at=at)
    assert effect.kind == "first"
    assert effect.clock.state == "waiting"
    assert effect.clock.follow_ups_sent == 0
    assert effect.clock.first_sent_at == at
    assert effect.clock.last_outbound_at == at
    assert effect.clock.next_follow_up_at == at + days(5)
    assert can_draft(cold, T + days(12) + days(89)) is None
    assert can_draft(cold, at) == "first"


def test_out_inside_ninety_days_of_a_cold_person_is_other() -> None:
    cold = exhausted_clock()
    effect = apply_touch(cold, direction="out", channel="email", at=T + days(12) + days(89))
    assert effect.kind == "other"
    assert effect.clock == cold


def test_reply_stops_the_clock() -> None:
    effect = apply_touch(waiting_clock(), direction="in", channel="linkedin", at=T + days(3))
    assert effect.kind == "reply"
    assert effect.clock.state == "replied"
    assert effect.clock.replied_at == T + days(3)
    assert effect.clock.next_follow_up_at is None
    assert effect.clock.first_sent_at == T
    assert effect.clock.follow_ups_sent == 0


def test_reply_from_a_planned_person_is_still_a_reply() -> None:
    effect = apply_touch(PersonClock.new(), direction="in", channel="email", at=T)
    assert effect.kind == "reply"
    assert effect.clock.state == "replied"
    assert effect.clock.first_sent_at is None


def test_reply_revives_a_cold_person() -> None:
    effect = apply_touch(exhausted_clock(), direction="in", channel="email", at=T + days(40))
    assert effect.kind == "reply"
    assert effect.clock.state == "replied"


@pytest.mark.parametrize("channel", ["call", "event"])
@pytest.mark.parametrize("direction", ["out", "in"])
def test_call_or_event_counts_as_reply_either_direction(channel: str, direction: str) -> None:
    effect = apply_touch(waiting_clock(), direction=direction, channel=channel, at=T + days(2))
    assert effect.kind == "reply"
    assert effect.clock.state == "replied"
    assert effect.clock.replied_at == T + days(2)
    assert effect.clock.next_follow_up_at is None


def test_out_after_reply_changes_nothing() -> None:
    replied = apply_touch(waiting_clock(), direction="in", channel="linkedin", at=T + days(3)).clock
    effect = apply_touch(replied, direction="out", channel="linkedin", at=T + days(4))
    assert effect.kind == "other"
    assert effect.clock == replied


def test_out_on_a_dropped_person_changes_nothing() -> None:
    dropped = PersonClock(
        state="dropped",
        first_sent_at=None,
        last_outbound_at=None,
        follow_ups_sent=0,
        next_follow_up_at=None,
        replied_at=None,
    )
    effect = apply_touch(dropped, direction="out", channel="linkedin", at=T)
    assert effect.kind == "other"
    assert effect.clock == dropped


def test_a_later_reply_keeps_the_first_replied_at() -> None:
    replied = apply_touch(waiting_clock(), direction="in", channel="linkedin", at=T + days(3)).clock
    again = apply_touch(replied, direction="in", channel="email", at=T + days(6))
    assert again.kind == "reply"
    assert again.clock.state == "replied"
    assert again.clock.replied_at == T + days(3)
    call = apply_touch(replied, direction="out", channel="call", at=T + days(7))
    assert call.kind == "reply"
    assert call.clock.replied_at == T + days(3)


def test_a_reply_logged_late_with_an_earlier_date_is_the_first_reply() -> None:
    replied = apply_touch(waiting_clock(), direction="in", channel="linkedin", at=T + days(6)).clock
    earlier = apply_touch(replied, direction="in", channel="email", at=T + days(3))
    assert earlier.clock.replied_at == T + days(3)


def test_out_dated_before_the_last_one_is_logged_without_moving_the_clock() -> None:
    clock = waiting_clock()
    effect = apply_touch(clock, direction="out", channel="email", at=T - days(1))
    assert effect.kind == "other"
    assert effect.clock == clock
    # The same instant is not "before": it is an ordinary follow-up.
    assert apply_touch(clock, direction="out", channel="email", at=T).kind == "follow_up"


def test_a_backdated_out_does_not_pull_a_follow_up_clock_back() -> None:
    clock = apply_touch(waiting_clock(), direction="out", channel="email", at=T + days(5)).clock
    effect = apply_touch(clock, direction="out", channel="email", at=T + days(2))
    assert effect.kind == "other"
    assert effect.clock == clock
    assert effect.clock.next_follow_up_at == T + days(12)


def test_a_backdated_reply_still_replies() -> None:
    clock = apply_touch(waiting_clock(), direction="out", channel="email", at=T + days(5)).clock
    effect = apply_touch(clock, direction="in", channel="email", at=T + days(2))
    assert effect.kind == "reply"
    assert effect.clock.state == "replied"
    assert effect.clock.replied_at == T + days(2)
    assert effect.clock.next_follow_up_at is None


def test_apply_touch_reads_a_naive_time_as_utc() -> None:
    effect = apply_touch(
        PersonClock.new(), direction="out", channel="linkedin", at=datetime(2026, 10, 10, 6, 0)
    )
    assert effect.clock.first_sent_at == T
    assert effect.clock.first_sent_at is not None
    assert effect.clock.first_sent_at.tzinfo is not None


def test_apply_touch_rejects_unknown_direction_and_channel() -> None:
    with pytest.raises(ValueError):
        apply_touch(PersonClock.new(), direction="sideways", channel="email", at=T)
    with pytest.raises(ValueError):
        apply_touch(PersonClock.new(), direction="out", channel="carrier_pigeon", at=T)


def test_apply_touch_does_not_mutate_its_input() -> None:
    before = waiting_clock()
    apply_touch(before, direction="out", channel="email", at=T + days(5))
    assert before == waiting_clock()


def test_snooze_bounds() -> None:
    clock = waiting_clock()
    with pytest.raises(ValueError):
        snooze(clock, days=0, now=T)
    with pytest.raises(ValueError):
        snooze(clock, days=15, now=T)
    with pytest.raises(ValueError):
        snooze(PersonClock.new(), days=3, now=T)
    snoozed = snooze(clock, days=3, now=T + days(5))
    assert snoozed.next_follow_up_at == T + days(8)
    assert snoozed.state == "waiting"
    assert snoozed.follow_ups_sent == clock.follow_ups_sent
    assert snooze(clock, days=14, now=T).next_follow_up_at == T + days(14)
    assert snooze(clock, days=1, now=T).next_follow_up_at == T + days(1)


def test_snooze_needs_a_follow_up_to_postpone() -> None:
    # Both nudges are spent: there is no follow-up to move, and moving one in
    # would make the person due for something can_draft refuses to offer.
    with pytest.raises(ValueError):
        snooze(exhausted_clock(), days=3, now=T + days(13))
    replied = apply_touch(waiting_clock(), direction="in", channel="email", at=T + days(1)).clock
    with pytest.raises(ValueError):
        snooze(replied, days=3, now=T + days(2))


def test_a_follow_up_cannot_be_drafted_before_it_is_due() -> None:
    # Written once at T (11:30 IST, 10 Oct), so the nudge is dated 15 Oct. Drafting it
    # the next morning would spend follow-up 1 four days early and restart the clock.
    clock = waiting_clock()
    assert can_draft(clock, T + days(1)) is None
    assert can_draft(clock, T + days(4)) is None
    # Due is a day in India: from 00:00 IST on the 15th, which is 18:30 UTC on the 14th.
    assert can_draft(clock, datetime(2026, 10, 14, 18, 29, tzinfo=UTC)) is None
    assert can_draft(clock, datetime(2026, 10, 14, 18, 30, tzinfo=UTC)) == "follow_up"
    assert can_draft(clock, T + days(5)) == "follow_up"
    assert can_draft(clock, T + days(9)) == "follow_up"  # overdue is still offered

    # The second nudge waits for its own date, seven days after the first.
    one_up = apply_touch(clock, direction="out", channel="email", at=T + days(5)).clock
    assert can_draft(one_up, T + days(6)) is None
    assert can_draft(one_up, T + days(12)) == "follow_up"

    # A snooze pushes the date out, and the offer with it.
    snoozed = snooze(clock, days=3, now=T + days(5))
    assert can_draft(snoozed, T + days(6)) is None
    assert can_draft(snoozed, T + days(8)) == "follow_up"


def test_can_draft_by_state() -> None:
    assert can_draft(PersonClock.new(), T) == "first"
    assert can_draft(waiting_clock(), T + days(5)) == "follow_up"
    one_up = apply_touch(waiting_clock(), direction="out", channel="email", at=T + days(5)).clock
    assert can_draft(one_up, T + days(12)) == "follow_up"
    assert can_draft(exhausted_clock(), T + days(13)) is None
    replied = apply_touch(waiting_clock(), direction="in", channel="email", at=T + days(1)).clock
    assert can_draft(replied, T + days(2)) is None
    dropped = PersonClock(
        state="dropped",
        first_sent_at=None,
        last_outbound_at=None,
        follow_ups_sent=0,
        next_follow_up_at=None,
        replied_at=None,
    )
    assert can_draft(dropped, T) is None


def test_is_due() -> None:
    clock = waiting_clock()
    assert is_due(clock, T + days(5) - timedelta(seconds=1)) is False
    assert is_due(clock, T + days(5)) is True
    assert is_due(clock, T + days(9)) is True
    assert is_due(PersonClock.new(), T + days(99)) is False
    assert is_due(exhausted_clock(), T + days(99)) is False
    replied = apply_touch(clock, direction="in", channel="email", at=T + days(1)).clock
    assert is_due(replied, T + days(99)) is False


def test_thread_stage_precedence() -> None:
    assert thread_stage(["waiting"], closed=True) == "closed"
    assert thread_stage(["cold", "replied"], closed=False) == "replied"
    assert thread_stage(["planned", "waiting"], closed=False) == "waiting"
    assert thread_stage(["cold", "planned"], closed=False) == "cold"
    assert thread_stage([], closed=False) == "planned"
    assert thread_stage(["dropped"], closed=False) == "planned"
    assert thread_stage(["cold", "waiting"], closed=False) == "waiting"
    streamed: list[PersonState] = ["planned", "replied"]
    assert thread_stage(iter(streamed), closed=False) == "replied"


def test_due_items_put_next_steps_first() -> None:
    items = [
        due_item("follow_up", T, thread_id="a"),
        due_item("follow_up", T - days(1), thread_id="b"),
        due_item("next_step", T, thread_id="c"),
    ]
    ordered = sort_due(items)
    assert [i.kind for i in ordered] == ["next_step", "follow_up", "follow_up"]
    assert [i.thread_id for i in ordered] == ["c", "b", "a"]
    assert [i.thread_id for i in items] == ["a", "b", "c"]


def test_due_items_serialise_with_utc_iso_times() -> None:
    payload = due_item("follow_up", T).to_dict()
    assert payload == {
        "thread_id": "t1",
        "person_id": "p1",
        "kind": "follow_up",
        "due_at": "2026-10-10T06:00:00+00:00",
        "company_name": "Tessel AI",
        "person_name": "Asha Verma",
        "follow_up_number": 1,
        "last_outbound_at": "2026-10-10T06:00:00+00:00",
        "next_step": None,
    }
    step = due_item("next_step", T).to_dict()
    assert step["kind"] == "next_step"
    assert step["person_id"] is None
    assert step["last_outbound_at"] is None
    assert step["next_step"] == "Send the repo link"


def test_ist_day_boundary() -> None:
    assert ist_today(datetime(2026, 10, 14, 19, 0, tzinfo=UTC)) == date(2026, 10, 15)
    assert ist_today(datetime(2026, 10, 14, 18, 29, tzinfo=UTC)) == date(2026, 10, 14)
    assert ist_day_bounds(date(2026, 10, 15)) == (
        datetime(2026, 10, 14, 18, 30, tzinfo=UTC),
        datetime(2026, 10, 15, 18, 30, tzinfo=UTC),
    )


def test_ist_day_bounds_are_utc_aware_and_half_open() -> None:
    start, end = ist_day_bounds(date(2026, 10, 15))
    assert start.utcoffset() == timedelta(0)
    assert end.utcoffset() == timedelta(0)
    # 23:50 IST on the 15th belongs to the 15th even though UTC says 18:20.
    late = datetime(2026, 10, 15, 18, 20, tzinfo=UTC)
    assert start <= late < end
    assert ist_today(late) == date(2026, 10, 15)
    # 00:00 IST on the 16th is the first instant of the next day.
    assert ist_today(end) == date(2026, 10, 16)


def test_clean_text_flattens_and_bounds() -> None:
    assert clean_text("a\x00b\r\n\n\n\nc", 500) == "ab\n\nc"
    assert clean_text("   ", 500) is None
    with pytest.raises(ValueError):
        clean_text("x" * 501, 500)


def test_clean_text_edges() -> None:
    assert clean_text(None, 500) is None
    assert clean_text("", 500) is None
    assert clean_text("\x00\x07 \n ", 500) is None
    assert clean_text("a \t  b", 500) == "a b"
    assert clean_text("  hi there  ", 500) == "hi there"
    assert clean_text("x" * 500, 500) == "x" * 500
    # The limit applies to the cleaned text, not the raw input.
    assert clean_text("x" * 500 + "   ", 500) == "x" * 500
    assert clean_text("one\ntwo", 500) == "one\ntwo"


def test_happened_at_rules() -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert validate_happened_at(None, now) == now
    assert validate_happened_at(now, now) == now
    with pytest.raises(ValueError):
        validate_happened_at(now + timedelta(minutes=1), now)
    with pytest.raises(ValueError):
        validate_happened_at(now - days(31), now)
    assert validate_happened_at(now - days(30), now) == now - days(30)


def test_happened_at_reads_naive_as_utc() -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    got = validate_happened_at(datetime(2026, 10, 14, 12, 0), now)
    assert got == datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    assert got.tzinfo is not None


def test_pacing_note_from_the_second_first_message() -> None:
    assert pacing_note("Tessel AI", 0) is None
    assert pacing_note("Tessel AI", 1) is None
    assert pacing_note("Tessel AI", 2) == (
        "You've written to 2 people at Tessel AI this week. Give them a few days before the next."
    )
    third = pacing_note("Tessel AI", 3)
    assert third is not None
    assert third.startswith("You've written to 3 people at Tessel AI this week.")
