"""
domain/outreach.py
~~~~~~~~~~~~~~~~~~
The follow-up clock, the thread stage and the bounded text behind Outreach.

The user writes to a person; this module says when it is time to write again,
and when to stop. The clock is per person, not per thread: a founder written
to on Monday and an engineer written to on Thursday are two clocks, and a
single thread clock would nag about the founder on the engineer's schedule.

Nothing here sends anything. Everything is pure: no I/O, no clock, no LLM.
``now`` and ``at`` are passed in, which is what lets one rule set back the
drawer, the Overview card and a test table without any of them disagreeing.

"Today" is Indian Standard Time, a fixed UTC+05:30 offset. India has no DST, and
the server runs UTC, so a user in Bengaluru would otherwise watch "today" roll
over at 05:30.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, get_args, overload

# --- the clock ----------------------------------------------------------------

# First follow-up 5 days after the first message, the second 7 days after the
# first follow-up. Indexed by how many follow-ups have already been sent.
FOLLOW_UP_AFTER_DAYS = (5, 7)
MAX_FOLLOW_UPS = 2

# A person who has been nudged twice cools this many days after the last nudge,
# and can be written to afresh once RECONTACT_AFTER_DAYS have passed.
COLD_AFTER_DAYS = 10
RECONTACT_AFTER_DAYS = 90

SNOOZE_MAX_DAYS = 14
BACKDATE_MAX_DAYS = 30

# First messages to one company in a week before the pacing note shows.
SAME_COMPANY_WEEK_SOFT_LIMIT = 2

# --- caps ---------------------------------------------------------------------

MAX_OPEN_THREADS = 100
MAX_PEOPLE_PER_THREAD = 12
MAX_TOUCHES_PER_THREAD = 60

NOTE_MAX = 500
NEXT_STEP_MAX = 140
BODY_MAX = 2000

DEFAULT_DAILY_TARGET = 3
MAX_DAILY_TARGET = 10

THREAD_RETENTION_DAYS = 365

# --- vocabularies -------------------------------------------------------------

Source = Literal["suggestion", "drawer", "list", "typed", "overview"]
Channel = Literal["linkedin", "email", "x", "github", "call", "event", "other"]
Outcome = Literal["referred", "interviewing", "offer", "not_now", "not_a_fit", "other"]

SOURCES: tuple[Source, ...] = get_args(Source)
CHANNELS: tuple[Channel, ...] = get_args(Channel)
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)
DIRECTIONS = ("out", "in")

# A call or an event is a conversation whichever side placed it.
_CONVERSATION_CHANNELS = ("call", "event")

StoredState = Literal["planned", "waiting", "replied", "dropped"]
# ``cold`` is never stored: it is derived from a waiting person at read time.
PersonState = StoredState | Literal["cold"]
ThreadStage = Literal["planned", "waiting", "replied", "cold", "closed"]
TouchKind = Literal["first", "follow_up", "reply", "other"]

# India has no DST, so a fixed offset is exact. Never zoneinfo: the container
# may not ship tzdata.
IST = timezone(timedelta(hours=5, minutes=30))

# Control characters other than newline (kept) and tab (collapsed later).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BLANK_RUNS = re.compile(r"\n{3,}")
_SPACE_RUNS = re.compile(r"[ \t]+")


# --- time ---------------------------------------------------------------------


@overload
def aware(value: datetime) -> datetime: ...


@overload
def aware(value: None) -> None: ...


def aware(value: datetime | None) -> datetime | None:
    """A UTC-aware datetime. A naive one is read as UTC, which is what storage returns."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def ist_today(now: datetime) -> date:
    """The calendar day it is in India at ``now``."""
    return aware(now).astimezone(IST).date()


def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC-aware ``[start, end)`` of an IST calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(UTC)
    return start, start + timedelta(days=1)


def validate_happened_at(at: datetime | None, now: datetime) -> datetime:
    """When a touch happened: now by default, never the future, at most 30 days back."""
    moment = aware(now)
    if at is None:
        return moment
    happened = aware(at)
    if happened > moment:
        raise ValueError("A touch cannot be dated in the future.")
    if happened < moment - timedelta(days=BACKDATE_MAX_DAYS):
        raise ValueError(f"A touch can be dated at most {BACKDATE_MAX_DAYS} days back.")
    return happened


# --- text ---------------------------------------------------------------------


