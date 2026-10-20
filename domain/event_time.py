"""
domain/event_time.py
~~~~~~~~~~~~~~~~~~~~
Time as Bengaluru reads it. Events are stored as absolute instants
(``timestamptz``); everything a person reads ("tonight", "this weekend",
"Thu 15 Oct · 18:30–20:00 IST") is computed here, in India Standard Time.

* **IST is a fixed offset**, +05:30. India has no daylight saving, so there is
  no ``zoneinfo`` lookup and no ``tzdata`` dependency on Windows dev machines
  or the slim image.
* **A day is an IST day.** An event at 00:30 IST on Friday is "tomorrow" at
  23:50 IST on Thursday and "today" at 00:10 IST on Friday, though both
  moments share its UTC date.
* **An unstated end is start + 2 h** (:func:`effective_end`). That is an
  assumption for "has it ended?", never printed as a fact: :func:`when_label`
  shows only the start when the source gave no end.

Every function takes aware datetimes. A naive one is read as UTC, the way the
database returns it, rather than as the server's local time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Literal

IST = timezone(timedelta(hours=5, minutes=30), "IST")
DEFAULT_DURATION = timedelta(hours=2)

Window = Literal["today", "week", "weekend", "month"]

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SATURDAY = 5


def as_utc(moment: datetime) -> datetime:
    """``moment`` as an aware UTC datetime; a naive one is taken to be UTC already."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def ist_date(moment: datetime) -> date:
    """The calendar date of ``moment`` in India."""
    return as_utc(moment).astimezone(IST).date()


def effective_end(starts_at: datetime, ends_at: datetime | None) -> datetime:
    """When the event is over: its stated end, else two hours after it starts.

    An end that is not after the start is a data error, so it is ignored the
    same way a missing one is.
    """
    if ends_at is None or as_utc(ends_at) <= as_utc(starts_at):
        return as_utc(starts_at) + DEFAULT_DURATION
    return as_utc(ends_at)


def _ist_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)


def window(name: Window, now: datetime) -> tuple[datetime, datetime]:
    """UTC-aware ``(start, end)`` bounds of a named listing window.

    * ``today``: IST midnight to the next IST midnight.
    * ``week``: now to seven days on.
    * ``weekend``: the coming Saturday 00:00 IST to Monday 00:00 IST, or from
      now when it is already Saturday or Sunday in India.
    * ``month``: now to thirty days on.
    """
    current = as_utc(now)
    today = current.astimezone(IST).date()
    if name == "today":
        return _ist_midnight(today), _ist_midnight(today + timedelta(days=1))
    if name == "week":
        return current, current + timedelta(days=7)
    if name == "month":
        return current, current + timedelta(days=30)
    if name == "weekend":
        weekday = today.weekday()
        if weekday >= _SATURDAY:
            return current, _ist_midnight(today + timedelta(days=7 - weekday))
        saturday = today + timedelta(days=_SATURDAY - weekday)
        return _ist_midnight(saturday), _ist_midnight(saturday + timedelta(days=2))
    raise ValueError(f"unknown window: {name!r}")


def is_today(starts_at: datetime, ends_at: datetime | None, now: datetime) -> bool:
    """The event starts on today's IST date and has not ended."""
    return ist_date(starts_at) == ist_date(now) and effective_end(starts_at, ends_at) > as_utc(now)


def _day_label(moment: datetime) -> str:
    return f"{_DAYS[moment.weekday()]} {moment.day} {_MONTHS[moment.month - 1]}"


def _clock(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def when_label(starts_at: datetime, ends_at: datetime | None) -> str:
    """``"Thu 15 Oct · 18:30–20:00 IST"``, or across days
    ``"Sat 24 Oct 09:30 – Sun 25 Oct 17:00 IST"``.

    Without a usable end only the start is shown: ``"Thu 15 Oct · 18:30 IST"``.
    """
    start = as_utc(starts_at).astimezone(IST)
    if ends_at is None or as_utc(ends_at) <= as_utc(starts_at):
        return f"{_day_label(start)} · {_clock(start)} IST"
    end = as_utc(ends_at).astimezone(IST)
    if end.date() == start.date():
        return f"{_day_label(start)} · {_clock(start)}–{_clock(end)} IST"
    return f"{_day_label(start)} {_clock(start)} – {_day_label(end)} {_clock(end)} IST"
