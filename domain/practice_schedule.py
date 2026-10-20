"""
domain/practice_schedule.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
When things happen in Practice, on India time.

Pure rules over instants. There is no clock and no I/O: every function takes the
instant it judges, and every instant must be timezone-aware (a naive datetime is
a ``ValueError``, never silently read as UTC or as IST).

India has no daylight saving, so IST is a fixed +05:30 offset. That is exact,
and it needs no tz database at runtime.

Intervals are half-open, ``[start, end)``, except where a docstring says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo

IST: tzinfo = timezone(timedelta(hours=5, minutes=30))

#: The write-up grace hour after a take-home's deadline. It is for writing up, not building.
GRACE = timedelta(minutes=60)

CONTEST_URL = "https://leetcode.com/contest/"
LEETCODE_PROBLEMSET_URL = "https://leetcode.com/problemset/"

#: The weekly LeetCode contest: Sunday 02:30 UTC (08:00 IST), 90 minutes long.
CONTEST_WEEKDAY = 6  # Monday is 0, so 6 is Sunday
CONTEST_START_UTC = time(2, 30)
CONTEST_MINUTES = 90
CONTEST_LENGTH = timedelta(minutes=CONTEST_MINUTES)
#: The contest card is shown from Saturday 20:00 IST, twelve hours before the start.
CONTEST_SHOW_LEAD = timedelta(hours=12)

#: A challenge edition opens on Friday at 18:00 IST and runs for one week.
CHALLENGE_WEEKDAY = 4  # Friday
CHALLENGE_OPENS_IST = time(18, 0)
EDITION_LENGTH = timedelta(days=7)


@dataclass(frozen=True)
class ContestWindow:
    """The weekly contest the card is about: when it runs, and whether its card is showing."""

    starts_at: datetime
    ends_at: datetime
    show: bool
    live: bool


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"naive datetime rejected, timezone-aware datetime required: {moment!r}")


def ist_day(now: datetime) -> date:
    """The IST calendar day that ``now`` falls on. The day rolls at IST midnight."""
    _require_aware(now)
    return now.astimezone(IST).date()


def _latest_contest_start(instant: datetime) -> datetime:
    """The most recent contest start (Sunday 02:30 UTC) at or before ``instant``, in UTC."""
    days_back = (instant.weekday() - CONTEST_WEEKDAY) % 7
    start = datetime.combine(
        instant.date() - timedelta(days=days_back), CONTEST_START_UTC, tzinfo=UTC
    )
    if start > instant:
        start -= timedelta(weeks=1)
    return start


def contest_window(now: datetime) -> ContestWindow:
    """The contest that is running at ``now``, or the next one if none is running.

    ``show`` is true from twelve hours before the start (Saturday 20:00 IST) until
    the contest ends (Sunday 09:30 IST). ``live`` is true only while it runs.
    """
    _require_aware(now)
    instant = now.astimezone(UTC)
    starts_at = _latest_contest_start(instant)
    if instant >= starts_at + CONTEST_LENGTH:
        starts_at += timedelta(weeks=1)
    ends_at = starts_at + CONTEST_LENGTH
    show = starts_at - CONTEST_SHOW_LEAD <= instant < ends_at
    live = starts_at <= instant < ends_at
    return ContestWindow(starts_at=starts_at, ends_at=ends_at, show=show, live=live)


def week_opening(now: datetime) -> datetime:
    """The latest Friday 18:00 IST at or before ``now``, as an aware UTC datetime."""
    _require_aware(now)
    local = now.astimezone(IST)
    days_back = (local.weekday() - CHALLENGE_WEEKDAY) % 7
    opened = datetime.combine(
        local.date() - timedelta(days=days_back), CHALLENGE_OPENS_IST, tzinfo=IST
    )
    if opened > local:
        opened -= timedelta(weeks=1)
    return opened.astimezone(UTC)


def edition_window(opens_at: datetime) -> tuple[datetime, datetime]:
    """One challenge edition: from its opening instant to one week later."""
    _require_aware(opens_at)
    return opens_at, opens_at + EDITION_LENGTH


def last_start(closes_at: datetime, hours: int) -> datetime:
    """The latest moment a take-home of ``hours`` can start and still finish before close."""
    _require_aware(closes_at)
    return closes_at - timedelta(hours=hours)


def deadline_for(started_at: datetime, hours: int) -> datetime:
    """When a take-home started at ``started_at`` is due: ``hours`` later."""
    _require_aware(started_at)
    return started_at + timedelta(hours=hours)


def submission_open(now: datetime, deadline_at: datetime) -> bool:
    """Whether a write-up can still be saved: until ``deadline_at`` plus the grace hour."""
    _require_aware(now)
    _require_aware(deadline_at)
    return now < deadline_at + GRACE
