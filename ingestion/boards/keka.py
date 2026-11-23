"""
ingestion/boards/keka.py
~~~~~~~~~~~~~~~~~~~~~~~~
Keka boards: ``GET {board}.keka.com/careers/api/jobs/default/active``.

Keka's robots.txt is ``Disallow: /`` with ``Allow: /careers/``, so this reader asks for
nothing outside ``/careers/``: the jobs list, and then, when the board has any job,
``/careers/api/organization/default/careerportalinfo`` for the company's name (the probe
checks it against the company it is looking for). That second request is optional: when
it fails or has no name the read is as good, and ``company_name`` is ``None``.

The board is a subdomain, so its id is checked to be a DNS label before it is put in a
host; one that is not is ``not_found`` without a request. The list is a bare array and
carries each job's description as HTML. A job names no URL, so it is built as
``{board}.keka.com/careers/jobdetails/{id}``. ``publishedOn`` is the publish date. A
location is each of the job's ``jobLocations`` as ``name, city, countryName``, ``;``
apart. Keka states no remote flag, so a location that says so is the only sign of it.
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
    looks_remote,
    one_line,
    place_name,
    plain_text,
    posting_id,
    read_items,
    text_value,
)

_JOBS = "https://{label}.keka.com/careers/api/jobs/default/active"
_PORTAL = "https://{label}.keka.com/careers/api/organization/default/careerportalinfo"
_POSTING = "https://{label}.keka.com/careers/jobdetails/{job}"
_JOB_ID = re.compile(r"[0-9]{1,12}")


def _location(item: dict[str, Any]) -> str:
    places = item.get("jobLocations")
    return join_locations(
        place_name(entry.get("name"), entry.get("city"), entry.get("countryName"))
        for entry in (places if isinstance(places, list) else [])
        if isinstance(entry, dict)
    )


def _parse(item: Any, ref: BoardRef, label: str, company_name: str | None) -> Job | None:
    if not isinstance(item, dict):
        return None
    title = one_line(item.get("title"))
    job = posting_id(item.get("id"))
    if not title or job is None or _JOB_ID.fullmatch(job) is None:
        return None

    location = _location(item)
    return build_job(
        ref,
        external_id=job,
        title=title,
        url=_POSTING.format(label=label, job=job),
        location=location,
        description=describe(
            html_to_text(text_value(item.get("description")))
            or plain_text(text_value(item.get("excerpt")))
        ),
        is_remote=looks_remote(location),
        posted_at=parse_source_timestamp(item.get("publishedOn")),
        company_name=company_name,
    )


async def _read_name(client: httpx.AsyncClient, label: str) -> str | None:
    """The company's name from the careers portal, or ``None`` for any answer that is not one."""
    outcome, data = await get_json(client, _PORTAL.format(label=label))
    if outcome != "ok" or not isinstance(data, dict):
        return None
    return one_line(data.get("name")) or None


async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead:
    """
    Every active job on the board, descriptions included, and the company's name.

    The name is asked for only after the jobs were read and only when there are some, so
    a board that is rate-limited or empty costs one request.
    """
    label = host_label(ref.board)
    if label is None:
        return failed("not_found")
    outcome, data = await get_json(client, _JOBS.format(label=label))
    if outcome != "ok":
        return failed(outcome)
    if not isinstance(data, list):
        return failed("error")
    name = await _read_name(client, label) if data else None
    result = await read_items(data, ref, lambda item, board: _parse(item, board, label, name))
    return replace(result, company_name=name)
