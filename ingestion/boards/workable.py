"""
ingestion/boards/workable.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Workable boards: ``GET apply.workable.com/api/v1/widget/accounts/{board}``.

One request returns the account's ``name`` and every posting. The widget list carries
no description, so a Workable role has none (the sweep never fetches one per posting).
Workable answers a client faster than one request every ten seconds with an HTML 429
page, which :func:`get_json` reads as ``error``; the pace is the caller's.

The widget's own ``url`` is the short link ``apply.workable.com/j/<shortcode>``, which
names no board, so the posting URL is built as ``apply.workable.com/{board}/j/{shortcode}/``,
the form ``domain.posting_urls`` can read back; a posting whose shortcode is not the shape
that module accepts (``WORKABLE_SHORTCODE``) is unreadable, so the read is incomplete.
``published_on`` is the day a role was published; ``created_at`` is when it was drafted
and is not a date we use. A location is
``city, region, country``; a role open in several places lists each, ``;`` apart, and a
place Workable marks ``hidden`` is not named (a role with only hidden places has none, or
is ``Remote`` when it is telecommuting). A posting with no ``locations`` list at all
falls back to its own ``city``, ``state`` and ``country``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from domain.entities import Job
from domain.job_boards import BoardRef
from domain.liveness import parse_source_timestamp
from domain.posting_urls import WORKABLE_SHORTCODE
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

_API = "https://apply.workable.com/api/v1/widget/accounts/{board}"
_POSTING = "https://apply.workable.com/{board}/j/{shortcode}/"
#: The one shape of shortcode ``domain.posting_urls`` reads back, so a role stored here can be re-verified.
SHORTCODE = WORKABLE_SHORTCODE


def _location(item: dict[str, Any], remote: bool) -> str:
    places = item.get("locations")
    entries = (
        [entry for entry in places if isinstance(entry, dict)] if isinstance(places, list) else []
    )
    if entries:
        names = [
            place_name(entry.get("city"), entry.get("region"), entry.get("country"))
            for entry in entries
            if entry.get("hidden") is not True
        ]
    else:
        names = [place_name(item.get("city"), item.get("state"), item.get("country"))]
    text = join_locations(names)
    return text or ("Remote" if remote else "")


def _parse(item: Any, ref: BoardRef, company_name: str | None) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("title"))
    shortcode = text_value(item.get("shortcode")).strip()
    if not title or SHORTCODE.fullmatch(shortcode) is None:
        return None

    remote = item.get("telecommuting") is True
    return build_job(
        ref,
        external_id=shortcode,
        title=title,
        url=_POSTING.format(board=quote(ref.board, safe=""), shortcode=shortcode),
        location=_location(item, remote),
        description=None,
        is_remote=remote,
        posted_at=parse_source_timestamp(item.get("published_on")),
        company_name=company_name,
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """Every posting on the account, and the account's name, in one request."""
    outcome, data = await get_json(client, _API.format(board=quote(ref.board, safe="")))
    if outcome != "ok":
        return failed(outcome)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return failed("error")
    name = one_line(data.get("name")) or None
    result = await read_items(data["jobs"], ref, lambda item, board: _parse(item, board, name))
    return replace(result, company_name=name)