def clean_text(value: str | None, limit: int) -> str | None:
    """Free text made safe to store and to put in front of a model.

    Control characters go (newline stays), ``\\r\\n`` becomes ``\\n``, three or
    more newlines become two, runs of spaces and tabs become one space, and the
    ends are stripped. Nothing left is ``None``. Longer than ``limit`` after all
    that is a ``ValueError`` - the text is refused, never silently cut.
    """
    if value is None:
        return None
    text = value.replace("\r\n", "\n")
    text = _CONTROL.sub("", text)
    text = _SPACE_RUNS.sub(" ", text)
    text = _BLANK_RUNS.sub("\n\n", text).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"Keep this to {limit} characters or fewer.")
    return text


# --- the clock ----------------------------------------------------------------


@dataclass(frozen=True)
class PersonClock:
    """Everything the follow-up rule knows about one person in a thread."""

    state: StoredState
    first_sent_at: datetime | None
    last_outbound_at: datetime | None
    follow_ups_sent: int
    next_follow_up_at: datetime | None
    replied_at: datetime | None

    @classmethod
    def new(cls) -> PersonClock:
        """A person added to a thread and not yet written to."""
        return cls(
            state="planned",
            first_sent_at=None,
            last_outbound_at=None,
            follow_ups_sent=0,
            next_follow_up_at=None,
            replied_at=None,
        )


@dataclass(frozen=True)
class TouchEffect:
    """What a touch did to the clock, and what kind of touch it was."""

    clock: PersonClock
    kind: TouchKind


def effective_state(clock: PersonClock, now: datetime) -> PersonState:
    """The state to show: ``waiting`` becomes ``cold`` once both nudges have gone
    unanswered for ``COLD_AFTER_DAYS``. Derived, so no job has to run for a
    thread to cool."""
    if (
        clock.state == "waiting"
        and clock.follow_ups_sent >= MAX_FOLLOW_UPS
        and clock.last_outbound_at is not None
        and aware(now) >= aware(clock.last_outbound_at) + timedelta(days=COLD_AFTER_DAYS)
    ):
        return "cold"
    return clock.state


def _may_recontact(clock: PersonClock, now: datetime) -> bool:
    """A cold person, long enough after the last message, can be written to afresh."""
    return (
        effective_state(clock, now) == "cold"
        and clock.last_outbound_at is not None
        and aware(now) >= aware(clock.last_outbound_at) + timedelta(days=RECONTACT_AFTER_DAYS)
    )


def _first_message(at: datetime) -> PersonClock:
    return PersonClock(
        state="waiting",
        first_sent_at=at,
        last_outbound_at=at,
        follow_ups_sent=0,
        next_follow_up_at=at + timedelta(days=FOLLOW_UP_AFTER_DAYS[0]),
        replied_at=None,
    )


