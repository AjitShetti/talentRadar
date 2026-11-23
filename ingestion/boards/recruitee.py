"""
ingestion/boards/recruitee.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Recruitee boards: ``GET {board}.recruitee.com/api/offers/``.

The board is a subdomain, so its id is checked to be a DNS label before it is put in a
host; one that is not is ``not_found`` without a request. The list carries each offer's
``description`` and ``requirements`` as HTML, so a board is one request. Dates are
``"YYYY-MM-DD HH:MM:SS UTC"``; ``published_at`` is the publish date.

The posting URL is built as ``{board}.recruitee.com/o/{slug}`` (the form
``domain.posting_urls`` reads back), since an offer's own ``careers_url`` can sit on a
custom domain; it is used only when an offer has no usable slug. A location is each of
the offer's ``locations`` as ``name, city, state, country``, ``;`` apart (``name`` is
what the employer shows, ``city`` is the structured one, and the two often differ), or,
for an offer with none, its ``location`` text. ``remote`` is Recruitee's own flag.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

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
    host_label,
    html_to_text,
    join_locations,
    one_line,
    place_name,
    posting_id,
    read_items,
    text_value,
)

_API = "https://{label}.recruitee.com/api/offers/"
_POSTING = "https://{label}.recruitee.com/o/{slug}"
_SLUG = re.compile(r"[a-z0-9-]{1,120}")
_STATED_FORMAT = "%Y-%m-%d %H:%M:%S UTC"


def _date(value: object) -> datetime | None:
    """A date as Recruitee states it, ``2026-11-12 06:42:00 UTC``, or as plain ISO text; else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), _STATED_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return parse_source_timestamp(value)


def _location(item: dict[str, Any]) -> str:
    places = item.get("locations")
    names = [
        place_name(entry.get("name"), entry.get("city"), entry.get("state"), entry.get("country"))
        for entry in (places if isinstance(places, list) else [])
        if isinstance(entry, dict)
    ]
    text = join_locations(names)
    return (
        text
        or one_line(item.get("location"))
        or place_name(item.get("city"), item.get("state_name"), item.get("country"))
    )


def _parse(item: Any, ref: BoardRef, label: str) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("title"))
    slug = text_value(item.get("slug")).strip()
    if _SLUG.fullmatch(slug):
        url = _POSTING.format(label=label, slug=slug)
    else:
        url = clean_url(item.get("careers_url")) or ""
    if not title or not url:
        return None

    remote = item.get("remote") is True
    return build_job(
        ref,
        external_id=posting_id(item.get("id")) or slug or None,
        title=title,
        url=url,
        location=_location(item),
        description=describe(
            html_to_text(text_value(item.get("description"))),
            html_to_text(text_value(item.get("requirements"))),
        ),
        is_remote=remote,
        posted_at=_date(item.get("published_at")),
        updated_at=_date(item.get("updated_at")),
        company_name=one_line(item.get("company_name")) or None,
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """Every offer on the board, descriptions included, in one request."""
    label = host_label(ref.board)
    if label is None:
        return failed("not_found")
    outcome, data = await get_json(client, _API.format(label=label))
    if outcome != "ok":
        return failed(outcome)
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        return failed("error")
    return await read_items(data["offers"], ref, lambda item, board: _parse(item, board, label))
