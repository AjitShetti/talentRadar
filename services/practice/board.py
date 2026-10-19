"""
services/practice/board.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The Practice leaderboards, the person's own Practice settings, and the admin's list of held
attempts.

* **Who is on a board.** Judged attempts that are not held and have a total. A queued, held,
  rejected, failed, missed, submitted or merely started attempt is not; an admin override is
  already the attempt's ``total``, so it ranks with the overridden total. The rules of the
  order are in ``domain.practice_board``.
* **Names.** A handle (``builder-7f3a``) unless the person chose a display name in their
  settings. The repo link appears only when they also opted in. Nothing else about an attempt
  reaches a board: no email, no user id, no proof code, no scores or notes, no write-up.
* **Cache.** The ranked board is kept for ten minutes (``tr:practice:board:<slug>`` and
  ``tr:practice:board:overall``) through ``CacheBackend``, so a popular page costs one query
  per ten minutes. The cached value stays inside the server; the viewer's own row and the
  ``you`` marks are made from it on every request, so one person's view is never served to
  another. Saving settings forgets the boards the person is on, so turning the repo link off or
  removing a name takes effect at once rather than ten minutes later. Nothing requires the
  cache: a miss, or a value that does not look right, is rebuilt from the database.
* **Provisional.** A challenge board says so until the edition's last possible submission has
  been saved and nothing for it is waiting to be judged (queued, or submitted and not yet
  queued).
* **Held list.** ``held_attempts`` is for an admin: which attempts the judge held, so a false
  hold can be found and cleared with ``judge.admin_clear``. It names the attempt and the
  edition and nothing the candidate wrote.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError

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
from domain.practice_repo import RepoRef, parse_repo_url
from domain.practice_schedule import GRACE
from services.base import parse_uuid
from services.cache_backend import CacheBackend
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from services.practice.judge import HOLD_CLEARED_FLAG, INJECTION_FLAG
from storage.database import AsyncSessionLocal
from storage.models import PracticeAttempt, PracticeChallenge, PracticeSettings

logger = logging.getLogger(__name__)

CHALLENGE_TOP = 25
OVERALL_TOP = 50
#: The overall board looks at this many of the most recent editions that have opened.
OVERALL_WINDOW = 12
#: How long a built board is kept.
BOARD_TTL_SECONDS = 600
#: The most attempts the admin list returns: holds in force first, then newest first.
HELD_LIMIT = 200

_BOARD_KEY = "tr:practice:board:"
_OVERALL_KEY = f"{_BOARD_KEY}overall"

_JUDGED = "judged"
_QUEUED = "queued"
_SUBMITTED = "submitted"

_NO_ACCOUNT = "We could not find your account."
_NO_BUILD = "We could not find that build."
_BAD_NAME = (
    "A leaderboard name is 2 to 32 letters, digits, spaces, dots, hyphens or underscores. "
    "It cannot hold an @ or a link, or start with builder-."
)
_TRY_AGAIN = "We could not save your settings just now. Try again."


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _moment(now: datetime | None = None) -> datetime:
    """``now`` as an aware UTC instant."""
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(UTC)


def _aware(moment: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; every stored instant is UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _user(user_id: str) -> uuid.UUID:
    user = parse_uuid(user_id)
    if user is None:
        raise PracticeNotFound(_NO_ACCOUNT)
    return user


def _shown_name(user: str, display_name: str | None) -> str:
    """The chosen display name if there is one that passes today's rules, else the handle."""
    if display_name and valid_display_name(display_name):
        return display_name
    return handle(user)


def _repo_link(owner: str | None, name: str | None) -> str | None:
    """The repo's page, built from the captured pieces and checked against the strict matcher."""
    if not owner or not name:
        return None
    url = RepoRef(owner, name).html_url
    return url if parse_repo_url(url) is not None else None


def _hours_used(started_at: datetime, pushed_at: datetime | None) -> float | None:
    """Hours from Start to the snapshot push, to one decimal; ``None`` without a snapshot."""
    if pushed_at is None:
        return None
    seconds = (_aware(pushed_at) - _aware(started_at)).total_seconds()
    return round(max(seconds, 0.0) / 3600, 1)


async def _opted_in(session: Any) -> dict[str, tuple[str | None, bool]]:
    """Display name and repo opt-in of everyone who chose either; all others show a handle."""
    rows = await session.execute(
        select(
            PracticeSettings.user_id, PracticeSettings.display_name, PracticeSettings.show_repo
        ).where(
            or_(PracticeSettings.display_name.is_not(None), PracticeSettings.show_repo.is_(True))
        )
    )
    return {str(user): (name, bool(show)) for user, name, show in rows}


# ---------------------------------------------------------------------------
# The challenge board
# ---------------------------------------------------------------------------