def apply_touch(clock: PersonClock, *, direction: str, channel: str, at: datetime) -> TouchEffect:
    """Record one touch against a person's clock.

    A reply (anything inbound, or a call or event either way) stops the clock.
    ``replied_at`` is the first reply: a later one does not move it, and one
    logged late with an earlier date pulls it back to that date. An outbound
    message starts the clock, nudges it, or - when the clock has nothing left to
    do - is logged without moving it: the user may have written anyway, and the
    timeline should say so.

    Touches are logged in the order the user remembers them, not the order they
    happened. An outbound message dated before the last one already logged is
    kept as kind ``other`` and never moves the clock: a late entry must not
    rewind a follow-up. A reply still replies, whenever it is dated.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown direction: {direction!r}")
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel!r}")
    moment = aware(at)

    if direction == "in" or channel in _CONVERSATION_CHANNELS:
        first_reply = moment if clock.replied_at is None else min(aware(clock.replied_at), moment)
        return TouchEffect(
            replace(clock, state="replied", replied_at=first_reply, next_follow_up_at=None),
            "reply",
        )

    if clock.last_outbound_at is not None and moment < aware(clock.last_outbound_at):
        return TouchEffect(clock, "other")

    if clock.state == "planned":
        return TouchEffect(_first_message(moment), "first")

    if clock.state == "waiting":
        if clock.follow_ups_sent < MAX_FOLLOW_UPS:
            sent = clock.follow_ups_sent + 1
            wait = FOLLOW_UP_AFTER_DAYS[sent] if sent < len(FOLLOW_UP_AFTER_DAYS) else None
            return TouchEffect(
                replace(
                    clock,
                    follow_ups_sent=sent,
                    last_outbound_at=moment,
                    next_follow_up_at=None if wait is None else moment + timedelta(days=wait),
                ),
                "follow_up",
            )
        if _may_recontact(clock, moment):
            return TouchEffect(_first_message(moment), "first")

    return TouchEffect(clock, "other")


def snooze(clock: PersonClock, *, days: int, now: datetime) -> PersonClock:
    """Push a pending follow-up out by ``days`` (1 to ``SNOOZE_MAX_DAYS``).

    Only a waiting person with a nudge still to send can be snoozed. Once both
    nudges are spent there is no follow-up to move, and giving one a date would
    make the person due for something ``can_draft`` refuses to offer.
    """
    if not 1 <= days <= SNOOZE_MAX_DAYS:
        raise ValueError(f"Snooze between 1 and {SNOOZE_MAX_DAYS} days.")
    if clock.state != "waiting" or clock.follow_ups_sent >= MAX_FOLLOW_UPS:
        raise ValueError("Only a follow-up that is still pending can be snoozed.")
    return replace(clock, next_follow_up_at=aware(now) + timedelta(days=days))


def can_draft(clock: PersonClock, now: datetime) -> Literal["first", "follow_up"] | None:
    """Which message the app may help write next for this person, if any.

    A follow-up is offered only once it is due (``is_due_today``). Before that the
    person is simply waiting: drafting and logging a nudge the day after the first
    note would spend follow-up 1 four days early and restart the clock, which is the
    nagging the product exists to prevent. A waiting person with no date set has no
    clock to wait for, so that is not held back.
    """
    if clock.state == "planned":
        return "first"
    if clock.state == "waiting":
        if clock.follow_ups_sent < MAX_FOLLOW_UPS:
            if clock.next_follow_up_at is None or is_due_today(clock, now):
                return "follow_up"
            return None
        if _may_recontact(clock, now):
            return "first"
    return None


def is_due(clock: PersonClock, now: datetime) -> bool:
    """Is a follow-up owed to this person right now?"""
    return (
        clock.state == "waiting"
        and clock.next_follow_up_at is not None
        and aware(clock.next_follow_up_at) <= aware(now)
    )


def is_due_today(clock: PersonClock, now: datetime) -> bool:
    """Is a follow-up owed to this person today in India?

    A follow-up dated any time today (IST) is due from 00:00 IST, not from the minute
    it was set for, so a Bengaluru morning shows what the day holds. ``is_due``
    compares instants; this is the day-granular rule every surface that says "due"
    shares, so a lamp, the due list and the draft button cannot disagree.
    """
    return (
        clock.state == "waiting"
        and clock.next_follow_up_at is not None
        and ist_today(clock.next_follow_up_at) <= ist_today(now)
    )


# --- threads ------------------------------------------------------------------


def thread_stage(states: Iterable[PersonState], *, closed: bool) -> ThreadStage:
    """Where a thread stands. First match wins: closed, replied, waiting, cold, planned."""
    if closed:
        return "closed"
    seen = set(states)
    if "replied" in seen:
        return "replied"
    if "waiting" in seen:
        return "waiting"
    if "cold" in seen:
        return "cold"
    return "planned"


@dataclass(frozen=True)
class DueItem:
    """One thing to do today: a follow-up owed, or a next step the user set."""

    thread_id: str
    person_id: str | None
    kind: Literal["follow_up", "next_step"]
    due_at: datetime
    company_name: str
    person_name: str | None
    follow_up_number: int | None
    last_outbound_at: datetime | None
    next_step: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "person_id": self.person_id,
            "kind": self.kind,
            "due_at": _iso(self.due_at),
            "company_name": self.company_name,
            "person_name": self.person_name,
            "follow_up_number": self.follow_up_number,
            "last_outbound_at": _iso(self.last_outbound_at),
            "next_step": self.next_step,
        }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else aware(value).isoformat()


def sort_due(items: Iterable[DueItem]) -> list[DueItem]:
    """Next steps first - a conversation already started - then follow-ups; each
    group oldest first."""
    return sorted(
        items,
        key=lambda item: (
            item.kind != "next_step",
            aware(item.due_at),
            item.thread_id,
            item.person_id or "",
        ),
    )


def pacing_note(company_name: str, first_messages_last_7_days: int) -> str | None:
    """A nudge to slow down when several people at one company were written to this
    week. It never blocks anything."""
    if first_messages_last_7_days < SAME_COMPANY_WEEK_SOFT_LIMIT:
        return None
    return (
        f"You've written to {first_messages_last_7_days} people at {company_name} this week. "
        "Give them a few days before the next."
    )
