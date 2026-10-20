"""
services/practice/challenges.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The weekly take-home: editions, a personal clock, a submission, and the status moves around it.

* **Editions.** One edition opens every Friday 18:00 IST and runs a week
  (``domain.practice_schedule``). ``ensure_editions`` creates this week's and next week's from
  the brief files, the least recently run brief first, and copies the brief onto the row, so
  editing a file later never changes a live challenge. The read path shows an edition only
  once ``now >= opens_at``.
* **A personal clock.** *Start* stores the server time and sets ``deadline_at = started_at +
  hours``. Start is allowed through ``last_start`` inclusive, so everyone gets the full window.
  A ten-character proof code is made at Start and shown only to its owner.
* **The submission** (repo link, four bounded write-up fields, an optional video link) can be
  saved and replaced from Start until ``deadline_at + GRACE``. Saving makes at most one request
  to GitHub (the repo must be public, not a fork, and created after Start, allowing
  ``REPO_CREATION_GRACE``); if GitHub cannot be reached the save still succeeds and the check
  runs again at judge time. The repo URL is matched strictly and every request is built from
  the captured owner and name (``github_reader``); nothing the person typed becomes a URL.
* **Statuses** move on a schedule, not on a read: ``advance_statuses`` queues what was
  submitted and marks the rest missed once the grace hour is over; ``prune`` applies the
  retention rules of the spec. Both are scheduled steps (``services/practice/steps.py``): a
  failure on one row is logged and skipped, never raised.

Concurrency. Every write is safe to repeat: ``start`` is an insert guarded by the unique
``(user, challenge)`` index (the loser of a double click gets :class:`PracticeConflict`);
``save_submission`` is one conditional ``UPDATE`` on the attempt's own row, so two saves leave
one row and a save that loses to ``advance_statuses`` changes nothing. The GitHub call is made
with no database session open.

Privacy. Every read and write is scoped by the signed-in user's id. Another user's attempt,
and above all its proof code, is invisible. The proof code appears only in the payload for the
owner's own attempt.

Saves are limited to ``SAVES_PER_MINUTE`` per user through ``CacheBackend`` (which falls back
to a per-process counter and never requires Redis). Above it, :class:`PracticeRateLimited`
(a :class:`PracticeConflict`) is raised; the router answers it with 429.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.practice_repo import (
    REPO_CREATION_GRACE,
    RepoRef,
    new_proof_code,
    parse_repo_url,
    parse_timestamp,
    valid_video_url,
)
from domain.practice_schedule import (
    GRACE,
    deadline_for,
    edition_window,
    ist_day,
    last_start,
    submission_open,
    week_opening,
)
from services.base import parse_uuid
from services.cache_backend import CacheBackend
from services.practice.daily import RETENTION_DAYS as DAY_RETENTION_DAYS
from services.practice.errors import (
    PracticeConflict,
    PracticeInvalid,
    PracticeNotFound,
    PracticeRateLimited,
)
from services.practice.github_reader import (
    GitHubUnavailable,
    RepoGone,
    fetch_repo,
    github_client,
)
from storage.database import AsyncSessionLocal
from storage.models import PracticeAttempt, PracticeChallenge, PracticeDay, User

logger = logging.getLogger(__name__)

#: Where the brief files live. Anchored on this file, not on the working directory, so it
#: resolves the same from any process (``hiring_list.py`` and the catalogue do the same).
BRIEFS_DIR = Path(__file__).resolve().parents[2] / "data" / "practice" / "challenges"

TRACKS = ("backend", "frontend", "fullstack", "data", "ai")
HOURS = (24, 48)
#: Spec 5.2: a stored brief is at most 8 KB.
BRIEF_MAX_BYTES = 8 * 1024
BRIEF_KEYS = (
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
)
_BRIEF_TEXT_KEYS = ("title", "summary", "bengaluru_context")
_BRIEF_LIST_KEYS = (
    "context",
    "must_have",
    "nice_to_have",
    "constraints",
    "deliverables",
    "judge_focus",
)
#: Sections a brief cannot do without; ``nice_to_have`` and ``constraints`` may be empty.
_BRIEF_REQUIRED_LISTS = frozenset({"context", "must_have", "deliverables", "judge_focus"})
_BRIEF_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SLUG_CHARS = 80  # practice_challenges.brief_slug is varchar(80)
_TITLE_CHARS = 160  # practice_challenges.title is varchar(160)

#: The four write-up fields and their (minimum, maximum) lengths in characters (spec 4.9).
WRITEUP_BOUNDS: dict[str, tuple[int, int]] = {
    "decisions": (200, 1500),
    "tradeoffs": (80, 1000),
    "ai_usage": (40, 1000),
    "next_steps": (40, 800),
}
VIDEO_URL_CHARS = 300

SAVES_PER_MINUTE = 10
_SAVE_COUNTER_TTL = 120

#: Statuses in which a submission can still be edited (and the only ones that move on a clock).
EDITABLE_STATUSES = ("started", "submitted")
PAST_EDITIONS = 12
#: Spec 5.3: ``missed`` and never-submitted ``started`` rows go 30 days after their deadline;
#: everything else after two years.
UNSUBMITTED_RETENTION = timedelta(days=30)
ATTEMPT_RETENTION = timedelta(days=730)
#: A scheduled run moves at most this many attempts; the rest wait for the next run.
_ADVANCE_BATCH = 500
_PROOF_CODE_TRIES = 3

_NO_ACCOUNT = "We could not find your account."
_NO_BUILD = "We could not find that build."
_STARTED_ALREADY = "You have already started this build."
_TOO_LATE = (
    "Not enough time left in this week's window to start this build. "
    "The next build opens on Friday at 18:00 IST."
)
_NOT_STARTED = "Press Start on this build before saving a submission."
_CLOSED = "The submission window for this build has closed."
_TOO_FAST = "You are saving too quickly. Wait a minute and try again."
_TRY_AGAIN = "We could not start your build just now. Try again."
_BAD_REPO_LINK = "Paste the repo link as https://github.com/<owner>/<repo>."
_REPO_NOT_FOUND = "We could not find that repo. Check the link; the repo must be public."
_REPO_PRIVATE = "The repo must be public so it can be judged."
_REPO_FORK = "The repo must not be a fork. Create a new repo after you press Start."
_REPO_BEFORE_START = "Create a new repo after you press Start."
_BAD_VIDEO_HOST = (
    "The video link must be an https link on youtube.com, youtu.be, loom.com or drive.google.com."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _moment(now: datetime | None) -> datetime:
    """``now`` as an aware UTC instant; a naive one is refused by :func:`ist_day`."""
    moment = now if now is not None else datetime.now(UTC)
    ist_day(moment)  # raises ValueError for a naive datetime
    return moment.astimezone(UTC)


def _aware(moment: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; every stored instant is UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return _aware(moment).isoformat()


def _user(user_id: str) -> uuid.UUID:
    user = parse_uuid(user_id)
    if user is None:
        raise PracticeNotFound(_NO_ACCOUNT)
    return user


def _matched(result: Result[Any]) -> bool:
    """Whether an UPDATE changed a row. A conditional update that matched none lost the race."""
    return cast("CursorResult[Any]", result).rowcount > 0


def _count(result: Result[Any]) -> int:
    return max(0, cast("CursorResult[Any]", result).rowcount)


# ---------------------------------------------------------------------------
# The brief files
# ---------------------------------------------------------------------------


def _is_text(value: object, *, limit: int | None = None) -> bool:
    return isinstance(value, str) and bool(value.strip()) and (limit is None or len(value) <= limit)


def _brief_problem(brief: object) -> str | None:
    """What is wrong with a brief (spec 5.5), or ``None``."""
    if not isinstance(brief, dict):
        return "a brief must be a JSON object"
    for key in BRIEF_KEYS:
        if key not in brief:
            return f"missing key '{key}'"
    slug, hours = brief["slug"], brief["hours"]
    if not (isinstance(slug, str) and len(slug) <= _SLUG_CHARS and _BRIEF_SLUG.fullmatch(slug)):
        return f"slug must be lowercase words joined by hyphens, at most {_SLUG_CHARS} characters"
    if not _is_text(brief["title"], limit=_TITLE_CHARS):
        return f"title must be text of at most {_TITLE_CHARS} characters"
    if brief["track"] not in TRACKS:
        return f"track must be one of {', '.join(TRACKS)}"
    if isinstance(hours, bool) or not isinstance(hours, int) or hours not in HOURS:
        return "hours must be 24 or 48"
    for key in _BRIEF_TEXT_KEYS:
        if not _is_text(brief[key]):
            return f"{key} must be non-empty text"
    for key in _BRIEF_LIST_KEYS:
        items = brief[key]
        if not isinstance(items, list) or not all(_is_text(item) for item in items):
            return f"{key} must be a list of non-empty text"
        if key in _BRIEF_REQUIRED_LISTS and not items:
            return f"{key} must have at least one entry"
    if len(json.dumps(brief, ensure_ascii=False).encode("utf-8")) > BRIEF_MAX_BYTES:
        return f"the brief is over {BRIEF_MAX_BYTES} bytes"
    return None


def load_briefs(path: Path = BRIEFS_DIR) -> list[dict[str, Any]]:
    """Every ``*.json`` brief in ``path``, sorted by file name, validated.

    A brief with a missing key, a wrong type, ``hours`` outside 24/48, an unknown track, or
    more than 8 KB raises :class:`PracticeInvalid` naming the file. A directory that does not
    exist has no briefs. The content is returned exactly as the file holds it.
    """
    if not path.is_dir():
        return []
    briefs: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for file in sorted(path.glob("*.json"), key=lambda candidate: candidate.name):
        try:
            brief = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PracticeInvalid(f"{file.name}: not readable as JSON ({exc})") from exc
        problem = _brief_problem(brief)
        if problem is not None:
            raise PracticeInvalid(f"{file.name}: {problem}")
        if brief["slug"] in seen:
            raise PracticeInvalid(
                f"{file.name}: slug '{brief['slug']}' is already used by {seen[brief['slug']]}"
            )
        seen[brief["slug"]] = file.name
        briefs.append(brief)
    return briefs


# ---------------------------------------------------------------------------
# Payloads. Their key sets are the types in frontend/lib/api.ts (plan Task 15).
# ---------------------------------------------------------------------------


def _brief_payload(challenge: PracticeChallenge) -> dict[str, Any]:
    """The brief as the page types it: exactly the documented keys, whatever the row holds."""
    stored = challenge.brief if isinstance(challenge.brief, dict) else {}
    payload: dict[str, Any] = {}
    for key in BRIEF_KEYS:
        value = stored.get(key)
        if key in _BRIEF_LIST_KEYS:
            payload[key] = [str(item) for item in value] if isinstance(value, list) else []
        else:
            payload[key] = str(value) if isinstance(value, str) else ""
    payload["slug"] = payload["slug"] or challenge.brief_slug
    payload["title"] = payload["title"] or challenge.title
    payload["track"] = payload["track"] or challenge.track
    payload["hours"] = challenge.hours  # the column is authoritative
    return payload


def _writeup_payload(stored: object) -> dict[str, str] | None:
    if not isinstance(stored, dict):
        return None
    return {field: str(stored.get(field, "")) for field in WRITEUP_BOUNDS}


def _attempt_payload(attempt: PracticeAttempt) -> dict[str, Any]:
    owner, name = attempt.repo_owner, attempt.repo_name
    return {
        "status": attempt.status,
        "started_at": _iso(attempt.started_at),
        "deadline_at": _iso(attempt.deadline_at),
        "proof_code": attempt.proof_code,
        "repo_url": RepoRef(owner, name).html_url if owner and name else None,
        "writeup": _writeup_payload(attempt.writeup),
        "video_url": attempt.video_url,
        "submitted_at": _iso(attempt.submitted_at) if attempt.submitted_at else None,
        "editable_until": _iso(_aware(attempt.deadline_at) + GRACE),
        "total": float(attempt.total) if attempt.total is not None else None,
        # Only whether, never why: the owner reads "Under review", not the flags.
        "held": bool(attempt.held),
    }


def _challenge_payload(
    challenge: PracticeChallenge, attempt: PracticeAttempt | None
) -> dict[str, Any]:
    return {
        "slug": challenge.slug,
        "opens_at": _iso(challenge.opens_at),
        "closes_at": _iso(challenge.closes_at),
        "last_start_at": _iso(last_start(_aware(challenge.closes_at), challenge.hours)),
        "brief": _brief_payload(challenge),
        "attempt": _attempt_payload(attempt) if attempt is not None else None,
    }


def _past_payload(challenge: PracticeChallenge, attempt: PracticeAttempt | None) -> dict[str, Any]:
    return {
        "slug": challenge.slug,
        "title": challenge.title,
        "track": challenge.track,
        "opens_at": _iso(challenge.opens_at),
        "status": attempt.status if attempt is not None else None,
        "total": float(attempt.total)
        if attempt is not None and attempt.total is not None
        else None,
        "held": bool(attempt.held) if attempt is not None else False,
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def _find_attempt(
    session: AsyncSession, user: uuid.UUID, challenge_id: uuid.UUID
) -> PracticeAttempt | None:
    return (
        await session.execute(
            select(PracticeAttempt).where(
                PracticeAttempt.user_id == user, PracticeAttempt.challenge_id == challenge_id
            )
        )
    ).scalar_one_or_none()


async def _visible_challenge(
    session: AsyncSession, slug: str, moment: datetime
) -> PracticeChallenge:
    """The edition named ``slug``, once it has opened. An unopened or unknown one is not found."""
    row = (
        await session.execute(select(PracticeChallenge).where(PracticeChallenge.slug == slug))
    ).scalar_one_or_none()
    if row is None or _aware(row.opens_at) > moment:
        raise PracticeNotFound(_NO_BUILD)
    return row


async def _user_exists(session: AsyncSession, user: uuid.UUID) -> bool:
    found = await session.execute(select(User.id).where(User.id == user))
    return found.first() is not None


# ---------------------------------------------------------------------------
# Editions
# ---------------------------------------------------------------------------


async def _scheduled_editions(session: AsyncSession) -> list[tuple[str, datetime]]:
    """``(brief slug, opens_at)`` of every edition that exists."""
    rows = (
        await session.execute(select(PracticeChallenge.brief_slug, PracticeChallenge.opens_at))
    ).all()
    return [(row.brief_slug, _aware(row.opens_at)) for row in rows]


def _least_recently_run(
    briefs: list[dict[str, Any]], last_run: Mapping[str, datetime]
) -> dict[str, Any]:
    """The brief that ran longest ago; one that never ran comes first, then file order."""
    never = datetime.min.replace(tzinfo=UTC)
    first = min(
        range(len(briefs)), key=lambda number: (last_run.get(briefs[number]["slug"], never), number)
    )
    return briefs[first]


def _edition_row(brief: dict[str, Any], opens_at: datetime) -> PracticeChallenge:
    opens, closes = edition_window(opens_at)
    return PracticeChallenge(
        slug=f"{brief['slug']}-{ist_day(opens).isoformat()}",
        brief_slug=brief["slug"],
        title=brief["title"],
        track=brief["track"],
        hours=brief["hours"],
        brief=brief,
        opens_at=opens,
        closes_at=closes,
    )


#: The constraints that say "this opening already has its edition". The opening itself is
#: unique; the slug is brief + opening, so a run that chose the same brief breaks both and
#: PostgreSQL names whichever index it checks first. No other constraint is a lost race.
_OPENING_CONSTRAINTS = ("uq_practice_challenges_opens_at", "uq_practice_challenges_slug")
#: SQLite never names a constraint, only the columns.
_SQLITE_OPENING = (
    "UNIQUE constraint failed: practice_challenges.opens_at",
    "UNIQUE constraint failed: practice_challenges.slug",
)


def _is_opening_conflict(error: IntegrityError) -> bool:
    """The failure is a second edition for an opening that has one, and nothing else.

    asyncpg names the constraint on the exception the driver's was raised from
    (``constraint_name``) and in its message; psycopg2 in its message; SQLite only gives the
    columns. A NOT NULL, a foreign key or any other violation is not this one.
    """
    constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
    message = str(error.orig)
    return (
        constraint in _OPENING_CONSTRAINTS
        or any(name in message for name in _OPENING_CONSTRAINTS)
        or any(text in message for text in _SQLITE_OPENING)
    )


async def ensure_editions(*, now: datetime | None = None) -> int:
    """Create this week's edition and next week's if either is missing. Returns how many.

    The brief for each is the least recently run one (a brief that never ran first, then file
    order; once all have run, the cycle starts again). Each edition is its own transaction.
    The opening is unique, so two runs never make two editions for one week, even if they
    chose different briefs: the loser meets that constraint (:func:`_is_opening_conflict`),
    rolls back its one edition, and goes on to the next. Any other integrity error propagates.
    An invalid brief file raises :class:`PracticeInvalid`; with no briefs there is nothing to
    create.
    """
    moment = _moment(now)
    briefs = load_briefs(BRIEFS_DIR)
    if not briefs:
        return 0
    this_week = week_opening(moment)
    openings = [this_week, edition_window(this_week)[1]]
    created = 0
    async with AsyncSessionLocal() as session:
        existing = await _scheduled_editions(session)
        have = {opens_at for _, opens_at in existing}
        last_run: dict[str, datetime] = {}
        for brief_slug, opens_at in existing:
            last_run[brief_slug] = max(opens_at, last_run.get(brief_slug, opens_at))
        for opens_at in openings:
            if opens_at in have:
                continue
            brief = _least_recently_run(briefs, last_run)
            last_run[brief["slug"]] = opens_at
            session.add(_edition_row(brief, opens_at))
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                if not _is_opening_conflict(error):
                    raise
                continue  # another run created this edition first
            created += 1
            logger.info("practice edition created: %s opens %s", brief["slug"], opens_at)
    return created


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def challenges_overview(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """The open edition with the reader's attempt, when the next one opens, and the last twelve.

    ``current`` is the latest edition with ``opens_at <= now < closes_at``. ``next_opens_at``
    is the next edition on record, or the schedule's next Friday 18:00 IST when none exists
    yet. ``past`` is the last twelve closed editions, newest first, each with the reader's
    own status and total.
    """
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(PracticeChallenge)
                    .where(PracticeChallenge.opens_at <= moment)
                    .order_by(PracticeChallenge.opens_at.desc())
                    .limit(PAST_EDITIONS + 1)
                )
            ).scalars()
        )
        attempts: dict[uuid.UUID, PracticeAttempt] = {}
        if rows:
            found = await session.execute(
                select(PracticeAttempt).where(
                    PracticeAttempt.user_id == user,
                    PracticeAttempt.challenge_id.in_([row.id for row in rows]),
                )
            )
            attempts = {attempt.challenge_id: attempt for attempt in found.scalars()}
        upcoming = (
            await session.execute(
                select(func.min(PracticeChallenge.opens_at)).where(
                    PracticeChallenge.opens_at > moment
                )
            )
        ).scalar()
        current = next((row for row in rows if moment < _aware(row.closes_at)), None)
        closed = [row for row in rows if _aware(row.closes_at) <= moment][:PAST_EDITIONS]
        return {
            "current": (_challenge_payload(current, attempts.get(current.id)) if current else None),
            "next_opens_at": _iso(
                _aware(upcoming)
                if upcoming is not None
                else edition_window(week_opening(moment))[1]
            ),
            "past": [_past_payload(row, attempts.get(row.id)) for row in closed],
        }


async def challenge_detail(
    user_id: str, slug: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """One edition's brief and the reader's own attempt, once the edition has opened."""
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        challenge = await _visible_challenge(session, slug, moment)
        attempt = await _find_attempt(session, user, challenge.id)
        return _challenge_payload(challenge, attempt)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


async def start(user_id: str, slug: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Press Start: record the server time, the deadline and a proof code. Once per build.

    Allowed while ``now <= last_start`` (inclusive). A second Start, or the second of two
    quick clicks, raises :class:`PracticeConflict` and changes nothing.
    """
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        challenge = await _visible_challenge(session, slug, moment)
        # Plain values, read before any rollback expires the row.
        challenge_id, hours = challenge.id, challenge.hours
        latest = last_start(_aware(challenge.closes_at), hours)
        if await _find_attempt(session, user, challenge_id) is not None:
            raise PracticeConflict(_STARTED_ALREADY)
        if moment > latest:
            raise PracticeInvalid(_TOO_LATE)
        await _insert_attempt(session, user, challenge_id, hours, moment)
    return await challenge_detail(user_id, slug, now=moment)


