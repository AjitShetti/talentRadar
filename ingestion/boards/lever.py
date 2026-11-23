"""
ingestion/boards/lever.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Lever boards: ``GET api.lever.co/v0/postings/{board}?mode=json&skip=N&limit=100``.

A posting is about 17 KB with its description, so a big board does not fit one
response under the body cap. The board is read a page of 100 at a time, one page
after another, until a page comes back short; at most ``MAX_PAGES`` pages, and a read
that ends on a full last page is ``complete=False`` (there may be more).

The list carries each posting's description as plain text: the opening
(``descriptionPlain``), the requirement lists (``lists``, where "what you will need"
lives) and the company boilerplate (``additionalPlain``), kept in that order so a
truncated description loses the boilerplate first. ``createdAt`` (epoch
milliseconds) is the publish date. Lever's own ``Crawl-delay`` is one second, which
is the pace the sweep keeps.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from domain.entities import Job
from domain.job_boards import BoardRef
from domain.liveness import parse_source_timestamp
from ingestion.boards.base import (
    BoardRead,
    build_job,
    clean_url,
    describe,
    failed,
    get_json,
    html_to_text,
    one_line,
    plain_text,
    posting_id,
    read_items,
    text_value,
)

_API = "https://api.lever.co/v0/postings/{board}"
_FALLBACK_POSTING = "https://jobs.lever.co/{board}/{posting}"

#: Postings asked for per request, and the most requests one read makes.
PAGE_SIZE = 100
MAX_PAGES = 10


def _location(item: dict[str, Any]) -> str:
    categories = item.get("categories")
    if not isinstance(categories, dict):
        return ""
    primary = one_line(categories.get("location"))
    if primary:
        return primary
    everywhere = categories.get("allLocations")
    for place in everywhere if isinstance(everywhere, list) else []:
        if one_line(place):
            return one_line(place)
    return ""


def _text_or_html(item: dict[str, Any], plain: str, html: str) -> str:
    return plain_text(text_value(item.get(plain))) or html_to_text(text_value(item.get(html)))


def _description(item: dict[str, Any]) -> str | None:
    parts = [_text_or_html(item, "descriptionPlain", "description")]
    lists = item.get("lists")
    for entry in lists if isinstance(lists, list) else []:
        if isinstance(entry, dict):
            heading = one_line(entry.get("text"))
            body = html_to_text(text_value(entry.get("content")))
            parts.append("\n".join(part for part in (heading, body) if part))
    parts.append(_text_or_html(item, "additionalPlain", "additional"))
    return describe(*parts)


def _parse(item: Any, ref: BoardRef) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("text"))
    posting = posting_id(item.get("id"))
    url = clean_url(item.get("hostedUrl"))
    if url is None and posting:
        url = _FALLBACK_POSTING.format(
            board=quote(ref.board, safe=""), posting=quote(posting, safe="")
        )
    if not title or url is None:
        return None

    return build_job(
        ref,
        external_id=posting,
        title=title,
        url=url,
        location=_location(item),
        description=_description(item),
        is_remote=text_value(item.get("workplaceType")).lower() == "remote",
        posted_at=parse_source_timestamp(item.get("createdAt")),
        updated_at=parse_source_timestamp(item.get("updatedAt")),
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """
    Every posting on the board, descriptions included, a page at a time.

    A page that fails or is not a list fails the whole read: a half-read board says
    nothing about the roles it did not reach. Pages are sequential; pacing between
    them is the caller's.
    """
    url = _API.format(board=quote(ref.board, safe=""))
    postings: list[Any] = []
    reached_the_end = False
    for page in range(MAX_PAGES):
        params = {"mode": "json", "skip": str(page * PAGE_SIZE), "limit": str(PAGE_SIZE)}
        outcome, data = await get_json(client, url, params=params)
        if outcome != "ok":
            return failed(outcome)
        if not isinstance(data, list):
            return failed("error")
        postings.extend(data)
        if len(data) < PAGE_SIZE:
            reached_the_end = True
            break
    return await read_items(postings, ref, _parse, exhaustive=reached_the_end)
