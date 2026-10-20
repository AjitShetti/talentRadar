"""Practice: the IST day, the weekly contest window, the challenge week and take-home deadlines.

Everything here is pure: every instant is passed in and every instant is timezone-aware.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from domain.practice_schedule import (
    CONTEST_URL,
    GRACE,
    IST,
    LEETCODE_PROBLEMSET_URL,
    contest_window,
    deadline_for,
    edition_window,
    ist_day,
    last_start,
    submission_open,
    week_opening,
)

# 2026-10-17 is a Saturday and 2026-10-18 a Sunday; IST is UTC+05:30.
SAT_BEFORE_SHOW = datetime(2026, 10, 17, 14, 29, tzinfo=UTC)  # Sat 19:59 IST
SAT_SHOW = datetime(2026, 10, 17, 14, 31, tzinfo=UTC)  # Sat 20:01 IST
SUN_LIVE = datetime(2026, 10, 18, 2, 45, tzinfo=UTC)  # Sun 08:15 IST
SUN_AFTER = datetime(2026, 10, 18, 4, 1, tzinfo=UTC)  # Sun 09:31 IST
NAIVE = datetime(2026, 10, 16, 12, 0)  # no tzinfo


def test_ist_is_a_fixed_offset_of_five_thirty() -> None:
    offset = datetime(2026, 10, 15, tzinfo=UTC).astimezone(IST).utcoffset()
    assert offset == timedelta(hours=5, minutes=30)


def test_ist_day_rolls_at_ist_midnight_not_utc() -> None:
    assert ist_day(datetime(2026, 10, 15, 18, 29, tzinfo=UTC)) == date(2026, 10, 15)
    assert ist_day(datetime(2026, 10, 15, 18, 31, tzinfo=UTC)) == date(2026, 10, 16)


def test_contest_card_is_hidden_until_saturday_2000_ist() -> None:
    assert contest_window(SAT_BEFORE_SHOW).show is False
    assert contest_window(SAT_SHOW).show is True


def test_contest_card_is_shown_from_exactly_saturday_2000_ist() -> None:
    assert contest_window(datetime(2026, 10, 17, 14, 30, tzinfo=UTC)).show is True


def test_contest_is_shown_and_live_during_the_contest() -> None:
    window = contest_window(SUN_LIVE)
    assert window.show is True
    assert window.live is True
    assert window.starts_at == datetime(2026, 10, 18, 2, 30, tzinfo=UTC)
    assert window.ends_at == datetime(2026, 10, 18, 4, 0, tzinfo=UTC)


def test_contest_is_live_from_start_until_end_but_not_at_end() -> None:
    assert contest_window(datetime(2026, 10, 18, 2, 30, tzinfo=UTC)).live is True
    assert contest_window(datetime(2026, 10, 18, 4, 0, tzinfo=UTC)).live is False


def test_contest_card_is_hidden_after_0930_ist_and_points_at_the_next_contest() -> None:
    window = contest_window(SUN_AFTER)
    assert window.show is False
    assert window.live is False
    assert window.starts_at == datetime(2026, 10, 25, 2, 30, tzinfo=UTC)
    assert window.ends_at == datetime(2026, 10, 25, 4, 0, tzinfo=UTC)


def test_contest_window_on_a_weekday_points_at_the_coming_sunday() -> None:
    thursday = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    window = contest_window(thursday)
    assert window.starts_at == datetime(2026, 10, 18, 2, 30, tzinfo=UTC)
    assert window.show is False


def test_week_opening_is_the_latest_friday_1800_ist() -> None:
    assert week_opening(datetime(2026, 10, 15, 10, 0, tzinfo=UTC)) == datetime(
        2026, 10, 9, 12, 30, tzinfo=UTC
    )
    assert week_opening(datetime(2026, 10, 16, 12, 30, tzinfo=UTC)) == datetime(
        2026, 10, 16, 12, 30, tzinfo=UTC
    )


def test_week_opening_is_still_last_friday_one_minute_before_the_opening() -> None:
    assert week_opening(datetime(2026, 10, 16, 12, 29, tzinfo=UTC)) == datetime(
        2026, 10, 9, 12, 30, tzinfo=UTC
    )


def test_week_opening_is_returned_in_utc() -> None:
    opened = week_opening(datetime(2026, 10, 15, 10, 0, tzinfo=UTC))
    assert opened.utcoffset() == timedelta(0)


def test_edition_runs_one_week_from_its_opening() -> None:
    opens = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)
    assert edition_window(opens) == (opens, opens + timedelta(days=7))


def test_last_start_leaves_the_whole_take_home_before_close() -> None:
    closes = datetime(2026, 10, 23, 12, 30, tzinfo=UTC)
    assert last_start(closes, 48) == closes - timedelta(hours=48)


def test_deadline_is_start_plus_the_take_home_hours() -> None:
    started = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    assert deadline_for(started, 24) == started + timedelta(hours=24)


def test_write_up_grace_is_one_hour() -> None:
    assert timedelta(minutes=60) == GRACE


def test_submission_is_open_through_the_grace_hour_and_closes_after_it() -> None:
    deadline = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    assert submission_open(deadline + timedelta(minutes=59), deadline) is True
    assert submission_open(deadline + timedelta(minutes=61), deadline) is False
    assert submission_open(deadline + GRACE, deadline) is False


def test_leetcode_links_are_fixed() -> None:
    assert CONTEST_URL == "https://leetcode.com/contest/"
    assert LEETCODE_PROBLEMSET_URL == "https://leetcode.com/problemset/"


_AWARE = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "call",
    [
        lambda: ist_day(NAIVE),
        lambda: contest_window(NAIVE),
        lambda: week_opening(NAIVE),
        lambda: edition_window(NAIVE),
        lambda: last_start(NAIVE, 24),
        lambda: deadline_for(NAIVE, 24),
        lambda: submission_open(NAIVE, _AWARE),
        lambda: submission_open(_AWARE, NAIVE),
    ],
    ids=[
        "ist_day",
        "contest_window",
        "week_opening",
        "edition_window",
        "last_start",
        "deadline_for",
        "submission_open-now",
        "submission_open-deadline",
    ],
)
def test_every_function_rejects_a_naive_datetime(call: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        call()