async def _insert_attempt(
    session: AsyncSession, user: uuid.UUID, challenge_id: uuid.UUID, hours: int, moment: datetime
) -> None:
    """Insert the attempt, or learn why not.

    The unique ``(user, challenge)`` index makes the loser of a double click fail here; the
    row is then there, and the answer is a conflict. With no row, either the account does not
    exist or the random proof code collided with another (about one in 10^15): try again.
    """
    for _attempt in range(_PROOF_CODE_TRIES):
        session.add(
            PracticeAttempt(
                user_id=user,
                challenge_id=challenge_id,
                status="started",
                started_at=moment,
                deadline_at=deadline_for(moment, hours),
                proof_code=new_proof_code(),
            )
        )
        try:
            await session.commit()
            return
        except IntegrityError as error:
            await session.rollback()
            if await _find_attempt(session, user, challenge_id) is not None:
                raise PracticeConflict(_STARTED_ALREADY) from error
            if not await _user_exists(session, user):
                raise PracticeNotFound(_NO_ACCOUNT) from error
    raise PracticeConflict(_TRY_AGAIN)


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


async def _count_save(user_id: str, moment: datetime) -> None:
    """Count this save against the user's minute; above ``SAVES_PER_MINUTE`` raise."""
    minute = int(moment.timestamp() // 60)
    used = await CacheBackend.incr(f"tr:practice:submit:{user_id}:{minute}", _SAVE_COUNTER_TTL)
    if used > SAVES_PER_MINUTE:
        raise PracticeRateLimited(_TOO_FAST)


def _clean_text(value: str) -> str:
    """Plain text safe to store: CRLF becomes LF, control characters and surrogates are dropped.

    Newlines and tabs stay. A NUL in a JSON document is a database error, so it must go.
    """
    kept = (
        ch
        for ch in value.replace("\r\n", "\n")
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cs")
    )
    return "".join(kept).strip()


def _checked_repo(repo_url: str) -> RepoRef:
    ref = parse_repo_url(repo_url.strip()) if isinstance(repo_url, str) else None
    if ref is None:
        raise PracticeInvalid(_BAD_REPO_LINK)
    return ref


def _checked_writeup(writeup: Mapping[str, Any]) -> dict[str, str]:
    """The four fields, cleaned and within their bounds. Anything else in ``writeup`` is dropped."""
    clean: dict[str, str] = {}
    for field, (minimum, maximum) in WRITEUP_BOUNDS.items():
        raw = writeup.get(field)
        if not isinstance(raw, str):
            raise PracticeInvalid(f"{field}: write {minimum} to {maximum} characters.")
        text = _clean_text(raw)
        if not minimum <= len(text) <= maximum:
            raise PracticeInvalid(
                f"{field}: write {minimum} to {maximum} characters (yours has {len(text)})."
            )
        clean[field] = text
    return clean


def _checked_video(video_url: str | None) -> str | None:
    if video_url is None:
        return None
    url = video_url.strip()
    if not url:
        return None
    if len(url) > VIDEO_URL_CHARS:
        raise PracticeInvalid(f"The video link can be at most {VIDEO_URL_CHARS} characters.")
    if not valid_video_url(url):
        raise PracticeInvalid(_BAD_VIDEO_HOST)
    return url


def _same_repo(known: tuple[str, str, datetime], ref: RepoRef) -> bool:
    """GitHub owner and repo names are case-insensitive."""
    return (known[0].lower(), known[1].lower()) == (ref.owner.lower(), ref.name.lower())


async def _check_repo(ref: RepoRef, started_at: datetime) -> datetime | None:
    """The save-time look at the repo: one request, no session open.

    Returns GitHub's ``created_at`` when the repo passes, ``None`` when it could not be
    checked (GitHub unreachable or an unreadable payload): the check is repeated at judge
    time. Raises :class:`PracticeInvalid` for a repo that is gone or private, a fork, or
    created before Start (allowing ``REPO_CREATION_GRACE``).
    """
    try:
        async with github_client() as client:
            repo = await fetch_repo(client, ref)
    except RepoGone as exc:
        raise PracticeInvalid(_REPO_NOT_FOUND) from exc
    except GitHubUnavailable:
        logger.info("practice: repo check deferred, GitHub unavailable")
        return None
    if repo.get("private") is True:
        raise PracticeInvalid(_REPO_PRIVATE)
    if repo.get("fork") is True:
        raise PracticeInvalid(_REPO_FORK)
    created_at = parse_timestamp(repo.get("created_at"))
    if created_at is None:
        return None
    if created_at < started_at - REPO_CREATION_GRACE:
        raise PracticeInvalid(_REPO_BEFORE_START)
    return created_at


async def save_submission(
    user_id: str,
    slug: str,
    *,
    repo_url: str,
    writeup: Mapping[str, str],
    video_url: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Save, or replace, the submission until ``deadline_at + GRACE``.

    Order: the per-minute limit, the attempt and its window (a closed window is a
    :class:`PracticeConflict` whatever else is wrong), the repo link, the write-up and the
    video link, then one GitHub request, then one conditional UPDATE of the attempt's row.
    Nothing is written when any step fails.
    """
    moment = _moment(now)
    user = _user(user_id)
    await _count_save(user_id, moment)
    async with AsyncSessionLocal() as session:
        challenge = await _visible_challenge(session, slug, moment)
        attempt = await _find_attempt(session, user, challenge.id)
        if attempt is None:
            if not await _user_exists(session, user):
                raise PracticeNotFound(_NO_ACCOUNT)
            raise PracticeConflict(_NOT_STARTED)
        if attempt.status not in EDITABLE_STATUSES or not submission_open(
            moment, _aware(attempt.deadline_at)
        ):
            raise PracticeConflict(_CLOSED)
        attempt_id = attempt.id
        started_at = _aware(attempt.started_at)
        known: tuple[str, str, datetime] | None = None
        if attempt.repo_owner and attempt.repo_name and attempt.repo_created_at:
            known = (attempt.repo_owner, attempt.repo_name, _aware(attempt.repo_created_at))
    ref = _checked_repo(repo_url)
    fields = _checked_writeup(writeup)
    video = _checked_video(video_url)
    created_at = await _check_repo(ref, started_at)
    if created_at is None and known is not None and _same_repo(known, ref):
        created_at = known[2]  # the same repo as the last save: its creation time stands
    async with AsyncSessionLocal() as session:
        changed = await session.execute(
            update(PracticeAttempt)
            .where(
                PracticeAttempt.id == attempt_id,
                PracticeAttempt.user_id == user,
                PracticeAttempt.status.in_(EDITABLE_STATUSES),
            )
            .values(
                status="submitted",
                repo_owner=ref.owner,
                repo_name=ref.name,
                writeup=fields,
                video_url=video,
                submitted_at=moment,
                repo_created_at=created_at,
            )
            .execution_options(synchronize_session=False)
        )
        if not _matched(changed):
            await session.rollback()
            raise PracticeConflict(_CLOSED)
        await session.commit()
    return await challenge_detail(user_id, slug, now=moment)


# ---------------------------------------------------------------------------
# Scheduled steps. Never raise for one bad row.
# ---------------------------------------------------------------------------


async def _due_attempts(moment: datetime) -> list[tuple[uuid.UUID, str, datetime]]:
    """``(id, status, deadline_at)`` of attempts whose deadline has passed and that are still
    ``started`` or ``submitted``, oldest deadline first. The grace hour is checked by the caller."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(PracticeAttempt.id, PracticeAttempt.status, PracticeAttempt.deadline_at)
                .where(
                    PracticeAttempt.status.in_(EDITABLE_STATUSES),
                    PracticeAttempt.deadline_at <= moment,
                )
                .order_by(PracticeAttempt.deadline_at)
                .limit(_ADVANCE_BATCH)
            )
        ).all()
    return [(row.id, row.status, _aware(row.deadline_at)) for row in rows]