def _encode_entries(ranked: list[tuple[int, Entry]]) -> list[dict[str, Any]]:
    return [
        {
            "u": e.user_id,
            "n": e.name,
            "t": e.total,
            "p": e.snapshot_pushed_at.isoformat() if e.snapshot_pushed_at else None,
            "h": e.hours_used,
            "r": e.repo_url,
        }
        for _, e in ranked
    ]


def _decode_entry(raw: Any) -> Entry | None:
    if not isinstance(raw, dict):
        return None
    user, name, total, pushed, hours, repo = (
        raw.get("u"),
        raw.get("n"),
        raw.get("t"),
        raw.get("p"),
        raw.get("h"),
        raw.get("r"),
    )
    if not isinstance(user, str) or not isinstance(name, str):
        return None
    if isinstance(total, bool) or not isinstance(total, int | float):
        return None
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, int | float)):
        return None
    if repo is not None and not isinstance(repo, str):
        return None
    when: datetime | None = None
    if pushed is not None:
        if not isinstance(pushed, str):
            return None
        try:
            when = _aware(datetime.fromisoformat(pushed))
        except ValueError:
            return None
    return Entry(user, name, float(total), when, None if hours is None else float(hours), repo)


def _decode_challenge(raw: Any) -> tuple[bool, int, list[Entry]] | None:
    """A cached challenge board, or ``None`` when it is not exactly what was stored."""
    if not isinstance(raw, dict):
        return None
    provisional, queued, stored = raw.get("provisional"), raw.get("queued"), raw.get("entries")
    if not isinstance(provisional, bool) or isinstance(queued, bool):
        return None
    if not isinstance(queued, int) or not isinstance(stored, list):
        return None
    entries = [_decode_entry(item) for item in stored]
    if any(entry is None for entry in entries):
        return None
    return provisional, queued, [entry for entry in entries if entry is not None]


async def _build_challenge(slug: str, moment: datetime) -> tuple[bool, int, list[Entry]]:
    async with AsyncSessionLocal() as session:
        challenge = (
            await session.execute(select(PracticeChallenge).where(PracticeChallenge.slug == slug))
        ).scalar_one_or_none()
        if challenge is None or _aware(challenge.opens_at) > moment:
            raise PracticeNotFound(_NO_BUILD)
        rows = (
            await session.execute(
                select(
                    PracticeAttempt.user_id,
                    PracticeAttempt.total,
                    PracticeAttempt.started_at,
                    PracticeAttempt.snapshot_pushed_at,
                    PracticeAttempt.repo_owner,
                    PracticeAttempt.repo_name,
                ).where(
                    PracticeAttempt.challenge_id == challenge.id,
                    PracticeAttempt.status == _JUDGED,
                    PracticeAttempt.held.is_(False),
                    PracticeAttempt.total.is_not(None),
                )
            )
        ).all()
        counted = await session.execute(
            select(PracticeAttempt.status, func.count())
            .where(
                PracticeAttempt.challenge_id == challenge.id,
                PracticeAttempt.status.in_((_QUEUED, _SUBMITTED)),
            )
            .group_by(PracticeAttempt.status)
        )
        waiting: dict[str, int] = {status: int(count) for status, count in counted}
        opted = await _opted_in(session)
        closes_at = _aware(challenge.closes_at)
    entries: list[Entry] = []
    for user, total, started_at, pushed_at, owner, repo in rows:
        who = str(user)
        display_name, show_repo = opted.get(who, (None, False))
        entries.append(
            Entry(
                who,
                _shown_name(who, display_name),
                float(total),
                _aware(pushed_at) if pushed_at is not None else None,
                _hours_used(started_at, pushed_at),
                _repo_link(owner, repo) if show_repo else None,
            )
        )
    queued = int(waiting.get(_QUEUED, 0))
    provisional = moment < closes_at + GRACE or queued > 0 or int(waiting.get(_SUBMITTED, 0)) > 0
    return provisional, queued, entries


def _challenge_row(rank: int, entry: Entry, viewer: str) -> dict[str, Any]:
    return {
        "rank": rank,
        "name": entry.name,
        "total": round(entry.total, 1),
        "hours_used": entry.hours_used,
        "repo_url": entry.repo_url,
        "you": entry.user_id == viewer,
    }


