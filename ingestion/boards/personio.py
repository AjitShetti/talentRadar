"""
ingestion/boards/personio.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Personio boards: ``GET {board}.jobs.personio.{de|com}/xml?language=en``.

The board is a subdomain, so its id is checked to be a DNS label before it is put in a host.
The region is the host's top-level part, ``de`` or ``com`` (a board with none is ``de``); any
other region makes no request.

The response is XML: a ``<workzag-jobs>`` root of ``<position>`` elements, read with
``xml.etree.ElementTree``. Two refusals come before any element is read. A body that declares
a DOCTYPE or an ENTITY is an ``error`` (no entity can expand, and no external one can be
fetched), and so is a body whose root is not ``workzag-jobs``: a valid XML page from a captive
portal is not a board, and must not read as one with no roles.

A position's ``id`` is its number and its posting URL is ``{board}.jobs.personio.{region}/job/{id}``.
``name`` is the title, ``createdAt`` the date the position was created (the only date the feed
states, so the publish date we have), and ``office`` the place. The feed states neither a remote
flag nor more than one place, so an office that says "Remote" is the only sign of remote. The
``jobDescriptions`` are each a heading and an HTML body, read as text.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
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
    get_bytes,
    host_label,
    html_to_text,
    looks_remote,
    one_line,
    read_items,
)

_API = "https://{label}.jobs.personio.{region}/xml"
_POSTING = "https://{label}.jobs.personio.{region}/job/{job}"
_REGIONS = frozenset({"de", "com"})
_DEFAULT_REGION = "de"
_ROOT = "workzag-jobs"
_JOB_ID = re.compile(r"[0-9]{1,12}")
#: A declaration that can expand entities or reach outside the body; refused before parsing.
_UNSAFE_XML = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def _board_root(body: bytes) -> ET.Element | None:
    """The board's root element, or ``None`` when the body is not a Personio board."""
    if _UNSAFE_XML.search(body) is not None:
        return None
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError, RecursionError):
        return None
    return root if root.tag == _ROOT else None


def _section(section: ET.Element) -> str:
    """One ``jobDescription``: its heading, then its body as plain text."""
    heading = one_line(section.findtext("name"))
    body = html_to_text(section.findtext("value") or "")
    return "\n".join(part for part in (heading, body) if part)


def _parse(item: Any, ref: BoardRef, label: str, region: str) -> Job | None:
    if not isinstance(item, ET.Element):
        return None
    title = one_line(item.findtext("name"))
    job = (item.findtext("id") or "").strip()
    if not title or _JOB_ID.fullmatch(job) is None:
        return None
    location = one_line(item.findtext("office"))
    return build_job(
        ref,
        external_id=job,
        title=title,
        url=_POSTING.format(label=label, region=region, job=job),
        location=location,
        description=describe(
            *(_section(section) for section in item.findall("jobDescriptions/jobDescription"))
        ),
        is_remote=looks_remote(location),
        posted_at=parse_source_timestamp(item.findtext("createdAt")),
    )


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """Every position on the board, descriptions included, in one request."""
    label = host_label(ref.board)
    region = _DEFAULT_REGION if ref.region is None else ref.region
    if label is None or region not in _REGIONS:
        return failed("not_found")
    outcome, body = await get_bytes(
        client, _API.format(label=label, region=region), params={"language": "en"}
    )
    if body is None:
        return failed(outcome)
    # A body near the cap takes tens of milliseconds to parse: not on the loop.
    root = await asyncio.to_thread(_board_root, body)
    if root is None:
        return failed("error")
    positions = root.findall("position")
    return await read_items(positions, ref, lambda item, board: _parse(item, board, label, region))
