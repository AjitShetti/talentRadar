"""
domain/liveness.py
~~~~~~~~~~~~~~~~~~
Is this posting still open? A verdict, and the evidence it rests on.

There is deliberately no score here. A 0-100 number would imply a model of
"realness" we do not have; what we do have is a handful of observable facts -
when the employer published the role, when we first and last saw it, whether
the source still serves it, and how often the same role has been re-listed.
``assess`` turns those into one of six states and returns the facts alongside,
so the interface can always show *why*.

Everything in this module is pure: no I/O, no clock, no LLM. ``now`` is passed
in. That is what lets the same function back a search card, a public page and
a test table without any of them disagreeing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

# A role open longer than this is called out. Most filled roles close well
# inside six weeks; one that has not is either hard to fill or not being filled.
AGEING_AFTER_DAYS = 45

# How long a successful re-check of the source stays good for.
VERIFICATION_VALID_FOR = timedelta(hours=72)

# A posting we cannot re-check is treated as open only while we keep seeing it.
SEEN_RECENTLY_WITHIN = timedelta(days=7)

# Re-listings needed before a role is described as reposted. One is routine -
# a requisition gets re-opened - so the flag starts at two.
REPOSTED_AT_LEAST = 2

# A new external id for the same company and title counts as a re-listing only
# after this gap. Inside it, two ids are far more likely to be two openings.
REPOST_GAP = timedelta(days=14)

TITLE_KEY_MAX_LENGTH = 200

# Sources whose postings can be re-fetched from a public, documented JSON API.
_VERIFIABLE_PREFIXES = ("greenhouse", "lever", "ashby")

# "R-12345", "REQ 9981", "Job ID: 4471" - noise that differs between two
# listings of the same role and must not make them look like different roles.
_REQ_ID = re.compile(r"\b(?:r|req|requisition|job\s*id|id)[\s\-#:]*\d{3,}\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Epoch values above this are milliseconds (Lever), below it seconds.
_EPOCH_MS_THRESHOLD = 100_000_000_000


class LivenessState(str, Enum):
    CLOSED = "closed"
    REPOSTED = "reposted"
    AGEING = "ageing"
    VERIFIED_OPEN = "verified_open"
    OPEN_UNVERIFIED = "open_unverified"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LivenessEvidence:
    """What we hold about one posting. Any field may be missing."""

    source_posted_at: datetime | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    last_verified_at: datetime | None
    closed_at: datetime | None
    times_reposted: int
    # True when the source can be re-fetched (an ATS JSON API).
    verifiable: bool


@dataclass(frozen=True)
class LivenessVerdict:
    state: LivenessState
    headline: str
    # Plain statements of fact, most important first.
    evidence: tuple[str, ...]
    open_days: int | None


@dataclass(frozen=True)
class SightingState:
    """The running record for one (company, normalised title)."""

    first_seen_at: datetime
    last_seen_at: datetime
    times_seen: int
    times_reposted: int
    last_external_id: str | None


# ── helpers ──────────────────────────────────────────────────────────────────


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _ago(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 3600:
        return "less than an hour ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _day(value: datetime) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def is_verifiable_source(source: str) -> bool:
    """True for sources whose postings can be re-checked against the employer's ATS."""
    return (source or "").split(":", 1)[0].strip().lower() in _VERIFIABLE_PREFIXES


def title_key(title: str) -> str:
    """Normalise a job title so two listings of the same role compare equal."""
    lowered = _REQ_ID.sub(" ", (title or "").lower())
    return _NON_ALNUM.sub(" ", lowered).strip()[:TITLE_KEY_MAX_LENGTH].strip()


def parse_source_timestamp(value: object) -> datetime | None:
    """
    Read a publish time as an ATS reports it: ISO-8601 text, or epoch
    seconds/milliseconds. Anything else is ``None`` - a posting with no
    trustworthy date must stay dateless rather than borrow the clock.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000 if value > _EPOCH_MS_THRESHOLD else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            return _utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


# ── repost detection ─────────────────────────────────────────────────────────


def next_sighting(
    previous: SightingState | None, *, external_id: str | None, seen_at: datetime
) -> SightingState:
    """Fold one more sighting of a role into its running record."""
    if previous is None:
        return SightingState(seen_at, seen_at, 1, 0, external_id)

    is_new_listing = bool(external_id) and external_id != previous.last_external_id
    reposted = is_new_listing and seen_at - previous.last_seen_at > REPOST_GAP
    return SightingState(
        first_seen_at=min(previous.first_seen_at, seen_at),
        last_seen_at=max(previous.last_seen_at, seen_at),
        times_seen=previous.times_seen + 1,
        times_reposted=previous.times_reposted + (1 if reposted else 0),
        last_external_id=external_id or previous.last_external_id,
    )


# ── the verdict ──────────────────────────────────────────────────────────────


def assess(e: LivenessEvidence, *, now: datetime) -> LivenessVerdict:
    """Decide a posting's liveness state. First matching rule wins."""
    now = _utc(now) or now
    posted = _utc(e.source_posted_at)
    first_seen = _utc(e.first_seen_at)
    last_seen = _utc(e.last_seen_at)
    verified = _utc(e.last_verified_at)
    closed = _utc(e.closed_at)

    opened = posted or first_seen
    open_days = max((now - opened).days, 0) if opened else None
    is_ageing = opened is not None and now - opened > timedelta(days=AGEING_AFTER_DAYS)
    verified_fresh = (
        e.verifiable and verified is not None and now - verified <= VERIFICATION_VALID_FOR
    )

    lines: list[str] = []
    if closed:
        lines.append(f"The employer's careers site stopped listing it on {_day(closed)}")
    if posted:
        lines.append(f"First published {_day(posted)} - {open_days} days ago")
    elif first_seen:
        lines.append(
            f"The source gives no publish date; we first saw it {_day(first_seen)} "
            f"- {open_days} days ago"
        )
    if e.times_reposted:
        times = "once" if e.times_reposted == 1 else f"{e.times_reposted} times"
        lines.append(f"Re-listed {times} under a new posting id")
    if verified and e.verifiable:
        lines.append(f"Last confirmed on the employer's careers site {_ago(now - verified)}")
    if not e.verifiable:
        lines.append("This source cannot be re-checked automatically")
        if last_seen:
            lines.append(f"Last seen in search results {_ago(now - last_seen)}")
    evidence = tuple(lines)

    if closed:
        return LivenessVerdict(LivenessState.CLOSED, "Closed at the source", evidence, open_days)
    if e.times_reposted >= REPOSTED_AT_LEAST:
        return LivenessVerdict(
            LivenessState.REPOSTED,
            f"Re-listed {e.times_reposted} times",
            evidence,
            open_days,
        )
    if is_ageing:
        return LivenessVerdict(
            LivenessState.AGEING, f"Open for {open_days} days", evidence, open_days
        )
    if verified_fresh and verified is not None:
        return LivenessVerdict(
            LivenessState.VERIFIED_OPEN,
            f"Confirmed open {_ago(now - verified)}",
            evidence,
            open_days,
        )
    if last_seen is not None and now - last_seen <= SEEN_RECENTLY_WITHIN:
        return LivenessVerdict(
            LivenessState.OPEN_UNVERIFIED,
            f"Seen {_ago(now - last_seen)}, not confirmed with the employer",
            evidence,
            open_days,
        )
    return LivenessVerdict(
        LivenessState.UNKNOWN, "Not enough evidence to say", evidence, open_days
    )
