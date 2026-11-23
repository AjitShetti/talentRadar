"""
ingestion/boards/greenhouse.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Greenhouse boards: ``GET boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true``.

``content=true`` is what puts each posting's description in the one list response,
so a board is one request, unless it is too big for the body cap with descriptions in
it: then it is read again without ``content`` and its roles carry no description. The
description arrives double-encoded (``&lt;p&gt;``): it is unescaped once, then cleaned
to text. ``first_published`` is when the role went up and ``updated_at`` moves on every
edit, so only the first is a publish date.
"""

from __future__ import annotations

from html import unescape
from typing import Any
from urllib.parse import quote

import httpx

from domain.entities import Job
from domain.job_boards import BoardRef
from domain.liveness import parse_source_timestamp
from ingestion.boards.base import (
    OVER_CAP,
    BoardRead,
    build_job,
    clean_url,
    describe,
    failed,
    get_json,
    html_to_text,
    looks_remote,
    one_line,
    posting_id,
    read_items,
    text_value,
)

_API = "https://boards-api.greenhouse.io/v1/boards/{board}"
_FALLBACK_POSTING = "https://boards.greenhouse.io/{board}/jobs/{posting}"


def _parse(item: Any, ref: BoardRef) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("title"))
    posting = posting_id(item.get("id"))
    url = clean_url(item.get("absolute_url"))
    if url is None and posting:
        url = _FALLBACK_POSTING.format(
            board=quote(ref.board, safe=""), posting=quote(posting, safe="")
        )
    if not title or url is None:
        return None

    place = item.get("location")
    location = one_line(place.get("name") if isinstance(place, dict) else place)
    return build_job(
        ref,
        external_id=posting,
        title=title,
        url=url,
        location=location,
        description=describe(html_to_text(unescape(text_value(item.get("content"))))),
        is_remote=looks_remote(location),
        posted_at=parse_source_timestamp(item.get("first_published")),
        updated_at=parse_source_timestamp(item.get("updated_at")),
        company_name=one_line(item.get("company_name")) or None,
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """
    Every posting on the board, descriptions included, in one request.

    A big board's ``content=true`` response can pass the body cap (about 9 KB a role).
    When that is why the read failed, and only then, the board is read again without
    ``content``: its roles then have no description, but the read is as complete as
    any other. Any other failure is reported as it is.
    """
    url = _API.format(board=quote(ref.board, safe="")) + "/jobs"
    outcome, data = await get_json(client, url, params={"content": "true"})
    if outcome == "error" and data is OVER_CAP:
        outcome, data = await get_json(client, url)
    if outcome != "ok":
        return failed(outcome)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return failed("error")
    return await read_items(data["jobs"], ref, _parse)


async def read_board_name(client: httpx.AsyncClient, ref: BoardRef) -> str | None:
    """The name Greenhouse states for the board (``GET /v1/boards/{board}``), or ``None``."""
    outcome, data = await get_json(client, _API.format(board=quote(ref.board, safe="")))
    if outcome != "ok" or not isinstance(data, dict):
        return None
    return one_line(data.get("name")) or None