async def challenge_board(
    user_id: str, slug: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """One edition's board: the top 25, the viewer's own row, and whether it is still provisional.

    Raises :class:`PracticeNotFound` for an edition that does not exist or has not opened.
    """
    moment = _moment(now)
    viewer = str(_user(user_id))
    key = f"{_BOARD_KEY}{slug}"
    cached = _decode_challenge(await CacheBackend.get_json(key))
    if cached is None:
        cached = await _build_challenge(slug, moment)
        await CacheBackend.set_json(
            key,
            {
                "provisional": cached[0],
                "queued": cached[1],
                "entries": _encode_entries(rank_entries(cached[2])),
            },
            BOARD_TTL_SECONDS,
        )
    provisional, queued, entries = cached
    ranked = rank_entries(entries)
    rows, mine = board_view(ranked, viewer, CHALLENGE_TOP)
    return {
        "provisional": provisional,
        "total_entries": len(ranked),
        "queued": queued,
        "rows": [_challenge_row(rank, entry, viewer) for rank, entry in rows],
        "me": _challenge_row(*mine, viewer) if mine is not None else None,
    }


# ---------------------------------------------------------------------------
# The overall board
# ---------------------------------------------------------------------------


def _decode_overall(raw: Any) -> tuple[int, list[OverallEntry]] | None:
    if not isinstance(raw, dict):
        return None
    window, stored = raw.get("window"), raw.get("entries")
    if isinstance(window, bool) or not isinstance(window, int) or not isinstance(stored, list):
        return None
    entries: list[OverallEntry] = []
    for item in stored:
        if not isinstance(item, dict):
            return None
        user, name, score, count = item.get("u"), item.get("n"), item.get("s"), item.get("e")
        if not isinstance(user, str) or not isinstance(name, str):
            return None
        if isinstance(score, bool) or not isinstance(score, int | float):
            return None
        if isinstance(count, bool) or not isinstance(count, int):
            return None
        entries.append(OverallEntry(user, name, float(score), count))
    return window, entries


async def _build_overall(moment: datetime) -> tuple[int, list[OverallEntry]]:
    async with AsyncSessionLocal() as session:
        editions = list(
            (
                await session.execute(
                    select(PracticeChallenge.id)
                    .where(PracticeChallenge.opens_at <= moment)
                    .order_by(PracticeChallenge.opens_at.desc())
                    .limit(OVERALL_WINDOW)
                )
            ).scalars()
        )
        if not editions:
            return 0, []
        rows = (
            await session.execute(
                select(PracticeAttempt.user_id, PracticeAttempt.total).where(
                    PracticeAttempt.challenge_id.in_(editions),
                    PracticeAttempt.status == _JUDGED,
                    PracticeAttempt.held.is_(False),
                    PracticeAttempt.total.is_not(None),
                )
            )
        ).all()
        opted = await _opted_in(session)
    totals: dict[str, list[float]] = defaultdict(list)
    for user, total in rows:
        totals[str(user)].append(float(total))
    entries = [
        OverallEntry(who, _shown_name(who, opted.get(who, (None, False))[0]), score, count)
        for who, score, count in overall_scores(totals)
    ]
    return len(editions), entries


def _overall_row(rank: int, entry: OverallEntry) -> dict[str, Any]:
    return {"rank": rank, "name": entry.name, "score": entry.score, "entries": entry.entries}


async def overall_board(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """The overall board: each person's best four totals over the last twelve editions.

    ``window_challenges`` is how many editions are in the window (twelve once there have been
    that many). The top 50 are returned, and the viewer's own row wherever it falls.
    """
    moment = _moment(now)
    viewer = str(_user(user_id))
    cached = _decode_overall(await CacheBackend.get_json(_OVERALL_KEY))
    if cached is None:
        cached = await _build_overall(moment)
        await CacheBackend.set_json(
            _OVERALL_KEY,
            {
                "window": cached[0],
                "entries": [
                    {"u": e.user_id, "n": e.name, "s": e.score, "e": e.entries}
                    for _, e in rank_overall(cached[1])
                ],
            },
            BOARD_TTL_SECONDS,
        )
    window, entries = cached
    rows, mine = board_view(rank_overall(entries), viewer, OVERALL_TOP)
    return {
        "window_challenges": window,
        "rows": [
            {**_overall_row(rank, entry), "you": entry.user_id == viewer} for rank, entry in rows
        ],
        "me": _overall_row(*mine) if mine is not None else None,
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _settings_payload(user: uuid.UUID, row: PracticeSettings | None) -> dict[str, Any]:
    if row is None:
        return {
            "display_name": None,
            "show_repo": False,
            "contest_reminder": True,
            "handle": handle(str(user)),
        }
    return {
        "display_name": row.display_name,
        "show_repo": bool(row.show_repo),
        "contest_reminder": bool(row.contest_reminder),
        "handle": handle(str(user)),
    }


def _checked_name(display_name: str | None) -> str | None:
    """The name to store: trimmed, ``None`` when blank, or :class:`PracticeInvalid`."""
    name = (display_name or "").strip()
    if not name:
        return None
    if not valid_display_name(name):
        raise PracticeInvalid(_BAD_NAME)
    return name


async def get_settings(user_id: str) -> dict[str, Any]:
    """The person's Practice settings; the defaults when they have never saved any."""
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        return _settings_payload(user, await session.get(PracticeSettings, user))


async def _forget_boards(user: uuid.UUID) -> None:
    """Drop the cached boards the person is on, so a changed name or repo setting shows at once.

    It runs after the settings are committed, so it never raises: a failure is logged and the
    boards catch up when their ten minutes are over.
    """
    try:
        async with AsyncSessionLocal() as session:
            slugs = list(
                (
                    await session.execute(
                        select(PracticeChallenge.slug)
                        .join(PracticeAttempt, PracticeAttempt.challenge_id == PracticeChallenge.id)
                        .where(PracticeAttempt.user_id == user)
                    )
                ).scalars()
            )
        await CacheBackend.delete(_OVERALL_KEY)
        for slug in slugs:
            await CacheBackend.delete(f"{_BOARD_KEY}{slug}")
    except Exception as exc:
        logger.warning("practice: could not forget the cached boards (%s)", type(exc).__name__)


async def put_settings(
    user_id: str, *, display_name: str | None, show_repo: bool, contest_reminder: bool
) -> dict[str, Any]:
    """Save the person's settings (all three at once) and return them.

    A blank name clears it. Raises :class:`PracticeInvalid` for a name that is not allowed and
    :class:`PracticeNotFound` for an account that does not exist.
    """
    user = _user(user_id)
    name = _checked_name(display_name)
    for _ in range(2):  # the second pass is for a double click that inserted the row first
        async with AsyncSessionLocal() as session:
            row = await session.get(PracticeSettings, user)
            if row is None:
                row = PracticeSettings(user_id=user)
                session.add(row)
            row.display_name = name
            row.show_repo = show_repo
            row.contest_reminder = contest_reminder
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                if await session.get(PracticeSettings, user) is None:
                    logger.warning("practice_settings insert failed without a competing row")
                    raise PracticeNotFound(_NO_ACCOUNT) from error
                continue
            payload = _settings_payload(user, row)
        await _forget_boards(user)
        return payload
    raise PracticeConflict(_TRY_AGAIN)


# ---------------------------------------------------------------------------
# The admin's list of held attempts
# ---------------------------------------------------------------------------


def _flag_names(flags: list[str]) -> list[str]:
    """The flags without the hash of the cleared input, which is for the judge to compare."""
    names: list[str] = []
    for flag in flags:
        shown = HOLD_CLEARED_FLAG if flag.startswith(f"{HOLD_CLEARED_FLAG}:") else flag
        if shown not in names:
            names.append(shown)
    return names


async def held_attempts() -> dict[str, Any]:
    """Attempts the judge held for text aimed at the grader: holds in force first, newest first.

    Each row is the attempt's id, the edition (slug and title), its status, the flags it
    carries, whether it is held now and whether an admin has cleared a hold on it. A cleared
    hold stays in the list, so it can be told apart from one nobody has read, and comes after
    every hold still in force, so a cleared one can never push an unread one off the page.
    ``submitted_at`` is the nearest time the row keeps: the hold itself is not timestamped.
    Nothing the candidate wrote, no repo and no user is included; the id is what
    ``judge.admin_clear`` takes.

    The database only matches the injection flag as text, so the rows are checked here and the
    list is cut to ``HELD_LIMIT`` afterwards: a row that is dropped never uses up a place.
    """
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    PracticeAttempt.id,
                    PracticeAttempt.status,
                    PracticeAttempt.flags,
                    PracticeAttempt.held,
                    PracticeAttempt.submitted_at,
                    PracticeChallenge.slug,
                    PracticeChallenge.title,
                )
                .join(PracticeChallenge, PracticeChallenge.id == PracticeAttempt.challenge_id)
                .where(
                    or_(
                        PracticeAttempt.held.is_(True),
                        cast(PracticeAttempt.flags, String).contains(
                            INJECTION_FLAG, autoescape=True
                        ),
                    )
                )
                .order_by(
                    PracticeAttempt.held.desc(),
                    PracticeAttempt.submitted_at.desc().nulls_last(),
                    PracticeAttempt.id,
                )
            )
        ).all()
    attempts: list[dict[str, Any]] = []
    for attempt_id, status, stored_flags, held, submitted_at, slug, title in rows:
        flags = [str(flag) for flag in (stored_flags or [])]
        if not held and INJECTION_FLAG not in flags:
            continue
        if len(attempts) == HELD_LIMIT:
            break
        attempts.append(
            {
                "id": str(attempt_id),
                "challenge": slug,
                "title": title,
                "status": status,
                "flags": _flag_names(flags),
                "held": bool(held),
                "cleared": any(
                    flag == HOLD_CLEARED_FLAG or flag.startswith(f"{HOLD_CLEARED_FLAG}:")
                    for flag in flags
                ),
                "submitted_at": _aware(submitted_at).isoformat() if submitted_at else None,
            }
        )
    return {"attempts": attempts}
