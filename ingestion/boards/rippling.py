"""
ingestion/boards/rippling.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Rippling boards: ``GET ats.rippling.com/api/v2/board/{board}/jobs?page=N``.

The list is paged twenty rows at a time. The reader asks for at most :data:`MAX_PAGES`
pages (ten, two hundred rows); a read that reaches the last page is complete, and one that
stops at the cap is ``complete=False``, because a board can run to dozens of pages (Rippling's
own board had 38 when it was recorded).

The list has one row per (posting, place): a role open in three cities is three rows with one
``id`` and one URL. The rows are merged by id before they are read, so the role is one
:class:`Job` whose places are joined ``"; "`` apart, and a posting that appears on two pages is
still one role.

The list states neither a date nor a description, so a role has neither: ``posted_at`` is never
invented. A place whose ``workplaceType`` is ``REMOTE`` makes the role remote.

The board is a path segment, not a host, but it still goes into a request and into a posting
URL, so its id is checked to be a slug first; one that is not makes no request. Pages are
sequential, ``PACE_SECONDS`` apart; pacing between boards is the caller's.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from domain.entities import Job
from domain.job_boards import PACE_SECONDS, BoardRef
from ingestion.boards.base import (
    BoardRead,
    build_job,
    failed,
    get_json,
    join_locations,
    one_line,
    place_name,
    read_items,
    text_value,
)

_API = "https://ats.rippling.com/api/v2/board/{board}/jobs"
_POSTING = "https://ats.rippling.com/{board}/jobs/{posting}"
#: A board id as ``domain.job_boards`` reads it from a posting link: one path segment.
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_POSTING_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

#: The most pages one read asks for.
MAX_PAGES = 10

#: How the reader waits between two pages of one board: a module-level reference, so a
#: test can replace it and not really wait.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def _places(item: dict[str, Any]) -> list[dict[str, Any]]:
    places = item.get("locations")
    if not isinstance(places, list):
        return []
    return [entry for entry in places if isinstance(entry, dict)]


def _place(entry: dict[str, Any]) -> str:
    """A place as Rippling names it ("Bangalore, India"), or built from its parts when it names none."""
    return one_line(entry.get("name")) or place_name(
        entry.get("city"), entry.get("state"), entry.get("country")
    )


def _merge(rows: list[Any]) -> list[Any]:
    """
    One entry per posting: the first row for each id, with the places of every row for that id.

    A row with no usable id is kept as it is, so it is counted and read as unreadable.
    """
    postings: list[Any] = []
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = text_value(row.get("id")).strip() if isinstance(row, dict) else ""
        if not key:
            postings.append(row)
        elif key in by_id:
            by_id[key]["locations"].extend(_places(row))
        else:
            merged = {**row, "locations": _places(row)}
            by_id[key] = merged
            postings.append(merged)
    return postings


def _parse(item: Any, ref: BoardRef) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("name"))
    posting = text_value(item.get("id")).strip().lower()
    if not title or _POSTING_ID.fullmatch(posting) is None:
        return None
    places = _places(item)
    return build_job(
        ref,
        external_id=posting,
        title=title,
        url=_POSTING.format(board=ref.board, posting=posting),
        location=join_locations(_place(entry) for entry in places),
        description=None,
        is_remote=any(
            text_value(entry.get("workplaceType")).upper() == "REMOTE" for entry in places
        ),
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """
    The board's postings, a page at a time, up to :data:`MAX_PAGES` pages.

    A page that fails, or is not a page of the board (no ``items`` list, no whole
    ``totalPages``), fails the whole read: a half-read board says nothing about the roles it
    did not reach.
    """
    if _BOARD.fullmatch(ref.board) is None:
        return failed("not_found")
    url = _API.format(board=ref.board)
    rows: list[Any] = []
    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            await _sleep(PACE_SECONDS["rippling"])
        outcome, data = await get_json(client, url, params={"page": str(page)})
        if outcome != "ok":
            return failed(outcome)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return failed("error")
        total_pages = data.get("totalPages")
        if not isinstance(total_pages, int) or isinstance(total_pages, bool):
            return failed("error")
        rows.extend(data["items"])
        if page >= total_pages:
            return await read_items(_merge(rows), ref, _parse, exhaustive=True)
    return await read_items(_merge(rows), ref, _parse, exhaustive=False)
