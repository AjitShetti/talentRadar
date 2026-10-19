"""
domain/practice_board.py
~~~~~~~~~~~~~~~~~~~~~~~~
The rules of the Practice leaderboards, as pure functions over plain values.

* **Names.** Everyone has a handle, ``builder-`` and four hex characters of the SHA-256 of
  their user id. A display name is opt-in; ``valid_display_name`` says what one may be (2 to 32
  letters, digits, spaces and ``. - _``; no ``@``, no link, nothing that starts like a handle).
* **A challenge board** ranks judged, unheld attempts by total, highest first. Equal totals
  share a rank (1, 1, 3) and are listed earliest snapshot first; a missing snapshot time comes
  after a known one, and the user id settles anything left, so the order never depends on the
  order the rows were read in.
* **The overall board** gives each person the sum of their best four totals (``OVERALL_BEST``),
  with the number of entries they have beside it. The caller decides which twelve challenges
  are in the window.
* **The view** is the top rows plus the viewer's own row wherever it falls. Nobody else below
  the cut is returned.

Nothing here reads a clock, a database or the network.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

#: How many totals make up a person's overall score.
OVERALL_BEST = 4

HANDLE_PREFIX = "builder-"
HANDLE_HEX_CHARS = 4

NAME_MIN_CHARS = 2
NAME_MAX_CHARS = 32
NAME_PUNCTUATION = " .-_"
#: Combining marks are allowed (Indic scripts need them) but not stacked into a smear.
_MAX_STACKED_MARKS = 3
#: One word that reads as a host name: dot-separated, the last part letters only ("ajit.dev").
#: "Ajit S." and "J.R." do not match, because the last part is empty or a single letter.
_LOOKS_LIKE_A_HOST = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}")
#: A word is checked without these around it: "ajit.dev." and ".ajit.dev" read as "ajit.dev".
_WORD_EDGE = ".-_"


def handle(user_id: str) -> str:
    """The pseudonym every board shows by default: ``builder-`` and 4 hex characters."""
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return HANDLE_PREFIX + digest[:HANDLE_HEX_CHARS]


def valid_display_name(name: str) -> bool:
    """Whether ``name`` may be shown on a board.

    2 to 32 characters; letters of any script (with their combining marks), digits, spaces and
    ``.``, ``-``, ``_`` only; it must start and end without a space and contain at least one
    letter or digit. ``@`` is outside that set, so an email cannot be shown, and neither can
    ``/``, ``:`` or ``<``; a word shaped like a host name (``ajit.dev``) is refused too, so a
    name cannot be a link, and so is one that starts with ``builder-`` in any case, so a name
    cannot pass for somebody's handle. That prefix is checked after NFKC normalising, so
    fullwidth look-alikes of its letters are refused too.
    """
    if not NAME_MIN_CHARS <= len(name) <= NAME_MAX_CHARS or name != name.strip():
        return False
    if unicodedata.normalize("NFKC", name).casefold().startswith(HANDLE_PREFIX):
        return False
    has_base = False
    after_letter = False  # the last character was a letter, a digit or a mark on one
    stacked = 0
    for char in name:
        category = unicodedata.category(char)
        if category[0] == "L" or category == "Nd":
            has_base = after_letter = True
            stacked = 0
        elif category in ("Mn", "Mc"):
            stacked += 1
            if not after_letter or stacked > _MAX_STACKED_MARKS:
                return False
        elif char in NAME_PUNCTUATION:
            after_letter = False
            stacked = 0
        else:
            return False
    if not has_base:
        return False
    words = (word.strip(_WORD_EDGE) for word in name.split(" "))
    return not any(_LOOKS_LIKE_A_HOST.fullmatch(word) for word in words)


@dataclass(frozen=True)
class Entry:
    """One judged, unheld attempt on a challenge board, with the name it is shown under."""

    user_id: str
    name: str
    total: float
    snapshot_pushed_at: datetime | None
    hours_used: float | None
    repo_url: str | None


@dataclass(frozen=True)
class OverallEntry:
    """One person on the overall board."""

    user_id: str
    name: str
    score: float
    entries: int


class _Owned(Protocol):
    @property
    def user_id(self) -> str: ...


_T = TypeVar("_T")
_R = TypeVar("_R", bound=_Owned)


def _competition_ranks(ordered: Sequence[_T], value: Callable[[_T], float]) -> list[tuple[int, _T]]:
    """Rank an already ordered sequence 1, 1, 3: equal values share the rank of the first."""
    ranked: list[tuple[int, _T]] = []
    rank = 0
    previous: float | None = None
    for position, item in enumerate(ordered, start=1):
        current = value(item)
        if previous is None or current != previous:
            rank = position
        previous = current
        ranked.append((rank, item))
    return ranked


def _utc_seconds(moment: datetime) -> float:
    """A snapshot time as seconds, a naive one read as UTC (SQLite hands datetimes back naive)."""
    return (moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment).timestamp()


def rank_entries(entries: Sequence[Entry]) -> list[tuple[int, Entry]]:
    """Rank a challenge's entries: total descending, equal totals sharing a rank.

    Among equal totals the earlier snapshot is listed first, an entry without a snapshot time
    after those with one, and the user id last, so the order is the same whatever the input order.
    """
    ordered = sorted(
        entries,
        key=lambda e: (
            -e.total,
            e.snapshot_pushed_at is None,
            _utc_seconds(e.snapshot_pushed_at) if e.snapshot_pushed_at is not None else 0.0,
            e.user_id,
        ),
    )
    return _competition_ranks(ordered, lambda e: e.total)


def overall_scores(
    totals: Mapping[str, Sequence[float]], best: int = OVERALL_BEST
) -> list[tuple[str, float, int]]:
    """``(user, score, entries)`` for each person with at least one entry, highest score first.

    A score is the sum of the person's ``best`` highest totals, to one decimal; ``entries`` is
    how many totals they have in all. Equal scores are listed by user id.
    """
    scores = [
        (user, round(sum(sorted(values, reverse=True)[:best]), 1), len(values))
        for user, values in totals.items()
        if values
    ]
    scores.sort(key=lambda item: (-item[1], item[0]))
    return scores


def rank_overall(entries: Sequence[OverallEntry]) -> list[tuple[int, OverallEntry]]:
    """Rank the overall board: score descending, equal scores sharing a rank, user id last."""
    ordered = sorted(entries, key=lambda e: (-e.score, e.user_id))
    return _competition_ranks(ordered, lambda e: e.score)


def board_view(
    ranked: Sequence[tuple[int, _R]], me: str, top: int
) -> tuple[list[tuple[int, _R]], tuple[int, _R] | None]:
    """The first ``top`` ranked rows, and the viewer's own row wherever it falls (or ``None``).

    The viewer's row is returned even when it is inside the top, so a caller can mark it either
    way. Rows below the cut that are not the viewer's are not returned.
    """
    rows = list(ranked[: max(top, 0)])
    mine = next((row for row in ranked if row[1].user_id == me), None)
    return rows, mine
