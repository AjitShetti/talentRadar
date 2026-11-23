"""
ingestion/boards/ashby.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Ashby boards: ``GET api.ashbyhq.com/posting-api/job-board/{board}``.

The list carries ``descriptionPlain`` (and ``descriptionHtml``), so a board is one
request. ``publishedAt`` is the publish date. A posting Ashby marks
``isListed: false`` is not on the public board and is left out of the count, so it
is never read as a role that closed.
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

_API = "https://api.ashbyhq.com/posting-api/job-board/{board}"
_FALLBACK_POSTING = "https://jobs.ashbyhq.com/{board}/{posting}"


def _parse(item: Any, ref: BoardRef) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("title"))
    posting = posting_id(item.get("id"))
    url = clean_url(item.get("jobUrl"))
    if url is None and posting:
        url = _FALLBACK_POSTING.format(
            board=quote(ref.board, safe=""), posting=quote(posting, safe="")
        )
    if not title or url is None:
        return None

    remote = item.get("isRemote") is True
    location = one_line(item.get("location")) or ("Remote" if remote else "")
    description = plain_text(text_value(item.get("descriptionPlain"))) or html_to_text(
        text_value(item.get("descriptionHtml"))
    )
    return build_job(
        ref,
        external_id=posting,
        title=title,
        url=url,
        location=location,
        description=describe(description),
        is_remote=remote,
        posted_at=parse_source_timestamp(item.get("publishedAt")),
        updated_at=parse_source_timestamp(item.get("updatedAt")),
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """Every listed posting on the board, descriptions included, in one request."""
    outcome, data = await get_json(client, _API.format(board=quote(ref.board, safe="")))
    if outcome != "ok":
        return failed(outcome)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return failed("error")
    listed = [
        item
        for item in data["jobs"]
        if not (isinstance(item, dict) and item.get("isListed") is False)
    ]
    return await read_items(listed, ref, _parse)