async def _advance_one(attempt_id: uuid.UUID, status: str, moment: datetime) -> bool:
    """Move one attempt on, only if it is still in ``status``. ``False`` if someone beat us."""
    values: dict[str, Any] = (
        {"status": "queued", "next_judge_at": moment}
        if status == "submitted"
        else {"status": "missed"}
    )
    async with AsyncSessionLocal() as session:
        changed = await session.execute(
            update(PracticeAttempt)
            .where(PracticeAttempt.id == attempt_id, PracticeAttempt.status == status)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return _matched(changed)


async def advance_statuses(*, now: datetime | None = None) -> dict[str, int]:
    """Once the grace hour is over, ``submitted`` becomes ``queued`` and ``started`` ``missed``.

    A queued attempt is due for the judge at ``now``. Each row is moved by a conditional UPDATE
    in its own transaction, so a row the judge or an admin already moved is left alone and a
    failure on one row is logged and skipped (the next run meets it again), never raised.
    """
    moment = _moment(now)
    counts = {"queued": 0, "missed": 0}
    for attempt_id, status, deadline_at in await _due_attempts(moment):
        if submission_open(moment, deadline_at):
            continue
        try:
            moved = await _advance_one(attempt_id, status, moment)
        except Exception:
            logger.exception("practice: could not advance attempt %s", attempt_id)
            continue
        if moved:
            counts["queued" if status == "submitted" else "missed"] += 1
    return counts


async def _prune_days(moment: datetime) -> int:
    """``practice_days`` older than 400 IST days (spec 5.1)."""
    cutoff = ist_day(moment) - timedelta(days=DAY_RETENTION_DAYS)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(PracticeDay)
            .where(PracticeDay.day < cutoff)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return _count(result)


async def _prune_unsubmitted(moment: datetime) -> int:
    """``missed`` and never-submitted ``started`` rows, 30 days after their deadline."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(PracticeAttempt)
            .where(
                PracticeAttempt.status.in_(("missed", "started")),
                PracticeAttempt.submitted_at.is_(None),
                PracticeAttempt.deadline_at < moment - UNSUBMITTED_RETENTION,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return _count(result)


async def _prune_expired(moment: datetime) -> int:
    """Every other attempt, two years after its deadline."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(PracticeAttempt)
            .where(PracticeAttempt.deadline_at < moment - ATTEMPT_RETENTION)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return _count(result)


async def prune(*, now: datetime | None = None) -> dict[str, int]:
    """Delete what the retention rules (spec 5.1 and 5.3) say is old. Editions are kept forever.

    Each rule is one DELETE in its own transaction. A rule that fails is logged and skipped (the
    next run meets the same rows again), and the others still run.
    """
    moment = _moment(now)
    counts = {"pruned_days": 0, "pruned_attempts": 0}
    rules = (
        ("pruned_days", _prune_days),
        ("pruned_attempts", _prune_unsubmitted),
        ("pruned_attempts", _prune_expired),
    )
    for key, rule in rules:
        try:
            counts[key] += await rule(moment)
        except Exception:
            logger.exception("practice: retention rule %s failed", rule.__name__)
    return counts
