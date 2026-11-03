"""
domain/routine.py
~~~~~~~~~~~~~~~~~
A user's daily routine: the blocks they set for the day and the ticks they make on it.

A block is a clock time (IST, 24 h) and what to do then: one of TalentRadar's own
activities, or a line the user wrote. The user sets the blocks; the day line draws them
and the Overview says which is next. Nothing here sends a reminder or does the work.

Everything is pure: no I/O, no clock. ``today`` is passed in, which is what lets the
Overview, the day line and a test table agree on when a tick expires.

Stored on ``profiles.routine`` as::

    {"blocks": [{"id", "at", "kind", "label"}, ...],
     "done": {"day": "YYYY-MM-DD", "ids": [...]}}

``done`` holds today's ticks only. It carries its own ``day``, so a tick made yesterday
reads as no tick today and no job has to reset anything at midnight. "Today" is an IST
day (see ``domain.event_time``).

A ``RoutineInvalid`` message is the sentence the user reads, so it says what to change.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime

from domain.event_time import ist_date

KINDS: tuple[str, ...] = (
    "apply",
    "connect",
    "applications",
    "practice",
    "mock",
    "events",
    "custom",
)

# What a block says when the user left its name empty. ``custom`` has none: the user's own
# text is the whole point of it.
DEFAULT_LABELS: dict[str, str] = {
    "apply": "Find roles and apply",
    "connect": "Reach out to people",
    "applications": "Follow up on applications",
    "practice": "Solve today's problem",
    "mock": "Mock interview",
    "events": "Check events",
}

MAX_BLOCKS = 8
LABEL_MAX = 60

# ASCII only: ``\d`` would also take digits from other scripts.
_ID = re.compile(r"[A-Za-z0-9_-]{1,36}")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")

# An id is made by the page, not typed, so a bad or repeated one means the page is stale.
_CANNOT_SAVE = "A block could not be saved. Reload the page and try again."


class RoutineInvalid(ValueError):  # noqa: N818 - reads as the condition, like StopIteration
    """The routine cannot be saved. The message is the sentence shown to the user."""


@dataclass(frozen=True)
class Block:
    id: str
    at: str  # "HH:MM", IST
    kind: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "at": self.at, "kind": self.kind, "label": self.label}


# --- time ---------------------------------------------------------------------------


def ist_day(now: datetime) -> date:
    """The calendar day it is in India at ``now`` (a naive time is read as UTC)."""
    return ist_date(now)


# --- the blocks ---------------------------------------------------------------------


def _label(value: object, kind: str) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise RoutineInvalid("A block's name has to be text.")
    label = " ".join(value.split())
    if not label:
        if kind == "custom":
            raise RoutineInvalid("Your own block needs a name.")
        return DEFAULT_LABELS[kind]
    if len(label) > LABEL_MAX:
        raise RoutineInvalid(f"A block's name can be at most {LABEL_MAX} characters.")
    return label


def _block(item: Mapping[str, object]) -> Block:
    block_id = item.get("id")
    if not isinstance(block_id, str) or _ID.fullmatch(block_id) is None:
        raise RoutineInvalid(_CANNOT_SAVE)
    at = item.get("at")
    if not isinstance(at, str) or _TIME.fullmatch(at) is None:
        raise RoutineInvalid("Give each block a time from 00:00 to 23:59, like 09:00.")
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise RoutineInvalid("Pick what each block is for.")
    return Block(id=block_id, at=at, kind=kind, label=_label(item.get("label"), kind))


def validate_blocks(raw: list[dict[str, object]]) -> list[Block]:
    """The blocks as they will be stored: cleaned, checked and in clock order.

    The label is trimmed and its runs of whitespace collapsed, and an empty one on a block
    that is not the user's own takes the kind's default. Raises ``RoutineInvalid``.
    """
    if len(raw) > MAX_BLOCKS:
        raise RoutineInvalid(f"A routine holds at most {MAX_BLOCKS} blocks.")
    blocks = [_block(item) for item in raw]
    ids: set[str] = set()
    times: set[str] = set()
    for block in blocks:
        if block.id in ids:
            raise RoutineInvalid(_CANNOT_SAVE)
        if block.at in times:
            raise RoutineInvalid(f"Two blocks are at {block.at}. Move one of them.")
        ids.add(block.id)
        times.add(block.at)
    return sorted(blocks, key=lambda block: block.at)


# --- the ticks ----------------------------------------------------------------------


def ticks_today(stored: Mapping[str, object] | None, today: date) -> list[str]:
    """The ids ticked on ``today``. Ticks from any other day are not ticks today."""
    done = stored.get("done") if stored is not None else None
    if not isinstance(done, Mapping) or done.get("day") != today.isoformat():
        return []
    ids = done.get("ids")
    if not isinstance(ids, list):
        return []
    ticked: list[str] = []
    for block_id in ids:
        if isinstance(block_id, str) and block_id not in ticked:
            ticked.append(block_id)
    return ticked


def _stored_blocks(stored: Mapping[str, object] | None) -> list[dict[str, object]]:
    """A copy of the stored blocks, so a result never shares a list with its input."""
    blocks = stored.get("blocks") if stored is not None else None
    if not isinstance(blocks, list):
        return []
    return [deepcopy(dict(block)) for block in blocks if isinstance(block, Mapping)]


def _routine(
    blocks: Sequence[Mapping[str, object]], ids: list[str], today: date
) -> dict[str, object]:
    return {"blocks": list(blocks), "done": {"day": today.isoformat(), "ids": ids}}


def with_tick(
    stored: Mapping[str, object] | None, block_id: str, done: bool, today: date
) -> dict[str, object]:
    """The routine with ``block_id`` ticked (``done``) or cleared, as of ``today``.

    Returns a new value and leaves ``stored`` as it was. Whether the id names a block is
    for the caller to check.
    """
    ids = ticks_today(stored, today)
    if done and block_id not in ids:
        ids.append(block_id)
    elif not done:
        ids = [ticked for ticked in ids if ticked != block_id]
    return _routine(_stored_blocks(stored), ids, today)


def with_blocks(
    stored: Mapping[str, object] | None, blocks: list[Block], today: date
) -> dict[str, object]:
    """The routine with its blocks replaced, keeping today's ticks of ids still present.

    ``blocks`` come from ``validate_blocks``. Returns a new value and leaves ``stored``
    as it was.
    """
    present = {block.id for block in blocks}
    ids = [ticked for ticked in ticks_today(stored, today) if ticked in present]
    return _routine([block.as_dict() for block in blocks], ids, today)
