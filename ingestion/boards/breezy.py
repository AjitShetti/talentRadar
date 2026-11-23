"""
ingestion/boards/breezy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Breezy boards: ``GET {board}.breezy.hr/json``.

The board is a subdomain, so its id is checked to be a DNS label before it is put in a host;
one that is not makes no request. The response is a bare JSON array of positions, so one
request reads the whole board.

A position's posting URL is ``{board}.breezy.hr/p/{friendly_id}``, the form
``domain.posting_urls`` reads back. A position whose ``friendly_id`` is not that shape is
unreadable, so the read is incomplete. ``published_date`` is the day the position was
published, the only date used. A place is each of the position's ``locations`` (``"; "``
apart), or its ``location`` when it lists none, and a place with ``is_remote`` makes the role
remote. Each position states its company; the board's name is the first of them.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import httpx

from domain.entities import Job
from domain.job_boards import BoardRef
from domain.liveness import parse_source_timestamp
from ingestion.boards.base import (
    BoardRead,
    build_job,
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

_API = "https://{label}.breezy.hr/json"
_POSTING = "https://{label}.breezy.hr/p/{friendly}"
#: The one shape of ``friendly_id`` that ``domain.posting_urls`` reads back from a Breezy link.
_FRIENDLY_ID = re.compile(r"[0-9a-f]{8,16}[a-z0-9-]*")


def _named(value: object) -> object:
    """A place part Breezy states as an object with a ``name`` (a state, a country), or as text."""
    return value.get("name") if isinstance(value, dict) else value


def _places(item: dict[str, Any]) -> list[dict[str, Any]]:
    places = item.get("locations")
    named = (
        [entry for entry in places if isinstance(entry, dict)] if isinstance(places, list) else []
    )
    if named:
        return named
    single = item.get("location")
    return [single] if isinstance(single, dict) else []


def _place(entry: dict[str, Any]) -> str:
    return one_line(entry.get("name")) or place_name(
        entry.get("city"), _named(entry.get("state")), _named(entry.get("country"))
    )


def _company(item: object) -> str | None:
    company = item.get("company") if isinstance(item, dict) else None
    name = one_line(company.get("name")) if isinstance(company, dict) else ""
    return name or None


def _parse(item: Any, ref: BoardRef, label: str) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("name"))
    friendly = text_value(item.get("friendly_id")).strip()
    if not title or _FRIENDLY_ID.fullmatch(friendly) is None:
        return None
    places = _places(item)
    return build_job(
        ref,
        external_id=posting_id(item.get("id")) or friendly,
        title=title,
        url=_POSTING.format(label=label, friendly=friendly),
        location=join_locations(_place(entry) for entry in places),
        description=describe(html_to_text(text_value(item.get("description")))),
        is_remote=any(entry.get("is_remote") is True for entry in places),
        posted_at=parse_source_timestamp(item.get("published_date")),
        company_name=_company(item),
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """Every position on the board, and the company's name, in one request."""
    label = host_label(ref.board)
    if label is None:
        return failed("not_found")
    outcome, data = await get_json(client, _API.format(label=label))
    if outcome != "ok":
        return failed(outcome)
    if not isinstance(data, list):
        return failed("error")
    name = next((found for found in (_company(job) for job in data) if found), None)
    result = await read_items(data, ref, lambda item, board: _parse(item, board, label))
    return replace(result, company_name=name)
